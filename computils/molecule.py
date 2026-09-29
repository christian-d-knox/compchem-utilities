# NEW!! Attempting to keep track of everything related to a job in one central location
# This allows for file name, extension, charge, multiplicity, and coordinate list to be edited and stored on a per-complex basis
from pathlib import Path

from .actions import SpinState

class Molecule:
    def __init__(self, fullPath: Path, baseName: str, charge: str|int, multiplicity: str|int, coordinateList: list[str]|int, extensionType: str, rootName: str,
                 spinState: SpinState = SpinState.CSS):
        self.fullPath = fullPath
        self.baseName = baseName
        self.charge = charge
        self.multiplicity = multiplicity
        self.coordinateList = coordinateList
        self.extensionType = extensionType
        self.rootName = rootName
        # Set by spin.ClassifySpin() in dispatch; read by catalog.RenderRoute()
        self.spinState = spinState
        # The file this molecule was loaded from. Never mutated, unlike fullPath (the twin of rootName)
        self.sourcePath = fullPath
        # The RouteTemplate genFile renders for the current job. Set with every job by fileops.Retarget()
        self.template = None