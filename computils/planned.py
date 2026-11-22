"""The jobs a batch command would submit (-sp, -b, -re, -r), planned without writing anything: each job's name, its
input as genFile will write it, what it runs, and why it would be skipped. The TUI's job screens show these; the names
come from the workflows' own functions and the text from jobs.JobInputText, so a preview is what ⏎ writes. No Textual."""
import copy
from dataclasses import dataclass
from pathlib import Path

from .catalog  import Catalog, RouteTemplate
from .defaults import Defaults
from .fileops  import (ExtractFromText, ExtractResources, HasContent, InputRoutes, JobChecks,
                       MapFile, MoleculeElements, ReRunTemplate, Retarget)
from .jobs     import JobInputText, StagesProblem
from .molecule import Molecule
from .workflows import BenchmarkName, ReRunName, SinglePointName



@dataclass
class Resources:
    """What a generated input's Link 0 / %pal and the job's options take (None: the config's, or per method)."""
    cpus: int | None = None
    memory: float | None = None       # GB per core (None: as the method needs, jobs.DefaultMemoryRatio)
    checkpoint: bool = False
    nbo: bool = False


@dataclass
class PlannedJob:
    """One job a command would submit."""
    name: str                         # the job's name (its input, script and output are named after it)
    source: Path                      # the file it comes from
    extension: str                    # its program, as its input extension
    text: str = ""                    # its input as it will be written ('' when it can't be)
    problem: str = ""                 # why it would be skipped ('' when it will be submitted)
    spin: str = ""                    # CSS / OSS / OPEN ('' for an input as written: nothing is classified)
    method: str = ""                  # a benchmark row's method
    template: RouteTemplate | None = None
    molecule: Molecule | None = None  # the molecule as its workflow retargets it (None for an input as written)
    cpus: int | None = None           # an input as written: its own CPUs and memory (GB)
    memoryGB: int | None = None

    def Routes(self) -> list[str]:
        """Each stage's route as written in the text."""
        return ExtractFromText(self.text, InputRoutes, self.extension, empty=[]) if self.text else []

    def Kinds(self) -> list[str]:
        """What each stage runs (JobChecks' kinds: SP, Opt+Freq, Stability...)."""
        inputText = self.text if self.extension == Defaults.orcaExtension else ""
        return [JobChecks(route, self.extension, inputText).kinds for route in self.Routes()]

    @property
    def runs(self) -> str:
        return " → ".join(self.Kinds())

    @property
    def optimizes(self) -> bool:
        inputText = self.text if self.extension == Defaults.orcaExtension else ""
        return any(JobChecks(route, self.extension, inputText).optimizes for route in self.Routes())


def _Generated(molecule: Molecule | None, source: Path, name: str, template: RouteTemplate | None, problem: str,
               resources: Resources, extension: str = "", method: str = "") -> PlannedJob:
    """A generated job: the molecule retargeted as its workflow retargets it, checked as genFile checks it (the one
    skip check, StagesProblem), and its text as genFile writes it."""
    if molecule is None or not molecule.coordinateList:
        return PlannedJob(name, source, extension, problem=problem or "No Coordinates Found", method=method,
                          spin=molecule.spinState.name if molecule is not None else "")
    if template is None:
        return PlannedJob(name, source, extension or molecule.extensionType, problem=problem,
                          spin=molecule.spinState.name, method=method)
    job = copy.copy(molecule)
    Retarget(job, name, template, extension)
    planned = PlannedJob(name, source, job.extensionType, spin=job.spinState.name, method=method, template=template,
                         molecule=job)
    planned.problem = StagesProblem(template, job, MoleculeElements(job.coordinateList))
    if not planned.problem:
        planned.text = JobInputText(job, resources.checkpoint, resources.nbo, resources.cpus, resources.memory)
    return planned


def PlanSinglePoints(molecules: list[tuple[Path, Molecule | None]], template: RouteTemplate | None,
                     resources: Resources, problem: str = "") -> list[PlannedJob]:
    """-sp: each file's single point with one template (a Single Point Methods entry, or a route of your own)."""
    jobs = []
    for source, molecule in molecules:
        name = SinglePointName(molecule) if molecule is not None else source.stem + Defaults.singlePointExtra
        jobs.append(_Generated(molecule, source, name, template, problem, resources))
    return jobs


def PlanBenchmark(molecules: list[tuple[Path, Molecule | None]], start: int, resources: Resources) -> list[PlannedJob]:
    """-b -ovr start: every Single Point Methods entry from start on, for each file (genBench: the first named as a
    single point, the rest by index and method)."""
    jobs = []
    for source, molecule in molecules:
        for index in range(start, len(Catalog.templates)):
            template = Catalog.templates[index]
            if molecule is None:
                name = source.stem + Defaults.singlePointExtra
            else:
                name = SinglePointName(molecule) if index == start else BenchmarkName(molecule, index, template)
            jobs.append(_Generated(molecule, source, name, template, "", resources, method=template.method))
    return jobs


def PlanReRuns(molecules: list[tuple[Path, Molecule | None]], resources: Resources,
               template: RouteTemplate | None = None, problem: str = "") -> list[PlannedJob]:
    """-re: each file re-run from its last geometry, named as a re-run (8a_re, past names with an output), with the
    route it ran (fileops.ReRunTemplate) or a corrected one (template: --route / --method; problem: why it can't be
    made)."""
    jobs = []
    for source, molecule in molecules:
        if molecule is None:
            jobs.append(_Generated(None, source, source.stem + Defaults.reRunExtra, None, "", resources))
            continue
        job = copy.copy(molecule)
        if template is not None or problem:
            jobs.append(_Generated(job, source, ReRunName(job), template, problem, resources))
            continue
        ownTemplate, ownProblem = ReRunTemplate(job)
        jobs.append(_Generated(job, source, ReRunName(job), ownTemplate, ownProblem, resources, job.extensionType))
    return jobs


def PlanAsWritten(paths: list[Path]) -> list[PlannedJob]:
    """-r: each input as written, with its own CPUs and memory (jobs.runJob reads them the same way)."""
    jobs = []
    for path in paths:
        path = Path(path)
        job = PlannedJob(path.stem, path, path.suffix)
        if path.suffix not in (Defaults.gaussianExtension, Defaults.orcaExtension):
            job.problem = f"Only Gaussian ({Defaults.gaussianExtension}) and ORCA ({Defaults.orcaExtension}) Inputs"
        elif not HasContent(path):
            job.problem = "Empty File"
        else:
            with MapFile(path) as data:
                job.cpus, job.memoryGB = ExtractResources(data, path.suffix, path.name)
            job.text = path.read_text(errors="replace")
        jobs.append(job)
    return jobs


def LoadMolecules(paths: list[Path]) -> list[tuple[Path, Molecule | None]]:
    """Each file's molecule as dispatch reads it (one map: its last geometry, charge, multiplicity, spin classified),
    without writing the .xyz dispatch writes. None for an empty file."""
    from .info import PreviewMolecule
    molecules = []
    for path in paths:
        path = Path(path).resolve()
        molecule = PreviewMolecule(path)[0] if HasContent(path) else None
        molecules.append((path, molecule))
    return molecules
