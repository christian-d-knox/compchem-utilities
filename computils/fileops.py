import regex, subprocess
from contextlib import closing, contextmanager
from dataclasses import dataclass
from mmap import mmap, ACCESS_READ
from pathlib import Path
from typing import Any

from .console  import console
from rich.markup import escape
from .defaults import Defaults
from .catalog  import Catalog
from .molecule import Molecule


# A new, working RegEx Refactor
@contextmanager
def MapFile(filePath: Path):
    # Handles the generator method for mmap-ing files. Call with a with(), and will return data (yield)
    with open(filePath, 'rb') as file:
        with closing(mmap(file.fileno(), 0, access=ACCESS_READ)) as data:
            yield data

# Helper method for performing the searches themselves
def FindInMap(data, pattern: str, reverse: bool = False, ignoreCase: bool = False) -> regex.Match | None:
    flags = 0
    if reverse:
        flags |= regex.REVERSE
    if ignoreCase:
        flags |= regex.IGNORECASE
    return regex.search(pattern.encode(), data, flags)

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
    tableLocation = FindInMap(data, "Standard orientation:", True)
    if tableLocation is None:
        return [], [], [], []

    SkipInMap(data, tableLocation, 4)

    at, X, Y, Z = [], [], [], []
    line = data.readline().decode().strip()
    while len(line.split()) > 2:
        # Extracts the Atomic Number, and X Y Z coordinates into their respective lists
        at.append(str(line.split()[1]))
        X.append(str(line.split()[3]))
        Y.append(str(line.split()[4]))
        Z.append(str(line.split()[5]))
        line = data.readline().decode().strip()
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
    routeLine = data.readline().decode().strip()
    # Strip only the leading marker ('#', '!', or a '#p'/'#n'/'#t' print-level flag). The flag letter must be followed by
    # whitespace, so method names starting with P/N/T (e.g. '#p PBEPBE', '#PBEPBE', '! PBE0') survive intact
    routeLine = regex.sub(r"^(?:#[pPnNtT](?=\s|$)|#|!)\s*", "", routeLine).strip()
    return routeLine


def IdentifyMethod(routeLine: str) -> str:
    """Find the method name in a route card by matching against Catalog.methodList.

    Returns the method name if found, or empty string if no match.
    """
    for token in routeLine.split():
        # Gaussian joins method and basis as 'method/basis' (e.g. PBEPBE/6-31G(d)); only the method part can match
        methodPart = token.split("/")[0]
        # Strip parentheses for matching — e.g. DLPNO-CCSD(T) may appear with basis set syntax
        cleanToken = methodPart.replace("(", "").replace(")", "").upper()
        # Exact name first, then with a Gaussian reference prefix removed (UB3LYP, ROB3LYP, RB3LYP -> B3LYP)
        candidates = [cleanToken] + [cleanToken[len(prefix):] for prefix in ("RO", "U", "R") if cleanToken.startswith(prefix)]
        for candidate in candidates:
            for method in Catalog.methodList:
                if candidate == method.replace("(", "").replace(")", "").upper():
                    return method
    return ""

def ExtractStalking(data, extractType: str) -> Any:
    match extractType:
        case "stability":
            containsStability = FindInMap(data, "Stability analysis")
            if containsStability is not None:
                hasStabilized = FindInMap(data, "The wavefunction is already stable.", True)
                if hasStabilized is not None:
                    stabilityInsert = "Wavefunction has stabilized."
                else:
                    stabilityInsert = "Wavefunction has not stabilized."
            else:
                stabilityInsert = ""
            return stabilityInsert
        case "convergence":
            finalTableHeader = FindInMap(data, "Item               Value     Threshold  Converged?", True)
            if finalTableHeader is not None:
                if len(finalTableHeader.group().decode()) != 0:
                    SkipInMap(data, finalTableHeader, 0)
                    # Telling what converged is currently a stub
                    convergeMet = []
                    for index in range(4):
                        convergeLine = data.readline().decode()
                        print(convergeLine)
                        convergeMet.append(convergeLine.split()[4])
                        convergeCriteria = convergeMet.count("YES")
                    return convergeCriteria
            else:
                convergeCriteria = 0
                return convergeCriteria
        case "termination":
            for termination in Defaults.terminationVariants:
                termLine = FindInMap(data, termination, True, True)
                if termLine is not None:
                    return True, termination
            return False, ""

def ExtractResources(data, extensionType: str) -> tuple[int, int]:
    """Extract CPU count and SLURM memory request from an mmap data stream.

    Returns (cpus, jobRam) ready for slurmHandler.
    Falls back to Defaults for any values not found in the file.
    """
    cpus, jobRam = 0, 0

    match extensionType:
        case Defaults.gaussianExtension:
            coreMatch = FindInMap(data, r"%nproc(?:shared)?=(\d+)", ignoreCase=True)
            if coreMatch:
                cpus = int(coreMatch.group(1).decode())
            else:
                cpus = Defaults.CPU
                console.print("[error]Couldn't find CPU count in input file. Submitting according to Defaults.[/error]")

            ramMatch = FindInMap(data, r"%mem=(\d+)GB", ignoreCase=True)
            if ramMatch:
                ram = int(ramMatch.group(1).decode())
                jobRam = ram + Defaults.memoryBuffer
            else:
                jobRam = cpus * Defaults.memoryRatio + Defaults.memoryBuffer
                console.print("[error]Couldn't find RAM count in input file. Submitting according to Defaults.[/error]")

        case Defaults.orcaExtension:
            coreMatch = FindInMap(data, r"nprocs\s+(\d+)", ignoreCase=True)
            if coreMatch:
                cpus = int(coreMatch.group(1).decode())
            else:
                cpus = Defaults.CPU
                console.print("[error]Couldn't find CPU count in input file. Submitting according to Defaults.[/error]")

            ramMatch = FindInMap(data, r"%maxcore\s+(\d+)", ignoreCase=True)
            if ramMatch:
                ramPerCore = int(ramMatch.group(1).decode()) / 1000
                jobRam = int(cpus * ramPerCore + Defaults.memoryBuffer)
            else:
                jobRam = cpus * Defaults.memoryRatio + Defaults.memoryBuffer
                console.print("[error]Couldn't find RAM count in input file. Submitting according to Defaults.[/error]")

        case _:
            cpus = Defaults.CPU
            jobRam = cpus * Defaults.memoryRatio + Defaults.memoryBuffer

    return cpus, jobRam

def ExtractSpinContamination(data) -> float | None:
    """Return the LAST reported <S**2> in a Gaussian or ORCA output, or None if the file reports none."""
    for pattern in (r"S\*\*2 before annihilation\s+(-?[\d.]+)",           # Gaussian
                    r"Expectation value of <S\*\*2>\s*:\s*(-?[\d.]+)"):    # ORCA
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
    if extra:
        return Path(baseName + extra + extensionType)
    return Path(baseName + extensionType)

# Formats checkpoints automatically
def formCheck(molecule: Molecule) -> None:
    subprocess.run(["bash", "-l", "-c", f"module load gaussian && formchk {molecule.fullPath}"], check=True)
    molecule.extensionType = ".fchk"
    molecule.fullPath = fileCreation(molecule.rootName, molecule.extensionType)

# A new fully pythonic solution to coordinate scraping, agnostic of the PERL bullshit on LOCAL_CLUSTER
def getCoords(fileName: Path, outputFileName: Path) -> list:
    coordinateList = []
    atSymbol = ATOMIC_SYMBOLS

    # Initialize local empty lists
    with MapFile(fileName) as inFile:
        at, X, Y, Z = ExtractCoords(inFile)

    with open(outputFileName, 'w') as outputFile:
        outputFile.write(str(len(at))+"\nPointless Comment Line\n")
        for k in range(len(at)):
            # Ensures the list elements are integers for dictionary pairing
            at[k] = int(at[k])
            # Translates from Atomic Number to Atomic Symbol and builds the entire line to be written with proper formatting
            coordLine = f"{atSymbol[at[k]]}   {X[k]}   {Y[k]}   {Z[k]}\n"
            coordLine = coordLine.replace(' ', ' ')
            outputFile.write(coordLine)
            coordinateList.append(coordLine)
    return coordinateList

# Handles extensions so I don't have to copypasta this
def extensionGetter(method: str) -> str:
    programTarget = ""
    for x in range(len(Catalog.methodList)):
        if method == Catalog.methodList[x]:
            programTarget = Catalog.targetProgram[x]
    match programTarget:
        case "G16":
            fileExtension = Defaults.gaussianExtension
        case "O":
            fileExtension = Defaults.orcaExtension
        case _:
            console.print("[error]Notice: One or more of your intended methods is not specified in programs file nor hardcoded."
                   " Defaulting to Gaussian16.[/error]")
            fileExtension = Defaults.gaussianExtension
    return fileExtension

# Gaussian16 Charge Finder in its own method
def gaussianChargeFinder(geometryFile: Path) -> tuple[str,str]:
    with MapFile(geometryFile) as inFile:
        charge, multiplicity = ExtractGaussianCharge(inFile)
    return charge, multiplicity

# This subroutine returns file name and extension for ease-of-use
def grabPaths(fileName: str|Path) -> tuple[str,str] | tuple[None,None]:
    filePath = Path(fileName)
    if filePath.exists():
        baseName, extension = filePath.stem, filePath.suffix
        return baseName, extension
    else:
        console.print(f"[error]Could not locate: {fileName} [/error]")
        return None, None