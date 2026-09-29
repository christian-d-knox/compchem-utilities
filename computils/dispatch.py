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
    FirstTimeSetupIntent, UpdateIntent, InitProjectIntent,
)
from .fileops   import grabPaths, gaussianChargeFinder, formCheck, getCoords, fileCreation
from .jobs      import runJob
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

# Spin state is classified once per molecule, from its source file, before any route is rendered
def _ClassifyMolecule(molecule: Molecule, jobPath, extension: str) -> None:
    molecule.spinState, reason = ClassifySpin(jobPath, molecule.rootName, molecule.multiplicity, extension)
    console.print(f"[info]{molecule.rootName}: {molecule.spinState.name} ({reason})[/info]")


def _DispatchRun(intent: RunIntent) -> None:
    CheckAndBroadcast(len(intent.files))
    stalkingSet: set = set()
    for jobPath in intent.files:
        baseName, extension = grabPaths(jobPath)
        if baseName is None:
            continue
        molecule = Molecule(jobPath, baseName, 0, 0, 0, extension, baseName)
        runJob(molecule, intent, stalkingSet)
    if intent.stalk:
        jobStalking(stalkingSet, Defaults.stalkDuration, Defaults.stalkFrequency, intent.stalkLoop)


def _DispatchSinglePoint(intent: SinglePointIntent) -> None:
    stalkingSet: set = set()
    for jobPath in intent.files:
        baseName, extension = grabPaths(jobPath)
        if baseName is None:
            continue
        charge, multiplicity = gaussianChargeFinder(jobPath)
        coordList = getCoords(jobPath, fileCreation(baseName, Defaults.coordExtension))
        molecule = Molecule(jobPath, baseName, charge, multiplicity, coordList, extension, baseName)
        _ClassifyMolecule(molecule, jobPath, extension)
        genSinglePoint(molecule, intent, stalkingSet)
    if intent.stalk:
        jobStalking(stalkingSet, Defaults.stalkDuration, Defaults.stalkFrequency, intent.stalkLoop)


def _DispatchBenchmark(intent: BenchmarkIntent) -> None:
    if not Catalog.canBench:
        console.print("[error]Notice: Benchmarking requires at least 2 entries in benchmarkMethods (programs.toml).[/error]")
        return
    stalkingSet: set = set()
    for jobPath in intent.files:
        baseName, extension = grabPaths(jobPath)
        if baseName is None:
            continue
        charge, multiplicity = gaussianChargeFinder(jobPath)
        coordList = getCoords(jobPath, fileCreation(baseName, Defaults.coordExtension))
        molecule = Molecule(jobPath, baseName, charge, multiplicity, coordList, extension, baseName)
        _ClassifyMolecule(molecule, jobPath, extension)
        genBench(molecule, intent, stalkingSet)
    if intent.stalk:
        jobStalking(stalkingSet, Defaults.stalkDuration, Defaults.stalkFrequency, intent.stalkLoop)


def _DispatchReRun(intent: ReRunIntent) -> None:
    stalkingSet: set = set()
    for jobPath in intent.files:
        baseName, extension = grabPaths(jobPath)
        if baseName is None:
            continue
        charge, multiplicity = gaussianChargeFinder(jobPath)
        coordList = getCoords(jobPath, fileCreation(baseName, Defaults.coordExtension, "_failed"))
        molecule = Molecule(jobPath, baseName, charge, multiplicity, coordList, extension, baseName)
        _ClassifyMolecule(molecule, jobPath, extension)
        genReRun(molecule, intent, stalkingSet)
    if intent.stalk:
        jobStalking(stalkingSet, Defaults.stalkDuration, Defaults.stalkFrequency, intent.stalkLoop)


def _DispatchCube(intent: CubeIntent) -> None:
    for jobPath in intent.files:
        baseName, extension = grabPaths(jobPath)
        if baseName is None:
            continue
        molecule = Molecule(jobPath, baseName, 0, 0, 0, extension, baseName)
        if extension == ".chk":
            formCheck(molecule)
        gimmeCubes(molecule, intent)


# ─── Non-SLURM dispatchers ────────────────────────────────────────────

def _DispatchFormCheck(intent: FormCheckIntent) -> None:
    for jobPath in intent.files:
        baseName, extension = grabPaths(jobPath)
        if baseName is None:
            continue
        molecule = Molecule(jobPath, baseName, 0, 0, 0, extension, baseName)
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
    from .project import FindProjectRoot, CreateProjectRoot
    here = Path.cwd().resolve()
    root = FindProjectRoot()
    if root == here:
        console.print(f"[info]{here} is already a project root.[/info]")
        return
    if root is not None:
        console.print(f"[warning]{here} is already inside project {root}.[/warning]")
        if not AskBool("Create a nested project root here anyway?", "n"):
            return
    CreateProjectRoot(here)
