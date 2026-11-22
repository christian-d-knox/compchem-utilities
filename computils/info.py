"""What a file says, read once (one MapFile) through the extractors: `cu -info` (an agent's way to read an output
without reading it) and the TUI's Details pane (tui/inspect.FileDetails formats the same facts), so the two can never
disagree. Nothing here writes, prompts or computes a value the program didn't print: corrections are as written."""
from pathlib import Path

import regex

from .defaults import Defaults
from .fileops  import (ElectronProblem, MapFile, FindInMap, HasContent, ExtractFrom, ExtractXyz, ExtractTermination, ExtractResources,
                       ExtractEnergy, ExtractThermalCorrections, ExtractFrequencies, ExtractConvergence,
                       ExtractStability, ExtractOutOfSteps, ExtractSpinContamination, ExtractThermochemistry,
                       ExtractSolvation, ExtractRouteEcho, InputRoutes, JobChecks, SplitRoute, ReadMolecule, Formula,
                       StageSpans, ExtractParameters, ExtractModRedInput, ExtractTSEigenvector, Symbols, PROGRAMS,
                       TERMINATION_TAIL, CREST_PROGRAM, ExtractProgram)
from .molecule import Molecule
from .spin     import ClassifySpin


def OutputExtensions() -> tuple[str, ...]:
    return ".log", Defaults.outputExtension


def SourceProgramName(program: str) -> str:
    """Display name of the program that wrote a file (ReadMolecule's sourceProgram, an input extension), or ''."""
    return next((name for name, extensionKey in PROGRAMS.values()
                 if program and getattr(Defaults, extensionKey) == program), "")


def Status(termination: str) -> str:
    """Normal / Error / Unknown (no termination line in the tail: running, or killed)."""
    if not termination:
        return "Unknown"
    return "Error" if termination == Defaults.terminationVariants[2] else "Normal"


def _Number(value: str):
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


def FileFacts(path: Path, full: bool = True) -> dict:
    """Everything one file says, as typed values; a value the file doesn't have is left out. full=False reads only what
    the TUI's Details pane shows (no energies, frequencies or convergence: a highlight must stay quick on big outputs).

    kind: output / input / xyz / other / empty. Outputs: program, status, termination, charge, multiplicity, spin (and
    why), atoms, formula, cpus, memoryGB, and each stage's results (StageFacts). A one-stage output's are in the record
    itself; a staged output's (--Link1--: Opt+Freq -> SP in one file) in `stages`, one record each, read only from that
    stage's part of the file, with status and termination the last stage's. full=False: the route, method, basis and
    keys of the job's first stage, and the status from the file's tail. Inputs: what the input says (stages: what each
    one runs), never run-derived values. coordinates (the last geometry) and the stages' frequencies (all of them) are
    for the data file (datafile.DATA_FILE_ONLY: `cu -info` leaves them out). A CREST output's are crest.CrestFacts'."""
    path = Path(path)
    facts: dict = {"file": path.name}
    if not HasContent(path):
        facts["kind"] = "empty"
        return facts
    if path.suffix == Defaults.coordExtension:
        coordinates = ExtractFrom(path, ExtractXyz, empty=[])
        facts.update(kind="xyz", atoms=len(coordinates), formula=Formula(coordinates))
        return facts
    with MapFile(path) as data:
        if path.suffix in OutputExtensions() and ExtractProgram(data, path.suffix) == CREST_PROGRAM:
            from .crest import CrestFacts
            return CrestFacts(data, path, full)
        molecule = ReadMolecule(data, path)
        program = molecule.sourceProgram
        isOutput = path.suffix in OutputExtensions()
        isInput = path.suffix == program
        staged = full and isOutput
        facts["kind"] = "output" if isOutput else "input" if isInput else "other"
        if SourceProgramName(program):
            facts["program"] = SourceProgramName(program)
        stages = []
        if staged:
            stages = [StageFacts(data, program, span, molecule, number)
                      for number, span in enumerate(StageSpans(data, program), 1)]
            last = stages[-1] if stages else {}
            facts["status"] = last.get("status", "Unknown")
            if last.get("termination"):
                facts["termination"] = last["termination"]
        elif isOutput:
            termination = ExtractTermination(data, -TERMINATION_TAIL)
            facts["status"] = Status(termination)
            if termination:
                facts["termination"] = termination
        if isInput:
            facts["stages"] = [JobChecks(route, program, molecule.sourceInput).kinds
                               for route in InputRoutes(data, program)]
        if molecule.multiplicity:
            facts["charge"], facts["multiplicity"] = _Number(molecule.charge), _Number(molecule.multiplicity)
            if stages:
                s2 = next((item["s2"] for item in reversed(stages) if "s2" in item), None)
                spinState, reason = ClassifySpin(molecule, data, spinSquared=s2)
            else:
                spinState, reason = ClassifySpin(molecule, data)
            facts["spin"], facts["spinReason"] = spinState.name, reason
        if molecule.coordinateList:
            facts["atoms"], facts["formula"] = len(molecule.coordinateList), Formula(molecule.coordinateList)
            # A charge and multiplicity the atoms can't have: Gaussian and ORCA would stop at once
            charge, multiplicity = facts.get("charge"), facts.get("multiplicity")
            if isinstance(charge, int) and isinstance(multiplicity, int):
                problem = ElectronProblem(molecule.coordinateList, charge, multiplicity)
                if problem:
                    facts["electronProblem"] = problem
        if molecule.sourceRoute and not staged:
            facts.update(_RouteFacts(molecule.sourceRoute))
        # ExtractResources silently falls back to Defaults, which would report made-up values for this file
        if program and FindInMap(data, r"%nproc|nprocs", ignoreCase=True):
            facts["cpus"], facts["memoryGB"] = ExtractResources(data, program)
        if staged and molecule.coordinateList:
            facts["coordinates"] = [" ".join(row.split()) for row in molecule.coordinateList]
    if len(stages) == 1:
        facts.update({key: value for key, value in stages[0].items() if key != "stage"})
    elif stages:
        facts["stages"] = stages
    return facts


def _RouteFacts(route: str) -> dict:
    method, basis, keys = SplitRoute(route)
    return {"route": route, **{name: value for name, value in (("method", method), ("basis", basis), ("keys", keys))
                               if value}}


def StageFacts(data, program: str, span: tuple[int, int, str], molecule: Molecule, number: int) -> dict:
    """One stage's results, every value read within its span (fileops.StageSpans): what it ran (its route as echoed),
    how it ended, its energy (the last SCF / FINAL SINGLE POINT, Hartree), solvation, the thermochemistry totals as
    printed (zeroPoint, energyZeroPoint, thermalEnergy, enthalpy, gibbs) and corrections (enthalpyCorrection,
    gibbsCorrection), its frequencies (all, imaginaryFrequencies, lowestFrequency), convergence (the last table's
    met/total), stability, outOfSteps and s2 (the last <S**2>)."""
    start, end, termination = span
    route = ((ExtractRouteEcho(data, start) if program == Defaults.gaussianExtension else None)
             or (molecule.sourceRoute if number == 1 else ""))
    stage: dict = {"stage": number}
    checks = JobChecks(route, program, molecule.sourceInput) if route else None
    if checks:
        stage["runs"] = checks.kinds
    stage["status"] = Status(termination)
    if termination:
        stage["termination"] = termination
    if route:
        stage.update(_RouteFacts(route))
    solvation = ExtractSolvation(data, program, molecule.sourceInput, start, end)
    if solvation:
        stage["solvation"] = solvation
    energy = ExtractEnergy(data, program, start, end)
    if energy is not None:
        stage["energy"] = energy
    stage.update(ExtractThermochemistry(data, program, start, end))
    corrections = ExtractThermalCorrections(data, program, start, end)
    if corrections is not None:
        # ORCA's enthalpy correction is a difference of two printed values: rounded past their precision
        stage["enthalpyCorrection"], stage["gibbsCorrection"] = (round(value, 9) for value in corrections)
    frequencies = ExtractFrequencies(data, program, start, end)
    if frequencies:
        stage["frequencies"] = frequencies
        stage["imaginaryFrequencies"] = [value for value in frequencies if value < 0]
        real = [value for value in frequencies if value > 0]
        if real:
            stage["lowestFrequency"] = real[0]
    convergence = ExtractConvergence(data, start, end) if (checks is None or checks.optimizes) else None
    if convergence is not None:
        stage["convergence"] = {"met": convergence[0], "total": convergence[1]}
    stability = ExtractStability(data, start, end, program)
    if stability is not None:
        stage["stability"] = stability
    if convergence is not None and ExtractOutOfSteps(data, start, end, program):
        stage["outOfSteps"] = True
    spin = ExtractSpinContamination(data, start, end, program)
    if spin is not None:
        stage["s2"] = spin
    if checks and checks.optimizes:
        stage.update(_OptimizationFacts(data, program, span, molecule, checks))
    return stage


# Gaussian's internal coordinate letters (ExtractParameters, ModRedundant lines) and ORCA's constraint letters
COORDINATES = {"R": "Bond", "B": "Bond", "A": "Angle", "D": "Dihedral", "L": "Linear Bend", "X": "Cartesian",
               "C": "Cartesian"}
COORDINATE_UNITS = {"Bond": "A", "Angle": "deg", "Dihedral": "deg", "Linear Bend": "deg"}


def _Atoms(numbers, symbols: list[str], offset: int) -> list[str]:
    # 'C48': the element and the number as the program numbers it (Gaussian from 1, ORCA from 0)
    return [f"{symbols[number - offset]}{number}" if 0 <= number - offset < len(symbols) else str(number)
            for number in numbers]


def _Coordinate(letter: str, atoms: list[str], value=None) -> dict:
    coordinate = COORDINATES.get(letter, letter)
    record = {"coordinate": coordinate, "atoms": atoms}
    if value is not None:
        record["value"] = value
    if coordinate in COORDINATE_UNITS:
        record["unit"] = COORDINATE_UNITS[coordinate]
    return record


def _OptimizationFacts(data, program: str, span: tuple[int, int, str], molecule: Molecule, checks) -> dict:
    """An optimizing stage's constraints (each frozen or scanned coordinate with its final value; `inherited` when the
    stage's route didn't ask for them: they came with the checkpoint) and, for a Gaussian TS optimization, its last TS
    eigenvector (tsEigenvector: the coordinates the mode is made of, each with its coefficient and final value) and
    tsEigenvalue (only when Gaussian prints that it should be negative). Everything as the program printed it."""
    start, end, _ = span
    symbols = Symbols(molecule.coordinateList or [])
    facts: dict = {}
    if program == Defaults.orcaExtension:
        constraints = [{**_Coordinate(letter, _Atoms(atoms, symbols, 0), value), "action": "Frozen"}
                       for letter, atoms, value in OrcaConstraints(molecule.sourceInput)]
        if constraints:
            facts["constraints"] = constraints
        return facts
    vector, eigenvalue = ExtractTSEigenvector(data, start, end) if checks.optKind == "OptTS" else ([], None)
    tsNames = {name for name, _ in vector}
    first = ExtractParameters(data, start, end, first=True, onlyNames=tsNames or None, onlyHeld=True)
    needLast = tsNames | set(first)
    last = ExtractParameters(data, start, end, onlyNames=needLast) if needLast else {}
    hasScan = any(info.lower().startswith("scan") for _, _, _, info in first.values())
    scans = ({(COORDINATES.get(kind, kind), tuple(sorted(atoms))): extra
              for kind, atoms, action, extra in ExtractModRedInput(data, start, end) if action == "S"}
             if hasScan else {})
    constraints = []
    for name, (letter, atoms, value, info) in first.items():
        held = info.lower()
        if not held.startswith(("frozen", "scan")):
            continue
        record = _Coordinate(letter, _Atoms(atoms, symbols, 1), last.get(name, (letter, atoms, value))[2])
        record["action"] = "Scan" if held.startswith("scan") else "Frozen"
        scan = scans.get((record["coordinate"], tuple(sorted(atoms))))
        if record["action"] == "Scan" and scan and len(scan) >= 2:
            try:
                record["steps"], record["step"] = int(scan[0]), float(scan[1])
            except ValueError:
                pass
        if checks.optKind != "ModRed":
            record["inherited"] = True
        constraints.append(record)
    if constraints:
        facts["constraints"] = constraints
    if checks.optKind == "OptTS":
        named = []
        for name, coefficient in vector:
            row = last.get(name) or first.get(name)
            named.append({"coefficient": coefficient,
                          **(_Coordinate(row[0], _Atoms(row[1], symbols, 1), row[2]) if row
                             else {"coordinate": name, "atoms": []})})
        if named:
            facts["tsEigenvector"] = named
        if eigenvalue is not None:
            facts["tsEigenvalue"] = eigenvalue
    return facts


ORCA_CONSTRAINT_ATOMS = {"B": 2, "A": 3, "D": 4, "C": 1}
_ORCA_CONSTRAINT_BLOCK_RE = regex.compile(r"(?is)%geom\b.*?\bconstraints\b(.*?)\bend\b")
_ORCA_CONSTRAINT_ENTRY_RE = regex.compile(r"\{([^}]*)\}")


def OrcaConstraints(inputText: str) -> list[tuple[str, tuple[int, ...], float | None]]:
    """An ORCA input's %geom Constraints, as written: (letter, atoms (ORCA's own, from 0), value or None), e.g.
    '{B 0 3 2.05 C}' -> ('B', (0, 3), 2.05)."""
    block = _ORCA_CONSTRAINT_BLOCK_RE.search(inputText or "")
    found = []
    for entry in _ORCA_CONSTRAINT_ENTRY_RE.findall(block.group(1) if block else ""):
        tokens = entry.split()
        if not tokens or tokens[0].upper() not in ORCA_CONSTRAINT_ATOMS or tokens[-1].upper() != "C":
            continue
        count = ORCA_CONSTRAINT_ATOMS[tokens[0].upper()]
        atoms, rest = tokens[1:1 + count], tokens[1 + count:-1]
        if len(atoms) != count or not all(atom.isdigit() for atom in atoms):
            continue
        try:
            value = float(rest[0]) if rest else None
        except ValueError:
            value = None
        found.append((tokens[0].upper(), tuple(int(atom) for atom in atoms), value))
    return found


def PreviewMolecule(path: Path) -> tuple[Molecule, str]:
    """A Molecule read the way dispatch reads one (one map: last geometry, charge, multiplicity, spin, route), but
    without writing the .xyz file, and why its spin state is what it is. The TUI's previews and `cu -opt`."""
    if not HasContent(path):
        return Molecule(path, path.stem, "", "", [], path.suffix, path.stem), ""
    with MapFile(path) as data:
        molecule = ReadMolecule(data, path)
        molecule.spinState, reason = ClassifySpin(molecule, data)
    return molecule, reason


def SourceMolecule(path: Path) -> tuple[Molecule, str, bool]:
    """The Optimization builder's two file sources: (molecule, why its spin is what it is, whether it's a prior job).
    An .xyz carries no charge or multiplicity (0 and 1 until set); a prior input or output, its own."""
    path = Path(path)
    if path.suffix == Defaults.coordExtension:
        return (Molecule(path, path.stem, "0", "1", ExtractFrom(path, ExtractXyz, empty=[]), path.suffix, path.stem),
                "", False)
    molecule, reason = PreviewMolecule(path)
    return molecule, reason, True


def LastStage(facts: dict) -> dict:
    """An output's last stage: a staged record's last `stages` entry, else the record itself (one stage)."""
    stages = facts.get("stages")
    return stages[-1] if stages and isinstance(stages[0], dict) else facts


def Optimizes(facts: dict) -> bool:
    """Whether an output's last stage optimizes (for -info's next step: its single point; a job that already ran its
    single point stage doesn't). A stage's record (StageFacts) works too."""
    route, program = LastStage(facts).get("route", ""), facts.get("program", "")
    extension = Defaults.orcaExtension if program == "ORCA" else Defaults.gaussianExtension
    return bool(route) and JobChecks(route, extension).optimizes
