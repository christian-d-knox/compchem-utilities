"""
Single execution path for all Intents.

The CLI (argparse) and the TUI both build typed Intents, which arrive
here. Dispatch() routes to the per-action handler based on intent type.
"""
import subprocess
from pathlib import Path

from .console   import console
from .defaults  import Defaults
from .catalog   import Catalog
from .molecule  import Molecule
from .intent    import (
    Intent,
    RunIntent, SinglePointIntent, BenchmarkIntent, ReRunIntent,
    CubeIntent, FormCheckIntent, ExcelIntent, GoodVibesIntent,
    FirstTimeSetupIntent, UpdateIntent, InitProjectIntent,
)
from .fileops   import grabPaths, gaussianChargeFinder, formCheck, getCoords, fileCreation
from .jobs      import runJob
from .          import jobs
from .workflows import genBench, genSinglePoint, genReRun, gimmeCubes
from .analysis  import goodVibesProcessor, GoodVibesArguments, SpcPartners, GOODVIBES_OUTPUT
from .notify    import CheckAndBroadcast
from .stalk     import jobStalking
from .spin      import ClassifySpin


def Dispatch(intent: Intent) -> None:
    """Single execution path. Routes to the per-action handler."""
    match intent:
        case RunIntent():             _DispatchRun(intent)
        case SinglePointIntent():     _DispatchSinglePoint(intent)
        case BenchmarkIntent():       _DispatchBenchmark(intent)
        case CubeIntent():            _DispatchCube(intent)
        case ReRunIntent():           _DispatchReRun(intent)
        case FormCheckIntent():       _DispatchFormCheck(intent)
        case ExcelIntent():           _DispatchExcel(intent)
        case GoodVibesIntent():       _DispatchGoodVibes(intent)
        case FirstTimeSetupIntent():  _DispatchFirstTimeSetup(intent)
        case UpdateIntent():          _DispatchUpdate(intent)
        case InitProjectIntent():     _DispatchInitProject(intent)
        case _:
            raise ValueError(f"Unknown intent: {type(intent).__name__}")


# ─── SLURM-submitting dispatchers ─────────────────────────────────────

# Shared by the dispatchers that generate new inputs. Returns None (skip this file) if it has no usable geometry
def _LoadMolecule(jobPath, coordExtra: str = "") -> Molecule | None:
    baseName, extension = grabPaths(jobPath)
    if baseName is None:
        return None
    charge, multiplicity = gaussianChargeFinder(jobPath)
    coordList = getCoords(jobPath, fileCreation(baseName, Defaults.coordExtension, coordExtra))
    if not coordList:
        console.print(f"[error]No coordinates found in {jobPath}. Skipping {baseName}.[/error]")
        return None
    molecule = Molecule(jobPath, baseName, charge, multiplicity, coordList, extension, baseName)
    # Spin state is classified once per molecule, from its source file, before any route is rendered
    molecule.spinState, reason = ClassifySpin(jobPath, molecule.rootName, molecule.multiplicity, extension)
    console.print(f"[info]{molecule.rootName}: {molecule.spinState.name} ({reason})[/info]")
    return molecule


# Run, Cube and FormChk use their files as-is: no coordinates (coordinateList=0 sentinel). Missing files are skipped
def _FileMolecules(files: list):
    for jobPath in files:
        baseName, extension = grabPaths(jobPath)
        if baseName is not None:
            yield Molecule(jobPath, baseName, 0, 0, 0, extension, baseName)


def _AfterSubmission(intent, stalkingSet: set) -> None:
    # Broadcast before stalking (which can take hours). Every job went through jobs.SubmitJob, so this is the total
    CheckAndBroadcast(jobs.submittedJobs)
    if intent.stalk and stalkingSet:
        jobStalking(stalkingSet, Defaults.stalkDuration, Defaults.stalkFrequency, intent.stalkLoop)


# SP, Benchmark and Re-run: load each file's molecule (skipping unusable ones), generate and submit, then broadcast/stalk
def _GenerateBatch(intent, workflow, coordExtra: str = "") -> None:
    stalkingSet: set = set()
    for jobPath in intent.files:
        molecule = _LoadMolecule(jobPath, coordExtra)
        if molecule is not None:
            workflow(molecule, intent, stalkingSet)
    _AfterSubmission(intent, stalkingSet)


def _DispatchRun(intent: RunIntent) -> None:
    stalkingSet: set = set()
    for molecule in _FileMolecules(intent.files):
        runJob(molecule, intent, stalkingSet)
    _AfterSubmission(intent, stalkingSet)


def _DispatchSinglePoint(intent: SinglePointIntent) -> None:
    _GenerateBatch(intent, genSinglePoint)


def _DispatchBenchmark(intent: BenchmarkIntent) -> None:
    if not Catalog.canBench:
        console.print("[error]Notice: Benchmarking requires at least 2 entries in benchmarkMethods (programs.toml).[/error]")
        return
    _GenerateBatch(intent, genBench)


def _DispatchReRun(intent: ReRunIntent) -> None:
    _GenerateBatch(intent, genReRun, "_failed")


def _DispatchCube(intent: CubeIntent) -> None:
    for molecule in _FileMolecules(intent.files):
        if molecule.extensionType == ".chk":
            formCheck(molecule)
        gimmeCubes(molecule, intent)
    _AfterSubmission(intent, set())


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


def _DispatchUpdate(intent: UpdateIntent) -> None:
    repoUrl = (
        f"git+https://github.com/christian-d-knox/"
        f"compchem-utilities.git@{intent.branch}"
    )
    console.print(f"[operation]Updating from branch '{intent.branch}'...[/operation]")
    result = subprocess.run(
        ["pip", "install", "--upgrade", "--force-reinstall",
         "--no-deps", "--no-cache-dir", repoUrl],
        capture_output=False,
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
