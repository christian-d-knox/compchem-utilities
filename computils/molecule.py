# NEW!! Attempting to keep track of everything related to a job in one central location
# This allows for file name, extension, charge, multiplicity, and coordinate list to be edited and stored on a per-complex basis
from pathlib import Path

class Molecule:
    def __init__(self, fullPath: Path, baseName: str, charge: str|int, multiplicity: str|int, coordinateList: list[str]|int, extensionType: str, rootName: str):
        self.fullPath = fullPath
        self.baseName = baseName
        self.charge = charge
        self.multiplicity = multiplicity
        self.coordinateList = coordinateList
        self.extensionType = extensionType
        self.rootName = rootName