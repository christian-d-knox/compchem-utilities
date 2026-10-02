import regex, subprocess
from contextlib import closing, contextmanager
from dataclasses import dataclass
from mmap import mmap, ACCESS_READ
from pathlib import Path

from .console  import console
from rich.markup import escape
from .defaults import Defaults
from .catalog  import Catalog, MethodKey, SplitReference, RouteTemplate, MatchTemplate
from .molecule import Molecule


# A new, working RegEx Refactor
@contextmanager
def MapFile(filePath: Path):
    # Handles the generator method for mmap-ing files. Call with a with(), and will return data (yield)
    with open(filePath, 'rb') as file:
        with closing(mmap(file.fileno(), 0, access=ACCESS_READ)) as data:
            yield data

@contextmanager
def MapText(text: str):
    # MapFile over text held in memory (e.g. an unsaved edit in the TUI), so the same extractors parse it
    encoded = text.encode()
    with closing(mmap(-1, len(encoded))) as data:
        data.write(encoded)
        data.seek(0)
        yield data

# Helper method for performing the searches themselves
# start/end limit the search to data[start:end] (still an mmap search, nothing is copied); a negative start counts from
# the end, like a slice (footers), and end bounds a header search. concurrent releases the GIL while
# matching, so a long search in a TUI worker doesn't freeze the screen; safe because every mapping is read-only.
# Reverse searches run from the pattern's END: start them with a literal and put trailing captures in a lookahead,
# or the engine retries the tail (e.g. a number) at every match of it in the file
def FindInMap(data, pattern: str, reverse: bool = False, ignoreCase: bool = False, start: int = 0,
              end: int | None = None) -> regex.Match | None:
    flags = 0
    if reverse:
        flags |= regex.REVERSE
    if ignoreCase:
        flags |= regex.IGNORECASE
    if start < 0:
        start = max(0, len(data) + start)
    return regex.search(pattern.encode(), data, flags, pos=start, endpos=end, concurrent=True)

# mmap can't map an empty file, so every read of a whole file checks this first
def HasContent(filePath: Path) -> bool:
    return filePath.is_file() and filePath.stat().st_size > 0

# One extractor over a whole file: maps it, runs extractor(data, *args), or returns `empty` for a missing/empty file.
# Callers that run several extractors on one file open it with MapFile themselves
def ExtractFrom(filePath: Path, extractor, *args, empty=None):
    if not HasContent(filePath):
        return empty
    with MapFile(filePath) as data:
        return extractor(data, *args)

def ExtractFromText(text: str, extractor, *args, empty=None):
    # ExtractFrom for text in memory; empty text can't be mapped either
    if not text:
        return empty
    with MapText(text) as data:
        return extractor(data, *args)

# Properly handle line-skipping in extractions
def SkipInMap(data, match, skipLines: int = 0, fromStart: bool = False) -> None:
    if not fromStart:
        data.seek(match.end())
    else:
        data.seek(match.start())
    for index in range(skipLines + 1):
        data.readline()

# Begin individual return methods for mmap extraction

# Gaussian and ORCA both write .out (SLURM -o), so an output's program comes from its header, found in the first few KB.
# ORCA's banner is line 2, Gaussian's 'Entering Gaussian System' line 1
PROGRAM_HEADER_BYTES = 4096
ORCA_HEADER = r"\* O   R   C   A \*"

def ExtractProgram(data, suffix: str) -> str:
    """The program that wrote this file, as its input extension (the key CompUtils branches on), or '' if unknown.
    An input file is known by its extension; an output by its header."""
    if suffix in (Defaults.gaussianExtension, Defaults.orcaExtension):
        return suffix
    header = FindInMap(data, ORCA_HEADER + "|Entering Gaussian System", end=PROGRAM_HEADER_BYTES)
    if header is None:
        return ""
    return Defaults.orcaExtension if header.group().startswith(b"*") else Defaults.gaussianExtension

def ExtractCoords(data, program: str) -> tuple[list[str], list[str], list[str], list[str]]:
    """The LAST geometry printed (an optimisation's final one) as (element symbols, X, Y, Z)."""
    if program == Defaults.orcaExtension:
        # One block per geometry step: header, dashed line, then 'C  x y z' rows ending at a blank line
        tableLocation, headerLines = FindInMap(data, r"CARTESIAN COORDINATES \(ANGSTROEM\)", True), 1
    else:
        # Jobs run with nosymm only print the Input orientation. With symmetry on, the last table printed is Standard.
        # Rows: center, atomic number, type, x y z, ending at a dashed line
        tableLocation, headerLines = FindInMap(data, r"(?:Standard|Input) orientation:", True), 4
    if tableLocation is None:
        return [], [], [], []

    SkipInMap(data, tableLocation, headerLines)

    symbols, X, Y, Z = [], [], [], []
    fields = data.readline().decode().split()
    while len(fields) > 2:
        symbols.append(fields[0] if program == Defaults.orcaExtension else ATOMIC_SYMBOLS[int(fields[1])])
        X.append(fields[-3])
        Y.append(fields[-2])
        Z.append(fields[-1])
        fields = data.readline().decode().split()
    return symbols, X, Y, Z

def ExtractCharge(data, program: str, fromInput: bool = False) -> tuple[str, str]:
    """(charge, multiplicity), or ('', '') if not found. fromInput: the file is an input (a Gaussian input has no
    'Charge =' line: its first stage's charge/multiplicity line follows the route and title sections)."""
    if program == Defaults.orcaExtension:
        # The input's coordinate line ('* xyz 0 1', '*xyzfile 0 1 geom.xyz'), echoed in an output
        chargeMatch = FindInMap(data, r"\*\s*(?:xyzfile|xyz|internal|int|gzmtfile|gzmt|pdbfile)\s+(-?\d+)\s+(\d+)",
                                ignoreCase=True)
        if chargeMatch is None:
            return "", ""
        return chargeMatch.group(1).decode(), chargeMatch.group(2).decode()
    if fromInput:
        return _GaussianInputCharge(data)
    # 'Charge =  0 Multiplicity = 1'. \W also spans non-breaking spaces (bytes C2 A0), which some outputs contain.
    # Anchored on the whole line, so a 'Charge' keyword earlier in the file (or any other file) can't be misread
    chargeMatch = FindInMap(data, r"Charge\W*?(-?\d+)\W+Multiplicity\W*?(\d+)")
    if chargeMatch is None:
        return "", ""
    return chargeMatch.group(1).decode(), chargeMatch.group(2).decode()

def ExtractOrcaInput(data) -> str:
    """ORCA's input as written: from an output, its verbatim echo ('|  N> ' prefixes removed, up to ****END OF INPUT****);
    from an .inp, the whole file. '' for an output without an echo."""
    echoStart = FindInMap(data, r"(?m)^\|\s*1> ")
    if echoStart is None:
        return "" if FindInMap(data, ORCA_HEADER, end=PROGRAM_HEADER_BYTES) else data[:].decode().replace("\r\n", "\n")
    data.seek(echoStart.start())
    lines = []
    for rawLine in iter(data.readline, b""):
        if not rawLine.startswith(b"|"):
            break
        line = regex.sub(r"^\|\s*\d+> ?", "", rawLine.decode().rstrip("\r\n"))
        if "****END OF INPUT****" in line:
            break
        lines.append(line + "\n")
    return "".join(lines)

def OrcaRouteLine(inputText: str) -> str:
    """The '!' line of an ORCA input (ORCA echoes it verbatim, never wrapped), without its marker or # comment."""
    for line in inputText.splitlines():
        if line.lstrip().startswith("!"):
            return _StripRouteMarker(line.split("#", 1)[0])
    return ""

def ExtractGoodVibes(data) -> list:
    headerLocation = FindInMap(data, "Structure", ignoreCase=True)
    if headerLocation is None:
        return []
    outputData = []
    data.seek(headerLocation.start())
    line = data.readline()
    tempSubs = line.decode().strip().split()
    outputData.append(tempSubs)
    data.readline()
    # Rows run to the closing **** rule; a truncated table ends at end of file (readline returns b"")
    line = data.readline()
    while line and b'*' not in line:
        # Drops the leading "o" marker
        outputData.append(line.decode().split()[1:])
        line = data.readline()
    return outputData

def ExtractRouteLine(data, program: str) -> str:
    """The route card of an input or output file (program from ExtractProgram), without its leading marker."""
    if program == Defaults.orcaExtension:
        return OrcaRouteLine(ExtractOrcaInput(data))
    echoed = ExtractRouteEcho(data)
    if echoed is not None:
        return echoed
    # A Gaussian input: its first stage's route section
    return InputRoutes(data, program)[0]

# A Gaussian input's stages are separated by '--Link1--' lines
LINK1_SEPARATOR = r"(?im)^\s*--link1--\s*$"
# A Gaussian input's route section: the '#' line and any lines continuing it, up to the blank line ending the section
GAUSSIAN_INPUT_ROUTE = r"(?m)^[ \t]*(#[^\r\n]*(?:\r?\n[ \t]*[^\s#%][^\r\n]*)*)"

def _GaussianSections(data) -> list[tuple[int, int]]:
    """(start, end) of each stage of a Gaussian input."""
    bounds = [0]
    for separator in regex.finditer(LINK1_SEPARATOR.encode(), data):
        bounds += [separator.start(), separator.end()]
    bounds.append(len(data))
    return list(zip(bounds[::2], bounds[1::2]))

def _GaussianInputRoute(data, start: int, end: int) -> regex.Match | None:
    return FindInMap(data, GAUSSIAN_INPUT_ROUTE, start=start, end=end)

def InputRoutes(data, program: str) -> list[str]:
    """One route per stage of an input file, without the leading marker ('' for a Gaussian stage without one). A
    Gaussian route continued over several lines is joined with spaces. ORCA's multi-step inputs are one stage."""
    if program == Defaults.orcaExtension:
        return [OrcaRouteLine(ExtractOrcaInput(data))]
    routes = []
    for start, end in _GaussianSections(data):
        routeMatch = _GaussianInputRoute(data, start, end)
        routes.append(_StripRouteMarker(" ".join(line.strip() for line in routeMatch.group(1).decode().splitlines()))
                      if routeMatch else "")
    return routes

def _GaussianInputCharge(data) -> tuple[str, str]:
    """A Gaussian input's first charge/multiplicity pair: after the route section, a blank line, the title section and
    another blank line. ('', '') without one (e.g. Geom=AllCheck, which reads both from the checkpoint)."""
    start, end = _GaussianSections(data)[0]
    routeMatch = _GaussianInputRoute(data, start, end)
    if routeMatch is None or regex.search(rb"(?i)allcheck", routeMatch.group(1)):
        return "", ""
    chargeMatch = regex.match(rb"[ \t]*\r?\n(?:[ \t]*\r?\n)*(?:[ \t]*\S[^\r\n]*\r?\n)+[ \t]*\r?\n[ \t]*(-?\d+)[ \t,]+(\d+)",
                              data, pos=routeMatch.end(), endpos=end)
    if chargeMatch is None:
        return "", ""
    return chargeMatch.group(1).decode(), chargeMatch.group(2).decode()

def ExtractRouteEcho(data, start: int = 0) -> str | None:
    """The first route a Gaussian output echoes after start, or None if none is written there yet. From a bookmark, the
    route of the step that follows it (a --Link1-- stage, or Gaussian's internal freq step)."""
    # Gaussian outputs echo the route between two dashed lines, wrapped mid-word at a fixed width:
    #  -------------------------------------------------------------
    #  #p opt freq=noraman b3lyp genecp scrf=(smd,solvent=water) 5d empiricald
    #  ispersion=gd3bj
    #  -------------------------------------------------------------
    blockMatch = FindInMap(data, r"(?m)^ *-{20,}\r?\n( *#[^\r\n]*\r?\n(?:[^\r\n]*\r?\n){0,20}?) *-{20,}\r?$",
                           start=start)
    if blockMatch is None:
        return None
    # Each echoed line has one leading space; the rest is the route verbatim, so rejoin without a separator
    wrappedLines = blockMatch.group(1).decode().splitlines()
    return _StripRouteMarker("".join(line[1:] if line.startswith(" ") else line for line in wrappedLines))

# Strip only the leading marker ('#', '!', or a '#p'/'#n'/'#t' print-level flag). The flag letter must be followed by
# whitespace, so method names starting with P/N/T (e.g. '#p PBEPBE', '#PBEPBE', '! PBE0') survive intact
def _StripRouteMarker(routeLine: str) -> str:
    return regex.sub(r"^(?:#[pPnNtT](?=\s|$)|#|!)\s*", "", routeLine.strip()).strip()


def IdentifyMethod(routeLine: str) -> str:
    """Find the method name in a route card by matching against Catalog.methodList.

    Returns the method name if found, or empty string if no match.
    """
    for token in routeLine.split():
        # Only the method half of Gaussian's 'method/basis' can match; compared without parentheses (MethodKey)
        # Exact name first, then with a Gaussian reference prefix removed (UB3LYP, ROB3LYP, RB3LYP -> B3LYP)
        found = SplitReference(MethodKey(token))
        if found:
            return Catalog.methodKeys[found[1]]
    return ""

# Common basis-set name stems. Only needed for ORCA-style routes, where the basis is its own token
BASIS_PATTERN = r"(?i)^(?:ma-|aug-|jun-)?(?:def2|cc-p|6-31|3-21|sto-|lanl|sdd|genecp$|gen$)"

def SplitRoute(routeLine: str) -> tuple[str, str, str]:
    """Split a route card into (method, basis, remaining keywords) for display. Empty strings where not found."""
    method, basis, keys = IdentifyMethod(routeLine), "", []
    methodTaken = False
    for token in routeLine.split():
        methodPart, _, slashBasis = token.partition("/")
        # The method token itself (possibly U/RO-prefixed), with Gaussian's '/basis' half if present
        if method and not methodTaken and IdentifyMethod(methodPart) == method:
            methodTaken, basis = True, basis or slashBasis
            continue
        if not basis and regex.match(BASIS_PATTERN, token):
            basis = token
            continue
        keys.append(token)
    return method, basis, " ".join(keys)

# What a job step reports while it runs, judged from its route (and, for ORCA, its % blocks). Route keywords only:
# Gaussian's 'stable=opt' is a stability option, not an optimization. Keyed by 'is ORCA' (extensions are user config)
_STEP_KEYWORDS = {
    "optimizes": {False: r"(?i)(?:^|\s)opt(?=$|[\s=(])",
                  True:  r"(?i)(?:^|\s)(?:(?:tight|loose|verytight|normal)?opt(?:ts)?|copt|zopt|scants)"
                         r"(?=\s|$)"},
    "stability": {False: r"(?i)(?:^|\s)stable(?=$|[\s=(])",
                  True:  r"(?i)(?:^|\s)stability(?=\s|$)"},
    "freq":      {False: r"(?i)(?:^|\s)freq(?=$|[\s=(])",
                  True:  r"(?i)(?:^|\s)(?:num|ana)?freq(?=\s|$)"},
}

@dataclass
class StepChecks:
    """What one job step reports: convergence while it optimizes, a verdict if it runs a stability analysis."""
    optimizes: bool
    stability: bool
    label:     str     # Opt, Stability, Freq or SP
    freq:      bool = False

    @property
    def kinds(self) -> str:
        """Everything the step runs, e.g. 'Opt+Freq', 'Stability' or 'SP'."""
        return "+".join(kind for kind, runs in (("Stability", self.stability), ("Opt", self.optimizes),
                                                ("Freq", self.freq)) if runs) or "SP"

def JobChecks(route: str, program: str, inputText: str = "") -> StepChecks:
    """The checks a step with this route needs. inputText: ORCA's input, whose %scf block can ask for the analysis."""
    isOrca = program == Defaults.orcaExtension
    found = {check: bool(regex.search(patterns[isOrca], route)) for check, patterns in _STEP_KEYWORDS.items()}
    if isOrca and regex.search(r"(?i)stabperform\s+true", inputText):
        found["stability"] = True
    label = ("Opt" if found["optimizes"] else "Stability" if found["stability"] else "Freq" if found["freq"]
             else "SP")
    return StepChecks(found["optimizes"], found["stability"], label, found["freq"])

def StageCount(data, program: str) -> int:
    """How many stages an input runs: Gaussian's --Link1-- separators + 1. ORCA's multi-step inputs aren't tracked yet."""
    if program != Defaults.gaussianExtension:
        return 1
    return len(_GaussianSections(data))

def FindTermination(data, start: int = 0, end: int | None = None, reverse: bool = True) -> regex.Match | None:
    """The last (or, reverse=False, the first) termination line in data[start:end]. Its offsets are the stalker's
    bookmarks: a later step's reporting is never searched for before one."""
    pattern = "|".join(regex.escape(variant) for variant in Defaults.terminationVariants)
    return FindInMap(data, pattern, reverse, True, start, end)

def TerminationVariant(termLine: regex.Match | None) -> str:
    """The terminationVariants entry a FindTermination match is, or '' for None."""
    if termLine is None:
        return ""
    found = termLine.group().decode().lower()
    return next(variant for variant in Defaults.terminationVariants if variant.lower() == found)

def ExtractTermination(data, start: int = 0) -> str:
    """The LAST termination line in an output, as its terminationVariants entry ('' if none: running, killed, or empty).
    Multi-link Gaussian jobs print one per link, so only the last one says how the job ended."""
    return TerminationVariant(FindTermination(data, start))

# What Gaussian prints right after the termination line of a step it added itself (opt freq's freq), but not after a
# --Link1-- stage the input asked for, which starts with 'Initial command:' instead
INTERNAL_STEP = r"[ \t]*Link1:\s+Proceeding to internal job step"

def IsInternalStep(data, termLine: regex.Match) -> bool | None:
    """Whether the step after this termination line is Gaussian's own (same stage), or None if the next line isn't
    complete yet (it can't be told apart until it is)."""
    lineEnd = data.find(b"\n", termLine.end())
    if lineEnd < 0:
        return None
    nextEnd = data.find(b"\n", lineEnd + 1)
    if nextEnd < 0:
        return None
    return regex.match(INTERNAL_STEP.encode(), data[lineEnd + 1:nextEnd]) is not None

# Stability analysis section headers, Gaussian then ORCA. Case-sensitive: ORCA also prints 'stability analysis' (its
# contributor list) and 'SCF Stability Analysis' (its timings)
STABILITY_HEADER = r"Stability analysis|WAVEFUNCTION STABILITY ANALYSIS"
ORCA_STABLE_VERDICT = r"The stability analysis shows that the wavefunction is stable"
STABLE_VERDICT = r"The wavefunction is already stable\.|" + ORCA_STABLE_VERDICT

def ExtractStability(data, start: int = 0, end: int | None = None) -> bool | None:
    """Whether the last stability analysis in data[start:end] found the wave function stable; None if there is none.
    An analysis without a stable verdict counts as not stabilized, so the unstable wording never has to be matched."""
    if FindInMap(data, STABILITY_HEADER, True, start=start, end=end) is None:
        return None
    return FindInMap(data, STABLE_VERDICT, True, start=start, end=end) is not None

def ExtractConvergence(data, start: int = 0, end: int | None = None) -> tuple[int, int] | None:
    """(criteria met, criteria) in the last optimization convergence table in data[start:end], or None if there is no
    complete row yet. Gaussian's table has 4 rows; ORCA's 4 or 5 (its first cycle has no energy change row)."""
    finalTableHeader = FindInMap(data, r"Item\s+Value\s+(?:Threshold|Tolerance)\s+Converged", True, True, start, end)
    if finalTableHeader is None:
        return None
    SkipInMap(data, finalTableHeader, 0)
    # Each row ends in YES/NO; ORCA puts a dashed line before them. While the job runs, the table can be half-written
    met = total = 0
    for line in iter(data.readline, b""):
        fields = line.split()
        if fields[-1:] in ([b"YES"], [b"NO"]):
            met, total = met + (fields[-1] == b"YES"), total + 1
        elif total or (fields and fields[0].strip(b"-")):
            break
    return (met, total) if total else None

def ExtractResources(data, extensionType: str) -> tuple[int, int]:
    """Extract CPU count and SLURM memory request from an mmap data stream.

    Returns (cpus, jobRam) ready for slurmHandler.
    Falls back to Defaults for any values not found in the file.
    """
    # Per program: CPU pattern, memory pattern, and the job's memory in GB from (matched number, cpus).
    # Gaussian's %mem=NGB is the total; ORCA's %maxcore N is MB per CPU
    match extensionType:
        case Defaults.gaussianExtension:
            corePattern, ramPattern, ToGB = r"%nproc(?:shared)?=(\d+)", r"%mem=(\d+)GB", lambda ram, cpus: ram
        case Defaults.orcaExtension:
            corePattern, ramPattern, ToGB = r"nprocs\s+(\d+)", r"%maxcore\s+(\d+)", lambda ram, cpus: cpus * ram / 1000
        case _:
            return Defaults.CPU, int(Defaults.CPU * Defaults.memoryRatio + Defaults.memoryBuffer)

    coreMatch = FindInMap(data, corePattern, ignoreCase=True)
    if not coreMatch:
        console.print("[error]Couldn't find CPU count in input file. Submitting according to Defaults.[/error]")
    cpus = int(coreMatch.group(1)) if coreMatch else Defaults.CPU
    ramMatch = FindInMap(data, ramPattern, ignoreCase=True)
    if not ramMatch:
        console.print("[error]Couldn't find RAM count in input file. Submitting according to Defaults.[/error]")
    ram = ToGB(int(ramMatch.group(1)), cpus) if ramMatch else cpus * Defaults.memoryRatio
    return cpus, int(ram + Defaults.memoryBuffer)

def ExtractSpinContamination(data) -> float | None:
    """Return the LAST reported <S**2> in a Gaussian or ORCA output, or None if the file reports none."""
    # Value in a lookahead: see FindInMap. Without it, a file with no <S**2> took ~0.14 s/MB for the ORCA pattern
    for pattern in (r"S\*\*2 before annihilation(?=\s+(-?[\d.]+))",           # Gaussian
                    r"Expectation value of <S\*\*2>(?=\s*:\s*(-?[\d.]+))"):    # ORCA
        spinMatch = FindInMap(data, pattern, reverse=True)
        if spinMatch:
            return float(spinMatch.group(1).decode())
    return None

def HasRestrictedInstability(data) -> bool:
    """Gaussian stable= output: 'The wavefunction has an RHF -> UHF instability.' ORCA prints no such line, so its
    analysis counts when it lacks the stable verdict (spin.py only asks for singlets whose route has no UKS/UHF)."""
    if FindInMap(data, r"R\w*\s*->\s*U\w*\s+instability", ignoreCase=True) is not None:
        return True
    return (FindInMap(data, "WAVEFUNCTION STABILITY ANALYSIS") is not None
            and FindInMap(data, ORCA_STABLE_VERDICT, True) is None)

# CompUtils writes these itself in genFile(), so user copies would conflict
RESERVED_ORCA_BLOCKS = {"pal", "maxcore"}

def OrcaBlockName(blockText: str) -> str:
    """'%cpcm\\n  smd true\\nend\\n' -> 'cpcm'."""
    return regex.match(r"\s*%(\w*)", blockText).group(1).lower()

def _SplitOrcaBlocks(lines: list[str]) -> list[tuple[str, str]]:
    """Split lines into (directive tag, block text). A block starts at a column-0 '%' line and runs until the next one,
    a '# @tag NAME' directive (which tags the block after it), or the end. Trailing blank/comment lines are trimmed.
    No 'end' counting, so nested ends (e.g. %geom constraints) are safe. Lines outside a block are dropped."""
    blocks, pendingTag, current = [], "", None
    for line in lines + ["%"]:   # the sentinel closes the last block (and is never closed itself)
        tagMatch = regex.match(r"#\s*@tag\s+([\w-]+)", line)
        if (tagMatch or line.startswith("%")) and current is not None:
            while current[1] and (not current[1][-1].strip() or current[1][-1].lstrip().startswith("#")):
                current[1].pop()
            blocks.append((current[0], "".join(current[1])))
            current = None
        if tagMatch:
            pendingTag = tagMatch.group(1).lower()
        elif line.startswith("%"):
            current, pendingTag = (pendingTag, []), ""
        if current is not None:
            current[1].append(line)
    return blocks

def ExtractOrcaBlocks(data) -> dict[str, str]:
    """Extract tagged %blocks from an orcablocks.txt mmap data stream (split by _SplitOrcaBlocks).
    A block's tag is the preceding '# @tag NAME' directive if present, else the block name. Tags are lowercased."""
    blocks: dict[str, str] = {}
    data.seek(0)
    lines = [rawLine.decode().replace("\r\n", "\n") for rawLine in iter(data.readline, b"")]
    for directiveTag, blockText in _SplitOrcaBlocks(lines):
        blockName = OrcaBlockName(blockText)
        if blockName in RESERVED_ORCA_BLOCKS:
            console.print(f"[warning]orcablocks.txt: %{blockName} is written by CompUtils. Ignoring this block.[/warning]")
            continue
        tag = directiveTag or blockName
        if tag in blocks:
            console.print(f"[error]Duplicate tag '{tag}' in orcablocks.txt. Keeping the first definition.[/error]")
            continue
        blocks[tag] = blockText
    return blocks

# Blocks an ORCA re-run never carries over: genFile writes %pal/%maxcore, and the new geometry comes from the output
RERUN_SKIPPED_BLOCKS = RESERVED_ORCA_BLOCKS | {"coords"}

def OrcaInputBlocks(inputText: str) -> list[str]:
    """The %blocks of an ORCA input (ExtractOrcaInput), as written, for a re-run to carry over. The '!' line and the
    geometry are left out, and so is anything after a $new_job (a re-run repeats the first job)."""
    lines, inGeometry = [], False
    for line in inputText.splitlines(keepends=True):
        stripped = line.strip()
        if inGeometry:
            inGeometry = stripped != "*"
            continue
        if stripped.lower().startswith("$new_job"):
            break
        if stripped.startswith("*"):
            # '* xyz 0 1' opens a block closed by a lone '*'; '*xyzfile 0 1 geom.xyz' is one line
            inGeometry = regex.match(r"\*\s*(?:xyz|internal|int|gzmt)\b", stripped, regex.IGNORECASE) is not None
            continue
        if stripped.startswith("!"):
            continue
        # ORCA allows an indented block start
        lines.append(line.lstrip() if stripped.startswith("%") else line)
    return [blockText for _, blockText in _SplitOrcaBlocks(lines) if OrcaBlockName(blockText) not in RERUN_SKIPPED_BLOCKS]

# Atomic number -> symbol, 1-103. Shared by getCoords() and the mixed basis element checks
ATOMIC_SYMBOLS = dict(enumerate((
    "H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn Ga Ge As Se Br Kr "
    "Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb "
    "Lu Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr"
).split(), start=1))
ELEMENT_SET = set(ATOMIC_SYMBOLS.values())

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

def NormalizeElement(token: str) -> str:
    return token.lstrip("-").capitalize()

def _ElementLine(line: str) -> tuple[list[str], bool] | None:
    """'C H N O 0' -> (tokens, False); '1 2 3 0' -> (tokens, True); anything else -> None."""
    lineMatch = regex.match(r"\s*((?:-?\w+\s+)+)0\s*$", line)
    if lineMatch is None:
        return None
    tokens = lineMatch.group(1).split()
    if all(regex.fullmatch(r"-?\d+", token) for token in tokens):
        return tokens, True
    if all(regex.fullmatch(r"-?[A-Za-z]{1,2}", token) and NormalizeElement(token) in ELEMENT_SET for token in tokens):
        return tokens, False
    return None

def ExtractMixedBasis(data) -> MixedBasis:
    """Parse a Gaussian Gen/GenECP section (mixedbasis.txt) from an mmap data stream.

    Basis groups run from an element line ('C H N O 0') to '****'; the first blank line after a group ends the basis
    section. ECP entries run from an element line to the next element line or blank line. An element listed in two
    groups of the same section is kept only in the first.
    """
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
            console.print(f"[warning]mixedbasis.txt line {index + 1}: expected an element line like 'C H N O 0', "
                          f"got '{escape(lines[index])}'. Ignoring the rest of the file.[/warning]")
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
            console.print(f"[warning]mixedbasis.txt line {index + 1}: expected an ECP element line like 'Co 0', "
                          f"got '{escape(lines[index])}'. Ignoring the rest of the file.[/warning]")
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
                console.print(f"[warning]mixedbasis.txt: {sectionName} group '{' '.join(entry.tokens)} 0' addresses atoms "
                              "by center number and can't be filtered by element. It is written to every job as-is.[/warning]")
                continue
            duplicates = [token for token in entry.tokens if NormalizeElement(token) in seen]
            if duplicates:
                console.print(f"[warning]mixedbasis.txt: {', '.join(NormalizeElement(t) for t in duplicates)} already has "
                              f"a {sectionName} entry. Keeping the first one.[/warning]")
                entry.tokens = [token for token in entry.tokens if token not in duplicates]
            seen.update(entry.elements)
    return MixedBasis([entry for entry in basis if entry.tokens], [entry for entry in ecp if entry.tokens])

def MoleculeElements(coordinateList: list[str]) -> set[str]:
    """Element symbols in a coordinate list. Handles atomic numbers, 'C(Fragment=1)', 'C-Bq' (ghost atoms still carry
    basis functions), 'C1' labels; drops bare 'Bq' and dummy 'X' atoms."""
    elements = set()
    for line in coordinateList:
        fields = line.split()
        if not fields:
            continue
        token = fields[0]
        if token.isdigit():
            symbol = ATOMIC_SYMBOLS.get(int(token), token)
        else:
            symbolMatch = regex.match(r"[A-Za-z]{1,2}", token)
            if symbolMatch is None:
                continue
            symbol = symbolMatch.group(0).capitalize()
        # Bq and X are not in the table, so ghost-only and dummy atoms are dropped here
        if symbol in ELEMENT_SET:
            elements.add(symbol)
    return elements

# Finally handle filename creation in one place to stop the infinite copypasta
def fileCreation(baseName: str, extensionType: str, extra: str = "") -> Path:
    return Path(baseName + extra + extensionType)

# Points a molecule at a new job: its working name, route recipe, program extension (from the recipe's method unless
# given) and input path. Every workflow sets up its jobs through here, so no field is left over from the previous job
def Retarget(molecule: Molecule, baseName: str, template: RouteTemplate | None = None, extensionType: str = "") -> None:
    molecule.baseName, molecule.template = baseName, template
    molecule.extensionType = extensionType or extensionGetter(template.method)
    molecule.fullPath = fileCreation(baseName, molecule.extensionType)

# The template a re-run renders, from the route card ReadMolecule stored: (template, '') or (None, why it can't be
# re-run). Sets molecule.extensionType to the program the job runs in. Shared by genReRun and the TUI's Re-run preview
def ReRunTemplate(molecule: Molecule) -> tuple[RouteTemplate | None, str]:
    routeLine = molecule.sourceRoute
    if not routeLine:
        return None, "No Route Card Found"
    methodName = IdentifyMethod(routeLine)
    if not methodName:
        return None, "Method Not Recognised"
    # A re-run repeats the job in the program that ran it (an ORCA B3LYP job stays ORCA, whatever programs.toml maps
    # B3LYP to). It must be known before matching: the U/RO reference step differs between Gaussian and ORCA
    molecule.extensionType = molecule.sourceProgram or extensionGetter(methodName)
    # Extracted route cards are already rendered (no tags/groups). Find the benchmark entry that renders to the same
    # route for THIS molecule's spin state, so its orcablocks tags carry over (a block fixed in orcablocks.txt applies)
    template = MatchTemplate(routeLine, molecule)
    if template is None:
        # Use the route verbatim, with the %blocks the ORCA job ran with. RenderRoute's reference step is idempotent,
        # so no double U prefix
        blocks = OrcaInputBlocks(molecule.sourceInput) if molecule.extensionType == Defaults.orcaExtension else []
        template = RouteTemplate(routeLine, method=methodName, blocks=blocks)
    return template, ""

# Appends a suffix, or bumps its counter if the name already ends in it: mol -> mol_re -> mol_re2 -> mol_re3
def IncrementSuffix(baseName: str, extra: str) -> str:
    suffixMatch = regex.fullmatch(rf"(.*){regex.escape(extra)}(\d*)", baseName)
    if suffixMatch is None:
        return baseName + extra
    count = int(suffixMatch.group(2) or 1) + 1
    return f"{suffixMatch.group(1)}{extra}{count}"

# Formats checkpoints automatically
# formchk's own output is captured and printed through the console (the TUI can't have it written to its screen).
# Returns False, after saying why, if it failed: the caller skips that molecule
def formCheck(molecule: Molecule) -> bool:
    try:
        result = subprocess.run(["bash", "-l", "-c", f"module load gaussian && formchk {molecule.fullPath}"],
                                capture_output=True, text=True)
    except FileNotFoundError:
        console.print(f"[error]Couldn't run formchk on {molecule.baseName} (no bash). Skipping it.[/error]")
        return False
    if result.stdout.strip():
        console.print(escape(result.stdout.strip()))
    if result.returncode != 0:
        console.print(f"[error]formchk failed on {molecule.baseName}: {escape(result.stderr.strip())}. Skipping it.[/error]")
        return False
    Retarget(molecule, molecule.rootName, extensionType=".fchk")
    return True

# Everything CompUtils reads from a source output, from ONE open map (the caller manages MapFile, and runs ClassifySpin
# on the same map): program, charge/multiplicity, final coordinates, route card and ORCA's input as written.
# Writes nothing, so the TUI preview shares it. A new fully pythonic solution to coordinate scraping, agnostic of the
# cluster's PERL bullshit
def ReadMolecule(data, path: Path) -> Molecule:
    program = ExtractProgram(data, path.suffix)
    charge, multiplicity = ExtractCharge(data, program, fromInput=path.suffix == program)
    symbols, X, Y, Z = ExtractCoords(data, program)
    # The lines are written to the .xyz (WriteXyz) and kept for the input files
    coordinateList = [f"{symbol}   {x}   {y}   {z}\n" for symbol, x, y, z in zip(symbols, X, Y, Z)]
    molecule = Molecule(path, path.stem, charge, multiplicity, coordinateList, path.suffix, path.stem)
    molecule.sourceProgram = program
    if program == Defaults.orcaExtension:
        molecule.sourceInput = ExtractOrcaInput(data)
        molecule.sourceRoute = OrcaRouteLine(molecule.sourceInput)
    else:
        molecule.sourceRoute = ExtractRouteLine(data, program)
    return molecule

def WriteXyz(molecule: Molecule, outputFileName: Path) -> None:
    with open(outputFileName, 'w') as outputFile:
        outputFile.write(f"{len(molecule.coordinateList)}\nPointless Comment Line\n")
        outputFile.writelines(molecule.coordinateList)

# Handles extensions so I don't have to copypasta this
# targetProgram codes: code -> (display name, the Defaults key holding that program's input extension)
PROGRAMS = {"G16": ("G16", "gaussianExtension"), "O": ("ORCA", "orcaExtension")}

# programOf defaults to the loaded Catalog's; the TUI's config editor passes its unsaved method map
def extensionGetter(method: str, programOf: dict | None = None) -> str:
    program = PROGRAMS.get((Catalog.programOf if programOf is None else programOf).get(method, ""))
    if program is None:
        console.print("[error]Notice: One or more of your intended methods is not specified in programs file nor hardcoded."
               " Defaulting to Gaussian16.[/error]")
        return Defaults.gaussianExtension
    return getattr(Defaults, program[1])

# This subroutine returns file name and extension for ease-of-use
def grabPaths(fileName: str|Path) -> tuple[str,str] | tuple[None,None]:
    filePath = Path(fileName)
    if filePath.exists():
        baseName, extension = filePath.stem, filePath.suffix
        return baseName, extension
    else:
        console.print(f"[error]Could not locate: {fileName} [/error]")
        return None, None