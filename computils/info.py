"""What a file says, read once (one MapFile) through the extractors: `cu -info` (an agent's way to read an output
without reading it) and the TUI's Details pane (tui/inspect.FileDetails formats the same facts), so the two can never
disagree. Nothing here writes, prompts or computes a value the program didn't print: corrections are as written."""
from pathlib import Path

from .defaults import Defaults
from .fileops  import (MapFile, FindInMap, HasContent, ExtractFrom, ExtractXyz, ExtractTermination, ExtractResources,
                       ExtractEnergy, ExtractThermalCorrections, ExtractFrequencies, ExtractConvergence,
                       ExtractStability, ExtractOutOfSteps, ExtractSpinContamination, InputRoutes, JobChecks, SplitRoute,
                       ReadMolecule, Formula, PROGRAMS, TERMINATION_TAIL)
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

    kind: output / input / xyz / other / empty. Outputs: program, termination, status, charge, multiplicity, spin (and
    why), atoms, formula, route, method, basis, keys, cpus, memoryGB; with full: energy (the last SCF / FINAL SINGLE
    POINT, Hartree), enthalpyCorrection / gibbsCorrection (Hartree, as printed), imaginaryFrequencies, lowestFrequency,
    convergence (met/total of the last table), stability, outOfSteps, s2 (the last <S**2>). Inputs: what the input says
    (stages with what each runs), never run-derived values."""
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
        molecule = ReadMolecule(data, path)
        program = molecule.sourceProgram
        isOutput = path.suffix in OutputExtensions()
        isInput = path.suffix == program
        facts["kind"] = "output" if isOutput else "input" if isInput else "other"
        if SourceProgramName(program):
            facts["program"] = SourceProgramName(program)
        if isOutput:
            termination = ExtractTermination(data, -TERMINATION_TAIL)
            facts["status"] = Status(termination)
            if termination:
                facts["termination"] = termination
        if isInput:
            facts["stages"] = [JobChecks(route, program, molecule.sourceInput).kinds
                               for route in InputRoutes(data, program)]
        if molecule.multiplicity:
            facts["charge"], facts["multiplicity"] = _Number(molecule.charge), _Number(molecule.multiplicity)
            spinState, reason = ClassifySpin(molecule, data)
            facts["spin"], facts["spinReason"] = spinState.name, reason
        if molecule.coordinateList:
            facts["atoms"], facts["formula"] = len(molecule.coordinateList), Formula(molecule.coordinateList)
        if molecule.sourceRoute:
            facts["route"] = molecule.sourceRoute
            method, basis, keys = SplitRoute(molecule.sourceRoute)
            facts.update({name: value for name, value in (("method", method), ("basis", basis), ("keys", keys)) if value})
        # ExtractResources silently falls back to Defaults, which would report made-up values for this file
        if program and FindInMap(data, r"%nproc|nprocs", ignoreCase=True):
            facts["cpus"], facts["memoryGB"] = ExtractResources(data, program)
        if full and isOutput:
            energy = ExtractEnergy(data, program)
            if energy is not None:
                facts["energy"] = energy
            corrections = ExtractThermalCorrections(data, program)
            if corrections is not None:
                # ORCA's enthalpy correction is a difference of two printed values: rounded past their precision
                facts["enthalpyCorrection"], facts["gibbsCorrection"] = (round(value, 9) for value in corrections)
            frequencies = ExtractFrequencies(data, program)
            if frequencies:
                facts["imaginaryFrequencies"] = [value for value in frequencies if value < 0]
                real = [value for value in frequencies if value > 0]
                if real:
                    facts["lowestFrequency"] = real[0]
            convergence = ExtractConvergence(data)
            if convergence is not None:
                facts["convergence"] = {"met": convergence[0], "total": convergence[1]}
            stability = ExtractStability(data)
            if stability is not None:
                facts["stability"] = stability
            if ExtractOutOfSteps(data):
                facts["outOfSteps"] = True
            spin = ExtractSpinContamination(data)
            if spin is not None:
                facts["s2"] = spin
    return facts


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


def Optimizes(facts: dict) -> bool:
    """Whether an output's route optimizes (for -info's next step: its single point)."""
    route, program = facts.get("route", ""), facts.get("program", "")
    extension = Defaults.orcaExtension if program == "ORCA" else Defaults.gaussianExtension
    return bool(route) and JobChecks(route, extension).optimizes
