"""
Single execution path for all Intents.

CLI argparse (and eventually TUI screens) build typed Intents which arrive
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
    FirstTimeSetupIntent, UpdateIntent,
)
from .fileops   import grabPaths, gaussianChargeFinder, formCheck, getCoords
from .jobs      import runJob
from .workflows import genBench, genSinglePoint, genReRun, gimmeCubes
from .analysis  import goodVibesProcessor
from .notify    import CheckAndBroadcast
from .stalk     import jobStalking


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
        case _:
            raise ValueError(f"Unknown intent: {type(intent).__name__}")


# ─── SLURM-submitting dispatchers ─────────────────────────────────────

def _DispatchRun(intent: RunIntent) -> None:
    CheckAndBroadcast(len(intent.files))
    stalkingSet: set = set()
    for jobPath in intent.files:
        baseName, extension = grabPaths(jobPath)
        molecule = Molecule(jobPath, baseName, 0, 0, 0, extension, baseName)
        runJob(molecule, intent, stalkingSet)
    if intent.stalk:
        jobStalking(stalkingSet, Defaults.stalkDuration, Defaults.stalkFrequency, intent.stalkLoop)


def _DispatchSinglePoint(intent: SinglePointIntent) -> None:
    stalkingSet: set = set()
    for jobPath in intent.files:
        baseName, extension = grabPaths(jobPath)
        charge, multiplicity = gaussianChargeFinder(jobPath)
        coordList = getCoords(jobPath, baseName + Defaults.coordExtension)
        molecule = Molecule(jobPath, baseName, charge, multiplicity, coordList, extension, baseName)
        genSinglePoint(molecule, intent, stalkingSet)
    if intent.stalk:
        jobStalking(stalkingSet, Defaults.stalkDuration, Defaults.stalkFrequency, intent.stalkLoop)


def _DispatchBenchmark(intent: BenchmarkIntent) -> None:
    if not Catalog.canBench:
        console.print("[error]Notice: Benchmarking is unavailable without requisite file.[/error]")
        return
    stalkingSet: set = set()
    for jobPath in intent.files:
        baseName, extension = grabPaths(jobPath)
        charge, multiplicity = gaussianChargeFinder(jobPath)
        coordList = getCoords(jobPath, baseName + Defaults.coordExtension)
        molecule = Molecule(jobPath, baseName, charge, multiplicity, coordList, extension, baseName)
        genBench(molecule, intent, stalkingSet)
    if intent.stalk:
        jobStalking(stalkingSet, Defaults.stalkDuration, Defaults.stalkFrequency, intent.stalkLoop)


def _DispatchReRun(intent: ReRunIntent) -> None:
    stalkingSet: set = set()
    for jobPath in intent.files:
        baseName, extension = grabPaths(jobPath)
        charge, multiplicity = gaussianChargeFinder(jobPath)
        coordList = getCoords(jobPath, baseName + "_failed" + Defaults.coordExtension)
        molecule = Molecule(jobPath, baseName, charge, multiplicity, coordList, extension, baseName)
        genReRun(molecule, intent, stalkingSet)
    if intent.stalk:
        jobStalking(stalkingSet, Defaults.stalkDuration, Defaults.stalkFrequency, intent.stalkLoop)


def _DispatchCube(intent: CubeIntent) -> None:
    for jobPath in intent.files:
        baseName, extension = grabPaths(jobPath)
        molecule = Molecule(jobPath, baseName, 0, 0, 0, extension, baseName)
        if extension == ".chk":
            formCheck(molecule)
        gimmeCubes(molecule, intent)


# ─── Non-SLURM dispatchers ────────────────────────────────────────────

def _DispatchFormCheck(intent: FormCheckIntent) -> None:
    for jobPath in intent.files:
        baseName, extension = grabPaths(jobPath)
        molecule = Molecule(jobPath, baseName, 0, 0, 0, extension, baseName)
        formCheck(molecule)


def _DispatchExcel(intent: ExcelIntent) -> None:
    goodVibesProcessor(intent.inputFile)


def _DispatchGoodVibes(intent: GoodVibesIntent) -> None:
    # Build the goodvibes command from the intent's fields.
    keyList: list[str] = ["-v", "1.0"]
    if intent.quasiharmonic:           keyList.append("-q")
    if intent.freqCutoff is not None:  keyList.extend(["-f", str(intent.freqCutoff)])
    if intent.tempCorrection is not None: keyList.extend(["-t", str(intent.tempCorrection)])
    if intent.concCorrection is not None: keyList.extend(["-c", str(intent.concCorrection)])
    if intent.vibeScale is not None:   keyList.extend(["-v", str(intent.vibeScale)])
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