"""
Single execution path for all Intents.

The CLI (argparse) and the TUI both build typed Intents, which arrive
here. Dispatch() routes to the per-action handler based on intent type.
"""
import os, subprocess
from pathlib import Path

from rich.markup import escape

from .console   import console
from .defaults  import Defaults
from .catalog   import Catalog
from .molecule  import Molecule
from .intent    import (
    Intent,
    RunIntent, SinglePointIntent, BenchmarkIntent, ReRunIntent, ThenSP,
    CubeIntent, FormCheckIntent, ExcelIntent, GoodVibesIntent,
    FirstTimeSetupIntent, UpdateIntent, PluginUpdateIntent, InitProjectIntent, ProfileIntent, RefreshIntent, StalkIntent,
    AttributionsIntent, NevaTouchIntent, EnvironmentIntent, InfoIntent, MethodsIntent, OptimizationIntent, SIIntent,
    CancelIntent, ScrapeIntent,
    AgentIntent, XyzIntent, CrestIntent,
)
from .fileops   import grabPaths, formCheck, fileCreation, HasContent, MapFile, ReadMolecule, WriteXyz
from .jobs      import HeaderSends, runJob
from .          import jobs
from .workflows import genBench, genSinglePoint, genReRun, gimmeCubes
from .analysis  import (goodVibesProcessor, GoodVibesArguments, SpcPartners, GOODVIBES_OUTPUT, ALLOW_ALL,
                        CHECK_CATEGORIES, CheckArguments, ReadCheck)
from .notify    import CheckAndBroadcast
from .          import environment
from .          import agent
from .stalk     import TrackedJob, LoadTracked, jobStalking
from .spin      import ClassifySpin


def Dispatch(intent: Intent) -> list[TrackedJob]:
    """Single execution path. Routes to the per-action handler. Returns the jobs it submitted (already tracked), so the
    caller decides how to stalk them: the CLI with -st, the TUI by offering its Job Stalker."""
    # Counted per dispatch: the TUI dispatches many times in one process
    jobs.submittedJobs = 0
    match intent:
        case RunIntent():             return _DispatchRun(intent)
        case SinglePointIntent():     return _DispatchSinglePoint(intent)
        case BenchmarkIntent():       return _DispatchBenchmark(intent)
        case CubeIntent():            _DispatchCube(intent)
        case ReRunIntent():           return _DispatchReRun(intent)
        case OptimizationIntent():    return _DispatchOptimize(intent)
        case CrestIntent():           return _DispatchCrest(intent)
        case FormCheckIntent():       _DispatchFormCheck(intent)
        case ExcelIntent():           _DispatchExcel(intent)
        case GoodVibesIntent():       _DispatchGoodVibes(intent)
        case FirstTimeSetupIntent():  _DispatchFirstTimeSetup(intent)
        case UpdateIntent():          _DispatchUpdate(intent)
        case PluginUpdateIntent():
            from .plugin import Plugin, UpdatePlugin
            UpdatePlugin(Plugin.Configured(intent.folder, intent.updater, intent.repo, intent.branch, intent.profile,
                                           intent.profileDigest, intent.profileDest, intent.clusterHost,
                                           intent.clusterCu))
        case EnvironmentIntent():     _DispatchEnvironment(intent)
        case InitProjectIntent():     _DispatchInitProject(intent)
        case ProfileIntent():         _DispatchProfile(intent)
        case RefreshIntent():         _DispatchRefresh(intent)
        case StalkIntent():           _DispatchStalk(intent)
        case InfoIntent():            _DispatchInfo(intent)
        case XyzIntent():             _DispatchXyz(intent)
        case MethodsIntent():         _DispatchMethods()
        case CancelIntent():
            from .stalk import Cancel
            Cancel(intent.jobs)
        case ScrapeIntent():          _DispatchScrape(intent)
        case SIIntent():              _DispatchSI(intent)
        case AgentIntent():           _DispatchAgent(intent)
        case AttributionsIntent():    _DispatchAttributions()
        case NevaTouchIntent():
            from .nevatouch import RunStep
            RunStep(intent)
        case _:
            raise ValueError(f"Unknown intent: {type(intent).__name__}")
    return []


# ─── SLURM-submitting dispatchers ─────────────────────────────────────

# Shared by the dispatchers that generate new inputs. Returns None (skip this file) if it has no usable geometry.
# The file is mapped once: everything later steps need from it (route card, ORCA input) is read now, onto the Molecule
def _LoadMolecule(jobPath, coordExtra: str = "") -> Molecule | None:
    baseName, extension = grabPaths(jobPath)
    if baseName is None:
        return None
    jobPath = Path(jobPath)
    if not HasContent(jobPath):
        agent.Skip(str(jobPath), f"No coordinates found in {jobPath}", f"No coordinates found in {jobPath}. Skipping {baseName}.")
        return None
    with MapFile(jobPath) as data:
        molecule = ReadMolecule(data, jobPath)
        if not molecule.coordinateList:
            agent.Skip(str(jobPath), f"No coordinates found in {jobPath}",
                       f"No coordinates found in {jobPath}. Skipping {baseName}.")
            return None
        # Spin state is classified once per molecule, from its source file, before any route is rendered
        molecule.spinState, reason = ClassifySpin(molecule, data)
    WriteXyz(molecule, fileCreation(baseName, Defaults.coordExtension, coordExtra))
    console.print(f"[info]{molecule.rootName}: {molecule.spinState.name} ({reason})[/info]")
    return molecule


def _WarnFrames(path: Path) -> None:
    """An .xyz holding several structures (a CREST ensemble) is read as its last one: say so, so nobody takes its
    highest-energy conformer by mistake."""
    from .fileops import ExtractFrom, XyzFrames
    if Path(path).suffix == Defaults.coordExtension:
        frames = ExtractFrom(Path(path), XyzFrames, empty=0)
        if frames > 1:
            agent.Warn(f"{Path(path).name} holds {frames} structures: only the last one is used.")


def _Whole(value: str, what: str) -> int:
    try:
        return int(value)
    except ValueError:
        raise agent.UsageError(f"The {what} must be a whole number (got '{value}').") from None


def _ChargeAndMultiplicity(charge: int | None, multiplicity: int | None, molecule: Molecule,
                           known: bool) -> tuple[int, int]:
    """A job's charge and multiplicity (-opt's and -crest's): given, else the molecule's when it carries them (a prior
    job, a SMILES), else asked (an .xyz carries neither)."""
    from .prompts import AskStr
    known = known and bool(molecule.multiplicity)
    charge = (charge if charge is not None else int(molecule.charge) if known
              else _Whole(AskStr("Charge", "0", key="charge"), "charge"))
    multiplicity = (multiplicity if multiplicity is not None else int(molecule.multiplicity) if known
                    else _Whole(AskStr("Multiplicity", "1", key="mult"), "multiplicity"))
    return charge, multiplicity


# Run, Cube and FormChk use their files as-is: no coordinates (coordinateList=0 sentinel). Missing files are skipped
def _FileMolecules(files: list):
    for jobPath in files:
        baseName, extension = grabPaths(jobPath)
        if baseName is not None:
            yield Molecule(jobPath, baseName, 0, 0, 0, extension, baseName)


# Every submitted job is tracked (runJob records each one as it is submitted), so it can be stalked now (-st) or
# re-hooked into later (bare -st, the TUI)
def _AfterSubmission(trackedJobs: list[TrackedJob]) -> list[TrackedJob]:
    # Every job went through jobs.SubmitJob, so this is the total
    CheckAndBroadcast(jobs.submittedJobs)
    # An agent waits for them without polling: one background command that exits when one of them ends
    if agent.jsonOutput and trackedJobs:
        agent.Next(agent.CommandLine(["-st", "--wait", *(job.jobID for job in trackedJobs), "--json"]),
                   "Run it as a background command and end your turn: it exits (waking you) when one of them ends.",
                   show=False)
    return trackedJobs


# SP, Benchmark and Re-run: load each file's molecule (skipping unusable ones), generate and submit, then track/broadcast
def _GenerateBatch(intent, workflow, coordExtra: str = "") -> list[TrackedJob]:
    trackedJobs: list[TrackedJob] = []
    for jobPath in intent.files:
        molecule = _LoadMolecule(jobPath, coordExtra)
        if molecule is not None:
            workflow(molecule, intent, trackedJobs)
    return _AfterSubmission(trackedJobs)


def _DispatchRun(intent: RunIntent) -> list[TrackedJob]:
    trackedJobs: list[TrackedJob] = []
    for molecule in _FileMolecules(intent.files):
        runJob(molecule, intent, trackedJobs)
    return _AfterSubmission(trackedJobs)


def _DispatchSinglePoint(intent: SinglePointIntent) -> list[TrackedJob]:
    return _GenerateBatch(intent, genSinglePoint)


def _DispatchBenchmark(intent: BenchmarkIntent) -> list[TrackedJob]:
    if not Catalog.canBench:
        agent.Human("Benchmarking requires at least 2 Single Point Methods (Config › Job Keylists).", "cu -tui")
        return []
    return _GenerateBatch(intent, genBench)


def _ScheduledSinglePoint(asked: ThenSP, partition: str | None, cluster: str | None, required: bool):
    """(the Neva-Touch Scheduler's single point, whether to stop) from --then-sp's flags (-opt's, -re's): a Benchmark
    Methods entry by name (the first by default) or a route of your own; its partition and cluster follow the job's,
    the rest the config's. No Single Point Methods: a failure when it was asked for, else no next job."""
    from .optimize import CustomMethod, SinglePointChoices, SinglePointStep
    spChoices = SinglePointChoices()
    if asked.route:
        spTemplate, _, problem = CustomMethod(asked.route)
        if spTemplate is None:
            raise agent.UsageError(f"--sp-route: {problem}.")
        spName, spRoute = "Custom", asked.route
    elif not spChoices:
        if required:
            agent.Fail("The Neva-Touch Scheduler needs Single Point Methods for its single point (Config › Job "
                       "Keylists), or --sp-route.")
            return None, True
        return None, False
    else:
        wanted = (asked.method or "").lower()
        choice = (next((value for value in spChoices.values() if value[0].lower() == wanted), None) if wanted
                  else next(iter(spChoices.values())))
        if choice is None:
            raise agent.UsageError(f"No Single Point Methods entry named '{asked.method}'. Here: "
                                   f"{', '.join(value[0] for value in spChoices.values())} (cu -methods).")
        spName, spRoute = choice[0], choice[3]
    step = SinglePointStep(spName, spRoute, asked.cpus or Defaults.CPU, asked.memory, asked.wallTime,
                           asked.partition or partition, asked.cluster or cluster, asked.nbo, asked.checkpoint)
    if step.nbo and not step.NboApplies():
        agent.Warn(f"--sp-nbo is Gaussian's ($NBO keylist): the SP ({step.name}) runs in ORCA, so none is written.")
    return step, False


def _DispatchReRun(intent: ReRunIntent) -> list[TrackedJob]:
    import copy
    from .crest   import IsCrestOutput
    # A CREST output's re-run is a search again (crest.ReRunJob); every other file's is genReRun's
    searches = [path for path in intent.files if IsCrestOutput(Path(path))]
    others = [path for path in intent.files if path not in searches]
    if intent.method and intent.route is None and others:
        # --method named only a Conformer Search Method: nothing to correct a DFT job's route with
        for path in others:
            agent.Skip(str(path), f"--method '{intent.method}' is a Conformer Search Method, not an Optimization Method")
        others = []
    if (intent.constrain is not None or intent.forceConstant is not None) and others:
        agent.Warn("--constrain and --force-constant are CREST's: the DFT re-runs ignore them.")
    trackedJobs: list[TrackedJob] = []
    if searches:
        trackedJobs += _ReRunSearches(intent, searches)
    if not others:
        return _AfterSubmission(trackedJobs) if searches else []
    intent = copy.copy(intent)
    intent.files = others
    # --then-sp: each re-run that optimizes gets the single point as its next job (runJob writes its chain file)
    if intent.thenSP is not None:
        step, stop = _ScheduledSinglePoint(intent.thenSP, intent.partition, intent.cluster, required=True)
        if stop:
            return trackedJobs
        if step is not None:
            intent.thenSteps = [step.ChainStep()]
    return trackedJobs + _GenerateBatch(intent, genReRun, "_failed")


def _ReRunSearches(intent: ReRunIntent, outputs: list[Path]) -> list[TrackedJob]:
    """-re on CREST outputs: each searched again as its next attempt (8a_CREST_re), from the same starting geometry with
    the options and constraints it ran with, unless corrected (--method/--route, --constrain). Every question is asked
    before anything is written."""
    from .crest import ReRunJob, Submit
    jobs = []
    for output in outputs:
        job, problem = ReRunJob(Path(output), intent, lambda charge, multiplicity, molecule: _ChargeAndMultiplicity(
            charge, multiplicity, molecule, False))
        if job is None:
            agent.Skip(str(output), problem)
            continue
        job.Apply()
        errors, warnings = job.Check()
        if errors:
            agent.Skip(str(output), "; ".join(errors))
            continue
        for warning in warnings:
            agent.Warn(f"{job.name}: {warning}")
        jobs.append(job)
    trackedJobs: list[TrackedJob] = []
    for job in jobs:
        if Submit(job, trackedJobs):
            agent.Result(job.Result())
            console.print(f"[info]{escape(job.name)}: {escape(job.CommandLine())}[/info]")
    return trackedJobs


def _DispatchCrest(intent: CrestIntent) -> list[TrackedJob]:
    """`cu -crest FILE...`: a CREST conformer search of each molecule (crest.py, which the TUI's Conformer Search builder
    uses too), named NAME_CREST (the next attempt, NAME_CREST_re, when that one has run here). Every question (the
    search, a charge or multiplicity an .xyz doesn't carry) is asked before anything is written."""
    import copy, sys
    from .crest    import CUSTOM, DEFAULT_FORCE_CONSTANT, CrestJob, JobName, Methods, OptionsProblem, Submit
    from .datafile import RootName
    from .fileops  import ReRunNameIn
    from .info     import SourceMolecule
    from .prompts  import AskChoice
    # The TUI's Conformer Search screen: its job, built from its settings, checked again and submitted as it is
    if intent.job is not None:
        job, trackedJobs = intent.job, []
        errors, warnings = job.Check()
        if errors:
            agent.Skip(job.name, "; ".join(errors))
            return []
        for warning in warnings:
            agent.Warn(f"{job.name}: {warning}")
        if Submit(job, trackedJobs):
            agent.Result(job.Result())
        return _AfterSubmission(trackedJobs)
    # CREST's options: a Conformer Search Method by name (cu -methods), typed in full, or asked (once for the batch)
    if intent.route is not None:
        label, options = CUSTOM, " ".join(intent.route.split())
        problem = OptionsProblem(options)
        if problem:
            raise agent.UsageError(f"--route: {problem}.")
    else:
        methods = Methods()
        if not methods:
            agent.Fail("There are no Conformer Search Methods here (Config › Job Keylists), so give CREST's options "
                       "with --route.")
            return []
        if intent.method:
            label = next(name for name in methods if name.lower() == intent.method.lower())
        else:
            names = list(methods)
            chosen = AskChoice("Which conformer search (cu -methods lists each one's options)?", names,
                               key="crest-method")
            if chosen is None:
                return []
            label = names[chosen]
        options = methods[label]
        problem = OptionsProblem(options)
        if problem:
            agent.Fail(f"Conformer Search Method {label}: {problem} (Config › Job Keylists).")
            return []

    # The molecules, each with its charge and multiplicity (asked here, before anything is written)
    sources = []
    if intent.smiles is not None:
        from .smiles import SmilesMolecule
        read, build, problem = SmilesMolecule(intent.smiles, intent.name, intent.rdkitConformers, intent.rdkitConformer)
        for warning in build.warnings if build is not None else []:
            agent.Warn(f"{intent.smiles}: {warning}")
        if read is None:
            agent.Skip(intent.smiles, problem)
            return []
        sources.append((intent.smiles, read, True, intent.name))
    for path in intent.files:
        path = Path(path)
        if not path.is_file():
            agent.Skip(str(path), "No such file")
            continue
        read, _, prior = SourceMolecule(path)
        if not read.coordinateList:
            agent.Skip(path.name, "No coordinates found")
            continue
        _WarnFrames(path)
        sources.append((path.name, read, prior, intent.name or RootName(path.stem)))
    jobs, taken = [], set()
    for item, read, known, species in sources:
        molecule = copy.copy(read)
        charge, multiplicity = _ChargeAndMultiplicity(intent.charge, intent.multiplicity, molecule, known)
        name = JobName(species)
        # A search that has run here keeps its output: this one is its next attempt (the species' data file ranks it)
        if (Path.cwd() / fileCreation(name, Defaults.outputExtension)).exists() or name in taken:
            name = ReRunNameIn(name, Path.cwd())
            while name in taken:
                name = ReRunNameIn(name, Path.cwd())
            console.print(f"[info]{escape(item)}: {JobName(species)} has run here, so this search is {name}, its next "
                          f"attempt.[/info]")
        taken.add(name)
        job = CrestJob(molecule, name, charge, multiplicity, label, options, intent.constrain or "",
                       intent.forceConstant or DEFAULT_FORCE_CONSTANT, intent.cpus or Defaults.CPU, intent.memoryRatio,
                       intent.wallTime, intent.partition, intent.cluster)
        job.Apply()
        errors, warnings = job.Check()
        if errors:
            agent.Skip(item, "; ".join(errors))
            continue
        for warning in warnings:
            agent.Warn(f"{item}: {warning}")
        jobs.append(job)

    trackedJobs: list[TrackedJob] = []
    for job in jobs:
        if Submit(job, trackedJobs):
            agent.Result(job.Result())
            console.print(f"[info]{escape(job.name)}: {escape(job.method)}, charge {job.charge}, multiplicity "
                          f"{job.multiplicity}: {escape(job.CommandLine())}[/info]")
    if agent.dryRun and jobs:
        command = [arg for arg in sys.argv[1:] if arg != "--dry-run"]
        agent.Next(agent.CommandLine(command), "Submit what was previewed.", show=False)
    return _AfterSubmission(trackedJobs)


def _DispatchOptimize(intent: OptimizationIntent) -> list[TrackedJob]:
    """`cu -opt FILE`: the Optimization builder without its screen (optimize.py, which the screen uses too). Every
    question (method, charge, multiplicity, replacing an input) is asked before anything is written."""
    import copy, sys
    from .actions  import SpinState
    from .info     import SourceMolecule
    from .optimize import (FROM_SOURCE_NAME, CustomMethod, MethodChoices, MethodProblems, ModRedStages, OptimizationJob,
                           SinglePointChoices, SinglePointStep, StartingSpin)
    from .prompts  import AskBool, AskChoice, AskStr
    # The molecule: a file's (an .xyz, a prior input or output), or a SMILES' RDKit conformer (its charge and
    # multiplicity the SMILES', named by --name)
    build = None
    if intent.smiles is not None:
        from .smiles import SmilesMolecule
        item = intent.name
        read, build, problem = SmilesMolecule(intent.smiles, intent.name, intent.rdkitConformers, intent.rdkitConformer)
        for warning in build.warnings if build is not None else []:
            agent.Warn(f"{intent.smiles}: {warning}")
        if read is None:
            agent.Skip(intent.smiles, problem)
            return []
        reason, prior, source = "", False, None
    else:
        source = Path(intent.file)
        item = source.name
        if not source.is_file():
            agent.Skip(str(source), "No such file")
            return []
        read, reason, prior = SourceMolecule(source)
        if not read.coordinateList:
            agent.Skip(source.name, "No coordinates found")
            return []
        _WarnFrames(source)
    # A copy: the job's settings are written onto it (as the builder's screen does)
    molecule = copy.copy(read)
    spinReason = StartingSpin(molecule, reason)

    # The method: by name (cu -methods), a route of your own, or asked
    methods, fromProblem = MethodChoices(molecule, prior)
    problems = MethodProblems(methods)
    if intent.route:
        template, extension, problem = CustomMethod(intent.route)
        label = "Custom"
        if template is None:
            raise agent.UsageError(f"--route: {problem}.")
    else:
        usable = {label: key for key, (label, template, _, _) in methods.items()
                  if template is not None and key not in problems}
        if not methods:
            agent.Fail("There are no Optimization Methods here (Config › Job Keylists), so give the route with --route.")
            return []
        if intent.method:
            wanted = intent.method.lower()
            key = next((key for key, (label, *_) in methods.items() if label.lower() == wanted), None)
            if key is None:
                raise agent.UsageError(f"No Optimization Method named '{intent.method}'. Here: "
                                       f"{', '.join(label for label, *_ in methods.values())} (cu -methods).")
        else:
            if not usable:
                agent.Fail("None of the Optimization Methods here can be written: "
                           + "; ".join(f"{methods[key][0]}: {problem}" for key, problem in problems.items())
                           + (f"; {FROM_SOURCE_NAME}: {fromProblem}" if fromProblem else ""))
                return []
            names = list(usable)
            chosen = AskChoice("Which method?", names, key="method")
            if chosen is None:
                return []
            key = usable[names[chosen]]
        label, template, extension, _ = methods[key]
        if template is None or key in problems:
            agent.Skip(item, f"{label}: {problems.get(key) or fromProblem}")
            return []

    # Charge and multiplicity: given, else the prior job's or the SMILES', else asked (an .xyz carries neither)
    charge, multiplicity = _ChargeAndMultiplicity(intent.charge, intent.multiplicity, molecule,
                                                  prior or build is not None)
    spin = (SpinState(intent.spin) if intent.spin else SpinState.OSS if molecule.spinState == SpinState.OSS
            else SpinState.CSS)

    # ModRed lines only for a stage that takes them
    modRedStages = ModRedStages(template, extension)
    for number in intent.modred:
        if number not in modRedStages:
            raise agent.UsageError(f"Stage {number} of {label} doesn't take ModRed lines"
                                   + (f" (stages that do: {', '.join(map(str, modRedStages))})." if modRedStages else "."))

    # The Neva-Touch Scheduler: the SP once the opt ends normally (qol.toml's autoNTS unless told; on with any --sp-
    # flag), a Single Point Methods entry by name (the first by default) or a route of your own. Its partition and
    # cluster follow the opt's, the rest the config's
    given = intent.spMethod or intent.spRoute
    scheduled = (Defaults.autoNTS or bool(given)) if intent.thenSP is None else intent.thenSP
    then = None
    if scheduled:
        then, stop = _ScheduledSinglePoint(
            ThenSP(intent.spMethod, intent.spRoute, intent.spCpus, intent.spMemory, intent.spWallTime,
                   intent.spPartition, intent.spCluster, intent.spNbo, intent.spCheckpoint),
            intent.partition, intent.cluster, required=bool(intent.thenSP or given))
        if stop:
            return []
    job = OptimizationJob(molecule, intent.name or molecule.rootName, charge, multiplicity, spin, template, extension,
                          intent.cpus or Defaults.CPU, intent.memoryRatio, intent.checkpoint, dict(intent.modred),
                          then, nbo=intent.nbo7, wallTime=intent.wallTime, partition=intent.partition,
                          cluster=intent.cluster)
    job.Apply()
    if job.nbo and not job.NboApplies():
        agent.Warn(f"-nbo is Gaussian's ($NBO keylist): {label} runs in ORCA, so none is written.")
    errors, warnings, fileProblem = job.Check()
    if fileProblem:
        errors.append(fileProblem)
    if errors:
        agent.Skip(item, "; ".join(errors))
        return []
    for warning in warnings:
        agent.Warn(f"{warning}: {job.spProblem}" if warning == "Scheduled SP Problem" else warning)

    path = job.path
    if path.exists():
        cameFrom = source is not None and path.resolve() == source.resolve()
        if not AskBool(f"Replace {path.name}{' (the file these coordinates came from)' if cameFrom else ''}?", "n",
                       key="replace-input"):
            agent.Skip(item, f"{path.name} already exists (not replaced)")
            return []

    with open(path, "w") as jobInput:
        jobInput.write(job.InputText())
    agent.Written(path)
    job.WriteChain(path)
    spinText = "Open Shell" if multiplicity > 1 else spin.name + (f" ({spinReason})" if spinReason and not intent.spin else "")
    console.print(f"[info]{escape(path.name)}: {escape(label)}, charge {charge}, multiplicity {multiplicity}, "
                  f"{escape(spinText)}" + (f", then SP: {escape(then.name)} ({escape(then.route)})" if then else "")
                  + "[/info]")
    result = {"input": str(path.resolve()), "method": label, "charge": charge, "multiplicity": multiplicity,
              "spin": "OPEN" if multiplicity > 1 else spin.name, "cpus": job.cpus,
              "memoryPerCore": job.memory if job.memory is not None else job.DefaultMemory(), "nbo": job.NboApplies(),
              "wallTimeHours": job.wallTime or Defaults.wallTime, "partition": job.partition or Defaults.partition}
    if HeaderSends("cluster"):
        result["cluster"] = job.cluster or Defaults.cluster
    if build is not None:
        from .smiles import SmilesResult
        result.update(SmilesResult(build, molecule))
    if then is not None:
        step = then.ChainStep()
        result["thenSP"] = {"method": then.name, "route": then.route, "cpus": then.cpus,
                            "memoryPerCore": then.memory if then.memory is not None
                            else then.DefaultMemory(job.molecule, job.name),
                            "wallTimeHours": step.wallTime or Defaults.wallTime,
                            "partition": step.partition or Defaults.partition, "nbo": step.nbo,
                            "checkpoint": step.checkpoint}
        if HeaderSends("cluster"):
            result["thenSP"]["cluster"] = step.cluster or Defaults.cluster
    agent.Result(result)
    draft = job.Draft(path)
    draft.stalk, draft.stalkLoop = intent.stalk, intent.stalkLoop
    submitted = _DispatchRun(draft.Finalize())
    if agent.dryRun:
        command = [arg for arg in sys.argv[1:] if arg != "--dry-run"]
        agent.Next(agent.CommandLine([*command, "--answer", "replace-input=y"]),
                   "Submit it (the input is written now, so replacing it is answered).", show=False)
    return submitted


# Cube jobs write .cube files, not outputs with a termination line, so they aren't tracked
def _DispatchCube(intent: CubeIntent) -> None:
    for molecule in _FileMolecules(intent.files):
        if molecule.extensionType == ".chk" and not formCheck(molecule):
            continue
        gimmeCubes(molecule, intent)
    CheckAndBroadcast(jobs.submittedJobs)


# ─── Non-SLURM dispatchers ────────────────────────────────────────────

def _DispatchFormCheck(intent: FormCheckIntent) -> None:
    for molecule in _FileMolecules(intent.files):
        formCheck(molecule)


def _DispatchExcel(intent: ExcelIntent) -> None:
    goodVibesProcessor(intent.inputFile)


def _DispatchGoodVibes(intent: GoodVibesIntent) -> None:
    # GoodVibes stops at the first structure without its single point file; name every one of them up front instead
    inputs, _, missing = SpcPartners(intent.files, intent.singlePointPattern)
    if missing:
        for path in missing:
            agent.Skip(path.name, f"No _{intent.singlePointPattern} file", show=False)
        agent.Fail(f"No _{intent.singlePointPattern} file for: {', '.join(path.name for path in missing)}. "
                   "Run their single points first, or run without single point corrections.")
        agent.Next(agent.CommandLine(["-sp", *map(str, missing)]), "Their single points are missing.", show=False)
        return
    if not inputs:
        agent.Fail("No structures to analyse (only single point files were given).")
        return
    # A stale table from an earlier run must never be converted after a failed one
    Path(GOODVIBES_OUTPUT).unlink(missing_ok=True)
    if ALLOW_ALL not in intent.allowDifferences and not _GoodVibesCheckPasses(intent, inputs):
        return
    console.print("[operation]Running GoodVibes...[/operation]")
    # Its output goes through the console: the terminal still shows it, and inside the TUI the Results pop-up does
    # (a subprocess writing to the real terminal under Textual would corrupt the screen)
    result = _RunGoodVibes(GoodVibesArguments(intent), inputs)
    if result is None:
        return
    for output in (result.stdout, result.stderr):
        if output.strip():
            console.print(escape(output.rstrip()), highlight=False)
    if result.returncode != 0:
        agent.Fail("GoodVibes stopped with an error (see above). No Excel file was written.")
        return
    agent.Written(GOODVIBES_OUTPUT)
    console.print("[operation]GoodVibes has terminated. Handing output over to the excel exporter.[/operation]")
    goodVibesProcessor(GOODVIBES_OUTPUT)
    console.print("[good]Enjoy your Excel-formatted GoodVibes output![/good]")
    # The table as GoodVibes wrote it (values as written, never corrected here): an agent's thermochemistry
    from .scrape import GoodVibesTable
    run = GoodVibesTable(Path.cwd())
    # No table (the processor said so): nothing to record, so the run reads as nothing done
    if run.rows:
        agent.Result({"temperature": run.temperature, "concentration": run.concentration or "1 atm",
                      "arguments": GoodVibesArguments(intent),
                      "rows": [{"structure": name, **{column: value for column, value in row.items()
                                                      if column != "Structure"}}
                               for name, row in run.rows.items()]})


def _RunGoodVibes(arguments: list[str], inputs: list[Path]):
    """goodvibes with these flags on these structures, its output captured (None: it isn't installed, said already)."""
    try:
        return subprocess.run(["goodvibes", *arguments, *(str(path) for path in inputs)], capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    except FileNotFoundError:
        agent.Fail("GoodVibes isn't installed, or isn't on PATH.")
        return None


def _GoodVibesCheckPasses(intent: GoodVibesIntent, inputs: list[Path]) -> bool:
    """The consistency check: a first pass with --check and no corrections, its table discarded. A finding in a
    stopping category (analysis.CHECK_CATEGORIES) that isn't waived stops the run before the real one; every other
    finding, and every waived one, is a warning. Waiving is the human's call (an agent asks them first)."""
    console.print("[operation]Checking the files' consistency (GoodVibes --check, no corrections)...[/operation]")
    result = _RunGoodVibes(CheckArguments(intent), inputs)
    Path(GOODVIBES_OUTPUT).unlink(missing_ok=True)
    if result is None:
        return False
    if result.returncode != 0:
        for output in (result.stdout, result.stderr):
            if output.strip():
                console.print(escape(output.rstrip()), highlight=False)
        agent.Fail("GoodVibes' check pass stopped with an error (see above). Nothing was run with corrections.")
        return False
    stops = []
    for finding in ReadCheck(result.stdout):
        if finding.category and finding.category not in intent.allowDifferences:
            stops.append(finding)
            console.print(f"[error]Stop ({CHECK_CATEGORIES[finding.category][0]}): {escape(finding.text)}[/error]")
        else:
            waived = f" (allowed: {CHECK_CATEGORIES[finding.category][0]})" if finding.category else ""
            agent.Warn(f"GoodVibes' check{waived}: {finding.text}")
    if not stops:
        console.print("[good]Consistency check passed.[/good]")
        return True
    categories = sorted({finding.category for finding in stops}, key=list(CHECK_CATEGORIES).index)
    labels = ", ".join(CHECK_CATEGORIES[category][0] for category in categories)
    waiver = ",".join(categories)
    agent.Human(f"GoodVibes' consistency check found differences ({labels}), so the real run didn't start. If they are "
                f"intended (e.g. a benchmark set never shares a method), waive them: --gv-allow {waiver} (in the TUI: "
                f"tick their rows under Consistency Check).", _WaivedCommand(waiver),
                why="Only if the human confirms these differences are intended.")
    return False


def _WaivedCommand(waiver: str) -> str:
    """This -gv command again with the waiver, for the report's next (empty: not run from the command line)."""
    import sys
    argv = sys.argv[1:]
    if not any(arg in ("-gv", "--goodvibes") for arg in argv):
        return ""
    words, skip = [], False
    for arg in argv:
        if skip:
            skip = False
            continue
        if arg == "--gv-allow":
            skip = True
            continue
        if not arg.startswith("--gv-allow="):
            words.append(arg)
    return agent.CommandLine([*words, "--gv-allow", waiver])


def _DispatchXyz(intent: XyzIntent) -> None:
    """`cu -xyz`: each file's last geometry (an output's last, an input's own; Gaussian or ORCA), read from one map as
    every workflow reads it (ReadMolecule) and written as NAME.xyz (WriteXyz, as -sp writes its own). With --smiles:
    its RDKit conformer as NAME.xyz, asked before replacing a file (a typed name can hit a hand-made one)."""
    if intent.smiles is not None:
        from .prompts import AskBool
        from .smiles  import SmilesMolecule, SmilesResult
        molecule, build, problem = SmilesMolecule(intent.smiles, intent.name, intent.rdkitConformers,
                                                  intent.rdkitConformer)
        for warning in build.warnings if build is not None else []:
            agent.Warn(f"{intent.smiles}: {warning}")
        if molecule is None:
            agent.Skip(intent.smiles, problem)
            return
        target = fileCreation(intent.name, Defaults.coordExtension)
        if target.exists() and not (intent.replace or AskBool(f"Replace {target.name}?", "n", key="replace-xyz")):
            agent.Skip(intent.smiles, f"{target.name} already exists (not replaced)")
            return
        WriteXyz(molecule, target)
        agent.Written(target)
        agent.Result({"xyz": str(Path(target).resolve()), "atoms": len(molecule.coordinateList),
                      "charge": build.charge, "multiplicity": build.multiplicity, **SmilesResult(build, molecule)})
        console.print(f"[good]{escape(intent.smiles)}: {escape(molecule.conformer.Label())}, "
                      f"{len(molecule.coordinateList)} atoms written to {target}[/good]")
        return
    for path in intent.files:
        baseName, _ = grabPaths(path)
        if baseName is None:
            continue
        if not HasContent(path):
            agent.Skip(str(path), "Empty file")
            continue
        with MapFile(path) as data:
            molecule = ReadMolecule(data, path)
        if not molecule.coordinateList:
            agent.Skip(str(path), "No coordinates found")
            continue
        _WarnFrames(path)
        target = fileCreation(baseName, Defaults.coordExtension)
        WriteXyz(molecule, target)
        agent.Written(target)
        console.print(f"[good]{path.name}: {len(molecule.coordinateList)} atoms written to {target}[/good]")


def _DispatchStalk(intent: StalkIntent) -> None:
    from .stalk import Dismiss, Snapshot, Wait
    from . import prompts
    if intent.dismiss:
        Dismiss(intent.dismiss)
    # An agent's background command: returns once a job it waits for has ended
    elif intent.wait is not None:
        Wait(intent.wait)
    # With --json, or with nobody at a terminal (an agent's ssh call): one snapshot, never a loop that holds the shell
    elif agent.jsonOutput or not prompts.interactive:
        Snapshot(LoadTracked())
    else:
        jobStalking(LoadTracked(), Defaults.stalkDuration, Defaults.stalkFrequency, intent.loop)


# ─── Read-only dispatchers (for people and agents alike) ──────────────

def _DispatchScrape(intent: ScrapeIntent) -> None:
    """`cu -scrape`: each file's matching lines (one map each), bounded; printed as file:line headers for people and
    one result per file for --json. A file without a match is a skip, so nothing found anywhere is exit 5."""
    from .fileops import ExtractFrom, ExtractMatches, ScrapePattern
    pattern = ScrapePattern(intent.find, intent.case)[0]
    until = ScrapePattern(intent.until, intent.case)[0] if intent.until else None
    for path in intent.files:
        if not Path(path).is_file():
            agent.Skip(str(path), "No such file")
            continue
        found = ExtractFrom(Path(path), ExtractMatches, pattern, intent.first, intent.last, intent.after,
                            intent.before, until, empty=None)
        if found is None:
            agent.Skip(str(path), "Empty file")
            continue
        if not found["matches"]:
            agent.Skip(str(path), "No match")
            continue
        for match in found["matches"]:
            console.print(f"{path}:{match['line']}", style="operation", markup=False, highlight=False)
            console.print(match["text"], markup=False, highlight=False)
        if found.get("truncated"):
            agent.Warn(f"{path}: a block was cut at the line limit (truncated).")
        agent.Result({"file": str(Path(path).resolve()), **found})


def _DispatchInfo(intent: InfoIntent) -> None:
    """`cu -info`: each file's facts (one map each), a table for people, records for --json, and what to run next."""
    from rich.table import Table
    from rich.box import SQUARE
    from .info import Optimizes
    from .datafile import Facts, ForReport
    table = Table(box=SQUARE, show_lines=False)
    for column in ("File", "Program", "Status", "Formula", "Charge/Mult", "Method", "Energy (Eh)", "Imag.", "Conv."):
        table.add_column(column)
    reRun, singlePoint = [], []
    for path in intent.files:
        if not Path(path).is_file():
            agent.Skip(str(path), "No such file")
            continue
        # An output's facts come from its data file (<root>.cudf) while that's current, else from the output, which
        # is then recorded there. The report leaves out the blocks kept only there (the geometry, every frequency)
        try:
            facts, dataFile = Facts(Path(path))
        except (OSError, ValueError) as error:
            agent.Skip(str(path), f"Couldn't be read: {error}")
            continue
        facts = ForReport(facts)
        if dataFile is not None:
            facts["dataFile"] = str(dataFile)
        agent.Result(facts)
        if "electronProblem" in facts:
            agent.Warn(f"{facts['file']}: {facts['electronProblem']} (charge {facts['charge']}, multiplicity "
                       f"{facts['multiplicity']}); a job from it would stop at once.")
        # One row per stage of a staged output (its file's cells on the first), else one
        stages = facts["stages"] if facts["kind"] == "output" and "stages" in facts else [facts]
        for stage in stages:
            first = stage is stages[0]
            convergence = stage.get("convergence")
            label = facts["file"] if len(stages) == 1 else f"{facts['file']} ({stage['stage']}/{len(stages)})"
            table.add_row(label if first else f"  ({stage['stage']}/{len(stages)})",
                          facts.get("program", "—") if first else "",
                          stage.get("status", facts["kind"].title()),
                          facts.get("formula", "—") if first else "",
                          (f"{facts['charge']} / {facts['multiplicity']}" if "multiplicity" in facts else "—")
                          if first else "",
                          escape(f"{stage['method']}/{stage['basis']}" if stage.get("basis")
                                 else stage.get("method", "—")),
                          f"{stage['energy']:.6f}" if "energy" in stage
                          else f"{stage['lowestEnergy']:.6f}" if "lowestEnergy" in stage else "—",
                          str(len(stage["imaginaryFrequencies"])) if "imaginaryFrequencies" in stage else "—",
                          f"{convergence['met']}/{convergence['total']}" if convergence else "—")
        # As given, so the next commands find it from here
        name = str(path)
        if facts["kind"] == "output" and (facts.get("status") == "Error"
                                          or any(stage.get("outOfSteps") for stage in stages)):
            reRun.append(name)
        # Its last stage optimizes, without its single point file, by the partner rule -gv and the SI Scraper use
        # (analysis.SpcPartners): a job that ran its single point as a later stage has it
        elif (facts["kind"] == "output" and facts.get("status") == "Normal" and Optimizes(facts)
              and SpcPartners([Path(path)], Defaults.singlePointExtra.lstrip("_"))[2]):
            singlePoint.append(name)
    console.print(table)
    if reRun:
        agent.Next(agent.CommandLine(["-re", *reRun]), "These ended with an error or ran out of optimizer steps.")
    if singlePoint:
        agent.Next(agent.CommandLine(["-sp", *singlePoint]),
                   "These optimizations finished and have no single point yet.")


def _DispatchMethods() -> None:
    """`cu -methods`: the Optimization, Benchmark and Conformer Search Methods in force here (an agent picks -opt's and
    -crest's --method from these)."""
    from .catalog  import ParseRouteTemplate
    from .fileops  import StageProblem, extensionGetter, PROGRAMS
    from .jobs     import OptMethods
    from .keylists import KeylistSource, OPTIMIZATION, BENCHMARK, CONFORMER_SEARCH
    from .crest    import Level, Methods, OptionsProblem, Solvation

    def Entry(name: str, route: str) -> dict:
        template = ParseRouteTemplate(route)
        program = next((label for label, key in PROGRAMS.values()
                        if getattr(Defaults, key) == extensionGetter(template.method)), "")
        entry = {"name": name, "route": route, "stages": len(template.stages), "program": program}
        problem = StageProblem(template)
        if problem:
            entry["problem"] = problem
        return entry

    for section, entries in ((OPTIMIZATION, list(OptMethods().items())),
                             (BENCHMARK, [(str(index), line) for index, line in enumerate(Catalog.benchmarkLines)])):
        source = KeylistSource(section)
        level = source[0].label if source else "None"
        console.print(f"[operation]{section} ({level})[/operation]")
        records = [Entry(name, route) for name, route in entries]
        for record in records:
            note = f" [warning]({escape(record['problem'])})[/warning]" if "problem" in record else ""
            console.print(f"[info]  {escape(record['name'])}: {escape(record['route'])}{note}[/info]")
        if not records:
            console.print("[info]  None (Config › Job Keylists)[/info]")
        agent.Result({"section": section, "level": level, "methods": records})
    # CREST's option lines (no level has the section: the default search)
    source = KeylistSource(CONFORMER_SEARCH)
    level = source[0].label if source else "Default"
    console.print(f"[operation]{CONFORMER_SEARCH} ({level})[/operation]")
    records = []
    for name, options in Methods().items():
        record = {"name": name, "options": options, "level": Level(options) or "CREST's Default (GFN2-xTB)",
                  "solvation": Solvation(options) or "Gas Phase"}
        problem = OptionsProblem(options)
        if problem:
            record["problem"] = problem
        records.append(record)
        note = f" [warning]({escape(problem)})[/warning]" if problem else ""
        console.print(f"[info]  {escape(name)}: {escape(options)}{note}[/info]")
    agent.Result({"section": CONFORMER_SEARCH, "level": level, "methods": records})


def _DispatchSI(intent: SIIntent) -> None:
    """`cu -si`: the SI Scraper without its screen (scrape.py, which the screen uses too): the same species, starting
    inclusions, checks and file. qh-G needs a current GoodVibes table: without one it stops (exit 6) and says which run
    to make, as the screen opens the GoodVibes Builder; it never runs GoodVibes itself."""
    from .analysis import DetectSuffix, Outputs
    from .prompts  import AskBool, AskStr
    from .scrape   import (ORDER_FILE, SI_FILE, BlockedProblems, FindSpecies, GoodVibesFiles, GoodVibesNeeded,
                           ListedOrder, MissingFromTable, ReadOrder, ReadSpecies, SIText, SPThermo, StartsIncluded,
                           WriteOrder)
    folder = Path.cwd()
    suffix = intent.suffix or DetectSuffix(Outputs(folder)) or Defaults.singlePointExtra.lstrip("_")
    quasiHarmonic = intent.free != "g"
    species = FindSpecies(folder, suffix)
    if not species:
        agent.Fail(f"No {Defaults.outputExtension} outputs here.")
        return
    for one in species:
        ReadSpecies(one)
    listed = intent.species if intent.species is not None else ReadOrder(folder)
    if intent.species is not None:
        unknown = [name for name in intent.species if name not in {one.name for one in species}]
        if unknown:
            raise agent.UsageError(f"No species named {', '.join(unknown)} here (with suffix _{suffix}).")
    ordered = ListedOrder(species, listed)
    starts = StartsIncluded(ordered, listed, quasiHarmonic)
    included = [one for one in ordered if one.name in starts]
    # Left out by choice (--species, list.txt) is said, not counted as skipped; left out because it can't be written is
    leftOut = [one.name for one in ordered if one.name not in starts and listed is not None]
    if leftOut:
        console.print(f"[info]Not in {'--species' if intent.species is not None else ORDER_FILE}: "
                      f"{escape(', '.join(leftOut))}.[/info]")
    for one in ordered:
        if one.name not in starts and listed is None:
            reason = one.Blocks(quasiHarmonic) or "Ended in error"
            agent.Skip(one.name, reason, f"{one.name}: {reason} (left out).")
    if not included:
        agent.Fail("No species can be written.")
        return
    problems = BlockedProblems(included, quasiHarmonic)
    if problems:
        agent.Fail("Nothing was written: " + "; ".join(problems) + ".")
        return
    # qh-G comes only from GoodVibes: its table must be current for every species it gives
    if quasiHarmonic:
        needed = GoodVibesNeeded(included, folder)
        if needed:
            files = [str(path) for path in GoodVibesFiles(included)]
            agent.Stop(f"GoodVibes' table has no current qh-G(T) for {', '.join(one.name for one in needed)}.",
                       agent.CommandLine(["-gv", *files, "--spc", suffix]),
                       "The SI's qh-G lines are GoodVibes' (it runs on every species it gives); run it, then cu -si again.")
            return
    optLabel = intent.optLabel or AskStr("Opt method, as the SI's energy lines name it (e.g. (U)B3LYP-D3/6-31G(d))",
                                         key="si-opt-label")
    spLabel = intent.spLabel or AskStr("SP method, as the SI's energy lines name it", key="si-sp-label")
    thermo = SPThermo(included, folder, quasiHarmonic)
    missing = MissingFromTable(thermo, included)
    if missing:
        agent.Fail(f"Goodvibes_output.dat has no qh-G(T) for: {', '.join(missing)}. Nothing was written.")
        return
    target = folder / (intent.output or SI_FILE)
    if target.exists() and not AskBool(f"Replace {target.name}?", "n", key="replace-si"):
        agent.Skip(target.name, f"{target.name} already exists (not replaced)")
        return
    target.write_text(SIText(included, optLabel, spLabel, intent.phase, thermo))
    WriteOrder(folder, [one.name for one in included])
    agent.Written(target)
    agent.Written(folder / ORDER_FILE)
    console.print(f"[good]Wrote {escape(target.name)} ({len(included)} species) and {ORDER_FILE}.[/good]")
    for one in included:
        # As the SI's lines give them (Hartree); sums rounded past the outputs' own precision
        record = {"name": one.name, "optEnergy": one.energy, "spEnergy": one.spEnergy,
                  "optEnthalpy": round(one.energy + one.corrections[0], 9),
                  "optFreeEnergy": round(one.energy + one.corrections[1], 9)}
        if one.name in thermo.values:
            record["spEnthalpy"], record["spFreeEnergy"] = (round(value, 9) for value in thermo.values[one.name])
        if one.imaginary:
            record["imaginaryFrequencies"] = one.imaginary
        agent.Result(record)


def _DispatchAgent(intent: AgentIntent) -> None:
    """`cu -agent [TOPIC]`: the skill, or a topic, as plain Markdown on stdout (never through Rich, which would wrap
    and style it); with --json, in the report instead."""
    import socket, sys
    from .agentskill import LocalInstall, SkillText, Topic, TopicNames
    host = intent.sshHost or socket.getfqdn()
    if intent.topic in ("", "skill"):
        text = SkillText(host, local=intent.local or LocalInstall())
        if intent.sshHost:
            from .agentskill import RememberHost
            RememberHost(intent.sshHost)
    elif intent.topic == "topics":
        text = "".join(f"{name}\n" for name in TopicNames())
    else:
        text = Topic(intent.topic, host)
        if text is None:
            raise agent.UsageError(f"No topic '{intent.topic}'. Topics: {', '.join(TopicNames())}.")
    if agent.jsonOutput:
        agent.Result({"topic": intent.topic or "skill", "text": text})
    else:
        sys.stdout.write(text)
        agent.Acted()


def _DispatchFirstTimeSetup(intent: FirstTimeSetupIntent) -> None:
    from . import prompts
    from .wizards import firstTimeSetup
    # Setup is a conversation with a person (cluster, notifications, lab profile)
    if not prompts.interactive:
        agent.Human("First-time setup asks a person about their cluster and notifications.", "cu -first")
        return
    firstTimeSetup()
    agent.Acted()


def _DispatchUpdate(intent: UpdateIntent) -> None:
    from . import update
    from .prompts import AskBool
    source = update.InstalledSource()
    if source.localPath:
        agent.Fail(update.LocalInstallMessage(source))
        return
    # --update-branch is a deliberate (re)install from that branch: no check
    if intent.branch is None:
        latest = update.LatestCommit(source.branch)
        for line in update.SourceLines(source, latest):
            console.print(f"[info]{escape(line)}[/info]")
        if update.UpToDate(source, latest) and not AskBool("Reinstall anyway?", "n", key="reinstall"):
            return
    # Never code the environment can't run, and never under a running environment job (its swap would undo it)
    problem = environment.CodeUpdateProblem(source, intent.branch or source.branch)
    if problem:
        agent.Stop(chr(10).join(problem), "cu -env refresh",
                   "The new version needs the environment updated first (run `cu -env` to see it); the refresh "
                   "brings CompUtils up to date on its branch too.")
        return
    if update.Install(intent.branch or source.branch):
        console.print("[good]The next cu run uses the new version.[/good]")
        agent.Acted()
    else:
        agent.Fail("The update failed (pip's output is above).")


def _DispatchEnvironment(intent: EnvironmentIntent) -> None:
    """`cu -env`: every variant's status; with a job, starts it in the background on one variant."""
    from . import update
    from .prompts import AskBool
    source = update.InstalledSource()
    variants = environment.ReadVariants(source)
    if isinstance(variants, str):
        agent.Fail(f"Couldn't read the environment files ({variants}).")
        return
    for problem in variants.problems:
        console.print(f"[warning]Skipped {escape(problem)}.[/warning]")
    running = environment.RunningName()
    if not intent.job:
        if running is not None and running not in (variant.name for variant in variants.found):
            console.print(f"[info]CompUtils runs from {escape(running)}, which no environment file names.[/info]")
        elif running is None:
            console.print("[info]CompUtils isn't running from a conda environment.[/info]")
        for variant in variants.found:
            status = environment.CheckStatus(variant)
            lines = environment.StatusLines(status, variants.where)
            console.print(f"[operation]{escape(lines[0])}[/operation]")
            for line in lines[1:]:
                console.print(f"[info]  {escape(line)}[/info]")
            agent.Result(environment.StatusRecord(status, variants.where))
        console.print("[info]`cu -env refresh|rebuild|install [ENVIRONMENT]` updates one in the background.[/info]")
        return
    wanted = (intent.variant or running or "").lower()
    variant = next((variant for variant in variants.found if wanted in (variant.label.lower(), variant.name.lower())),
                   None)
    if variant is None:
        names = ", ".join(f"{variant.label} ({variant.name})" for variant in variants.found)
        agent.Fail(f"{'No environment named ' + wanted if wanted else 'Say which environment'}. "
                   f"From {variants.where}: {names}.")
        return
    status = environment.CheckStatus(variant)
    if intent.job == "refresh" and status.current:
        console.print(f"[good]{escape(variant.name)} was built from {escape(variants.where)}'s current "
                      f"{escape(variant.file)}: nothing to refresh (`cu -env rebuild` builds it afresh).[/good]")
        return
    if intent.job == "rebuild" and not AskBool(f"Rebuild {variant.name} from {variant.file}? Packages you added to it "
                                               f"yourself are not kept.", "n", key="env-rebuild"):
        return
    started, message = environment.Start(status, intent.job, source)
    if started:
        console.print(f"[good]{escape(message)}[/good]")
        agent.Acted()
        agent.Next("cu -env --json", "The job runs in the background: check on it later.", show=False)
    else:
        agent.Fail(message)


def _DispatchInitProject(intent: InitProjectIntent) -> None:
    from pathlib  import Path
    from .prompts import AskBool
    from .project import FindProjectRoot, CreateProjectRoot, EnsureProjectConfig
    here = Path.cwd().resolve()
    root = FindProjectRoot()
    if root == here:
        console.print(f"[info]{here} is already a project root.[/info]")
        # Re-running -init repairs a missing or unparseable project.toml
        EnsureProjectConfig(here)
        agent.Acted()
        return
    if root is not None:
        console.print(f"[warning]{here} is already inside project {root}.[/warning]")
        if not AskBool("Create a nested project root here anyway?", "n", key="nested-project"):
            return
    if CreateProjectRoot(here):
        agent.Acted()
    else:
        agent.Fail(f"Couldn't create a project root in {here}.")


def _DispatchProfile(intent: ProfileIntent) -> None:
    from .profile import ApplyProfile
    if ApplyProfile(intent.profileFile):
        agent.Acted()
    else:
        agent.Fail("The lab profile wasn't applied (see above).")


def _DispatchAttributions() -> None:
    from .attributions import AttributionText
    console.print(AttributionText())
    agent.Acted()


def _DispatchRefresh(intent: RefreshIntent) -> None:
    # The old benchmarkMethods key goes into keylists.cusf first: refresh drops the key, so it rewrites nothing
    # while the key's list couldn't be merged
    from .keylists import GlobalLevel, MergeLegacy
    if not MergeLegacy(GlobalLevel()):
        agent.Fail("Refresh stopped: nothing was rewritten, so programs.toml keeps its benchmarkMethods.")
        return
    Defaults.RefreshFiles()
    if intent.project:
        from .project import RefreshProjectConfig
        RefreshProjectConfig()
    agent.Acted()
