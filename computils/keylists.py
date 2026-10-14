"""jobRelatedKeylists: every job-running set in one sectioned file (TUI_DESIGN 4.12).

    === Optimization Methods ===     named routes for the Optimization builder ('# @NAME' above each; '>' later stages)
    === Benchmark Methods ===        the benchmark suite, one route per entry (entry 0 is -sp's default)
    === Mixed Basis ===              Gen/GenECP basis groups and ECPs
    === ORCA Blocks ===              %blocks, pulled into ORCA jobs by {tag} tokens

Each section keeps its own format and extractor (fileops). The file has no extension: it is edited in the TUI (Config ›
Job Keylists, one section at a time). It lives at two levels, and the nearer level that HAS a section wins, section by
section: the project marker (<root>/.computils/), then binDirectory (global). So a project can override only its
Benchmark Methods and keep the global ORCA blocks. A copy in the CWD is never read.

Until `cu -refresh` merges them (MergeLegacy), the files these sections replaced (mixedbasis.txt, orcablocks.txt,
optmethods.txt in the marker) and the old benchmarkMethods TOML key (project.toml, programs.toml) still stand in for
their section at their level. No Textual import."""
import functools
import tomllib as tom
from dataclasses import dataclass
from pathlib import Path

import regex
from rich.markup import escape

from .console  import console
from .defaults import BackUpFile, Defaults
from .fileops  import (ExtractBenchmarkMethods, ExtractFromText, ExtractMixedBasis, ExtractOptMethods, ExtractOrcaBlocks,
                       HasContent, JoinStages, MapFile, MixedBasis, NamedRoutes, RouteEntries)
from .project  import FindProjectRoot, ProjectFilePath

KEYLISTS_FILE = "jobRelatedKeylists"
OPTIMIZATION, BENCHMARK, MIXED_BASIS, ORCA_BLOCKS = "Optimization Methods", "Benchmark Methods", "Mixed Basis", "ORCA Blocks"
# The TOML key the Benchmark Methods section replaced (programs.toml, project.toml, a lab profile's [settings])
LEGACY_KEY = "benchmarkMethods"
# The suite when no level has one
DEFAULT_BENCHMARK = ["M062X 6-311+G(d,p)"]
# '=== Name ===' on a line of its own. Nothing in any section's format starts with '='
SECTION_HEADER = r"(?m)^===[ \t]*(.*?)[ \t]*===[ \t]*(?=\r?$)"
_HEADER_TEXT, _HEADER_BYTES = regex.compile(SECTION_HEADER), regex.compile(SECTION_HEADER.encode())
FILE_PREAMBLE = ("# jobRelatedKeylists -- CompUtils' job-related keylists. Edit them in the TUI: Config > Job Keylists.\n"
                 "# Each section starts at its '=== Name ===' line; a section a level doesn't have follows the next level.\n")


@dataclass(frozen=True)
class Section:
    name: str
    extractor: object       # fileops extractor run on the section's text
    empty: object           # zero-argument factory: the value without the section
    legacyFile: str = ""    # the project file this section replaced (read until `cu -refresh` merges it)


SECTIONS = {section.name: section for section in (
    Section(OPTIMIZATION, ExtractOptMethods, dict, "optmethods.txt"),
    Section(BENCHMARK, ExtractBenchmarkMethods, list),
    Section(MIXED_BASIS, ExtractMixedBasis, lambda: MixedBasis([], []), "mixedbasis.txt"),
    Section(ORCA_BLOCKS, ExtractOrcaBlocks, dict, "orcablocks.txt"),
)}


def SectionName(header: str) -> str:
    """A header's section, matched case-insensitively ('' for an unknown one)."""
    return next((name for name in SECTIONS if name.lower() == header.strip().lower()), "")


# ─── Splitting a file's text ──────────────────────────────────────────

@dataclass
class Part:
    """One header and its body, as written (an unknown header keeps its text, never parsed)."""
    header: str     # the header line, as written
    name: str       # its section ('' if unknown)
    body: str       # everything up to the next header


def SplitSections(text: str) -> tuple[str, list[Part]]:
    """(text before the first header, the parts in order)."""
    headers = list(_HEADER_TEXT.finditer(text))
    preamble = text[:headers[0].start()] if headers else text
    parts = [Part(header.group(0), SectionName(header.group(1)),
                  text[header.end():headers[index + 1].start() if index + 1 < len(headers) else len(text)])
             for index, header in enumerate(headers)]
    return preamble, parts


def SectionBody(text: str, name: str) -> str | None:
    """A section's text (its first occurrence) without the blank lines around it; None if the text has no such section."""
    _, parts = SplitSections(text)
    part = next((part for part in parts if part.name == name), None)
    return None if part is None else part.body.strip("\r\n")


def ReplaceSection(text: str, name: str, body: str) -> str:
    """text with a section's body replaced (added at the end, header and all, if it has none). Every other part is
    kept as written."""
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = body.strip("\r\n").replace("\r\n", "\n")
    written = (newline + lines.replace("\n", newline) + newline if lines else newline) + newline
    preamble, parts = SplitSections(text)
    for part in parts:
        if part.name == name:
            part.body = written
            break
    else:
        if parts or preamble.strip():
            # A blank line between the last part and the new header
            last = parts[-1] if parts else None
            if last is not None and not last.body.endswith(newline * 2):
                last.body = last.body.rstrip("\r\n") + newline * 2
            elif last is None and not preamble.endswith(newline * 2):
                preamble = preamble.rstrip("\r\n") + newline * 2
        parts.append(Part(f"=== {name} ===", name, written))
    return preamble + "".join(part.header + part.body for part in parts)


def RemoveSection(text: str, name: str) -> str:
    """text without a section (its header and body), so that level follows the next one for it."""
    preamble, parts = SplitSections(text)
    return preamble + "".join(part.header + part.body for part in parts if part.name != name)


def FileProblems(text: str) -> list[str]:
    """What in a keylists file is ignored: unknown headers, a section written twice, text before the first header."""
    preamble, parts = SplitSections(text)
    problems = []
    if any(line.strip() and not line.lstrip().startswith("#") for line in preamble.splitlines()):
        problems.append("Text Before the First Section Is Ignored")
    seen = set()
    for part in parts:
        if not part.name:
            problems.append(f"Unknown Section '{part.header.strip('= ').strip()}' Is Ignored")
        elif part.name in seen:
            problems.append(f"{part.name} Appears Twice: the First Is Used")
        seen.add(part.name)
    return problems


def _ParseParts(parts) -> dict:
    """{section: parsed value} from (name, body text) pairs; a section written twice keeps its first. A Benchmark Methods
    section with no routes counts as missing (an empty suite runs nothing), so the next level's applies."""
    parsed = {}
    for name, body in parts:
        if name and name not in parsed:
            section = SECTIONS[name]
            parsed[name] = ExtractFromText(body, section.extractor, empty=section.empty())
    if BENCHMARK in parsed and not parsed[BENCHMARK]:
        del parsed[BENCHMARK]
    return parsed


def RouteAt(name: str, text: str, row: int) -> tuple[str, int, int, str] | None:
    """The route on a methods section's line (the TUI editor's cursor): (its label, the stage the line is, stage count,
    the whole route card, stages joined), or None off a route. Labels are the Optimization Methods' names, or 'Entry N'
    (Benchmark)."""
    entries = ExtractFromText(text, RouteEntries, empty=[])
    labelled = (NamedRoutes(entries) if name == OPTIMIZATION
                else [(f"Entry {index}", stages) for index, (_, stages) in enumerate(entries)])
    for label, stages in labelled:
        for number, (line, route) in enumerate(stages, 1):
            if line == row:
                return label, number, len(stages), JoinStages(stages)
    return None


def ParseKeylists(text: str) -> dict:
    """Every section a keylists text has, parsed (the TUI's unsaved text)."""
    return _ParseParts((part.name, part.body) for part in SplitSections(text)[1])


# Parsed once per run per file, however many jobs read it (project.ReloadConfig clears it after a save)
@functools.cache
def _LoadKeylists(path: Path) -> dict:
    # One map: the headers are found in it, and each section's text is parsed by its own extractor
    with MapFile(path) as data:
        headers = list(_HEADER_BYTES.finditer(data))
        parts = [(SectionName(header.group(1).decode()),
                  data[header.end():headers[index + 1].start() if index + 1 < len(headers) else len(data)].decode())
                 for index, header in enumerate(headers)]
    return _ParseParts(parts)


@functools.cache
def _LoadLegacyFile(path: Path, name: str):
    section = SECTIONS[name]
    with MapFile(path) as data:
        return section.extractor(data)


@functools.cache
def _LegacyBenchmark(tomlPath: Path) -> list[str] | None:
    """The old benchmarkMethods key in a TOML file, if it holds a list of route strings."""
    try:
        with open(tomlPath, "rb") as file:
            value = tom.load(file).get(LEGACY_KEY)
    except (OSError, tom.TOMLDecodeError):
        return None
    if isinstance(value, list) and value and all(isinstance(entry, str) for entry in value):
        return list(value)
    return None


def ClearCaches() -> None:
    for loader in (_LoadKeylists, _LoadLegacyFile, _LegacyBenchmark):
        loader.cache_clear()


# ─── Levels ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Level:
    label: str                  # Project, Global
    path: Path                  # this level's jobRelatedKeylists (absolute; may not exist)
    legacyFolder: Path | None   # where the files the sections replaced are still read from
    legacyToml: Path | None     # the TOML whose old benchmarkMethods key is still read


def GlobalLevel() -> Level:
    binDirectory = Path(Defaults.binDirectory).expanduser().absolute()
    return Level("Global", binDirectory / KEYLISTS_FILE, None, binDirectory / "programs.toml")


def Levels() -> list[Level]:
    """The levels, nearest first: the project's (inside one), then the global one. Config is Global / Project only:
    a copy in the CWD is never read."""
    root = FindProjectRoot()
    levels = []
    if root is not None:
        marker = ProjectFilePath(root, "").absolute()
        levels.append(Level("Project", marker / KEYLISTS_FILE, marker, marker / "project.toml"))
    global_ = GlobalLevel()
    if all(global_.path != level.path for level in levels):
        levels.append(global_)
    return levels


def LevelSections(level: Level, texts: dict[Path, str] | None = None) -> dict[str, tuple[object, Path]]:
    """{section: (parsed value, the file it came from)} for every section this level has: its keylists file (or its
    unsaved text in texts), then the legacy files and TOML key standing in for sections it doesn't have."""
    if texts and level.path in texts:
        parsed = ParseKeylists(texts[level.path])
    else:
        parsed = _LoadKeylists(level.path) if HasContent(level.path) else {}
    found = {name: (value, level.path) for name, value in parsed.items()}
    for section in SECTIONS.values():
        if section.name in found:
            continue
        if section.legacyFile and level.legacyFolder is not None:
            legacy = level.legacyFolder / section.legacyFile
            if HasContent(legacy):
                found[section.name] = (_LoadLegacyFile(legacy, section.name), legacy)
        elif section.name == BENCHMARK and level.legacyToml is not None and level.legacyToml.is_file():
            entries = _LegacyBenchmark(level.legacyToml)
            if entries is not None:
                found[BENCHMARK] = (entries, level.legacyToml)
    return found


def KeylistSource(name: str, texts: dict[Path, str] | None = None) -> tuple[Level, Path] | None:
    """(the level, the file) a section comes from, or None when no level has it."""
    for level in Levels():
        found = LevelSections(level, texts)
        if name in found:
            return level, found[name][1]
    return None


def Keylist(name: str, texts: dict[Path, str] | None = None):
    """A section's parsed value from the nearest level that has it: OptMethods' {name: route}, the benchmark list,
    MixedBasis, ORCA {tag: block}. Without one anywhere: its empty value (the default suite for Benchmark Methods).
    texts ({level path: unsaved text}) stands in for files (the TUI's config editor)."""
    for level in Levels():
        found = LevelSections(level, texts)
        if name in found:
            return found[name][0]
    return list(DEFAULT_BENCHMARK) if name == BENCHMARK else SECTIONS[name].empty()


def Where(name: str) -> str:
    """Where to add a missing section, for skip reasons."""
    return f"Add It in Config › Job Keylists › {name}"


# ─── Writing ──────────────────────────────────────────────────────────

def WriteKeylists(path: Path, text: str) -> bool:
    # Never creates its folder (binDirectory is setup's to create, as for the TOMLs)
    try:
        with open(path, "w", encoding="utf-8", newline="") as file:
            file.write(text)
        return True
    except OSError as error:
        console.print(f"[error]Could not write {path}: {error}[/error]")
        return False


def SaveSection(path: Path, name: str, body: str | None) -> bool:
    """Write one section's body into a level's file (None removes the section), keeping every other part as written.
    A missing file is created."""
    current = path.read_text(encoding="utf-8") if path.is_file() else FILE_PREAMBLE + "\n"
    text = RemoveSection(current, name) if body is None else ReplaceSection(current, name, body)
    ClearCaches()
    return WriteKeylists(path, text)


def DefaultText(benchmark: list[str]) -> str:
    """A new global file: every section's header, the benchmark suite in its section."""
    text = FILE_PREAMBLE
    for name in SECTIONS:
        text = ReplaceSection(text, name, "\n".join(benchmark) if name == BENCHMARK else "")
    return text


def EnsureGlobalKeylists() -> bool:
    """Generate the global file if it is missing (Defaults.Load, like a missing TOML): its Benchmark Methods seeded from
    programs.toml's old benchmarkMethods key, else the default suite. True if it was written."""
    global_ = GlobalLevel()
    if global_.path.exists():
        return False
    seeded = _LegacyBenchmark(global_.legacyToml) if global_.legacyToml.is_file() else None
    console.print(f"[warning]\\[config] {KEYLISTS_FILE} not found — generating it"
                  f"{' (Benchmark Methods from programs.toml)' if seeded else ''}.[/warning]")
    if not WriteKeylists(global_.path, DefaultText(seeded or DEFAULT_BENCHMARK)):
        return False
    ClearCaches()
    return True


def GlobalBenchmark() -> list[str] | None:
    """The global level's Benchmark Methods (its file, else programs.toml's old key), or None without one."""
    global_ = GlobalLevel()
    found = LevelSections(global_).get(BENCHMARK)
    return None if found is None else found[0]


def SetGlobalBenchmark(entries: list[str]) -> bool:
    """A lab profile's benchmarkMethods: the global file's Benchmark Methods section."""
    global_ = GlobalLevel()
    return SaveSection(global_.path, BENCHMARK, "\n".join(entries))


def _Difference(expected, readBack) -> str:
    """The first thing a merged section would read back differently, for MergeLegacy's message."""
    if isinstance(expected, list):
        readBack = readBack or []
        for index, entry in enumerate(expected):
            if index >= len(readBack):
                return f": entry {index} '{entry}' would be lost"
            if readBack[index] != entry:
                return f": entry {index} '{entry}' would read as '{readBack[index]}'"
        return f": it would gain entry {len(expected)} '{readBack[len(expected)]}'"
    if isinstance(expected, dict):
        readBack = readBack or {}
        for key, value in expected.items():
            if key not in readBack:
                return f": '{key}' would be lost"
            if readBack[key] != value:
                return f": '{key}' would read differently"
        return f": it would gain '{next(key for key in readBack if key not in expected)}'"
    return ": its basis or ECP entries would read differently"


def MergeLegacy(level: Level) -> bool:
    """`cu -refresh`: write what the old files and TOML key give this level into its keylists file, section by section
    (only for sections it doesn't have yet), and back the merged old files up to .bak (refresh drops the TOML key
    itself). A section is merged only if it reads back the same: TOML entries as the old Catalog read them (stripped,
    blank ones skipped), old files as their extractor reads them (bytes, line endings as written). A section that
    wouldn't is named and left where it is. False if any was left, so refresh rewrites nothing that still holds it."""
    found = LevelSections(level)
    legacy = {name: (value, source) for name, (value, source) in found.items() if source != level.path}
    if not legacy:
        return True
    text = level.path.read_text(encoding="utf-8") if level.path.is_file() else FILE_PREAMBLE + "\n"
    merged, left = [], []
    for name, (value, source) in legacy.items():
        if source.suffix == ".toml":
            expected = [entry.strip() for entry in value if entry.strip()]
            body = "\n".join(expected)
        else:
            expected, body = value, source.read_bytes().decode()
        candidate = ReplaceSection(text, name, body)
        readBack = ParseKeylists(candidate).get(name)
        if readBack != expected:
            left.append(f"{name} (from {source}){_Difference(expected, readBack)}")
            continue
        text = candidate
        merged.append((name, source))
    if merged:
        if not WriteKeylists(level.path, text):
            return False
        for _, source in merged:
            if source.suffix != ".toml":
                BackUpFile(source)
        ClearCaches()
        console.print(f"[good]Merged into {level.path}: "
                      f"{', '.join(f'{name} (from {source.name})' for name, source in merged)}.[/good]")
    for reason in left:
        console.print(f"[error]{escape(str(level.path))}: {escape(reason)}. It was left where it is; fix it there "
                      f"(or add the section in Config › Job Keylists) and run the refresh again.[/error]")
    return not left
