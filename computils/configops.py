"""Project-level configuration (`cu -config`): inspect or edit ONLY the nearest project's `.computils/` files
(`project.toml`, `keylists.cusf`, and `spinstates.txt`). Global config (`~/bin`) and lab profiles are never modified."""
import sys
from contextlib import contextmanager
from pathlib import Path

import regex
from rich.markup import escape

from . import agent
from .catalog import ROUTE_LEAK_PATTERN, STAGE_SPLIT, ParseRouteTemplate, UnknownSelectors
from .console import console
from .defaults import DELETE, Defaults
from .fileops import (
    TAG_DIRECTIVE,
    ExtractFromText,
    OrcaBlockName,
    StageProblem,
)
from .keylists import (
    BENCHMARK,
    CONFORMER_SEARCH,
    MIXED_BASIS,
    OPTIMIZATION,
    ORCA_BLOCKS,
    SECTIONS,
    ClearCaches,
    DefaultBody,
    DefaultValue,
    GlobalLevel,
    KeylistsPath,
    Level,
    LevelSections,
    ReservedOrcaBlocks,
    SaveSection,
    SectionBody,
)
from .project import (
    PROJECT_CONFIG,
    FindProjectRoot,
    ProjectFilePath,
    ReloadConfig,
    ResolveProjectFile,
    SaveProjectConfig,
)
from .solvent import Known as KnownSolvents, Lookup as LookupSolvent
from .spin import ParseSpinOverrides, _LoadSpinOverrides

SPINSTATES_FILE = "spinstates.txt"
_NAME_PATTERN = regex.compile(r"^[A-Za-z0-9_.-]+$")
_ROUTE_LEAK_RE = regex.compile(ROUTE_LEAK_PATTERN)
_STAGE_SPLIT_RE = regex.compile(STAGE_SPLIT)
_TAG_FULL_RE = regex.compile(TAG_DIRECTIVE + r"\s*")

_SECTION_ALIASES: dict[str, str] = {
    "opt": OPTIMIZATION,
    "optimization": OPTIMIZATION,
    "optimization methods": OPTIMIZATION,
    "sp": BENCHMARK,
    "single point": BENCHMARK,
    "single point methods": BENCHMARK,
    "benchmark": BENCHMARK,
    "benchmark methods": BENCHMARK,
    "basis": MIXED_BASIS,
    "mixed basis": MIXED_BASIS,
    "orca": ORCA_BLOCKS,
    "orca blocks": ORCA_BLOCKS,
    "crest": CONFORMER_SEARCH,
    "conformer": CONFORMER_SEARCH,
    "conformer search": CONFORMER_SEARCH,
    "conformer search methods": CONFORMER_SEARCH,
}

_PROJECT_KEY_MAP: dict[str, str] = {key.lower(): key for key in Defaults._PROJECT_KEYS}


def _ReadExistingFile(text: str) -> str | None:
    """Return file contents if `text` points to an existing regular file, else None."""
    stripped = text.strip()
    if not stripped or "\n" in stripped:
        return None
    try:
        candidate = Path(stripped).expanduser()
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None
    return None


def ResolveSection(name: str) -> str:
    """Map a CLI section alias or canonical name to its keylists.cusf section header."""
    normalized = " ".join(name.strip().lower().split())
    section = _SECTION_ALIASES.get(normalized)
    if section is None:
        raise agent.UsageError(
            f"Unknown keylists section '{name}'. Choose one of: opt, sp, basis, orca, crest "
            f"(or full section names)."
        )
    return section


def ResolveProjectKey(name: str) -> str:
    """Match a project-overridable key name case-insensitively against Defaults._PROJECT_KEYS."""
    key = _PROJECT_KEY_MAP.get(name.strip().lower())
    if key is None:
        raise agent.UsageError(
            f"'{name.strip()}' cannot be set in project.toml. Allowed project keys: "
            f"{', '.join(Defaults._PROJECT_KEYS)}."
        )
    return key


def ValidateProjectValue(key: str, raw: str, *, allowEmptyCluster: bool = False) -> tuple[object, str]:
    """Validate and coerce a setting string for a project-overridable key: returns (value, '') or (None, problem)."""
    text = raw.strip()
    if key == "CPU":
        try:
            value = int(text)
        except ValueError:
            return None, f"CPU must be a whole number >= 1 (got '{raw}')."
        return (value, "") if value >= 1 else (None, f"CPU must be >= 1 (got {value}).")
    if key in ("memoryRatio", "highMemoryRatio"):
        try:
            value = float(text)
        except ValueError:
            return None, f"{key} must be a number > 0 (got '{raw}')."
        return (value, "") if value > 0 else (None, f"{key} must be > 0 (got {value}).")
    if key == "memoryBuffer":
        try:
            value = int(text)
        except ValueError:
            return None, f"memoryBuffer must be a whole number >= 0 (got '{raw}')."
        return (value, "") if value >= 0 else (None, f"memoryBuffer must be >= 0 (got {value}).")
    if key == "wallTime":
        if text.isdigit() and int(text) > 0:
            return text, ""
        return None, f"wallTime takes whole hours > 0 (got '{raw}')."
    if key == "cluster":
        if not text:
            return ("", "") if allowEmptyCluster else (
                None, f"cluster must be a single non-empty name with no spaces (got '{raw}')."
            )
        if any(ch.isspace() for ch in text):
            return None, f"cluster must be a single name with no spaces (got '{raw}')."
        return text, ""
    if key == "partition":
        if not text or any(ch.isspace() for ch in text):
            return None, f"partition must be a single non-empty name with no spaces (got '{raw}')."
        return text, ""
    if key == "openShellReference":
        upper = text.upper()
        return (upper, "") if upper in ("U", "RO") else (None, f"openShellReference must be U or RO (got '{raw}').")
    if key == "ossSpinThreshold":
        try:
            value = float(text)
        except ValueError:
            return None, f"ossSpinThreshold must be a number >= 0 (got '{raw}')."
        return (value, "") if value >= 0 else (None, f"ossSpinThreshold must be >= 0 (got {value}).")
    if key == "nboKeylist":
        return (text, "") if text.upper().startswith("$NBO") else (None, f"nboKeylist must start with '$NBO' (got '{raw}').")
    if key == "solvent":
        if text == "" or text.lower() in ("none", "gas", "gas phase"):
            return "", ""
        found = LookupSolvent(text)
        if found is None:
            return None, (
                f"Unknown solvent '{raw}'. Known solvents: {', '.join(KnownSolvents())} (or '' / gas for gas phase)."
            )
        return found.name, ""
    return None, f"Unhandled project key '{key}'."


def ParseProjectValue(key: str, raw: str):
    """Validate and coerce a CLI `--set KEY=VALUE` string for a project-overridable key."""
    value, problem = ValidateProjectValue(key, raw)
    if problem:
        raise agent.UsageError(problem)
    return value


def ProjectKeylistsPath(root: Path) -> Path:
    return KeylistsPath(ProjectFilePath(root, "").absolute())


def EffectiveSectionBody(section: str, projectPath: Path) -> str:
    """Return the project's section body if defined there, else the global body, else DefaultBody(section)."""
    if projectPath.is_file():
        body = SectionBody(projectPath.read_text(encoding="utf-8"), section)
        if body is not None:
            return body
    globalPath = GlobalLevel().path
    if globalPath.is_file():
        body = SectionBody(globalPath.read_text(encoding="utf-8"), section)
        if body is not None:
            return body
    return DefaultBody(section)


def ValidateSectionBody(section: str, body: str) -> None:
    """Validate a keylists.cusf section body in memory; raise agent.UsageError on any problem."""
    spec = SECTIONS[section]
    parsed = ExtractFromText(body, spec.extractor, empty=spec.empty())
    if section in (OPTIMIZATION, BENCHMARK):
        routes = list(parsed.values()) if section == OPTIMIZATION else list(parsed)
        if not routes:
            raise agent.UsageError(f"{section} must contain at least one route.")
        for route in routes:
            unknown = UnknownSelectors(route)
            if unknown:
                raise agent.UsageError(f"{section}: unknown selector {', '.join(unknown)} in '{route}'.")
            template = ParseRouteTemplate(route)
            for stage in template.stages:
                if _ROUTE_LEAK_RE.search(stage.base):
                    raise agent.UsageError(
                        f"{section}: malformed [ ] group or unknown % directive in '{route}'."
                    )
            problem = StageProblem(template)
            if problem:
                raise agent.UsageError(f"{section}: {problem} in '{route}'.")
    elif section == CONFORMER_SEARCH:
        from .crest import OptionsProblem
        if not parsed:
            raise agent.UsageError(f"{CONFORMER_SEARCH} must contain at least one search entry.")
        for label, options in parsed.items():
            problem = OptionsProblem(options)
            if problem:
                raise agent.UsageError(f"{CONFORMER_SEARCH} '{label}': {problem}.")
    elif section == ORCA_BLOCKS:
        reserved = ReservedOrcaBlocks(body)
        if reserved:
            raise agent.UsageError(f"{ORCA_BLOCKS}: %{reserved[0]} is written by CompUtils and not allowed here.")
        if not parsed:
            raise agent.UsageError(f"{ORCA_BLOCKS} must contain at least one valid %block ... end entry.")
    elif section == MIXED_BASIS:
        if not parsed or all(not (bs.basis or bs.ecp) for bs in parsed.values()):
            raise agent.UsageError(f"{MIXED_BASIS} must contain at least one basis set or ECP definition.")


def _DecodeBlockPayload(text: str) -> str:
    content = _ReadExistingFile(text)
    return (content if content is not None else text.replace("\\n", "\n")).strip("\r\n")


def _StripLeadingTag(text: str, name: str) -> str:
    lines = text.splitlines()
    if lines:
        match = _TAG_FULL_RE.fullmatch(lines[0].strip())
        if match and match.group(1).lower() == name.lower():
            return "\n".join(lines[1:]).strip("\r\n")
    return text.strip("\r\n")


def _FormatRouteText(routeText: str) -> str:
    """Convert inline 'stage1 -> stage2' or multiline text into keylists '>' stage lines."""
    rawLines = [line.strip() for line in _DecodeBlockPayload(routeText).splitlines() if line.strip()]
    stages = [part.strip() for line in rawLines for part in _STAGE_SPLIT_RE.split(line.lstrip("> ").strip())
              if part.strip()]
    if not stages:
        raise agent.UsageError("Route entry cannot be empty.")
    return "\n".join([stages[0], *(f"> {stage}" for stage in stages[1:])])


def _RouteEntrySpans(lines: list[str]) -> list[tuple[str, int, int]]:
    """Return (lowercased tag or '', spanStart, spanEnd) line indices for each entry in a route/option section."""
    spans: list[tuple[str, int, int]] = []
    pendingTag, tagLine = "", None
    for idx, line in enumerate(lines):
        stripped = line.strip()
        match = _TAG_FULL_RE.fullmatch(stripped)
        if match:
            pendingTag, tagLine = match.group(1).lower(), idx
            continue
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith(">"):
            if spans:
                tag, start, _ = spans[-1]
                spans[-1] = (tag, start, idx)
            continue
        start = tagLine if tagLine is not None else idx
        spans.append((pendingTag, start, idx))
        pendingTag, tagLine = "", None
    return spans


def _CloseSpans(headers: list[tuple[str, int]], lines: list[str]) -> list[tuple[str, int, int]]:
    spans = []
    for pos, (tag, start) in enumerate(headers):
        end = (headers[pos + 1][1] - 1) if pos + 1 < len(headers) else (len(lines) - 1)
        while end > start and not lines[end].strip():
            end -= 1
        spans.append((tag, start, end))
    return spans


def _TaggedBlockSpans(section: str, lines: list[str]) -> list[tuple[str, int, int]]:
    """Return (lowercased tag, spanStart, spanEnd) for Mixed Basis or ORCA Blocks."""
    if section == MIXED_BASIS:
        starts: list[tuple[str, int]] = []
        for idx, line in enumerate(lines):
            match = _TAG_FULL_RE.fullmatch(line.strip())
            if match:
                starts.append((match.group(1).lower(), idx))
        return _CloseSpans(starts, lines) if starts else []

    # ORCA_BLOCKS: '# @tag' or column-0 '%block' starts an entry
    headers: list[tuple[str, int]] = []
    pendingTag, tagLine = "", None
    for idx, line in enumerate(lines):
        match = _TAG_FULL_RE.fullmatch(line.strip())
        if match:
            pendingTag, tagLine = match.group(1).lower(), idx
        elif line.startswith("%"):
            blockName = OrcaBlockName(line)
            headers.append((pendingTag or blockName, tagLine if tagLine is not None else idx))
            pendingTag, tagLine = "", None
    return _CloseSpans(headers, lines)


def _SpliceLines(lines: list[str], start: int, end: int, replacement: list[str] | None) -> str:
    if replacement is None:
        # Also trim one adjacent blank separator so deletions stay tidy
        delEnd = end + 1
        if delEnd < len(lines) and not lines[delEnd].strip():
            delEnd += 1
        elif start > 0 and not lines[start - 1].strip():
            start -= 1
        newLines = lines[:start] + lines[delEnd:]
    else:
        newLines = lines[:start] + replacement + lines[end + 1:]
    return "\n".join(newLines).strip("\r\n")


def _SplitNameAndPayload(section: str, spec: str) -> tuple[str, str]:
    """Parse ENTRY specification into (tagName, payloadText)."""
    fromFile = _ReadExistingFile(spec)
    if fromFile is not None:
        stripped = fromFile.strip("\r\n")
        firstLine = stripped.splitlines()[0].strip() if stripped.splitlines() else ""
        tagMatch = _TAG_FULL_RE.fullmatch(firstLine)
        if tagMatch:
            return tagMatch.group(1).lower(), _StripLeadingTag(stripped, tagMatch.group(1))
        if section == ORCA_BLOCKS and stripped.lstrip().startswith("%"):
            return OrcaBlockName(stripped.lstrip()), stripped
        if section == BENCHMARK:
            return "", stripped
        raise agent.UsageError(f"{section}: file '{spec.strip()}' must start with '# @NAME'.")

    head, sep, tail = spec.partition(":")
    if sep and _NAME_PATTERN.fullmatch(head.strip()):
        name = head.strip().lower()
        payload = _DecodeBlockPayload(tail)
        payload = _StripLeadingTag(payload, name)
        return name, payload

    if section == BENCHMARK:
        return "", spec.strip()
    if section == ORCA_BLOCKS and spec.lstrip().startswith("%"):
        payload = _DecodeBlockPayload(spec)
        return OrcaBlockName(payload.lstrip()), payload
    raise agent.UsageError(
        f"{section}: expected 'NAME:VALUE' (or a file path); got '{spec}'."
    )


def UpsertSectionEntry(section: str, body: str, spec: str) -> tuple[str, str]:
    """Add or update an entry in `section`, returning (newBody, entryLabel)."""
    name, payload = _SplitNameAndPayload(section, spec)
    if section in (OPTIMIZATION, CONFORMER_SEARCH, BENCHMARK):
        formatted = _FormatRouteText(payload) if section != CONFORMER_SEARCH else " ".join(payload.split())
        if not formatted:
            raise agent.UsageError(f"{section}: entry value cannot be empty.")
        entryLines = ([f"# @{name}"] if name else []) + formatted.splitlines()
        lines = body.splitlines()
        spans = _RouteEntrySpans(lines)
        if name:
            hit = next((span for span in spans if span[0] == name), None)
            if hit is not None:
                newBody = _SpliceLines(lines, hit[1], hit[2], entryLines)
                ValidateSectionBody(section, newBody)
                return newBody, name
        elif section == BENCHMARK:
            # Avoid appending an identical unnamed single-point route twice
            existing = ExtractFromText(body, SECTIONS[BENCHMARK].extractor, empty=[])
            candidate = " -> ".join(line.lstrip("> ").strip() for line in formatted.splitlines())
            if candidate in existing:
                ValidateSectionBody(section, body)
                return body, candidate
        prefix = (body.rstrip("\r\n") + "\n\n") if body.strip() else ""
        newBody = (prefix + "\n".join(entryLines)).strip("\r\n")
        ValidateSectionBody(section, newBody)
        return newBody, name or formatted

    # MIXED_BASIS or ORCA_BLOCKS
    if not payload.strip():
        raise agent.UsageError(f"{section}: block body for '{name}' cannot be empty.")
    entryLines = [f"# @{name}"] + payload.splitlines()
    lines = body.splitlines()
    spans = _TaggedBlockSpans(section, lines)
    hit = next((span for span in spans if span[0] == name), None)
    if hit is not None:
        newBody = _SpliceLines(lines, hit[1], hit[2], entryLines)
    else:
        prefix = (body.rstrip("\r\n") + "\n\n") if body.strip() else ""
        newBody = (prefix + "\n".join(entryLines)).strip("\r\n")
    ValidateSectionBody(section, newBody)
    return newBody, name


def RemoveSectionEntry(section: str, body: str, target: str) -> str:
    """Remove a named entry (or 0-based index for Single Point Methods) from `section`."""
    wanted = target.strip().lower()
    if not wanted:
        raise agent.UsageError(f"{section}: entry name to remove cannot be empty.")
    lines = body.splitlines()
    if section in (OPTIMIZATION, BENCHMARK, CONFORMER_SEARCH):
        spans = _RouteEntrySpans(lines)
        hit = next((span for span in spans if span[0] == wanted), None)
        if hit is None and section == BENCHMARK and wanted.isdigit():
            idx = int(wanted)
            if 0 <= idx < len(spans):
                hit = spans[idx]
        if hit is None:
            raise agent.UsageError(f"{section}: no entry named '{target}' found.")
        newBody = _SpliceLines(lines, hit[1], hit[2], None)
    else:
        spans = _TaggedBlockSpans(section, lines)
        hit = next((span for span in spans if span[0] == wanted), None)
        if hit is None:
            raise agent.UsageError(f"{section}: no entry named '{target}' found.")
        newBody = _SpliceLines(lines, hit[1], hit[2], None)

    if not newBody.strip():
        raise agent.UsageError(
            f"Removing '{target}' would leave {section} empty. Use `--unset-section '{section}'` instead."
        )
    ValidateSectionBody(section, newBody)
    return newBody


def UpsertSpinState(text: str, pattern: str, state: str) -> str:
    """Add or update a `<pattern>  <css|oss>` line in spinstates.txt."""
    pat = pattern.strip()
    st = state.strip().lower()
    if not pat or any(ch.isspace() for ch in pat) or "#" in pat:
        raise agent.UsageError(f"spinstates.txt pattern cannot be empty or contain spaces/# (got '{pattern}').")
    if st not in ("css", "oss"):
        raise agent.UsageError(f"Spin state must be css or oss (got '{state}').")

    lines = text.splitlines()
    newLine = f"{pat}  {st}"
    replaced = False
    outLines = []
    for line in lines:
        fields = line.split("#", 1)[0].split()
        if fields and fields[0] == pat:
            if not replaced:
                outLines.append(newLine)
                replaced = True
            continue
        outLines.append(line)
    if not replaced:
        outLines.append(newLine)
    newText = "\n".join(outLines).rstrip("\r\n") + "\n"
    _, problems = ParseSpinOverrides(newText)
    if problems:
        raise agent.UsageError(f"Invalid spinstates.txt line {problems[0][0]}: '{problems[0][1]}'.")
    return newText


def RemoveSpinState(text: str, pattern: str) -> str:
    """Remove all entries matching `pattern` from spinstates.txt."""
    pat = pattern.strip()
    if not pat:
        raise agent.UsageError("Spin-state pattern to remove cannot be empty.")
    lines = text.splitlines()
    outLines, removed = [], False
    for line in lines:
        fields = line.split("#", 1)[0].split()
        if fields and fields[0] == pat:
            removed = True
            continue
        outLines.append(line)
    if not removed:
        raise agent.UsageError(f"spinstates.txt has no entry for '{pat}'.")
    return ("\n".join(outLines).rstrip("\r\n") + "\n") if outLines else ""


def _SplitPair(raw: str, sep: str, usageHint: str) -> tuple[str, str]:
    left, found, right = raw.partition(sep)
    if not found or not left.strip():
        raise agent.UsageError(f"Expected {usageHint} (got '{raw}').")
    return left.strip(), right


def _ParseKeylistsState(path: Path) -> dict[str, tuple[object, str]]:
    """Return {sectionName: (parsedValue, sourceLabel)} for effective keylists."""
    projectSections = LevelSections(Level("Project", path)) if path is not None else {}
    globalSections = LevelSections(GlobalLevel())
    state = {}
    for name in SECTIONS:
        if name in projectSections:
            state[name] = (projectSections[name][0], "project")
        elif name in globalSections:
            state[name] = (globalSections[name][0], "global")
        else:
            state[name] = (DefaultValue(name), "default")
    return state


def ConfigSnapshot() -> dict:
    """Build a JSON-friendly snapshot of the nearest project's effective settings, keylists, and spinstates."""
    root = FindProjectRoot()
    settings = {}
    for key in Defaults._PROJECT_KEYS:
        effective = getattr(Defaults, key)
        globalVal = Defaults.GlobalValue(key)
        overridden = root is not None and key in Defaults._projectValues
        entry = {"value": effective, "global": globalVal, "source": "project" if overridden else "global"}
        if overridden:
            entry["override"] = Defaults._projectValues[key]
        settings[key] = entry

    klPath = ProjectKeylistsPath(root) if root is not None else None
    klRaw = _ParseKeylistsState(klPath)
    keylistsOut = {}
    for name, (val, source) in klRaw.items():
        if name in (OPTIMIZATION, CONFORMER_SEARCH, ORCA_BLOCKS):
            entries = list(val.keys())
        elif name == BENCHMARK:
            entries = list(val)
        else:
            entries = [key or "(default)" for key in val.keys()]
        keylistsOut[name] = {"source": source, "entries": entries}

    spinPath = ProjectFilePath(root, SPINSTATES_FILE) if root is not None else None
    spinEntries = []
    if spinPath is not None and spinPath.is_file():
        overrides, _ = ParseSpinOverrides(spinPath.read_text(encoding="utf-8"))
        spinEntries = [{"pattern": pat, "spin": st.value} for pat, st in overrides]

    return {
        "projectRoot": str(root) if root is not None else None,
        "settings": settings,
        "keylists": keylistsOut,
        "spinstates": spinEntries,
    }


def _PrintSnapshot(snap: dict) -> None:
    from rich.box import SQUARE
    from rich.table import Table
    root = snap["projectRoot"] or "None (not inside a project; showing global defaults)"
    console.print(f"[operation]Project Config ({escape(str(root))})[/operation]")
    table = Table(box=SQUARE, show_lines=False)
    for col in ("Key", "Effective", "Global", "Source"):
        table.add_column(col)
    for key, info in snap["settings"].items():
        table.add_row(key, escape(repr(info["value"])), escape(repr(info["global"])), info["source"])
    console.print(table)
    for sec, info in snap["keylists"].items():
        items = ", ".join(info["entries"]) if info["entries"] else "None"
        console.print(f"[info]  {escape(sec)} ({info['source']}): {escape(items)}[/info]")
    if snap["spinstates"]:
        pairs = ", ".join(f"{e['pattern']}={e['spin']}" for e in snap["spinstates"])
        console.print(f"[info]  {SPINSTATES_FILE}: {escape(pairs)}[/info]")


@contextmanager
def _TemporarilySetSolvent(tempSolvent: str | None):
    """Context manager updating Defaults.solvent temporarily during validation."""
    prev = Defaults.solvent
    if tempSolvent is not None:
        Defaults.solvent = tempSolvent
    try:
        yield
    finally:
        Defaults.solvent = prev


def ApplyProjectConfig(intent) -> None:
    """Read or update project-level `.computils/` configuration (never global config)."""
    hasEdits = any((
        intent.setValues, intent.unsetKeys, intent.sections, intent.unsetSections,
        intent.addEntries, intent.removeEntries, intent.spinStates, intent.unsetSpinStates,
    ))
    if not hasEdits:
        snap = ConfigSnapshot()
        _PrintSnapshot(snap)
        agent.Result(snap)
        return

    root = FindProjectRoot()
    if root is None:
        agent.Stop(
            "Not inside a CompUtils project: project-level config requires a project root.",
            "cu -init --json",
            "Create a project root here first.",
        )
        return

    # 1. Validate & stage project.toml updates
    tomlUpdates: dict = {}
    setKeys, unsetKeys = set(), set()
    for pair in intent.setValues:
        rawKey, rawVal = _SplitPair(pair, "=", "KEY=VALUE")
        key = ResolveProjectKey(rawKey)
        if key in unsetKeys:
            raise agent.UsageError(f"Conflicting --set and --unset for '{key}'.")
        setKeys.add(key)
        tomlUpdates[key] = ParseProjectValue(key, rawVal)
    for rawKey in intent.unsetKeys:
        key = ResolveProjectKey(rawKey)
        if key in setKeys:
            raise agent.UsageError(f"Conflicting --set and --unset for '{key}'.")
        unsetKeys.add(key)
        tomlUpdates[key] = DELETE

    # Determine effective solvent for section validation (e.g. --set solvent=THF alongside --add-entry crest=...)
    effectiveSolvent = None
    if "solvent" in tomlUpdates:
        effectiveSolvent = (
            Defaults.GlobalValue("solvent") if tomlUpdates["solvent"] is DELETE else tomlUpdates["solvent"]
        )

    # 2. Validate & stage keylists.cusf changes
    klPath = ProjectKeylistsPath(root)
    klCurrent = klPath.read_text(encoding="utf-8") if klPath.is_file() else ""
    sectionEdits: dict[str, str | None] = {}
    addedSections, removedSections = set(), set()

    def WorkingBody(section: str) -> str:
        if section in sectionEdits and sectionEdits[section] is not None:
            return sectionEdits[section]  # type: ignore[return-value]
        return EffectiveSectionBody(section, klPath)

    with _TemporarilySetSolvent(effectiveSolvent):
        for pair in intent.sections:
            rawSec, rawContent = _SplitPair(pair, "=", "SECTION=CONTENT")
            sec = ResolveSection(rawSec)
            if sec in removedSections:
                raise agent.UsageError(f"Conflicting --section and --unset-section for '{sec}'.")
            addedSections.add(sec)
            body = _DecodeBlockPayload(rawContent)
            ValidateSectionBody(sec, body)
            sectionEdits[sec] = body

        for rawSec in intent.unsetSections:
            sec = ResolveSection(rawSec)
            if sec in addedSections:
                raise agent.UsageError(f"Conflicting --section and --unset-section for '{sec}'.")
            removedSections.add(sec)
            sectionEdits[sec] = None

        entryAudit: list[tuple[str, str, str]] = []  # (section, action, label)
        for pair in intent.addEntries:
            rawSec, rawEntry = _SplitPair(pair, "=", "SECTION=ENTRY")
            sec = ResolveSection(rawSec)
            if sec in removedSections:
                raise agent.UsageError(f"Cannot combine --add-entry and --unset-section for '{sec}'.")
            newBody, label = UpsertSectionEntry(sec, WorkingBody(sec), rawEntry)
            sectionEdits[sec] = newBody
            entryAudit.append((sec, "set-entry", label))

        for pair in intent.removeEntries:
            rawSec, rawTarget = _SplitPair(pair, "=", "SECTION=TARGET")
            sec = ResolveSection(rawSec)
            if sec in removedSections:
                raise agent.UsageError(f"Cannot combine --remove-entry and --unset-section for '{sec}'.")
            newBody = RemoveSectionEntry(sec, WorkingBody(sec), rawTarget)
            sectionEdits[sec] = newBody
            entryAudit.append((sec, "remove-entry", rawTarget.strip()))

    # 3. Validate & stage spinstates.txt changes
    spinPath = ProjectFilePath(root, SPINSTATES_FILE)
    spinOriginal = spinPath.read_text(encoding="utf-8") if spinPath.is_file() else ""
    spinText = spinOriginal
    setPatterns, unsetPatterns = set(), set()
    spinChanges: list[tuple[str, str]] = []
    for pair in intent.spinStates:
        pat, st = _SplitPair(pair, "=", "PATTERN=css|oss")
        if pat in unsetPatterns:
            raise agent.UsageError(f"Conflicting --spin-state and --unset-spin-state for '{pat}'.")
        setPatterns.add(pat)
        spinText = UpsertSpinState(spinText, pat, st)
        spinChanges.append((pat, st.strip().lower()))
    for rawPat in intent.unsetSpinStates:
        pat = rawPat.strip()
        if pat in setPatterns:
            raise agent.UsageError(f"Conflicting --spin-state and --unset-spin-state for '{pat}'.")
        unsetPatterns.add(pat)
        spinText = RemoveSpinState(spinText, pat)
        spinChanges.append((pat, "removed"))

    # 4. Compute exact diff records
    changes: list[dict] = []
    tomlPath = ProjectFilePath(root, PROJECT_CONFIG)
    appliedToml: dict = {}
    for key, val in tomlUpdates.items():
        hadOverride = key in Defaults._projectValues
        before = Defaults._projectValues[key] if hadOverride else None
        if val is DELETE:
            if not hadOverride:
                continue
            changes.append({"file": str(tomlPath), "kind": "unset", "target": key, "before": before, "after": None})
            appliedToml[key] = DELETE
        else:
            if hadOverride and before == val:
                continue
            changes.append({"file": str(tomlPath), "kind": "set", "target": key, "before": before, "after": val})
            appliedToml[key] = val

    appliedSections: dict[str, str | None] = {}
    for sec, newBody in sectionEdits.items():
        curBody = SectionBody(klCurrent, sec) if klCurrent else None
        if newBody is None:
            if curBody is None:
                continue
            changes.append({"file": str(klPath), "kind": "unset-section", "target": sec, "before": curBody, "after": None})
            appliedSections[sec] = None
        else:
            if curBody == newBody:
                continue
            changes.append({"file": str(klPath), "kind": "section", "target": sec, "before": curBody, "after": newBody})
            appliedSections[sec] = newBody

    spinChanged = spinText != spinOriginal
    if spinChanged:
        for pat, state in spinChanges:
            kind = "unset-spin-state" if state == "removed" else "spin-state"
            changes.append({
                "file": str(spinPath), "kind": kind, "target": pat,
                "after": None if state == "removed" else state,
            })

    # 5. Dry-run stops here without writing anything
    if agent.dryRun:
        if changes:
            console.print(f"[operation]Dry run ({escape(str(root))}): {len(changes)} planned change(s):[/operation]")
            for ch in changes:
                console.print(f"[info]  {ch['kind']} {escape(ch['target'])} -> {escape(repr(ch['after']))}[/info]")
            command = [arg for arg in sys.argv[1:] if arg != "--dry-run"]
            if command:
                agent.Next(agent.CommandLine(command), "Apply the previewed project config changes.", show=False)
        else:
            console.print("[info]Dry run: no changes needed (project config already matches).[/info]")
        agent.Result({"projectRoot": str(root), "dryRun": True, "changes": changes})
        return

    # 6. Apply changes atomically per file
    if appliedToml:
        if not SaveProjectConfig(root, appliedToml):
            agent.Fail(f"Could not update {tomlPath}.")
            return
        agent.Written(tomlPath)
    if appliedSections:
        for sec, newBody in appliedSections.items():
            if not SaveSection(klPath, sec, newBody):
                agent.Fail(f"Could not update {klPath} ({sec}).")
                return
        agent.Written(klPath)
    if spinChanged:
        spinPath.parent.mkdir(parents=True, exist_ok=True)
        spinPath.write_text(spinText, encoding="utf-8")
        agent.Written(spinPath)

    if not changes:
        console.print("[info]No changes needed (project config already matches).[/info]")
        agent.Acted()

    # Clear caches and reload project overlay so ConfigSnapshot() reflects saved state
    ClearCaches()
    _LoadSpinOverrides.cache_clear()
    ResolveProjectFile.cache_clear()
    ReloadConfig(readGlobals=False)

    snap = ConfigSnapshot()
    if changes:
        console.print(f"[good]Updated project config in {escape(str(root))} ({len(changes)} change(s)).[/good]")
    agent.Result({"changes": changes, **snap})
