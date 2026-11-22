"""The Optimization builder's engine, with no Textual import: the TUI's OptimizationScreen and
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
from .fileops  import (ElectronProblem, MoleculeElements, NamedRoutes, ReRunTemplate, Retarget, StageProblem,
                       extensionGetter, fileCreation)
from .intent   import IntentDraft
from .jobs     import DefaultMemoryRatio, HeaderSends, JobInputText, OptMethods, StagesProblem, WallTimeProblem
from .molecule import Molecule
from .nevatouch import STEP_SP, ChainPath, ChainStep, WriteChain
from .spin     import SpinOverride

# The method choices' keys: a prior job's own route, then each Optimization Methods entry (method-0, method-1, ...)
FROM_SOURCE = "method-from"
# What `cu -opt --method` calls the prior job's own route
FROM_SOURCE_NAME = "From Source"
# A stage whose route needs lines after the geometry: Gaussian's Opt=ModRedundant
STAGE_INPUT = r"(?i)modred"
_STAGE_INPUT_RE = regex.compile(STAGE_INPUT)
_SIGNED_INT_RE = regex.compile(r"-?\d+")
# How Gaussian stages are separated: split on the separator line alone, so the stages join back exactly
# (fileops.InputStageTexts also takes the blank lines around it: right for re-runs, not for editing in place)
LINK1_LINE = r"(?im)^[ \t]*--link1--[ \t]*\r?\n"
STAGE_SEPARATOR = "--Link1--\n"
# Stands in for a stage's extra input (ModRed lines) to find where JobInputText writes it (ReadStage)
EXTRA_SENTINEL = "@@COMPUTILS-EXTRA-INPUT@@"


def SplitStages(text: str, extension: str) -> list[str]:
    """An input's stages exactly as written: a Gaussian input's --Link1-- sections (JoinStages puts them back byte
    for byte), an ORCA input whole."""
    if extension != Defaults.gaussianExtension:
        return [text]
    return regex.split(LINK1_LINE, text)


def JoinStages(stages: list[str]) -> str:
    return STAGE_SEPARATOR.join(stages)


def ExtraInputOf(withSentinel: str, typed: str) -> str | None:
    """The lines typed where a stage's extra input goes, when the typed stage differs from the generated one only
    there ('' for no lines), or None when it was changed anywhere else. withSentinel: the stage generated with
    EXTRA_SENTINEL as its extra input (JobInputText writes it as its own part: the parts around it end with a blank
    line); without the sentinel in it (a stage that takes no extra input), only an unchanged stage is ''."""
    normal = lambda text: text.replace("\r\n", "\n").rstrip("\n")
    typed = normal(typed)
    before, marker, after = withSentinel.partition(EXTRA_SENTINEL + "\n")
    if not marker:
        return "" if typed == normal(withSentinel) else None
    # Without extra input JobInputText writes no part at all: the blank line after the sentinel goes too
    if typed == normal(before + after[1:]):
        return ""
    after = normal(after)
    if not typed.startswith(before) or not typed.endswith(after) or len(typed) < len(before) + len(after):
        return None
    lines = typed[len(before):len(typed) - len(after)].strip("\n")
    # One block of lines: a blank line inside would end the section (anything after it is another section's)
    return lines if lines and "\n\n" not in lines else None


def ModRedCheck(text: str, atoms: int) -> tuple[str, list[str]]:
    """(a short summary, problems) for a stage's ModRed lines: atom numbers must be in the geometry (1 to atoms)."""
    lines = [line.split() for line in text.splitlines() if line.strip()]
    problems = []
    for number, fields in enumerate(lines, 1):
        # The type letter, then atom numbers until the first field that isn't one ('*' is any atom)
        for field in fields[1:]:
            if field == "*":
                continue
            if not _SIGNED_INT_RE.fullmatch(field):
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
    return [number for number, stage in enumerate(template.stages, 1) if _STAGE_INPUT_RE.search(stage.base)]


def Chained(template: RouteTemplate, extension: str) -> bool:
    """Whether the method has later stages (Gaussian), which need the checkpoint: they read the geometry and orbitals
    from it."""
    return extension == Defaults.gaussianExtension and len(template.stages) > 1


# ─── The Neva-Touch Scheduler's next job: a single point ──────────────

# Where the SP's coordinates go: they are the optimization's last geometry, unknown until it ends, so a preview marks
# the place and never shows the starting ones (never display something known to be potentially wrong)
COORDINATES_MARKER = "‹{atoms} Atoms: the {source}'s Last Geometry›"


def SlurmOverrides(wallTime: str | None, partition: str | None, cluster: str | None) -> dict[str, str | None]:
    """A job's SLURM settings for its intent (or chain step): each one only where it differs from the config (None
    follows it), so a job left at the config's writes the same script and hook as any other."""
    values = {"wallTime": wallTime, "partition": partition, "cluster": cluster}
    return {key: value.strip() if value is not None and value.strip() != str(getattr(Defaults, key)) else None
            for key, value in values.items()}


def SlurmProblems(wallTime: str | None, partition: str | None, cluster: str | None) -> list[str]:
    """What stops these SLURM settings: a wall time that isn't whole hours, an empty partition or cluster (where the
    cluster's header has the line)."""
    errors = [WallTimeProblem(wallTime)] if wallTime is not None and WallTimeProblem(wallTime) else []
    for key, value in (("partition", partition), ("cluster", cluster)):
        if value is not None and HeaderSends(key) and not value.strip():
            errors.append(f"Enter a {key.title()}")
    return errors


def SinglePointChoices() -> dict[str, tuple[str, RouteTemplate, str, str]]:
    """The scheduled SP's methods, keyed and shaped as MethodChoices' are ({key: (label, template, input extension,
    route as written)}): Single Point Methods in force, named as Optimization Methods are (the method, a repeat
    numbered; fileops.NamedRoutes), entry 0 first (cu -sp's default)."""
    entries = NamedRoutes([("", [(0, route)]) for route in Catalog.benchmarkLines])
    choices = {}
    for index, ((label, _), route, template) in enumerate(zip(entries, Catalog.benchmarkLines, Catalog.templates)):
        choices[f"sp-method-{index}"] = (label, template, extensionGetter(template.method), route)
    return choices


@dataclass
class SinglePointStep:
    """The Neva-Touch Scheduler's next job after an optimization: a single point of its last geometry, written when it
    ends as `cu -sp` writes one (its charge, multiplicity and spin read from the output then). name: the method's
    label; route: its entry as written (a Single Point Methods entry, or a Custom route). memory None: as the method
    needs. wallTime/partition/cluster None: the config's. Carried to the hook as a nevatouch.ChainStep."""
    name:       str
    route:      str
    cpus:       int          = 0
    memory:     float | None = None
    wallTime:   str | None   = None
    partition:  str | None   = None
    cluster:    str | None   = None
    nbo:        bool         = False
    checkpoint: bool         = False

    @property
    def template(self) -> RouteTemplate:
        return ParseRouteTemplate(self.route)

    @property
    def extension(self) -> str:
        return extensionGetter(self.template.method)

    def Molecule(self, optimization: Molecule, optName: str) -> Molecule:
        """The SP's molecule as `cu -sp` points one: a copy of the optimization's (its charge, multiplicity and spin as
        set now), named <opt>_SP, with this template and program."""
        molecule = copy.copy(optimization)
        Retarget(molecule, (optName or "untitled") + Defaults.singlePointExtra, self.template, self.extension)
        return molecule

    def DefaultMemory(self, optimization: Molecule, optName: str) -> float:
        from .catalog import RenderRoute
        route, _ = RenderRoute(self.template, self.Molecule(optimization, optName))
        return DefaultMemoryRatio(route, self.extension)

    def NboApplies(self) -> bool:
        return self.nbo and self.extension == Defaults.gaussianExtension

    def Check(self, optimization: Molecule, optName: str) -> tuple[list[str], str]:
        """(errors, the project-file problem): its resources and SLURM settings, then genFile's check (StagesProblem)
        for its template on this molecule, so a missing basis shows now, not at 2 a.m."""
        errors = []
        if self.cpus < 1:
            errors.append("CPUs Must Be a Whole Number Above 0")
        if self.memory is not None and self.memory <= 0:
            errors.append("Memory Must Be a Number Above 0")
        errors += SlurmProblems(self.wallTime, self.partition, self.cluster)
        problem = StageProblem(self.template)
        if not problem:
            molecule = self.Molecule(optimization, optName)
            problem = StagesProblem(self.template, molecule, MoleculeElements(molecule.coordinateList))
        return errors, problem

    def InputText(self, optimization: Molecule, optName: str, source: str = "Optimization") -> str:
        """The SP's input as genFile will write it once the optimization ends, its coordinates marked
        (COORDINATES_MARKER, naming the job it follows: source), never the starting ones. The route renders for the
        spin chosen now; the SP's is classified from the optimization's output then (spin.ClassifySpin)."""
        molecule = self.Molecule(optimization, optName)
        memory = self.memory if self.memory is not None else self.DefaultMemory(optimization, optName)
        text = JobInputText(molecule, self.checkpoint, self.NboApplies(), self.cpus, memory)
        coordinates = "".join(molecule.coordinateList or [])
        if coordinates:
            text = text.replace(coordinates, COORDINATES_MARKER.format(atoms=len(molecule.coordinateList),
                                                                       source=source) + "\n")
        return text

    def ChainStep(self) -> ChainStep:
        slurm = SlurmOverrides(self.wallTime, self.partition, self.cluster)
        return ChainStep(STEP_SP, self.name, self.route, self.cpus, self.memory, slurm["wallTime"],
                         slurm["partition"], slurm["cluster"], self.NboApplies(),
                         self.checkpoint and self.extension == Defaults.gaussianExtension)


# ─── One job ──────────────────────────────────────────────────────────

@dataclass
class OptimizationJob:
    """Everything the builder's settings say. charge/multiplicity None: not valid yet (the TUI, mid-typing: the
    molecule keeps its own, and electrons aren't checked). memory None: as the method needs (DefaultMemoryRatio).
    stageInputs: ModRed lines by stage number. then: the Neva-Touch Scheduler's next job, submitted when it ends (None:
    no chain). nbo: the $NBO keylist, read by the last stage (Gaussian only). wallTime/partition/cluster: the job's
    SLURM settings (None: the config's)."""
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
    then:        SinglePointStep | None = None
    nbo:         bool                = False
    wallTime:    str | None          = None
    partition:   str | None          = None
    cluster:     str | None          = None
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
        for number in ModRedStages(self.template, self.extension):
            summary, problems = ModRedCheck(self.stageInputs.get(number, ""), len(molecule.coordinateList))
            errors += [problem.removeprefix("✗ ").replace("Line", f"Stage {number} ModRed Line", 1) for problem in problems]
            if summary == "Empty":
                warnings.append(f"Stage {number} ModRed Is Empty")
        if self.memory is not None and self.memory <= 0:
            errors.append("Memory Must Be a Number Above 0")
        errors += SlurmProblems(self.wallTime, self.partition, self.cluster)
        # The same check genFile skips jobs with: every stage's route, its project files, and the stages themselves
        fileProblem = StagesProblem(self.template, molecule, MoleculeElements(molecule.coordinateList))
        # ...and the next job's, named as its step (its project-file problem is a warning: it is written when the opt
        # ends, so it can still be fixed)
        self.spProblem = ""
        if self.then is not None:
            thenErrors, self.spProblem = self.then.Check(molecule, self.name)
            errors += [f"Then SP: {error}" for error in thenErrors]
        if self.spProblem:
            warnings.append("Scheduled SP Problem")
        return errors, warnings, fileProblem

    def InputText(self, rows: int | None = None) -> str:
        """The input as genFile would write it (the builder's preview)."""
        memory = self.memory if self.memory is not None else self.DefaultMemory()
        return JobInputText(self.molecule, self.checkpoint or self.chained, self.NboApplies(), self.cpus, memory,
                            extraInput=self.ExtraInput(rows))

    def NboApplies(self) -> bool:
        """The $NBO keylist is Gaussian's (JobInputText writes none for ORCA)."""
        return self.nbo and self.extension == Defaults.gaussianExtension

    def Slurm(self) -> dict[str, str | None]:
        return SlurmOverrides(self.wallTime, self.partition, self.cluster)

    def StageTexts(self, stageInputs: dict[int, str] | None = None) -> list[str]:
        """The input's stages as generated (SplitStages), from these ModRed lines (default: the job's own)."""
        saved = self.stageInputs
        if stageInputs is not None:
            self.stageInputs = stageInputs
        try:
            return SplitStages(self.InputText(), self.extension)
        finally:
            self.stageInputs = saved

    def ReadStage(self, number: int, typed: str) -> str | None:
        """What a stage typed by hand means: its ModRed lines when it differs from the generated stage only there ('' for
        none; the stage keeps following the settings), or None when it was changed elsewhere (an edited stage)."""
        stages = self.StageTexts({**self.stageInputs, number: EXTRA_SENTINEL})
        if number > len(stages):
            return None
        return ExtraInputOf(stages[number - 1], typed)

    @property
    def path(self) -> Path:
        return Path.cwd() / fileCreation(self.name, self.extension)

    def WriteChain(self, path: Path) -> None:
        """The chain file beside the saved input (nevatouch.ChainPath), whose next step the job's hook submits."""
        if self.then is not None:
            WriteChain(ChainPath(path.resolve()), [self.then.ChainStep()])

    def Draft(self, path: Path) -> IntentDraft:
        """Run as Written for the saved input, with the Neva-Touch Scheduler's step: its chain file (WriteChain) names
        the next job, with its method, resources and SLURM settings."""
        draft = IntentDraft(action=Action.RUN, files=[Path(path.name)])
        draft.wallTime, draft.partition, draft.cluster = self.Slurm().values()
        if self.then is not None:
            draft.nevaTouch = NTS_SP
            draft.chain = str(ChainPath(path.resolve()))
        return draft
