"""
Single execution path for all Intents.

The CLI (argparse) and the TUI both build typed Intents, which arrive
here. Dispatch() routes to the per-action handler based on intent type.
"""
import subprocess

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
from .analysis  import goodVibesProcessor
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
    # Build the goodvibes command from the intent's fields.
    vibeScale = str(intent.vibeScale) if intent.vibeScale is not None else "1.0"
    keyList: list[str] = ["-v", vibeScale]
    if intent.quasiharmonic:           keyList.append("-q")
    if intent.freqCutoff is not None:  keyList.extend(["-f", str(intent.freqCutoff)])
    if intent.tempCorrection is not None: keyList.extend(["-t", str(intent.tempCorrection)])
    if intent.concCorrection is not None: keyList.extend(["-c", str(intent.concCorrection)])
    if intent.singlePointPattern is not None: keyList.extend(["--spc", intent.singlePointPattern])
    if intent.extraKeys:               keyList.extend(intent.extraKeys.split())

    console.print("[operation]Running GoodVibes...[/operation]")
    keyString = " ".join(keyList)
    subprocess.run(f"goodvibes {keyString} *.out", shell=True, check=True)
    console.print("[operation]GoodVibes has terminated. Handing output over to the excel exporter.[/operation]")
    goodVibesProcessor("Goodvibes_output.dat")
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
