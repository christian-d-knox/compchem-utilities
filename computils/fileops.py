import os, regex, subprocess
from contextlib import closing, contextmanager
from mmap import mmap, ACCESS_READ
from pathlib import Path
from typing import Any

from .console  import console
from .defaults import Defaults
from .catalog  import Catalog

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
    chargeSub = data.readline().decode().strip().split()
    # This chunk handles the special case where a stupid non-breaking space is used for neutral charges?
    if chargeSub[2] == '':
        del chargeSub[2]
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

def ExtractPriorMethod(data) -> str:
    methodLocation = FindInMap(data, "Will use up to")
    if methodLocation is None:
        return ""
    SkipInMap(data, methodLocation, 2, True)
    originalMethod = data.readline().decode()
    return originalMethod

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
                    convergenceCriteria = 0
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
                termLine = FindInMap(data, termination)
                if termLine is not None:
                    return True, termination
            return False

# Finally handle filename creation in one place to stop the infinite copypasta
def fileCreation(baseName, extensionType, extra) -> Path:
    if not len(extra) == 0:
        fullFile = baseName + extra + extensionType
    else:
        fullFile = baseName + extensionType
    return fullFile

# Formats checkpoints automatically
def formCheck(molecule: object) -> None:
    subprocess.run(["formchk", molecule.fullPath], check=True)
    molecule.extensionType = ".fchk"
    molecule.fullPath = molecule.rootName + molecule.extensionType

# A new fully pythonic solution to coordinate scraping, agnostic of the PERL bullshit on H2P
def getCoords(fileName: Path, outputFileName: Path) -> list:
    coordinateList = []
    atSymbol = {
        1: 'H', 2: 'He', 3: 'Li', 4: 'Be', 5: 'B', 6: 'C', 7: 'N', 8: 'O', 9: 'F', 10: 'Ne',
        11: 'Na', 12: 'Mg', 13: 'Al', 14: 'Si', 15: 'P', 16: 'S', 17: 'Cl', 18: 'Ar', 19: 'K',
        20: 'Ca', 21: 'Sc', 22: 'Ti', 23: 'V', 24: 'Cr', 25: 'Mn', 26: 'Fe', 27: 'Co', 28: 'Ni',
        29: 'Cu', 30: 'Zn', 31: 'Ga', 32: 'Ge', 33: 'As', 34: 'Se', 35: 'Br', 36: 'Kr',

        42: 'Mo', 44: 'Ru', 45: 'Rh', 46: 'Pd', 47: 'Ag', 48: 'Cd', 50: 'Sn', 51: 'Sb',
        53: 'I', 54: 'Xe', 77: 'Ir', 78: 'Pt', 79: 'Au', 80: 'Hg', 81: 'Tl', 82: 'Pb',
        83: 'Bi'
    }

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
        case "Q":
            fileExtension = Defaults.qChemExtension
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
def grabPaths(fileName: str) -> tuple[str,str] | tuple[None,None]:
    filePath = Path(fileName)
    if filePath.exists():
        baseName, extension = filePath.stem, filePath.suffix
        return baseName, extension
    else:
        console.print(f"[error]Could not locate: {fileName} [/error]")
        return None, None