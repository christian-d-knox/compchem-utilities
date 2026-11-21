# NEW!! Attempting to keep track of everything related to a job in one central location
# This allows for file name, extension, charge, multiplicity, and coordinate list to be edited and stored on a per-complex basis
from dataclasses import dataclass
from pathlib import Path

from .actions import SpinState


@dataclass
class Conformer:
    """Where a generated geometry came from: which conformer of how many, by which generator (RDKit now; CREST's
    conformers later), so every place that names it says the same (Label: the Structure pane, the Job screen's title,
    the .xyz comment line, the agent report)."""
    generator: str          # "RDKit"
    smiles: str             # the SMILES as given
    rank: int               # 1 = the lowest energy
    distinct: int           # distinct conformers after optimization
    built: int              # conformers embedded
    forceField: str         # "MMFF94", "UFF", or "" (none applied)
    energy: float           # ΔE from the lowest, kcal/mol

    def Label(self, forceField: bool = True) -> str:
        """'RDKit Conformer 1 of 7 (MMFF94)' (forceField False: without it, for a title with little room)."""
        field = f" ({self.forceField})" if self.forceField and forceField else ""
        return f"{self.generator} Conformer {self.rank} of {self.distinct}{field}"


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
        # Read from sourcePath with the rest of the molecule (fileops.ReadMolecule), never mutated: the program that wrote
        # it (its input extension, '' if unknown), its route card, and ORCA's input as written ('' for Gaussian)
        self.sourceProgram = ""
        self.sourceRoute = ""
        self.sourceInput = ""
        # The RouteTemplate genFile renders for the current job. Set with every job by fileops.Retarget()
        self.template = None
        # A generated geometry's origin (a Conformer: smiles.RDKitBuild.Molecule), None for one read from a file
        self.conformer: Conformer | None = None