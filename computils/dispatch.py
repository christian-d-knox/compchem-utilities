"""
Single execution path for all Intents.

The CLI (argparse) and the TUI both build typed Intents, which arrive
here. Dispatch() routes to the per-action handler based on intent type.
"""
import importlib.metadata
import json
import subprocess
import sys
from pathlib import Path

from .console   import console
from .defaults  import Defaults
from .catalog   import Catalog
from .molecule  import Molecule
from .intent    import (
    Intent,
    RunIntent, SinglePointIntent, BenchmarkIntent, ReRunIntent,
    CubeIntent, FormCheckIntent, ExcelIntent, GoodVibesIntent,
    FirstTimeSetupIntent, UpdateIntent, InitProjectIntent, ProfileIntent, RefreshIntent, StalkIntent,
    AttributionsIntent,
)
from .fileops   import grabPaths, formCheck, fileCreation, HasContent, MapFile, ReadMolecule, WriteXyz
from .jobs      import runJob
from .          import jobs
from .workflows import genBench, genSinglePoint, genReRun, gimmeCubes
from .analysis  import goodVibesProcessor, GoodVibesArguments, SpcPartners, GOODVIBES_OUTPUT
from .notify    import CheckAndBroadcast
from .stalk     import TrackedJob, TrackJobs, LoadTracked, jobStalking
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
        case FormCheckIntent():       _DispatchFormCheck(intent)
        case ExcelIntent():           _DispatchExcel(intent)
        case GoodVibesIntent():       _DispatchGoodVibes(intent)
        case FirstTimeSetupIntent():  _DispatchFirstTimeSetup(intent)
        case UpdateIntent():          _DispatchUpdate(intent)
        case InitProjectIntent():     _DispatchInitProject(intent)
        case ProfileIntent():         _DispatchProfile(intent)
        case RefreshIntent():         _DispatchRefresh(intent)
        case StalkIntent():           jobStalking(LoadTracked(), Defaults.stalkDuration, Defaults.stalkFrequency, intent.loop)
        case AttributionsIntent():    _DispatchAttributions()
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
        console.print(f"[error]No coordinates found in {jobPath}. Skipping {baseName}.[/error]")
        return None
    with MapFile(jobPath) as data:
        molecule = ReadMolecule(data, jobPath)
        if not molecule.coordinateList:
            console.print(f"[error]No coordinates found in {jobPath}. Skipping {baseName}.[/error]")
            return None
        # Spin state is classified once per molecule, from its source file, before any route is rendered
        molecule.spinState, reason = ClassifySpin(molecule, data)
    WriteXyz(molecule, fileCreation(baseName, Defaults.coordExtension, coordExtra))
    console.print(f"[info]{molecule.rootName}: {molecule.spinState.name} ({reason})[/info]")
    return molecule


# Run, Cube and FormChk use their files as-is: no coordinates (coordinateList=0 sentinel). Missing files are skipped
def _FileMolecules(files: list):
    for jobPath in files:
        baseName, extension = grabPaths(jobPath)
        if baseName is not None:
            yield Molecule(jobPath, baseName, 0, 0, 0, extension, baseName)


# Every submitted job is tracked, so it can be stalked now (-st) or re-hooked into later (bare -st, the TUI)
def _AfterSubmission(trackedJobs: list[TrackedJob]) -> list[TrackedJob]:
    TrackJobs(trackedJobs)
    # Every job went through jobs.SubmitJob, so this is the total
    CheckAndBroadcast(jobs.submittedJobs)
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
        console.print("[error]Notice: Benchmarking requires at least 2 entries in benchmarkMethods (programs.toml).[/error]")
        return []
    return _GenerateBatch(intent, genBench)


def _DispatchReRun(intent: ReRunIntent) -> list[TrackedJob]:
    return _GenerateBatch(intent, genReRun, "_failed")


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
        console.print(f"[error]No _{intent.singlePointPattern} file for: {', '.join(path.name for path in missing)}. "
                      "Run their single points first, or run without single point corrections.[/error]")
        return
    if not inputs:
        console.print("[error]No structures to analyse (only single point files were given).[/error]")
        return
    # A stale table from an earlier run must never be converted after a failed one
    Path(GOODVIBES_OUTPUT).unlink(missing_ok=True)
    console.print("[operation]Running GoodVibes...[/operation]")
    try:
        result = subprocess.run(["goodvibes", *GoodVibesArguments(intent), *(str(path) for path in inputs)])
    except FileNotFoundError:
        console.print("[error]GoodVibes isn't installed, or isn't on PATH.[/error]")
        return
    if result.returncode != 0:
        console.print("[error]GoodVibes stopped with an error (see above). No Excel file was written.[/error]")
        return
    console.print("[operation]GoodVibes has terminated. Handing output over to the excel exporter.[/operation]")
    goodVibesProcessor(GOODVIBES_OUTPUT)
    console.print("[good]Enjoy your Excel-formatted GoodVibes output![/good]")


def _DispatchFirstTimeSetup(intent: FirstTimeSetupIntent) -> None:
    from .wizards import firstTimeSetup
    firstTimeSetup()


def _InstalledBranch() -> str:
    """The branch CompUtils was pip-installed from (pip records it in direct_url.json), else dev (the only branch until
    the first release; then main)."""
    try:
        record = json.loads(importlib.metadata.distribution("compchem-utilities").read_text("direct_url.json") or "{}")
    except (importlib.metadata.PackageNotFoundError, ValueError):
        return "dev"
    return record.get("vcs_info", {}).get("requested_revision") or "dev"


def _DispatchUpdate(intent: UpdateIntent) -> None:
    branch = intent.branch or _InstalledBranch()
    # The same install conda-installer.py runs
    repoUrl = f"git+https://github.com/christian-d-knox/compchem-utilities.git@{branch}"
    console.print(f"[operation]Updating from branch '{branch}'...[/operation]")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--upgrade", "--force-reinstall",
         "--no-deps", "--no-cache-dir", repoUrl],
    )
    if result.returncode == 0:
        console.print("[good]Update complete! Next launch of cu will use the new version.[/good]")
    else:
        console.print("[error]Update failed. See pip output.[/error]")


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
        return
    if root is not None:
        console.print(f"[warning]{here} is already inside project {root}.[/warning]")
        if not AskBool("Create a nested project root here anyway?", "n"):
            return
    CreateProjectRoot(here)


def _DispatchProfile(intent: ProfileIntent) -> None:
    from .profile import ApplyProfile
    ApplyProfile(intent.profileFile)


def _DispatchAttributions() -> None:
    from .attributions import AttributionText
    console.print(AttributionText())


def _DispatchRefresh(intent: RefreshIntent) -> None:
    Defaults.RefreshFiles()
    if intent.project:
        from .project import RefreshProjectConfig
        RefreshProjectConfig()
