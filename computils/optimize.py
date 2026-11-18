"""The Optimization builder's engine (TUI_DESIGN 4.11), with no Textual import: the TUI's OptimizationScreen and
`cu -opt` both build an OptimizationJob from a molecule, check it with the same checks, render the same input
(jobs.JobInputText) and submit it the same way (written, then Run as Written, with the Neva-Touch Scheduler's step).
A later builder (high-throughput, conformers) makes many OptimizationJobs."""
import copy
from dataclasses import dataclass, field
from pathlib import Path

import regex

from .actions  import Action, SpinState, NTS_SP
from .catalog  import Catalog, ParseRouteTemplate, RouteTemplate, UnknownSelectors
from .defaults import Defaults
from .fileops  import (ElectronProblem, MoleculeElements, ReRunTemplate, Retarget, StageProblem, extensionGetter,
                       fileCreation)
from .intent   import IntentDraft
from .jobs     import DefaultMemoryRatio, JobInputText, OptMethods, StagesProblem
from .molecule import Molecule
from .spin     import SpinOverride

# The method choices' keys: a prior job's own route, then each Optimization Methods entry (method-0, method-1, ...)
FROM_SOURCE = "method-from"
# What `cu -opt --method` calls the prior job's own route
FROM_SOURCE_NAME = "From Source"
# A stage whose route needs lines after the geometry: Gaussian's Opt=ModRedundant
STAGE_INPUT = r"(?i)modred"


def ModRedCheck(text: str, atoms: int) -> tuple[str, list[str]]:
    """(a short summary, problems) for a stage's ModRed lines: atom numbers must be in the geometry (1 to atoms)."""
    lines = [line.split() for line in text.splitlines() if line.strip()]
    problems = []
    for number, fields in enumerate(lines, 1):
        # The type letter, then atom numbers until the first field that isn't one ('*' is any atom)
        for field in fields[1:]:
            if field == "*":
                continue
            if not regex.fullmatch(r"-?\d+", field):
                break
            if not 1 <= int(field) <= atoms:
                problems.append(f"✗ Line {number}: Atom {field} Isn't in the Geometry (1–{atoms})")
    summary = f"{len(lines)} Line{'s' if len(lines) != 1 else ''}" if lines else "Empty"
    return summary, problems


# ─── The method choices ───────────────────────────────────────────────

def MethodChoices(molecule: Molecule, prior: bool) -> tuple[dict[str, tuple[str, RouteTemplate | None, str, str]], str]:
    """({key: (label, template, input extension, its route as written)}, why From Source can't be used). A prior job's
    own route comes first (ReRunTemplate: the program it ran in), then the Optimization Methods in force. A None
    template can't be made. ReRunTemplate sets the molecule's program, so pass the job's own copy."""
    methods, fromProblem = {}, ""
    if prior:
        template, fromProblem = ReRunTemplate(molecule)
        # ReRunTemplate sets the program the job ran in, which a verbatim route's first token may not name
        methods[FROM_SOURCE] = (FROM_SOURCE_NAME, template, molecule.extensionType, molecule.sourceRoute)
    for index, (tag, route) in enumerate(OptMethods().items()):
        template = ParseRouteTemplate(route)
        methods[f"method-{index}"] = (tag, template, extensionGetter(template.method), route)
    return methods, fromProblem


def MethodProblems(methods: dict) -> dict[str, str]:
    """Why a method's stages can't be written (ORCA stages, mixed programs), by key."""
    return {key: StageProblem(template) for key, (_, template, _, _) in methods.items()
            if template is not None and StageProblem(template)}


def CustomMethod(route: str) -> tuple[RouteTemplate | None, str, str]:
    """(template, input extension, why there is none) for a route typed in full."""
    route = route.strip()
    if not route:
        return None, "", "Enter the Custom Route"
    unknown = UnknownSelectors(route)
    if unknown:
        return None, "", f"Unknown Selector {', '.join(unknown)}"
    template = ParseRouteTemplate(route)
    return template, extensionGetter(template.method), ""


def StartingSpin(molecule: Molecule, reason: str = "") -> str:
    """Sets where a singlet's spin starts (spinstates.txt, else how the prior job was classified, else CSS: the
    molecule's own spinState) and returns why ('' when nothing decided it). User input before heuristics: a person's
    choice (the builder's row, -opt --spin) replaces it afterwards."""
    override = SpinOverride(molecule.rootName) if molecule.rootName else None
    if override is not None:
        molecule.spinState, reason = override[0], f"spinstates.txt: {override[1]}"
    decided = reason and not reason.startswith("Multiplicity") and str(molecule.multiplicity or 1) == "1"
    return reason if decided else ""


def ModRedStages(template: RouteTemplate, extension: str) -> list[int]:
    """The stages (numbered from 1) whose route takes ModRed lines after the geometry (Gaussian only)."""
    if extension != Defaults.gaussianExtension:
        return []
    return [number for number, stage in enumerate(template.stages, 1) if regex.search(STAGE_INPUT, stage.base)]


def Chained(template: RouteTemplate, extension: str) -> bool:
    """Whether the method has later stages (Gaussian), which need the checkpoint: they read the geometry and orbitals
    from it."""
    return extension == Defaults.gaussianExtension and len(template.stages) > 1


# ─── The Neva-Touch Scheduler's SP ────────────────────────────────────

def SinglePointDefaults() -> tuple[int, float] | None:
    """The SP's default (CPUs, memory per core): the config's, the memory as its method needs (DLPNO). None without
    Benchmark Methods (no SP to schedule)."""
    if not Catalog.templates:
        return None
    template = Catalog.templates[0]
    return Defaults.CPU, DefaultMemoryRatio(template.base, extensionGetter(template.method))


def SinglePointProblem(molecule: Molecule, name: str) -> str:
    """Why the SP would be skipped for this molecule ('' if it can be written): genFile's check, on a copy pointed at
    the SP (Benchmark Methods' first entry), as `cu -sp` would point it."""
    template = Catalog.templates[0]
    spMolecule = copy.copy(molecule)
    Retarget(spMolecule, name + Defaults.singlePointExtra, template)
    return StagesProblem(template, spMolecule, MoleculeElements(spMolecule.coordinateList))


# ─── One job ──────────────────────────────────────────────────────────

@dataclass
class OptimizationJob:
    """Everything the builder's settings say. charge/multiplicity None: not valid yet (the TUI, mid-typing: the
    molecule keeps its own, and electrons aren't checked). memory None: as the method needs (DefaultMemoryRatio).
    stageInputs: ModRed lines by stage number. scheduled: the Neva-Touch Scheduler submits the SP (spCpus/spMemory)
    when it ends."""
    molecule:    Molecule
    name:        str
    charge:      int | None
    multiplicity: int | None
    spin:        SpinState
    template:    RouteTemplate
    extension:   str
    cpus:        int                 = 0
    memory:      float | None        = None
    checkpoint:  bool                = False
    stageInputs: dict[int, str]      = field(default_factory=dict)
    scheduled:   bool                = False
    spCpus:      int | None          = None
    spMemory:    float | None        = None
    spProblem:   str                 = ""      # why the scheduled SP would be skipped (set by Check)

    def Apply(self) -> None:
        """The settings onto the molecule (a copy the job owns): charge, multiplicity, spin, then its name, template,
        program and input path (Retarget)."""
        molecule = self.molecule
        if self.charge is not None:
            molecule.charge, molecule.multiplicity = self.charge, self.multiplicity
        # Spin only means something for singlets: a higher multiplicity is open shell
        molecule.spinState = SpinState.OPEN if (self.multiplicity or 1) > 1 else self.spin
        Retarget(molecule, self.name or "untitled", self.template, self.extension)

    def DefaultMemory(self) -> float:
        from .catalog import RenderRoute
        route, _ = RenderRoute(self.template, self.molecule)
        return DefaultMemoryRatio(route, self.extension)

    @property
    def chained(self) -> bool:
        return Chained(self.template, self.extension)

    def ExtraInput(self, rows: int | None = None) -> list[str]:
        """Each stage's lines after its geometry, for JobInputText ('' for a stage without ModRed lines)."""
        shown = set(ModRedStages(self.template, self.extension))
        count = rows if rows is not None else len(self.template.stages)
        return [self.stageInputs.get(number, "") if number in shown else "" for number in range(1, count + 1)]

    def Check(self) -> tuple[list[str], list[str], str]:
        """(errors, warnings, the project-file problem) after Apply. Errors stop the job; warnings don't; the
        project-file problem (StagesProblem: a missing basis or block) stops a generated input (the TUI lets a person
        fix an edited one)."""
        molecule, errors, warnings = self.molecule, [], []
        if self.cpus < 1:
            errors.append("CPUs Must Be a Whole Number Above 0")
        if self.charge is not None:
            electrons = ElectronProblem(molecule.coordinateList, self.charge, self.multiplicity)
            if electrons:
                errors.append(electrons)
        if self.scheduled and not (self.spCpus and self.spCpus >= 1 and self.spMemory and self.spMemory > 0):
            errors.append("SP CPUs and Memory Must Be Numbers Above 0")
        for number in ModRedStages(self.template, self.extension):
            summary, problems = ModRedCheck(self.stageInputs.get(number, ""), len(molecule.coordinateList))
            errors += [problem.removeprefix("✗ ").replace("Line", f"Stage {number} ModRed Line", 1) for problem in problems]
            if summary == "Empty":
                warnings.append(f"Stage {number} ModRed Is Empty")
        if self.memory is not None and self.memory <= 0:
            errors.append("Memory Must Be a Number Above 0")
        # The same check genFile skips jobs with: every stage's route, its project files, and the stages themselves
        fileProblem = StagesProblem(self.template, molecule, MoleculeElements(molecule.coordinateList))
        # ...and for the SP the Neva-Touch Scheduler will submit, so a missing basis shows now, not at 2 a.m.
        self.spProblem = (SinglePointProblem(molecule, self.name or "untitled")
                          if self.scheduled and Catalog.templates else "")
        if self.spProblem:
            warnings.append("Scheduled SP Problem")
        return errors, warnings, fileProblem

    def InputText(self, rows: int | None = None) -> str:
        """The input as genFile would write it (the builder's preview)."""
        memory = self.memory if self.memory is not None else self.DefaultMemory()
        return JobInputText(self.molecule, self.checkpoint or self.chained, False, self.cpus, memory,
                            extraInput=self.ExtraInput(rows))

    @property
    def path(self) -> Path:
        return Path.cwd() / fileCreation(self.name, self.extension)

    def Draft(self, path: Path) -> IntentDraft:
        """Run as Written for the saved input, with the Neva-Touch Scheduler's step (the SP, with its resources)."""
        draft = IntentDraft(action=Action.RUN, files=[Path(path.name)])
        if self.scheduled:
            draft.nevaTouch = NTS_SP
            draft.nextCpus, draft.nextMemoryRatio = self.spCpus, self.spMemory
        return draft
