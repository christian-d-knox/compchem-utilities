import regex, subprocess
from contextlib import closing, contextmanager
from dataclasses import dataclass
from mmap import mmap, ACCESS_READ
from pathlib import Path

from .console  import console
from rich.markup import escape
from .defaults import Defaults
from .catalog  import Catalog, MethodKey, SplitReference, RouteTemplate
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
# start limits the search to data[start:] (still an mmap search, nothing is copied); a negative start counts from the
# end, like a slice. concurrent releases the GIL while
# matching, so a long search in a TUI worker doesn't freeze the screen; safe because every mapping is read-only.
# Reverse searches run from the pattern's END: start them with a literal and put trailing captures in a lookahead,
# or the engine retries the tail (e.g. a number) at every match of it in the file
def FindInMap(data, pattern: str, reverse: bool = False, ignoreCase: bool = False, start: int = 0) -> regex.Match | None:
    flags = 0
    if reverse:
        flags |= regex.REVERSE
    if ignoreCase:
        flags |= regex.IGNORECASE
    if start < 0:
        start = max(0, len(data) + start)
    return regex.search(pattern.encode(), data, flags, pos=start, concurrent=True)

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
def ExtractCoords(data) -> tuple[list[int], list[str], list[str], list[str]]:
    # Jobs run with nosymm only print the Input orientation. With symmetry on, the last table printed is Standard
    tableLocation = FindInMap(data, r"(?:Standard|Input) orientation:", True)
    if tableLocation is None:
        return [], [], [], []

    SkipInMap(data, tableLocation, 4)

    at, X, Y, Z = [], [], [], []
    fields = data.readline().decode().split()
    while len(fields) > 2:
        # Extracts the Atomic Number, and X Y Z coordinates into their respective lists
        at.append(fields[1])
        X.append(fields[3])
        Y.append(fields[4])
        Z.append(fields[5])
        fields = data.readline().decode().split()
    return at, X, Y, Z

def ExtractGaussianCharge(data) -> tuple[str, str]:
    chargeLocation = FindInMap(data, "Charge")
    if chargeLocation is None:
        return "", ""
    data.seek(chargeLocation.start())
    # Replace non-breaking spaces so they don't swallow a charge value and shift indices
    chargeSub = data.readline().decode().replace('\xa0', ' ').strip().split()
    charge = chargeSub[2]
    multiplicity = chargeSub[5]
    return charge, multiplicity

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
    line = data.readline().decode().strip()
    while '*' not in line:
        subLines = line.split()
        subLines.pop(0)
        outputData.append(subLines)
        line = data.readline().decode().strip()
    return outputData

def ExtractRouteLine(data, extensionType: str) -> str:
    """Extract the route card from an input or output file via mmap regex.

    Searches for the first '#' (Gaussian) or '!' (ORCA) marker,
    then reads the full line. For output files or unknown extensions, tries both.
    Returns the route card with the leading marker stripped.
    """
    # Gaussian outputs echo the route between two dashed lines, wrapped mid-word at a fixed width:
    #  -------------------------------------------------------------
    #  #p opt freq=noraman b3lyp genecp scrf=(smd,solvent=water) 5d empiricald
    #  ispersion=gd3bj
    #  -------------------------------------------------------------
    if extensionType != Defaults.orcaExtension:
        blockMatch = FindInMap(data, r"(?m)^ *-{20,}\r?\n( *#[^\r\n]*\r?\n(?:[^\r\n]*\r?\n){0,20}?) *-{20,}\r?$")
        if blockMatch:
            # Each echoed line has one leading space; the rest is the route verbatim, so rejoin without a separator
            wrappedLines = blockMatch.group(1).decode().splitlines()
            routeLine = "".join(line[1:] if line.startswith(" ") else line for line in wrappedLines).strip()
            return _StripRouteMarker(routeLine)
    match extensionType:
        case Defaults.gaussianExtension:
            routeMatch = FindInMap(data, r"#")
        case Defaults.orcaExtension:
            routeMatch = FindInMap(data, r"!")
        case _:
            routeMatch = FindInMap(data, r"#")
            if routeMatch is None:
                routeMatch = FindInMap(data, r"!")
    if routeMatch is None:
        return ""
    data.seek(routeMatch.start())
    return _StripRouteMarker(data.readline().decode())

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

def ExtractTermination(data, start: int = 0) -> str:
    """The LAST termination line in an output, as its terminationVariants entry ('' if none: running, killed, or empty).
    Multi-link Gaussian jobs print one per link, so only the last one says how the job ended."""
    pattern = "|".join(regex.escape(variant) for variant in Defaults.terminationVariants)
    termLine = FindInMap(data, pattern, True, True, start)
    if termLine is None:
        return ""
    found = termLine.group().decode().lower()
    return next(variant for variant in Defaults.terminationVariants if variant.lower() == found)

def ExtractStability(data) -> str:
    if FindInMap(data, "Stability analysis") is None:
        return ""
    if FindInMap(data, "The wavefunction is already stable.", True) is not None:
        return "Wavefunction has stabilized."
    return "Wavefunction has not stabilized."

def ExtractConvergence(data) -> int | None:
    """How many of the 4 optimization criteria the last convergence table marks YES, or None if there is no table yet."""
    finalTableHeader = FindInMap(data, "Item               Value     Threshold  Converged?", True)
    if finalTableHeader is None:
        return None
    SkipInMap(data, finalTableHeader, 0)
    # Each row ends in YES/NO. While the job runs, the table can still be half-written
    return sum(data.readline().decode().split()[-1:] == ["YES"] for index in range(4))

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
    """Gaussian stable= output: 'The wavefunction has an RHF -> UHF instability.'"""
    return FindInMap(data, r"R\w*\s*->\s*U\w*\s+instability", ignoreCase=True) is not None

# CompUtils writes these itself in genFile(), so user copies would conflict
RESERVED_ORCA_BLOCKS = {"pal", "maxcore"}

def ExtractOrcaBlocks(data) -> dict[str, str]:
    """Extract tagged %blocks from an orcablocks.txt mmap data stream.

    A block starts at a column-0 '%' line and runs until the next column-0 '%' line, '# @tag' directive, or EOF.
    Its tag is the preceding '# @tag NAME' directive if present, else the block name. Tags are lowercased.
    Trailing blank/comment lines are trimmed. No 'end' counting, so nested ends (e.g. %geom constraints) are safe.
    """
    blocks: dict[str, str] = {}
    pendingTag = ""
    currentTag, currentLines = "", []

    def CloseBlock() -> None:
        while currentLines and (not currentLines[-1].strip() or currentLines[-1].lstrip().startswith("#")):
            currentLines.pop()
        if not currentTag:
            return
        if currentTag in blocks:
            console.print(f"[error]Duplicate tag '{currentTag}' in orcablocks.txt. Keeping the first definition.[/error]")
            return
        blocks[currentTag] = "".join(currentLines)

    data.seek(0)
    for rawLine in iter(data.readline, b""):
        line = rawLine.decode().replace("\r\n", "\n")
        tagMatch = regex.match(r"#\s*@tag\s+([\w-]+)", line)
        if tagMatch or line.startswith("%"):
            CloseBlock()
            currentTag, currentLines = "", []
        if tagMatch:
            pendingTag = tagMatch.group(1).lower()
            continue
        if line.startswith("%"):
            blockName = regex.match(r"%(\w*)", line).group(1).lower()
            if blockName in RESERVED_ORCA_BLOCKS:
                console.print(f"[warning]orcablocks.txt: %{blockName} is written by CompUtils. Ignoring this block.[/warning]")
                pendingTag = ""
                continue
            currentTag, pendingTag = pendingTag or blockName, ""
        if currentTag:
            currentLines.append(line)
    CloseBlock()
    return blocks

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

# Appends a suffix, or bumps its counter if the name already ends in it: mol -> mol_re -> mol_re2 -> mol_re3
def IncrementSuffix(baseName: str, extra: str) -> str:
    suffixMatch = regex.fullmatch(rf"(.*){regex.escape(extra)}(\d*)", baseName)
    if suffixMatch is None:
        return baseName + extra
    count = int(suffixMatch.group(2) or 1) + 1
    return f"{suffixMatch.group(1)}{extra}{count}"

# Formats checkpoints automatically
def formCheck(molecule: Molecule) -> None:
    subprocess.run(["bash", "-l", "-c", f"module load gaussian && formchk {molecule.fullPath}"], check=True)
    Retarget(molecule, molecule.rootName, extensionType=".fchk")

# A new fully pythonic solution to coordinate scraping, agnostic of the PERL bullshit on LOCAL_CLUSTER
def getCoords(fileName: Path, outputFileName: Path) -> list:
    at, X, Y, Z = ExtractFrom(fileName, ExtractCoords, empty=([], [], [], []))
    # Translates from Atomic Number to Atomic Symbol; the lines are written to the .xyz and kept for the input files
    coordinateList = [f"{ATOMIC_SYMBOLS[int(number)]}   {x}   {y}   {z}\n" for number, x, y, z in zip(at, X, Y, Z)]
    with open(outputFileName, 'w') as outputFile:
        outputFile.write(str(len(at))+"\nPointless Comment Line\n")
        outputFile.writelines(coordinateList)
    return coordinateList

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

# Gaussian16 Charge Finder in its own method
def gaussianChargeFinder(geometryFile: Path) -> tuple[str,str]:
    return ExtractFrom(geometryFile, ExtractGaussianCharge, empty=("", ""))

# This subroutine returns file name and extension for ease-of-use
def grabPaths(fileName: str|Path) -> tuple[str,str] | tuple[None,None]:
    filePath = Path(fileName)
    if filePath.exists():
        baseName, extension = filePath.stem, filePath.suffix
        return baseName, extension
    else:
        console.print(f"[error]Could not locate: {fileName} [/error]")
        return None, None