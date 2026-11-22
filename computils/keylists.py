"""keylists.cusf: every job-running set in one sectioned file.

    === Optimization Methods ===     named routes for the Optimization builder ('# @NAME' above each; '>' later stages)
    === Single Point Methods ===        the benchmark suite, one route per entry (entry 0 is -sp's default)
    === Mixed Basis ===              Gen/GenECP basis groups and ECPs
    === ORCA Blocks ===              %blocks, pulled into ORCA jobs by {tag} tokens
    === Conformer Search Methods === named CREST option lines for conformer searches ('# @NAME' above each; crest.py)

It lives at two levels, and the nearer level that HAS a section wins, section by section: the project marker
(<root>/.computils/), then binDirectory (global). So a project can override only its Single Point Methods and keep the
global ORCA blocks. A copy in the CWD is never read. No Textual import."""
import functools
from dataclasses import dataclass
from pathlib import Path

import regex
from rich.markup import escape

from .catalog  import STAGE_JOIN
from .console  import console
from .defaults import BackUpFile, Defaults
from .elements import ELEMENT_SET, NormalizeElement
from .fileops  import (RESERVED_ORCA_BLOCKS, TAG_DIRECTIVE, ExtractFromText, HasContent, MapFile, OrcaBlockName,
                       SplitOrcaBlocks)
from .project  import FindProjectRoot, ProjectFilePath

KEYLISTS_FILE = "keylists.cusf"
# Its name until 2026-11-21: a level's file while it has no keylists.cusf, until `cu -refresh` renames it
OLD_KEYLISTS_FILE = "jobRelatedKeylists"
OPTIMIZATION, BENCHMARK, MIXED_BASIS, ORCA_BLOCKS = ("Optimization Methods", "Single Point Methods", "Mixed Basis",
                                                     "ORCA Blocks")
# Headers a section had before: still read as that section; a write (or `cu -refresh`) gives the part its own header
OLD_SECTION_NAMES = {"benchmark methods": BENCHMARK}
CONFORMER_SEARCH = "Conformer Search Methods"
# The suite when no level has one
DEFAULT_BENCHMARK = ["M062X 6-311+G(d,p)"]
# The conformer searches when no level has any: GFN2-xTB in the gas phase, with looser deduplication thresholds than
# CREST's own. A project adds its solvent (--alpb toluene) as an entry of its own
DEFAULT_CONFORMER_SEARCH = {"gfn2": "--gfn2 --ewin 8 --rthr 0.5 --ethr 0.25 --bthr 0.05 --noreftopo"}
# A section's named entries as its file writes them ('# @name' above each)
NAMED_SECTIONS = (OPTIMIZATION, CONFORMER_SEARCH)
# '=== Name ===' on a line of its own. Nothing in any section's format starts with '='
SECTION_HEADER = r"(?m)^===[ \t]*(.*?)[ \t]*===[ \t]*(?=\r?$)"
_HEADER_TEXT, _HEADER_BYTES = regex.compile(SECTION_HEADER), regex.compile(SECTION_HEADER.encode())
_TAG_DIRECTIVE_RE = regex.compile(TAG_DIRECTIVE)
_DECLARED_TAG_RE = regex.compile(rf"(?m)^\s*{TAG_DIRECTIVE}")
_ORCA_BLOCK_START_RE = regex.compile(r"(?m)^%(\w+)")
_ELEMENT_LINE_RE = regex.compile(r"\s*((?:-?\w+\s+)+)0\s*$")
_ELEMENT_INT_RE = regex.compile(r"-?\d+")
_ELEMENT_SYM_RE = regex.compile(r"-?[A-Za-z]{1,2}")
_TAG_DIRECTIVE_FULL_RE = regex.compile(TAG_DIRECTIVE + r"\s*")
FILE_PREAMBLE = ("# keylists.cusf -- CompUtils' settings file: the methods it runs jobs with, and their basis sets\n"
                 "# and blocks. Edit them in the TUI: Config > Job Keylists.\n"
                 "# Each section starts at its '=== Name ===' line; a section a level doesn't have follows the next level.\n")


# ─── Section Parsers ──────────────────────────────────────────────────

def ExtractOrcaBlocks(data) -> dict[str, str]:
    """Extract tagged %blocks from an ORCA Blocks section (keylists.cusf) mmap data stream (split by
    SplitOrcaBlocks). A block's tag is the preceding '# @NAME' directive if present, else the block name. Tags are
    lowercased."""
    blocks: dict[str, str] = {}
    data.seek(0)
    lines = [rawLine.decode().replace("\r\n", "\n") for rawLine in iter(data.readline, b"")]
    for directiveTag, blockText in SplitOrcaBlocks(lines):
        blockName = OrcaBlockName(blockText)
        if blockName in RESERVED_ORCA_BLOCKS:
            console.print(f"[warning]ORCA Blocks: %{blockName} is written by CompUtils. Ignoring this block.[/warning]")
            continue
        tag = directiveTag or blockName
        if tag in blocks:
            console.print(f"[error]Duplicate tag '{tag}' in ORCA Blocks. Keeping the first definition.[/error]")
            continue
        blocks[tag] = blockText
    return blocks


def RouteEntries(data) -> list[tuple[str, list[tuple[int, str]]]]:
    """(directive name or '', [(line index, stage route), ...]) for each route in a methods section (keylists.cusf's
    Optimization or Single Point Methods), in order. A route is a line that is neither blank nor a # comment; a line
    starting with '>' is a later stage of the route above it ('>' with no route above is skipped). '# @NAME' names the
    route after it (lowercased). Line indices count from 0, so the TUI's editor can tell which stage its cursor is on."""
    entries: list[tuple[str, list[tuple[int, str]]]] = []
    pendingTag = ""
    data.seek(0)
    for index, rawLine in enumerate(iter(data.readline, b"")):
        line = rawLine.decode().strip()
        tagMatch = _TAG_DIRECTIVE_RE.match(line)
        if tagMatch:
            pendingTag = tagMatch.group(1).lower()
            continue
        if not line or line.startswith("#"):
            continue
        if line.startswith(">"):
            stage = line[1:].strip()
            if entries and stage:
                entries[-1][1].append((index, stage))
            continue
        entries.append((pendingTag, [(index, line)]))
        pendingTag = ""
    return entries


def JoinStages(stages: list[tuple[int, str]]) -> str:
    """A route's stages as one route card: 'route -> route' (STAGE_JOIN)."""
    return STAGE_JOIN.join(route for _, route in stages)


def NamedRoutes(entries: list[tuple[str, list[tuple[int, str]]]]) -> list[tuple[str, list[tuple[int, str]]]]:
    """RouteEntries named as the Optimization Methods are: the '# @NAME' directive, else the method (the first token
    without Gaussian's /basis), both lowercased. No route is dropped: a repeated name is numbered (ts, ts-2, ...), as
    two unnamed routes of one method are."""
    named, taken = [], set()
    for tag, stages in entries:
        # A CREST option line's first flag names it without its dashes (--gfn2: gfn2)
        tag = tag or stages[0][1].split()[0].split("/")[0].lstrip("-").lower()
        name, number = tag, 2
        while name in taken:
            name, number = f"{tag}-{number}", number + 1
        taken.add(name)
        named.append((name, stages))
    return named


def ExtractOptMethods(data) -> dict[str, str]:
    """Named routes from an Optimization Methods section (NamedRoutes): name -> route card (stages joined with
    STAGE_JOIN), in order."""
    return {name: JoinStages(stages) for name, stages in NamedRoutes(RouteEntries(data))}


def ExtractCrestMethods(data) -> dict[str, str]:
    """Named option lines from a Conformer Search Methods section (NamedRoutes): name -> CREST's options, one line each
    ('>' lines mean nothing to CREST, so only an entry's first line counts)."""
    return {name: stages[0][1] for name, stages in NamedRoutes(RouteEntries(data))}


def ExtractBenchmarkMethods(data) -> list[str]:
    """The route cards of a Single Point Methods section, in order (stages joined with STAGE_JOIN); entry 0 is -sp's
    default. '# @NAME' lines are allowed and ignored."""
    return [JoinStages(stages) for _, stages in RouteEntries(data)]


@dataclass
class BasisEntry:
    tokens: list[str]   # element tokens as written (e.g. "-C"), or center numbers when byCenter
    body: str           # everything after the element line ('****' included for basis groups)
    byCenter: bool = False

    @property
    def elements(self) -> list[str]:
        return [] if self.byCenter else [NormalizeElement(token) for token in self.tokens]


@dataclass
class MixedBasis:
    basis: list[BasisEntry]
    ecp: list[BasisEntry]


def _ElementLine(line: str) -> tuple[list[str], bool] | None:
    """'C H N O 0' -> (tokens, False); '1 2 3 0' -> (tokens, True); anything else -> None."""
    lineMatch = _ELEMENT_LINE_RE.match(line)
    if lineMatch is None:
        return None
    tokens = lineMatch.group(1).split()
    if all(_ELEMENT_INT_RE.fullmatch(token) for token in tokens):
        return tokens, True
    if all(_ELEMENT_SYM_RE.fullmatch(token) and NormalizeElement(token) in ELEMENT_SET for token in tokens):
        return tokens, False
    return None


def ExtractMixedBasisSets(data) -> dict[str, MixedBasis]:
    """The Mixed Basis section as its sets: each `# @name` line starts a named set (a Gaussian route picks one with
    {name}, as an ORCA route picks ORCA Blocks); whatever comes before the first is the unnamed set '' (the one a route
    naming none uses). Each set is ExtractMixedBasis's syntax. A name given twice keeps its first set."""
    data.seek(0)
    lines = data.read().decode().replace("\r\n", "\n").split("\n")
    chunks, name, current = [], "", []
    for line in lines:
        directive = _TAG_DIRECTIVE_FULL_RE.fullmatch(line.strip())
        if directive:
            chunks.append((name, current))
            name, current = directive.group(1).lower(), []
        else:
            current.append(line)
    chunks.append((name, current))
    sets = {}
    for setName, chunk in chunks:
        text = "\n".join(chunk)
        if (not setName and not text.strip()) or setName in sets:
            if setName in sets:
                console.print(f"[warning]Mixed Basis: the set @{escape(setName)} is named twice. Using the first."
                              "[/warning]")
            continue
        sets[setName] = ExtractFromText(text + "\n", ExtractMixedBasis, empty=MixedBasis([], []))
    return sets


def ExtractMixedBasis(data) -> MixedBasis:
    """Parse a Mixed Basis section (keylists.cusf: Gaussian Gen/GenECP syntax) from an mmap data stream."""
    data.seek(0)
    lines = [rawLine.decode().replace("\r\n", "\n").rstrip() for rawLine in iter(data.readline, b"")]
    basis, ecp = [], []
    index = 0

    def SkipBlank() -> None:
        nonlocal index
        while index < len(lines) and not lines[index].strip():
            index += 1

    # Basis section: element line, body, ****
    SkipBlank()
    while index < len(lines) and lines[index].strip():
        header = _ElementLine(lines[index])
        if header is None:
            console.print(f"[warning]Mixed Basis line {index + 1}: expected an element line like 'C H N O 0', "
                          f"got '{escape(lines[index])}'. Ignoring the rest of the section.[/warning]")
            return MixedBasis(basis, ecp)
        index += 1
        body = []
        while index < len(lines) and lines[index].strip() != "****":
            body.append(lines[index])
            index += 1
        body.append("****")
        index += 1
        basis.append(BasisEntry(header[0], "\n".join(body) + "\n", header[1]))

    # ECP section: element line, body until the next element line or blank line
    SkipBlank()
    while index < len(lines) and lines[index].strip():
        header = _ElementLine(lines[index])
        if header is None:
            console.print(f"[warning]Mixed Basis line {index + 1}: expected an ECP element line like 'Co 0', "
                          f"got '{escape(lines[index])}'. Ignoring the rest of the section.[/warning]")
            break
        index += 1
        body = []
        # Only an element line ends an entry: explicit ECP data can contain all-numeric lines ending in 0
        while index < len(lines) and lines[index].strip():
            nextHeader = _ElementLine(lines[index])
            if nextHeader and not nextHeader[1]:
                break
            body.append(lines[index])
            index += 1
        ecp.append(BasisEntry(header[0], "\n".join(body) + "\n", header[1]))

    for sectionName, entries in (("basis", basis), ("ECP", ecp)):
        seen = set()
        for entry in entries:
            if entry.byCenter:
                console.print(f"[warning]Mixed Basis: {sectionName} group '{' '.join(entry.tokens)} 0' addresses atoms "
                              "by center number and can't be filtered by element. It is written to every job as-is.[/warning]")
                continue
            duplicates = [token for token in entry.tokens if NormalizeElement(token) in seen]
            if duplicates:
                console.print(f"[warning]Mixed Basis: {', '.join(NormalizeElement(t) for t in duplicates)} already has "
                              f"a {sectionName} entry. Keeping the first one.[/warning]")
                entry.tokens = [token for token in entry.tokens if token not in duplicates]
            seen.update(entry.elements)
    return MixedBasis([entry for entry in basis if entry.tokens], [entry for entry in ecp if entry.tokens])


@dataclass(frozen=True)
class Section:
    name: str
    extractor: object       # parser run on the section's text
    empty: object           # zero-argument factory: the value without the section


SECTIONS = {section.name: section for section in (
    Section(OPTIMIZATION, ExtractOptMethods, dict),
    Section(BENCHMARK, ExtractBenchmarkMethods, list),
    Section(MIXED_BASIS, ExtractMixedBasisSets, dict),
    Section(ORCA_BLOCKS, ExtractOrcaBlocks, dict),
    Section(CONFORMER_SEARCH, ExtractCrestMethods, dict),
)}


def SectionName(header: str) -> str:
    """A header's section, matched case-insensitively ('' for an unknown one); an old header reads as its section."""
    key = header.strip().lower()
    return next((name for name in SECTIONS if name.lower() == key), OLD_SECTION_NAMES.get(key, ""))


def DeclaredTags(body: str) -> list[str]:
    """Lowercased '# @NAME' declarations in a keylist section body, in order."""
    return [found.lower() for found in _DECLARED_TAG_RE.findall(body)]


def ReservedOrcaBlocks(body: str) -> list[str]:
    """Lowercased CompUtils-reserved ORCA %block names (%pal, %maxcore) present in a section body."""
    return sorted({found.lower() for found in _ORCA_BLOCK_START_RE.findall(body)} & RESERVED_ORCA_BLOCKS)


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
            part.header, part.body = f"=== {name} ===", written
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
    """{section: parsed value} from (name, body text) pairs; a section written twice keeps its first. A Single Point
    Methods section with no routes counts as missing (an empty suite runs nothing), so the next level's applies."""
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
    the whole route card, stages joined), or None off a route. Labels are the Optimization (or Conformer Search)
    Methods' names, or 'Entry N' (Benchmark)."""
    entries = ExtractFromText(text, RouteEntries, empty=[])
    labelled = (NamedRoutes(entries) if name in NAMED_SECTIONS
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


def ClearCaches() -> None:
    _LoadKeylists.cache_clear()


# ─── Levels ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Level:
    label: str                  # Project, Global
    path: Path                  # this level's keylists.cusf (absolute; may not exist), or its old jobRelatedKeylists


def KeylistsPath(folder: Path) -> Path:
    """A level's file: keylists.cusf, or the old jobRelatedKeylists while it is the only one (read and written under its
    old name until `cu -refresh` renames it, so a level is never split across two files)."""
    new, old = folder / KEYLISTS_FILE, folder / OLD_KEYLISTS_FILE
    return old if old.is_file() and not new.exists() else new


def GlobalLevel() -> Level:
    binDirectory = Path(Defaults.binDirectory).expanduser().absolute()
    return Level("Global", KeylistsPath(binDirectory))


def Levels() -> list[Level]:
    """The levels, nearest first: the project's (inside one), then the global one. Config is Global / Project only:
    a copy in the CWD is never read."""
    root = FindProjectRoot()
    levels = []
    if root is not None:
        marker = ProjectFilePath(root, "").absolute()
        levels.append(Level("Project", KeylistsPath(marker)))
    global_ = GlobalLevel()
    if all(global_.path != level.path for level in levels):
        levels.append(global_)
    return levels


def LevelSections(level: Level, texts: dict[Path, str] | None = None) -> dict[str, tuple[object, Path]]:
    """{section: (parsed value, the file it came from)} for every section this level has."""
    if texts and level.path in texts:
        parsed = ParseKeylists(texts[level.path])
    else:
        parsed = _LoadKeylists(level.path) if HasContent(level.path) else {}
    return {name: (value, level.path) for name, value in parsed.items()}


def KeylistSource(name: str, texts: dict[Path, str] | None = None) -> tuple[Level, Path] | None:
    """(the level, the file) a section comes from, or None when no level has it."""
    for level in Levels():
        found = LevelSections(level, texts)
        if name in found:
            return level, found[name][1]
    return None


def Keylist(name: str, texts: dict[Path, str] | None = None):
    """A section's parsed value from the nearest level that has it: OptMethods' {name: route}, the benchmark list,
    MixedBasis, ORCA {tag: block}, Conformer Search {name: options}. Without one anywhere: its empty value (the
    default suite for Single Point Methods, the default search for Conformer Search Methods). texts ({level path:
    unsaved text}) stands in for files (the TUI's config editor)."""
    for level in Levels():
        found = LevelSections(level, texts)
        if name in found:
            return found[name][0]
    return DefaultValue(name)


def DefaultValue(name: str):
    """A section's value when no level has it."""
    if name == BENCHMARK:
        return list(DEFAULT_BENCHMARK)
    if name == CONFORMER_SEARCH:
        return dict(DEFAULT_CONFORMER_SEARCH)
    return SECTIONS[name].empty()


# A name starting with 'example' marks an entry as the format shown by example: a new file's Mixed Basis and ORCA Blocks
# hold one each, and a route uses it only by naming it ({example}), never as a fallback (jobs.MixedBasisSet)
EXAMPLE_NAME = "example"
EXAMPLE_MIXED_BASIS = """# @example
C H N O 0
6-31G(d)
****
Pd 0
SDD
****

Pd 0
SDD"""
# The SMD block {smd} writes for the project's solvent (solvent.py), as one would write it by hand
EXAMPLE_ORCA_BLOCKS = """# @example-smd
%cpcm
    smd true
    SMDsolvent "water"
end"""


def IsExample(name: str) -> bool:
    return name.lower() == EXAMPLE_NAME or name.lower().startswith(EXAMPLE_NAME + "-")


def DefaultBody(name: str, benchmark: list[str] | None = None) -> str:
    """A new file's text for a section: the benchmark suite, the default conformer search ('# @name' above it), and
    an example of the Mixed Basis and ORCA Blocks formats ('# @example', '# @example-smd')."""
    if name == BENCHMARK:
        return "\n".join(benchmark or DEFAULT_BENCHMARK)
    if name == CONFORMER_SEARCH:
        return "\n".join(f"# @{entry}\n{options}" for entry, options in DEFAULT_CONFORMER_SEARCH.items())
    if name == MIXED_BASIS:
        return EXAMPLE_MIXED_BASIS
    if name == ORCA_BLOCKS:
        return EXAMPLE_ORCA_BLOCKS
    return ""


def Where(name: str) -> str:
    """Where to add a missing section, for skip reasons."""
    return f"Add It in Config › Job Keylists › {name}"


# ─── Writing ──────────────────────────────────────────────────────────

def WriteKeylists(path: Path, text: str) -> bool:
    # Never creates its folder (binDirectory is the installer's to create, as for the TOMLs)
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
    """A new global file: every section's header, the benchmark suite and the default conformer search in theirs."""
    text = FILE_PREAMBLE
    for name in SECTIONS:
        text = ReplaceSection(text, name, DefaultBody(name, benchmark))
    return text


def EnsureGlobalKeylists() -> bool:
    """Generate the global file if it is missing (Defaults.Load, like a missing TOML). True if it was written."""
    global_ = GlobalLevel()
    if global_.path.exists():
        return False
    console.print(f"[warning]\\[config] {KEYLISTS_FILE} not found — generating it.[/warning]")
    if not WriteKeylists(global_.path, DefaultText(DEFAULT_BENCHMARK)):
        return False
    ClearCaches()
    return True


def SetGlobalSection(name: str, body: str) -> bool:
    """A lab profile's [keylists] section, written into the global file (a project's own section still wins). True
    only when the file changed."""
    path = GlobalLevel().path
    current = path.read_text(encoding="utf-8") if path.is_file() else FILE_PREAMBLE + "\n"
    text = ReplaceSection(current, name, body)
    if text == current:
        return False
    ClearCaches()
    return WriteKeylists(path, text)


def RenameOld(level: Level) -> bool:
    """`cu -refresh`: a level's old jobRelatedKeylists becomes keylists.cusf, its old section headers their current
    ones (Benchmark Methods -> Single Point Methods). Returns False only on write or OS error."""
    if level.path.name != OLD_KEYLISTS_FILE:
        return True
    new = level.path.with_name(KEYLISTS_FILE)
    try:
        text = level.path.read_text(encoding="utf-8")
        preamble, parts = SplitSections(text)
        # CompUtils' own first line from before (not a person's comment) becomes the current one
        oldLine = ("# jobRelatedKeylists -- CompUtils' job-related keylists. Edit them in the TUI: "
                   "Config > Job Keylists.")
        if preamble.startswith(oldLine):
            preamble = FILE_PREAMBLE.split("\n# Each section")[0] + preamble[len(oldLine):]
        for part in parts:
            # A known section gets its current header; an unknown one keeps its text
            if part.name:
                part.header = f"=== {part.name} ==="
        if not WriteKeylists(new, preamble + "".join(part.header + part.body for part in parts)):
            return False
        BackUpFile(level.path)
    except OSError as error:
        console.print(f"[error]Could not rename {level.path} to {new.name}: {error}[/error]")
        return False
    ClearCaches()
    console.print(f"[good]Renamed {level.path.name} to {new.name} ({level.label}); the old file is kept as a "
                  f"backup.[/good]")
    return True
