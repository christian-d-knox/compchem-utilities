"""SMILES to 3D starting coordinates with RDKit (no Textual; shared by the Optimization builder's Coordinates screen,
Home's Write XYZ, cu -opt --smiles and cu -xyz --smiles). Several RDKit conformers are embedded (ETKDGv3, a fixed seed:
the same SMILES always gives the same geometry), optimized with MMFF94 (UFF where MMFF has no parameters), ranked by
energy with duplicates dropped; the lowest is used unless another is picked. Every geometry made here is labelled an
RDKit conformer (molecule.Conformer), so CREST's conformers can sit beside them later. RDKit is imported inside the
functions: an environment without it gets a reason (RDKIT_MISSING), never a crash."""
from dataclasses import dataclass, field
from pathlib import Path

from .fileops  import CoordinateLines
from .molecule import Conformer, Molecule

GENERATOR = "RDKit"
RDKIT_MISSING = "RDKit Isn't Installed in This Environment (cu -env refresh)"
SEED = 0xC0FFEE             # ETKDG's random seed: the same SMILES gives the same conformers
OPTIMIZER_STEPS = 2000      # the force field's step limit
SAME_ENERGY = 0.01          # kcal/mol: optimized conformers this close in energy...
SAME_RMS = 0.1              # ...and heavy-atom RMSD (Å), directly or as mirror images, are the same minimum, kept once
MAX_MATCHES = 1000          # symmetry matches GetBestRMS tries (a symmetric molecule has many)


@dataclass
class RDKitBuild:
    """The RDKit conformers of one SMILES, lowest energy first."""
    smiles: str                 # as given
    canonical: str              # RDKit's canonical SMILES
    charge: int                 # the sum of the formal charges
    multiplicity: int           # radical electrons + 1 (high spin)
    forceField: str             # "MMFF94", "UFF", or "" (none had parameters: embedded geometries, unranked)
    built: int                  # conformers embedded
    conformers: list[tuple[float, list[str]]] = field(default_factory=list)   # (ΔE kcal/mol, coordinateList)
    warnings: list[str] = field(default_factory=list)

    @property
    def atoms(self) -> int:
        return len(self.conformers[0][1]) if self.conformers else 0

    def RankProblem(self, rank: int) -> str:
        """Why rank can't be picked ('' if it can)."""
        if 1 <= rank <= len(self.conformers):
            return ""
        return f"There {'is' if len(self.conformers) == 1 else 'are'} only {len(self.conformers)} distinct RDKit conformer" \
               f"{'' if len(self.conformers) == 1 else 's'} (asked for number {rank})"

    def Conformer(self, rank: int = 1) -> Conformer:
        return Conformer(GENERATOR, self.smiles, rank, len(self.conformers), self.built, self.forceField,
                         self.conformers[rank - 1][0])

    def Molecule(self, name: str, rank: int = 1) -> Molecule:
        """The rank-th conformer as a molecule (no file: in memory, as pasted coordinates are), its charge and
        multiplicity the SMILES'. rank must be valid (RankProblem)."""
        molecule = Molecule(Path(""), name, str(self.charge), str(self.multiplicity),
                            list(self.conformers[rank - 1][1]), "", name)
        molecule.conformer = self.Conformer(rank)
        return molecule

    def Energies(self) -> list[dict]:
        """Each conformer's rank and ΔE (kcal/mol, None without a force field), for the agent report: read, never
        computed by the agent."""
        return [{"rank": rank, "deltaE": round(energy, 2) if self.forceField else None}
                for rank, (energy, _) in enumerate(self.conformers, 1)]


def Available() -> str:
    """'' when RDKit imports here, else why not."""
    try:
        import rdkit  # noqa: F401
    except ImportError:
        return RDKIT_MISSING
    return ""


def _Sentence(message: str) -> str:
    # RDKit's messages start lower case and count atoms from 0 ("Explicit valence for atom # 0 N, 4, is greater than
    # permitted"): as a sentence, atoms counted from 1 as in the SMILES
    import regex
    message = regex.sub(r"atom # (\d+) ([A-Z][a-z]?)", lambda match: f"atom {int(match[1]) + 1} ({match[2]})",
                        message.strip().rstrip("."))
    return message[:1].upper() + message[1:]


def RDKitConformers(smiles: str, count: int) -> tuple[RDKitBuild | None, str]:
    """(the build, '') or (None, why not): an invalid SMILES, a chemistry problem, a geometry RDKit can't embed."""
    smiles = smiles.strip()
    if not smiles:
        return None, "Enter a SMILES"
    if count < 1:
        return None, "RDKit Conformers Must Be a Whole Number Above 0"
    if Available():
        return None, RDKIT_MISSING
    from rdkit import Chem, RDLogger
    from rdkit.Chem import AllChem, Descriptors, rdMolAlign
    # RDKit logs to stderr, which would print into the TUI and around --json output: its reasons are given here
    RDLogger.DisableLog("rdApp.*")

    molecule = Chem.MolFromSmiles(smiles, sanitize=False)
    if molecule is None or not molecule.GetNumAtoms():
        return None, "Not a Valid SMILES"
    problems = Chem.DetectChemistryProblems(molecule)
    if problems:
        return None, _Sentence(problems[0].Message())
    # Parsed again in full: sanitized, with its stereochemistry assigned (what's given in it, and what isn't)
    molecule = Chem.MolFromSmiles(smiles)
    if molecule is None:
        return None, "Not a Valid SMILES"
    warnings = []
    unassigned = [info for info in Chem.FindPotentialStereo(molecule)
                  if info.specified == Chem.StereoSpecified.Unspecified]
    centers = sum(1 for info in unassigned if info.type == Chem.StereoType.Atom_Tetrahedral)
    bonds = sum(1 for info in unassigned if info.type == Chem.StereoType.Bond_Double)
    if centers:
        warnings.append(f"{centers} Unassigned Stereocenter{'s' if centers > 1 else ''}: RDKit Picks One")
    if bonds:
        warnings.append(f"{bonds} Unassigned Double Bond{'s' if bonds > 1 else ''} (E/Z): RDKit Picks One")
    build = RDKitBuild(smiles, Chem.MolToSmiles(molecule), Chem.GetFormalCharge(molecule),
                       Descriptors.NumRadicalElectrons(molecule) + 1, "", 0, warnings=warnings)

    # Embedding (ETKDGv3), retried from random coordinates (cages, macrocycles) before giving up
    withH = Chem.AddHs(molecule)
    params = AllChem.ETKDGv3()
    # No pruning while embedding (small molecules would keep one): duplicates are dropped once optimized
    params.randomSeed, params.numThreads = SEED, 0
    ids = list(AllChem.EmbedMultipleConfs(withH, numConfs=count, params=params))
    if not ids:
        params.useRandomCoords = True
        ids = list(AllChem.EmbedMultipleConfs(withH, numConfs=count, params=params))
    if not ids:
        return None, "RDKit Couldn't Embed It in 3D"
    build.built = len(ids)

    # Optimized with MMFF94, else UFF (metals), else left as embedded (unranked)
    if AllChem.MMFFHasAllMoleculeParams(withH):
        results, build.forceField = AllChem.MMFFOptimizeMoleculeConfs(withH, numThreads=0, maxIters=OPTIMIZER_STEPS), "MMFF94"
    elif AllChem.UFFHasAllMoleculeParams(withH):
        results, build.forceField = AllChem.UFFOptimizeMoleculeConfs(withH, numThreads=0, maxIters=OPTIMIZER_STEPS), "UFF"
        warnings.append("MMFF94 Has No Parameters for It: Optimized With UFF")
    else:
        results = [(0, 0.0)] * len(ids)
        warnings.append("No Force Field Has Parameters for It: Embedded Geometries, Not Optimized or Ranked")
    unconverged = sum(1 for notConverged, _ in results if notConverged)
    if build.forceField and unconverged:
        warnings.append(f"{unconverged} of {len(ids)} Conformers Didn't Converge in {OPTIMIZER_STEPS} Steps")

    # Ranked by energy; conformers that optimized to the same minimum are kept once
    order = sorted(range(len(ids)), key=lambda index: results[index][1]) if build.forceField else list(range(len(ids)))
    lowest = results[order[0]][1]
    # Mirror images count as the same: an achiral molecule's conformers come in mirror pairs of equal energy, and a QM
    # job starts the same from either. Copies (Chem.Mol): aligning them never moves the coordinates written
    heavy, mirror = Chem.RemoveHs(withH), Chem.RemoveHs(withH)
    for conformer in mirror.GetConformers():
        for atom in range(mirror.GetNumAtoms()):
            x, y, z = conformer.GetAtomPosition(atom)
            conformer.SetAtomPosition(atom, (x, y, -z))

    def Same(index: int, other: int) -> bool:
        return (abs(results[index][1] - results[other][1]) < SAME_ENERGY
                and any(rdMolAlign.GetBestRMS(heavy, reference, ids[index], ids[other], maxMatches=MAX_MATCHES) < SAME_RMS
                        for reference in (heavy, mirror)))
    kept = []
    for index in order:
        if not (build.forceField and any(Same(index, other) for other in kept)):
            kept.append(index)
    symbols = [atom.GetSymbol() for atom in withH.GetAtoms()]
    for index in kept:
        positions = withH.GetConformer(ids[index]).GetPositions()
        lines = CoordinateLines(symbols, *([f"{value:.6f}" for value in positions[:, axis]] for axis in range(3)))
        build.conformers.append((results[index][1] - lowest, lines))
    return build, ""


def SmilesMolecule(smiles: str, name: str, count: int | None = None, rank: int = 1
                   ) -> tuple[Molecule | None, RDKitBuild | None, str]:
    """A SMILES' rank-th RDKit conformer as a molecule named name (count None: qol.toml's rdkitConformers), the build
    (for its conformer list and warnings), or why not: the one path cu -opt --smiles and cu -xyz --smiles share."""
    from .defaults import Defaults
    build, problem = RDKitConformers(smiles, count or Defaults.rdkitConformers)
    if build is None:
        return None, None, problem
    problem = build.RankProblem(rank)
    if problem:
        return None, build, problem
    return build.Molecule(name, rank), build, ""


def SmilesResult(build: RDKitBuild, molecule: Molecule) -> dict:
    """What a SMILES run reports (an agent reads these numbers, never computes them)."""
    return {"smiles": build.smiles, "canonicalSmiles": build.canonical, "rdkitConformer": molecule.conformer.Label(),
            "forceField": build.forceField or None, "rdkitConformersBuilt": build.built,
            "rdkitConformers": build.Energies()}
