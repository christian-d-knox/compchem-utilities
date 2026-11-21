"""
Intent classes describe what a user wants CompUtils to do.

The flow is:
    CLI argparse / TUI screens
        -> populate an IntentDraft
        -> validate it
        -> .Finalize() to a concrete Intent subclass
        -> dispatch.Dispatch(intent) executes it

This module declares the types.
"""
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Optional

from .actions import Action, CubeOption, FILE_ACTIONS, JOB_ACTIONS, NTS_STEPS

# CLI flags indicate a specific task. The parser populates an IntentDraft field-by-field as it reads each flag —
# including setting draft.action to one of the Action enum values. After all fields are set, Validate() checks that the
# draft has everything the chosen Action requires; if so, Finalize() constructs the appropriate Intent subclass with only
# the relevant fields populated. Dispatch(intent) then runs the per-action handler.
# Each Intent subclass is a @dataclass that declares its own fields plus inherits fields from its parent class. Each
# instantiation produces an independent object with its own field values — modifying one Intent doesn't affect any other.
# The field(default_factory=list) calls ensure mutable defaults are created fresh per instance rather than shared."""


# ─── Base ─────────────────────────────────────────────────────────────
# This is the master class, shaping all subclasses as an Intent
@dataclass
class Intent:
    """Abstract base for all dispatchable intents. Never instantiated directly."""
    pass


# ─── SLURM-submitting intents ──────────────────────────────────────────
# Subclass Master for all batch-file operations (SLURM, etc.)
@dataclass
class JobIntent(Intent):
    """Base for any intent that submits one or more SLURM jobs.

    The modifier fields (stalk, stalkLoop, checkpoint, nbo7, indexOverride)
    correspond to today's -st, the stalk-loop sub-prompt, -ch, -nbo, -ovr.
    The Neva-Touch Scheduler's: nevaTouch is the step each submitted job's hook takes once it ends (NTS_SP, NTS_DONE;
    '' off), nextCpus/nextMemoryRatio the resources that step's job gets (None: the config's), and cpus/memoryRatio
    the resources of the inputs generated here (None: the config's; the scheduled single point's come from its hook).
    reRuns/firstStage: a Neva-Touch Scheduler re-run's count, so far, of re-runs of the stage it starts at, and that
    stage's number in the original job (an opt that ran out of steps in stage 2 of 3 is re-run as stages 2-3).
    wallTime/partition/cluster: the jobs' own SLURM settings (--wall-time/--partition/--cluster, a builder's rows;
    None: the config's), carried in a Neva-Touch Scheduler hook for a re-run. chain: the job's chain file
    (nevatouch.ChainPath), whose next step the hook submits (None: Benchmark Methods' first entry, nextCpus/...).
    thenSteps: a batch's next job (nevatouch.ChainStep records, e.g. -re --then-sp, the Re-Run screen's Then: SP):
    runJob writes each job that optimizes its own chain file and hook, and gives the others none.
    """
    files:           list[Path]
    stalk:           bool            = False
    stalkLoop:       bool            = False
    checkpoint:      bool            = False
    nbo7:            bool            = False
    indexOverride:   int             = 0
    nevaTouch:       str             = ""
    nextCpus:        Optional[int]   = None
    nextMemoryRatio: Optional[float] = None
    cpus:            Optional[int]   = None
    memoryRatio:     Optional[float] = None
    reRuns:          int             = 0
    firstStage:      int             = 1
    wallTime:        Optional[str]   = None
    partition:       Optional[str]   = None
    cluster:         Optional[str]   = None
    chain:           Optional[str]   = None
    thenSteps:       list            = field(default_factory=list)


@dataclass
class ThenSP:
    """The Neva-Touch Scheduler's next job as asked for on the command line (--then-sp and the --sp-* flags), resolved
    to a chain step when dispatched (optimize.SinglePointStep). method: a Benchmark Methods entry by name (None: the
    first) or route: one of your own; partition/cluster None follow the job's own."""
    method:     Optional[str]   = None
    route:      Optional[str]   = None
    cpus:       Optional[int]   = None
    memory:     Optional[float] = None
    wallTime:   Optional[str]   = None
    partition:  Optional[str]   = None
    cluster:    Optional[str]   = None
    nbo:        bool            = False
    checkpoint: bool            = False


# Subclass of JobIntent, specific for runJob() by Action()
@dataclass
class RunIntent(JobIntent):
    """`cu -r <pattern>` — submit existing input files as-written."""
    pass


@dataclass
class SinglePointIntent(JobIntent):
    """`cu -sp <pattern>` — generate single-point input, then submit. route: a method entry as written (`--route`, a
    Neva-Touch Scheduler chain step's); None: Benchmark Methods' entry indexOverride (-ovr, default 0)."""
    route: Optional[str] = None


@dataclass
class BenchmarkIntent(JobIntent):
    """`cu -b <pattern>` — generate the full benchmark suite, then submit each."""
    pass

@dataclass
class ReRunIntent(JobIntent):
    """`cu -re <pattern>` — re-run a job from its last geometry, named as a re-run (8a_re), then submit.

    route None: the route it ran (ReRunTemplate, method detection automatic); else a corrected route (--route, or
    --method's Optimization Method, resolved by Finalize). thenSP: --then-sp's next job for each re-run that optimizes
    (None: none). A CREST output's re-run (crest.py) takes method as a Conformer Search Methods name, route as its
    options, and constrain/forceConstant as its constraints (None: the ones it ran with)."""
    route:         Optional[str]    = None
    thenSP:        Optional[ThenSP] = None
    method:        Optional[str]    = None
    constrain:     Optional[str]    = None
    forceConstant: Optional[float]  = None


@dataclass
class CubeIntent(JobIntent):
    """`cu -cu <pattern>` — generate cube files via cubegen.

    orbitalRange is required iff CubeOption.RANGE is in cubeOptions.
    """
    cubeOptions:  list[CubeOption] = field(default_factory=list)
    orbitalRange: Optional[str]    = None


# ─── Non-SLURM intents ─────────────────────────────────────────────────

@dataclass
class FormCheckIntent(Intent):
    """`cu -form <pattern>` — run formchk directly (no SLURM submission)."""
    files: list[Path]


@dataclass
class ExcelIntent(Intent):
    """`cu -ex <file>` — convert a Goodvibes_output.dat to xlsx."""
    inputFile: Path


@dataclass
class GoodVibesIntent(Intent):
    """`cu -gv [FILE ...]` — run goodvibes on the given outputs (default: every output in the CWD), then convert to xlsx.

    analysis.GoodVibesArguments turns the fields into goodvibes flags. A check pass runs first (analysis.CheckArguments:
    --check, no corrections); allowDifferences waives its categories (analysis.CHECK_CATEGORIES; ["all"]: no check pass).
    """
    files:              list[Path]
    headGordonEnthalpy: bool            = False  # -q
    truhlarEntropy:     bool            = False  # --qs truhlar (Grimme otherwise)
    allowDifferences:   list[str]       = field(default_factory=list)   # --gv-allow
    freqCutoff:         Optional[float] = None
    tempCorrection:     Optional[float] = None   # K
    concCorrection:     Optional[float] = None   # mol/L
    vibeScale:          Optional[float] = 1.0    # None: GoodVibes picks one from the level of theory
    singlePointPattern: Optional[str]   = None   # e.g. "SP"
    extraKeys:          Optional[str]   = None   # raw passthrough


@dataclass
class FirstTimeSetupIntent(Intent):
    """`cu -first` — re-run the HPC + notification setup wizard."""
    pass


@dataclass
class UpdateIntent(Intent):
    """`cu --update` — re-install CompUtils from GitHub."""
    branch: Optional[str] = None


@dataclass
class PluginUpdateIntent(Intent):
    """`cu --update-plugin` — fast-forward the lab's agent plugin's git clone on this computer (plugin.py). Each None:
    the lab profile's value (agentPlugin*)."""
    folder: Optional[str] = None
    updater: Optional[str] = None
    repo: Optional[str] = None
    branch: Optional[str] = None


@dataclass
class EnvironmentIntent(Intent):
    """`cu -env [refresh|rebuild|install] [VARIANT]` — the environments' status, or a job on one (environment.py).
    job '' shows the status; variant '' is the one CompUtils runs from."""
    job:     str = ""
    variant: str = ""


@dataclass
class InitProjectIntent(Intent):
    """`cu -init` — mark the CWD as a project root."""
    pass


@dataclass
class ProfileIntent(Intent):
    """`cu -profile [FILE]` — apply a lab profile (None: re-apply the last one)."""
    profileFile: Optional[Path] = None


@dataclass
class RefreshIntent(Intent):
    """`cu -refresh [project]` — rewrite the config files in the current layout (project: the nearest project.toml too)."""
    project: bool = False


@dataclass
class AttributionsIntent(Intent):
    """`cu -attributions` — credit for everything CompUtils relies on (attributions.py)."""
    pass


@dataclass
class NevaTouchIntent(Intent):
    """`cu --neva-touch STEP FILE JOBID` — a job's Neva-Touch Scheduler hook (nevatouch.RunStep). Written into job
    scripts by runJob, and run by a stalker for a step the hook didn't take; never typed."""
    step:        str
    output:      Path
    jobID:       str
    cpus:        Optional[int]   = None   # the next job's resources (None: the config's)
    memoryRatio: Optional[float] = None
    reRuns:      int             = 0      # the job's own (JobIntent's), carried in its hook
    firstStage:  int             = 1
    wallTime:    Optional[str]   = None   # the job's own SLURM settings, which a re-run repeats
    partition:   Optional[str]   = None
    cluster:     Optional[str]   = None
    chain:       Optional[str]   = None   # the job's chain file: its next step is what this step submits


@dataclass
class StalkIntent(Intent):
    """`cu -st` on its own — stalk every tracked job, whichever directory it was submitted from. With --json, one
    snapshot (stalk.Snapshot). dismiss: job IDs or names to stop tracking ('ended': every ended one), and nothing else.
    wait: job IDs or names to wait for ([]: every running one; stalk.Wait), None when not waiting."""
    loop:    bool                = False
    dismiss: list[str]           = field(default_factory=list)
    wait:    Optional[list[str]] = None


@dataclass
class OptimizationIntent(Intent):
    """`cu -opt FILE` — the Optimization builder without the TUI (optimize.py): one molecule from an .xyz or a prior
    input/output, a named Optimization Method (or 'From Source', or a full --route), written and submitted as the
    builder submits one. None means the builder's starting value (charge/multiplicity: the prior job's, else asked;
    spin: spinstates.txt, else the prior job's classification, else CSS; thenSP: qol.toml's autoNTS). smiles: the
    molecule from a SMILES instead (file None; smiles.py: an RDKit conformer, rdkitConformer by energy, its charge and
    multiplicity the SMILES' unless given). modred: stage
    number -> its ModRed lines (a file, or lines separated by ';'). The scheduled SP (a Neva-Touch Scheduler chain
    step): spMethod (a Benchmark Methods name; None: the first) or spRoute, its resources (None: the config's, its
    memory as its method needs), and its SLURM settings (partition/cluster None: the opt's)."""
    file:         Optional[Path]
    method:       Optional[str]   = None
    route:        Optional[str]   = None
    charge:       Optional[int]   = None
    multiplicity: Optional[int]   = None
    spin:         Optional[str]   = None     # 'css' / 'oss'
    name:         Optional[str]   = None
    cpus:         Optional[int]   = None
    memoryRatio:  Optional[float] = None
    checkpoint:   bool            = False
    nbo7:         bool            = False
    wallTime:     Optional[str]   = None     # the job's SLURM settings (None: the config's)
    partition:    Optional[str]   = None
    cluster:      Optional[str]   = None
    modred:       dict[int, str]  = field(default_factory=dict)
    thenSP:       Optional[bool]  = None
    spCpus:       Optional[int]   = None
    spMemory:     Optional[float] = None
    smiles:          Optional[str] = None
    rdkitConformers: Optional[int] = None
    rdkitConformer:  int           = 1
    spMethod:     Optional[str]   = None
    spRoute:      Optional[str]   = None
    spWallTime:   Optional[str]   = None
    spPartition:  Optional[str]   = None
    spCluster:    Optional[str]   = None
    spNbo:        bool            = False
    spCheckpoint: bool            = False
    stalk:        bool            = False
    stalkLoop:    bool            = False


@dataclass
class SIIntent(Intent):
    """`cu -si` — the SI Scraper without the TUI (scrape.py): the folder's species into SI_coordinates.txt. None means
    the screen's starting value (suffix: detected; species: list.txt, else every one that can be written); the method
    labels are asked when not given. Stops (exit 6) when qh-G needs a GoodVibes run first."""
    optLabel: Optional[str]       = None
    spLabel:  Optional[str]       = None
    phase:    str                 = "in solution"
    suffix:   Optional[str]       = None
    free:     str                 = "qh"      # 'qh' (GoodVibes' qh-G) or 'g'
    output:   Optional[str]       = None
    species:  Optional[list[str]] = None


@dataclass
class AgentIntent(Intent):
    """`cu -agent [TOPIC]` — the agent skill (SKILL.md), or one topic of it (agentskill.py)."""
    topic:   str = ""
    sshHost: str = ""
    local:   object = None              # agentskill.LocalInstall: the agent's own computer (None: undecided)


@dataclass
class XyzIntent(Intent):
    """`cu -xyz FILE...` — each file's last geometry (fileops.ReadMolecule) written as NAME.xyz (fileops.WriteXyz).
    With smiles (and name, no files): that SMILES' RDKit conformer (smiles.py; rdkitConformer: by energy, 1 the
    lowest; rdkitConformers None: qol.toml's) as name.xyz. replace: the person already chose to replace an existing
    name.xyz (the TUI's pop-up shows it), so replace-xyz isn't asked."""
    files:           list[Path]
    smiles:          Optional[str] = None
    name:            Optional[str] = None
    rdkitConformers: Optional[int] = None
    rdkitConformer:  int           = 1
    replace:         bool          = False


@dataclass
class CrestIntent(Intent):
    """`cu -crest FILE...` — a CREST conformer search of each molecule (crest.py), as the TUI's Conformer Search builder
    submits one: from an .xyz, a prior input or output (its last geometry), or a SMILES (smiles: an RDKit conformer,
    files empty). method: a Conformer Search Methods entry by name (asked when neither it nor route is given); route:
    CREST's options typed in full. charge/multiplicity None: the source's, else asked. name: the species (the job is
    NAME_CREST; default the file's). constrain: constraint lines ('B 12 15;B 15 21'); forceConstant None: 1.0.
    cpus/memoryRatio None: the config's; wallTime/partition/cluster None: the config's. job: a crest.CrestJob the TUI's
    Conformer Search screen built from its settings (any source, pasted rows too): checked and submitted as it is."""
    files:           list[Path]
    smiles:          Optional[str]   = None
    rdkitConformers: Optional[int]   = None
    rdkitConformer:  int             = 1
    method:          Optional[str]   = None
    route:           Optional[str]   = None
    charge:          Optional[int]   = None
    multiplicity:    Optional[int]   = None
    name:            Optional[str]   = None
    constrain:       Optional[str]   = None
    forceConstant:   Optional[float] = None
    cpus:            Optional[int]   = None
    memoryRatio:     Optional[float] = None
    wallTime:        Optional[str]   = None
    partition:       Optional[str]   = None
    cluster:         Optional[str]   = None
    stalk:           bool            = False
    stalkLoop:       bool            = False
    job:             object          = None


@dataclass
class InfoIntent(Intent):
    """`cu -info FILE...` — what each file says (info.FileFacts: one map each), read-only."""
    files: list[Path]


@dataclass
class MethodsIntent(Intent):
    """`cu -methods` — the Optimization and Benchmark Methods in force here (project, else global), read-only."""
    pass


# The Intent each Action finalizes to. Excel, Update and Environment aren't listed: their fields are named differently
# on the draft
_INTENT_TYPES = {
    Action.RUN: RunIntent, Action.SINGLE_POINT: SinglePointIntent, Action.BENCHMARK: BenchmarkIntent,
    Action.RERUN: ReRunIntent, Action.CUBE: CubeIntent, Action.FORM_CHECK: FormCheckIntent,
    Action.GOODVIBES: GoodVibesIntent, Action.FIRST_TIME_SETUP: FirstTimeSetupIntent,
    Action.INIT_PROJECT: InitProjectIntent, Action.PROFILE: ProfileIntent, Action.ATTRIBUTIONS: AttributionsIntent,
    Action.INFO: InfoIntent, Action.METHODS: MethodsIntent, Action.XYZ: XyzIntent,
}
# SI_SCRAPER finalizes in Finalize (its fields are named si* on the draft)


# ─── Mutable draft (TUI assembles this progressively) ──────────────────
# Draft containing ALL options. This is what a parser will add arguments to, before Validating and Finalizing
# to a specific Intent type as shown above
@dataclass
class IntentDraft:
    """
    Accumulator used by the TUI as the user answers prompts.
    Convert to a typed Intent via Finalize() after Validate() returns no errors.

    The CLI also uses this — argparse populates the draft, validation runs,
    then Finalize() produces the concrete Intent. Same flow as the TUI;
    just a different way of filling the fields.
    """
    action: Optional[Action] = None

    # File-bearing actions (RUN, SP, BENCH, CUBE, RERUN, FORM_CHECK, GOODVIBES)
    files: list[Path] = field(default_factory=list)

    # SLURM modifiers (only meaningful when action ∈ JobIntent subclasses)
    stalk:         bool = False
    stalkLoop:     bool = False
    checkpoint:    bool = False
    nbo7:          bool = False
    indexOverride: int  = 0

    # The Neva-Touch Scheduler (JobIntent's fields; NEVA_TOUCH: nevaTouchStep, files[0], nevaTouchJob, cpus/memoryRatio)
    nevaTouch:       str             = ""
    nextCpus:        Optional[int]   = None
    nextMemoryRatio: Optional[float] = None
    cpus:            Optional[int]   = None
    memoryRatio:     Optional[float] = None
    nevaTouchStep:   str             = ""
    thenSteps:       list            = field(default_factory=list)
    nevaTouchJob:    str             = ""
    reRuns:          int             = 0
    firstStage:      int             = 1

    # A job's own SLURM settings (JobIntent's; None: the config's), and its Neva-Touch Scheduler chain file
    wallTime:        Optional[str]   = None
    partition:       Optional[str]   = None
    cluster:         Optional[str]   = None
    chain:           Optional[str]   = None
    # SP: a method entry as written instead of Benchmark Methods' (--route with -sp); -re: a corrected route
    route:           Optional[str]   = None
    # -re: an Optimization Method by name for the corrected route (--method with -re)
    reRunMethod:     Optional[str]   = None

    # CUBE
    cubeOptions:  list[CubeOption] = field(default_factory=list)
    orbitalRange: Optional[str]    = None

    # EXCEL
    excelInputFile: Optional[Path] = None

    # GOODVIBES
    headGordonEnthalpy: bool            = False
    truhlarEntropy:     bool            = False
    allowDifferences:   list[str]       = field(default_factory=list)
    freqCutoff:         Optional[float] = None
    tempCorrection:     Optional[float] = None
    concCorrection:     Optional[float] = None
    vibeScale:          Optional[float] = 1.0
    singlePointPattern: Optional[str]   = None
    extraKeys:          Optional[str]   = None

    # UPDATE
    updateBranch: Optional[str] = None  # None: the branch CompUtils was installed from

    # UPDATE_PLUGIN: the plugin (None: the lab profile's agentPlugin* values)
    pluginDir: Optional[str] = None
    pluginUpdater: Optional[str] = None
    pluginRepo: Optional[str] = None
    pluginBranch: Optional[str] = None

    # ENVIRONMENT: the job ('' status) and the variant ('' the running one)
    environmentJob:     str = ""
    environmentVariant: str = ""

    # PROFILE
    profileFile: Optional[Path] = None  # None: re-apply the last one

    # REFRESH: '' (global files) or 'project' (also the nearest project.toml)
    refreshScope: str = ""

    # STALK: tracked jobs to dismiss (IDs, names, or 'ended'), or to wait for (None: not waiting; []: every running one)
    dismiss: list[str]           = field(default_factory=list)
    wait:    Optional[list[str]] = None

    # AGENT
    agentTopic:   str = ""
    agentSshHost: str = ""
    agentLocal: object = None

    # SI_SCRAPER
    siOptLabel: Optional[str]       = None
    siSpLabel:  Optional[str]       = None
    siPhase:    str                 = "in solution"
    siFree:     str                 = "qh"
    siOutput:   Optional[str]       = None
    siSpecies:  Optional[list[str]] = None

    # A SMILES instead of a file (OPTIMIZE, XYZ: smiles.py); optName names what it makes for both
    smiles:          Optional[str]   = None
    rdkitConformers: Optional[int]   = None
    rdkitConformer:  Optional[int]   = None

    # CREST (and -re on a CREST output): its constraint lines and force constant. Its method, route, charge,
    # multiplicity and name are -opt's fields below (the same flags)
    crestConstrain:     Optional[str]   = None
    crestForceConstant: Optional[float] = None

    # OPTIMIZE (files[0] is the source; cpus/memoryRatio/checkpoint are shared with the job actions)
    optMethod:       Optional[str]   = None
    optRoute:        Optional[str]   = None
    optCharge:       Optional[int]   = None
    optMultiplicity: Optional[int]   = None
    optSpin:         Optional[str]   = None
    optName:         Optional[str]   = None
    optModRed:       dict[int, str]  = field(default_factory=dict)
    optThenSP:       Optional[bool]  = None
    optSpCpus:       Optional[int]   = None
    optSpMemory:     Optional[float] = None
    optSpMethod:     Optional[str]   = None
    optSpRoute:      Optional[str]   = None
    optSpWallTime:   Optional[str]   = None
    optSpPartition:  Optional[str]   = None
    optSpCluster:    Optional[str]   = None
    optSpNbo:        bool            = False
    optSpCheckpoint: bool            = False

    # This validates the fields for an Action() wants from its Intent()
    def Validate(self) -> list[str]:
        """Return human-readable errors. Empty list means valid."""
        errors: list[str] = []
        if self.action is None:
            return ["No action selected."]

        # -opt says it itself (a file, or --smiles)
        if self.action in FILE_ACTIONS and not self.files and self.smiles is None and self.action != Action.OPTIMIZE:
            errors.append("No matching files for the given pattern.")

        if self.smiles is not None:
            if self.action not in (Action.OPTIMIZE, Action.XYZ, Action.CREST):
                errors.append("--smiles goes with -opt, -xyz or -crest.")
            elif self.files:
                errors.append(f"-{self.action.value} takes files or --smiles, not both.")
            elif not self.smiles.strip():
                errors.append("--smiles is empty.")
            elif not self.optName:
                errors.append("--smiles needs --name: a SMILES names no file (e.g. --name 'ethanol').")
        elif self.rdkitConformers is not None or self.rdkitConformer is not None:
            errors.append("--rdkit-conformers and --rdkit-conformer go with --smiles.")
        if self.action == Action.XYZ and self.optName is not None and self.smiles is None:
            errors.append("--name goes with -opt, or -xyz --smiles.")
        for value, flag in ((self.rdkitConformers, "--rdkit-conformers"), (self.rdkitConformer, "--rdkit-conformer")):
            if value is not None and value < 1:
                errors.append(f"{flag} must be 1 or more.")

        if self.action == Action.CUBE:
            if not self.cubeOptions:
                errors.append("Select at least one cube option.")
            if CubeOption.RANGE in self.cubeOptions and not self.orbitalRange:
                errors.append("Orbital range required when 'Range' is selected.")

        if self.action == Action.EXCEL and self.excelInputFile is None:
            errors.append("Input file required for Excel conversion.")

        if self.action == Action.PROFILE and self.profileFile is not None and not self.profileFile.is_file():
            errors.append(f"No lab profile file at {self.profileFile}.")

        if self.stalk and self.action not in (*JOB_ACTIONS, Action.STALK, Action.OPTIMIZE):
            errors.append("-st follows submitted jobs: use it with -r, -sp, -b, -cu, -re, -opt or -crest, or on its own "
                          "to stalk every tracked job.")

        slurm = {"--wall-time": self.wallTime, "--partition": self.partition, "--cluster": self.cluster}
        given = [flag for flag, value in slurm.items() if value is not None]
        if given and self.action not in (*JOB_ACTIONS, Action.OPTIMIZE, Action.NEVA_TOUCH):
            errors.append(f"{', '.join(given)} {'go' if len(given) > 1 else 'goes'} with a job command (-r, -sp, -b, "
                          "-cu, -re, -opt or -crest).")
        if self.wallTime is not None and not (self.wallTime.isdigit() and int(self.wallTime) > 0):
            errors.append(f"--wall-time takes whole hours above 0 (got '{self.wallTime}').")
        for flag in ("--partition", "--cluster"):
            if slurm[flag] is not None and (not slurm[flag].strip() or any(c.isspace() for c in slurm[flag])):
                errors.append(f"{flag} takes one name, with no spaces (got '{slurm[flag]}').")

        if self.action == Action.NEVA_TOUCH:
            if self.nevaTouchStep not in NTS_STEPS:
                errors.append(f"Unknown Neva-Touch Scheduler step '{self.nevaTouchStep}' (expected {', '.join(NTS_STEPS)}).")
            if len(self.files) != 1:
                errors.append("The Neva-Touch Scheduler takes one output file.")

        if self.nevaTouch and self.nevaTouch not in NTS_STEPS:
            errors.append(f"Unknown Neva-Touch Scheduler step '{self.nevaTouch}'.")

        if self.action != Action.UPDATE_PLUGIN:
            for flag, value in (("--plugin-dir", self.pluginDir), ("--plugin-updater", self.pluginUpdater),
                                ("--plugin-repo", self.pluginRepo), ("--plugin-branch", self.pluginBranch)):
                if value is not None:
                    errors.append(f"{flag} goes with --update-plugin.")

        if self.action == Action.ENVIRONMENT and self.environmentJob not in ("", "refresh", "rebuild", "install"):
            errors.append(f"-env takes refresh, rebuild or install, then an environment (got '{self.environmentJob}').")

        generates = (Action.SINGLE_POINT, Action.BENCHMARK, Action.RERUN, Action.OPTIMIZE, Action.NEVA_TOUCH,
                     Action.CREST)
        if (self.cpus is not None or self.memoryRatio is not None) and self.action not in generates:
            errors.append("--cpus and --memory go with -sp, -b, -re, -opt or -crest (an input as written has its own).")
        if self.action in (Action.SINGLE_POINT, Action.BENCHMARK, Action.RERUN, Action.CREST):
            if self.cpus is not None and self.cpus < 1:
                errors.append("--cpus must be 1 or more.")
            if self.memoryRatio is not None and self.memoryRatio <= 0:
                errors.append("--memory must be above 0.")
        if self.action == Action.RERUN:
            errors += self.ThenSPProblems()
            if self.reRunMethod and self.route:
                errors.append("-re takes --method or --route, not both.")
            elif self.reRunMethod and self.ReRunMethodRoute() is None and not self.CrestMethodKnown():
                from .jobs import OptMethods
                from .crest import Methods
                names = ", ".join(OptMethods()) or "none (Config › Job Keylists)"
                errors.append(f"--method: no Optimization Method '{self.reRunMethod}' here (cu -methods lists them: "
                              f"{names}), and no Conformer Search Method of that name (for a CREST output: "
                              f"{', '.join(Methods()) or 'none'}).")

        if self.action in (Action.CREST, Action.RERUN):
            if self.crestForceConstant is not None and self.crestForceConstant <= 0:
                errors.append("--force-constant must be above 0.")
        elif self.crestConstrain is not None or self.crestForceConstant is not None:
            errors.append("--constrain and --force-constant go with -crest (or -re on a CREST output).")
        if self.action == Action.CREST:
            if self.optMethod and self.optRoute:
                errors.append("-crest takes --method or --route, not both.")
            if self.optMultiplicity is not None and self.optMultiplicity < 1:
                errors.append("--mult must be 1 or more.")
            if self.optName and len(self.files) > 1:
                errors.append("--name names one search: give -crest one file with it (or none, with --smiles).")
            if self.optMethod:
                from .crest import Methods
                if self.optMethod.lower() not in {name.lower() for name in Methods()}:
                    errors.append(f"--method: no Conformer Search Method '{self.optMethod}' here (cu -methods lists "
                                  f"them: {', '.join(Methods()) or 'none'}).")

        if self.action == Action.OPTIMIZE:
            if len(self.files) != 1 and self.smiles is None:
                errors.append("-opt takes one file, or --smiles (one molecule per call: run it once per molecule).")
            if self.optMethod and self.optRoute:
                errors.append("-opt takes --method or --route, not both.")
            if self.optSpin is not None and self.optSpin not in ("css", "oss"):
                errors.append(f"--spin takes css or oss (got '{self.optSpin}').")
            if self.optMultiplicity is not None and self.optMultiplicity < 1:
                errors.append("--mult must be 1 or more.")
            for value, flag in ((self.cpus, "--cpus"), (self.optSpCpus, "--sp-cpus")):
                if value is not None and value < 1:
                    errors.append(f"{flag} must be 1 or more.")
            for value, flag in ((self.memoryRatio, "--memory"), (self.optSpMemory, "--sp-memory")):
                if value is not None and value <= 0:
                    errors.append(f"{flag} must be above 0.")
            if self.optSpMethod and self.optSpRoute:
                errors.append("-opt takes --sp-method or --sp-route, not both.")
            if self.optSpWallTime is not None and not (self.optSpWallTime.isdigit() and int(self.optSpWallTime) > 0):
                errors.append(f"--sp-wall-time takes whole hours above 0 (got '{self.optSpWallTime}').")
            for value, flag in ((self.optSpPartition, "--sp-partition"), (self.optSpCluster, "--sp-cluster")):
                if value is not None and (not value.strip() or any(c.isspace() for c in value)):
                    errors.append(f"{flag} takes one name, with no spaces (got '{value}').")
        if self.route is not None and self.action not in (Action.SINGLE_POINT, Action.RERUN):
            errors.append("--route goes with -opt (its method), -sp (instead of Benchmark Methods') or -re (a "
                          "corrected route).")

        if self.action == Action.SI_SCRAPER and self.siFree not in ("qh", "g"):
            errors.append(f"--free takes qh or g (got '{self.siFree}').")

        if self.dismiss and self.action != Action.STALK:
            errors.append("--dismiss goes with -st on its own (e.g. 'cu -st --dismiss 123456').")
        if self.wait is not None and self.action != Action.STALK:
            errors.append("--wait goes with -st on its own (e.g. 'cu -st --wait 123456 --json').")
        if self.wait is not None and self.dismiss:
            errors.append("--wait and --dismiss are separate runs: dismiss first, then wait.")

        if self.action == Action.REFRESH and self.refreshScope not in ("", "project"):
            errors.append(f"-refresh takes no argument, or 'project' (got '{self.refreshScope}').")

        return errors

    def CrestMethodKnown(self) -> bool:
        """Whether -re --method names a Conformer Search Method (for a CREST output's re-run)."""
        from .crest import Methods
        return bool(self.reRunMethod) and self.reRunMethod.lower() in {name.lower() for name in Methods()}

    def ReRunMethodRoute(self) -> str | None:
        """-re --method's route: the Optimization Method's as written, '' for From Source (the route it ran), None for
        a name that isn't one."""
        from .jobs import OptMethods
        from .optimize import FROM_SOURCE_NAME
        if self.reRunMethod.strip().lower() == FROM_SOURCE_NAME.lower():
            return ""
        return OptMethods().get(self.reRunMethod)

    def ThenSPProblems(self) -> list[str]:
        """The --sp-* flags' checks (-opt's and -re's)."""
        errors = []
        if self.optSpMethod and self.optSpRoute:
            errors.append("--sp-method or --sp-route, not both.")
        if self.optSpCpus is not None and self.optSpCpus < 1:
            errors.append("--sp-cpus must be 1 or more.")
        if self.optSpMemory is not None and self.optSpMemory <= 0:
            errors.append("--sp-memory must be above 0.")
        if self.optSpWallTime is not None and not (self.optSpWallTime.isdigit() and int(self.optSpWallTime) > 0):
            errors.append(f"--sp-wall-time takes whole hours above 0 (got '{self.optSpWallTime}').")
        for value, flag in ((self.optSpPartition, "--sp-partition"), (self.optSpCluster, "--sp-cluster")):
            if value is not None and (not value.strip() or any(c.isspace() for c in value)):
                errors.append(f"{flag} takes one name, with no spaces (got '{value}').")
        return errors

    def ThenSP(self) -> "ThenSP | None":
        """--then-sp's next job (on with any --sp-* flag unless --no-then-sp), None when off."""
        given = any(value is not None for value in (self.optSpMethod, self.optSpRoute, self.optSpCpus,
                                                    self.optSpMemory, self.optSpWallTime, self.optSpPartition,
                                                    self.optSpCluster)) or self.optSpNbo or self.optSpCheckpoint
        on = self.optThenSP if self.optThenSP is not None else given
        if not on:
            return None
        return ThenSP(self.optSpMethod, self.optSpRoute, self.optSpCpus, self.optSpMemory, self.optSpWallTime,
                      self.optSpPartition, self.optSpCluster, self.optSpNbo, self.optSpCheckpoint)

    def Finalize(self) -> Intent:
        """Caller MUST have validated first.

        Returns a concrete Intent subclass corresponding to self.action,
        with only the fields relevant to that action populated: each one is
        copied from the draft field of the same name.
        """
        match self.action:
            case Action.EXCEL:
                return ExcelIntent(inputFile=self.excelInputFile)
            case Action.UPDATE:
                return UpdateIntent(branch=self.updateBranch)
            case Action.UPDATE_PLUGIN:
                return PluginUpdateIntent(folder=self.pluginDir, updater=self.pluginUpdater, repo=self.pluginRepo,
                                          branch=self.pluginBranch)
            case Action.ENVIRONMENT:
                return EnvironmentIntent(job=self.environmentJob, variant=self.environmentVariant)
            case Action.REFRESH:
                return RefreshIntent(project=self.refreshScope == "project")
            case Action.STALK:
                return StalkIntent(loop=self.stalkLoop, dismiss=list(self.dismiss),
                                   wait=None if self.wait is None else list(self.wait))
            case Action.AGENT:
                return AgentIntent(topic=self.agentTopic, sshHost=self.agentSshHost, local=self.agentLocal)
            case Action.SI_SCRAPER:
                return SIIntent(optLabel=self.siOptLabel, spLabel=self.siSpLabel, phase=self.siPhase,
                                suffix=self.singlePointPattern, free=self.siFree, output=self.siOutput,
                                species=self.siSpecies)
            case Action.OPTIMIZE:
                return OptimizationIntent(
                    file=self.files[0] if self.files else None, method=self.optMethod, route=self.optRoute, charge=self.optCharge,
                    multiplicity=self.optMultiplicity, spin=self.optSpin, name=self.optName, cpus=self.cpus,
                    memoryRatio=self.memoryRatio, checkpoint=self.checkpoint, nbo7=self.nbo7, wallTime=self.wallTime,
                    partition=self.partition, cluster=self.cluster, modred=dict(self.optModRed),
                    thenSP=self.optThenSP, spCpus=self.optSpCpus, spMemory=self.optSpMemory, spMethod=self.optSpMethod,
                    spRoute=self.optSpRoute, spWallTime=self.optSpWallTime, spPartition=self.optSpPartition,
                    spCluster=self.optSpCluster, spNbo=self.optSpNbo, spCheckpoint=self.optSpCheckpoint,
                    smiles=self.smiles, rdkitConformers=self.rdkitConformers, rdkitConformer=self.rdkitConformer or 1,
                    stalk=self.stalk, stalkLoop=self.stalkLoop)
            case Action.RERUN:
                intent = ReRunIntent(**{entry.name: getattr(self, entry.name) for entry in fields(ReRunIntent)
                                        if entry.name not in ("thenSP", "method", "constrain", "forceConstant")})
                if self.reRunMethod:
                    intent.route = self.ReRunMethodRoute() or None
                    intent.method = self.reRunMethod
                intent.thenSP = self.ThenSP()
                intent.constrain, intent.forceConstant = self.crestConstrain, self.crestForceConstant
                return intent
            case Action.CREST:
                return CrestIntent(
                    files=list(self.files), smiles=self.smiles, rdkitConformers=self.rdkitConformers,
                    rdkitConformer=self.rdkitConformer or 1, method=self.optMethod, route=self.optRoute,
                    charge=self.optCharge, multiplicity=self.optMultiplicity, name=self.optName,
                    constrain=self.crestConstrain, forceConstant=self.crestForceConstant, cpus=self.cpus,
                    memoryRatio=self.memoryRatio, wallTime=self.wallTime, partition=self.partition,
                    cluster=self.cluster, stalk=self.stalk, stalkLoop=self.stalkLoop)
            case Action.XYZ:
                return XyzIntent(files=list(self.files), smiles=self.smiles, name=self.optName,
                                 rdkitConformers=self.rdkitConformers, rdkitConformer=self.rdkitConformer or 1)
            case Action.NEVA_TOUCH:
                return NevaTouchIntent(step=self.nevaTouchStep, output=self.files[0], jobID=self.nevaTouchJob,
                                       cpus=self.cpus, memoryRatio=self.memoryRatio, reRuns=self.reRuns,
                                       firstStage=self.firstStage, wallTime=self.wallTime, partition=self.partition,
                                       cluster=self.cluster, chain=self.chain)
        intentType = _INTENT_TYPES.get(self.action)
        if intentType is None:
            raise ValueError(f"Unknown action: {self.action}")
        return intentType(**{entry.name: getattr(self, entry.name) for entry in fields(intentType)})

# ─── Smoke tests (run with: python -m computils.intent) ────────────────

if __name__ == "__main__":
    from pathlib import Path

    print("Smoke tests for intent.py")
    print("=" * 50)

    # Test 1: Valid run intent
    draft = IntentDraft()
    draft.action = Action.RUN
    draft.files = [Path("test.gjf")]
    draft.stalk = True
    errors = draft.Validate()
    assert errors == [], f"Test 1 failed: {errors}"
    intent = draft.Finalize()
    assert isinstance(intent, RunIntent), f"Test 1 type: {type(intent)}"
    assert intent.stalk is True
    print("Test 1 (valid RUN intent): PASS")

    # Test 2: Run intent with no files fails validation
    draft = IntentDraft()
    draft.action = Action.RUN
    draft.files = []
    errors = draft.Validate()
    assert errors, "Test 2 should have produced errors"
    assert any("No matching files" in e for e in errors), \
        f"Test 2 wrong error: {errors}"
    print("Test 2 (RUN with no files): PASS")

    # Test 3: Cube intent without RANGE option doesn't need orbitalRange
    draft = IntentDraft()
    draft.action = Action.CUBE
    draft.files = [Path("test.chk")]
    draft.cubeOptions = [CubeOption.POTENTIAL, CubeOption.DENSITY]
    errors = draft.Validate()
    assert errors == [], f"Test 3 errors: {errors}"
    intent = draft.Finalize()
    assert isinstance(intent, CubeIntent)
    assert CubeOption.RANGE not in intent.cubeOptions
    print("Test 3 (CUBE without RANGE): PASS")

    # Test 4: Cube intent WITH RANGE requires orbitalRange
    draft = IntentDraft()
    draft.action = Action.CUBE
    draft.files = [Path("test.chk")]
    draft.cubeOptions = [CubeOption.RANGE]
    errors = draft.Validate()
    assert errors, "Test 4 should have produced errors"
    assert any("Orbital range" in e for e in errors), \
        f"Test 4 wrong error: {errors}"
    print("Test 4 (CUBE with RANGE, no range): PASS")

    # Test 5: ReRun intent finalizes correctly
    draft = IntentDraft()
    draft.action = Action.RERUN
    draft.files = [Path("failed.out")]
    errors = draft.Validate()
    assert errors == [], f"Test 5 errors: {errors}"
    intent = draft.Finalize()
    assert isinstance(intent, ReRunIntent)
    print("Test 5 (RERUN intent): PASS")

    # Test 6: GoodVibes intent takes files, and needs at least one
    draft = IntentDraft()
    draft.action = Action.GOODVIBES
    draft.headGordonEnthalpy = True
    draft.freqCutoff = 100.0
    assert draft.Validate(), "Test 6 should need files"
    draft.files = [Path("ethane.out")]
    errors = draft.Validate()
    assert errors == [], f"Test 6 errors: {errors}"
    intent = draft.Finalize()
    assert isinstance(intent, GoodVibesIntent)
    assert intent.headGordonEnthalpy is True
    assert intent.freqCutoff == 100.0 and intent.files == [Path("ethane.out")]
    print("Test 6 (GOODVIBES with options): PASS")

    # Test 7: Init project intent needs no files
    draft = IntentDraft()
    draft.action = Action.INIT_PROJECT
    errors = draft.Validate()
    assert errors == [], f"Test 7 errors: {errors}"
    intent = draft.Finalize()
    assert isinstance(intent, InitProjectIntent)
    print("Test 7 (INIT_PROJECT intent): PASS")

    # Test 8: Profile intent re-applies without a file, and needs an existing file when given one
    draft = IntentDraft()
    draft.action = Action.PROFILE
    assert draft.Validate() == [], "Test 8 bare -profile should be valid"
    assert draft.Finalize() == ProfileIntent(profileFile=None)
    draft.profileFile = Path("no-such-profile.toml")
    assert any("No lab profile file" in e for e in draft.Validate()), "Test 8 should reject a missing file"
    print("Test 8 (PROFILE intent): PASS")

    # Test 9: Refresh takes nothing or 'project'
    draft = IntentDraft()
    draft.action = Action.REFRESH
    assert draft.Validate() == [] and draft.Finalize() == RefreshIntent(project=False)
    draft.refreshScope = "project"
    assert draft.Validate() == [] and draft.Finalize() == RefreshIntent(project=True)
    draft.refreshScope = "banana"
    assert any("-refresh takes" in e for e in draft.Validate()), "Test 9 should reject another argument"
    print("Test 9 (REFRESH intent): PASS")

    # Test 10: -st on its own stalks every tracked job; -st with a non-job action is rejected
    draft = IntentDraft()
    draft.action, draft.stalk, draft.stalkLoop = Action.STALK, True, True
    assert draft.Validate() == [] and draft.Finalize() == StalkIntent(loop=True)
    draft = IntentDraft()
    draft.action, draft.stalk = Action.INIT_PROJECT, True
    assert any("-st follows" in e for e in draft.Validate()), "Test 10 should reject -st with -init"
    print("Test 10 (STALK intent): PASS")

    # Test 11: Attributions needs nothing
    draft = IntentDraft()
    draft.action = Action.ATTRIBUTIONS
    assert draft.Validate() == [] and draft.Finalize() == AttributionsIntent()
    print("Test 11 (ATTRIBUTIONS intent): PASS")

    # Test 12: Neva-Touch Scheduler: a run carries its step and the next job's resources; the hook's intent needs a
    # known step and one file
    draft = IntentDraft()
    draft.action, draft.files = Action.RUN, [Path("8a.gjf")]
    draft.nevaTouch, draft.nextCpus, draft.nextMemoryRatio = "SP", 32, 2.0
    intent = draft.Finalize()
    assert draft.Validate() == [] and intent.nevaTouch == "SP" and (intent.nextCpus, intent.nextMemoryRatio) == (32, 2.0)
    draft = IntentDraft()
    draft.action, draft.files, draft.nevaTouchStep, draft.nevaTouchJob = Action.NEVA_TOUCH, [Path("8a.out")], "SP", "12"
    draft.cpus = 32
    assert draft.Validate() == []
    assert draft.Finalize() == NevaTouchIntent(step="SP", output=Path("8a.out"), jobID="12", cpus=32)
    draft.reRuns, draft.firstStage = 2, 3
    assert draft.Finalize() == NevaTouchIntent(step="SP", output=Path("8a.out"), jobID="12", cpus=32, reRuns=2,
                                               firstStage=3)
    draft.nevaTouchStep = "Freq"
    assert any("Unknown Neva-Touch" in e for e in draft.Validate()), "Test 12 should reject an unknown step"
    print("Test 12 (Neva-Touch Scheduler intents): PASS")

    print("=" * 50)
    print("All smoke tests passed.")