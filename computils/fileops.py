import os, regex, subprocess
from contextlib import closing
from mmap import mmap, ACCESS_READ
from pathlib import Path

from .console  import console
from .defaults import Defaults
from .catalog  import Catalog

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
    at, X, Y, Z = [], [], [], []

    with open(fileName, 'r+') as inFile, open(outputFileName, 'w') as outputFile:
        # Maps the file into memory for reading byte-wise, without any read buffer. AFAIK this is the most memory efficient
        # way to be able to read files of any size
        with closing(mmap(inFile.fileno(), 0, access=ACCESS_READ)) as data:
            tableHeader = "                         Standard orientation:                         "
            tableBytes = tableHeader.encode()
            finalTableHeader = regex.search(tableBytes, data, regex.REVERSE)
            # Finds where the header ends, sets that as the pointer, and reads ahead two bytes to skip over the newline character
            pointer = finalTableHeader.ends()
            data.seek(pointer[0])
            data.read(2)
            for index in range(4):
                data.readline()
            line = data.readline().decode().strip()
            while len(line.split()) > 2:
                # Extracts the Atomic Number, and X Y Z coordinates into their respective lists
                at.append(str(line.split()[1]))
                X.append(str(line.split()[3]))
                Y.append(str(line.split()[4]))
                Z.append(str(line.split()[5]))
                line = data.readline().decode().strip()

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
    chargeLine = "Charge"
    chargeLineBytes = chargeLine.encode()
    with open(geometryFile, 'r') as geomFile:
        with closing(mmap(geomFile.fileno(), 0, access=ACCESS_READ)) as data:
            chargeLineLocation = regex.search(chargeLineBytes, data)
            pointer = chargeLineLocation.starts()
            data.seek(pointer[0])
            targetLine = data.readline().decode()
            chargeSub = targetLine.strip().split()
            # This chunk handles the special case where a stupid non-breaking space is used for neutral charges?
            if chargeSub[2] == '':
                del chargeSub[2]
            charge = chargeSub[2]
            multiplicity = chargeSub[5]
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