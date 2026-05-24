import argparse, glob, os, time, subprocess
from pathlib import Path

from .console   import console
from .defaults  import Defaults
from .catalog   import Catalog
from .molecule  import Molecule
from .fileops    import grabPaths, gaussianChargeFinder, formCheck, getCoords
from .jobs      import runJob
from .notify import CheckAndBroadcast
from .prompts import AskBool, AskStr
from .workflows import genBench, genSinglePoint, genReRun, gimmeCubes
from .analysis  import goodVibesInteractive, goodVibesProcessor
from .wizards   import firstTimeSetup
from .intent import RunIntent, SinglePointIntent, BenchmarkIntent, ReRunIntent, CubeIntent, Intent, IntentDraft
from .actions import CubeOption, Action


# Defines all the terminal flags the program can accept
def commandLineParser():
    parser = argparse.ArgumentParser(description="The main command line argument parser for flag handling")

    #The various flags for defining the features of this utility
    parser.add_argument('-r', '--run', type=str, help="Indicates the 'run' subroutine for the given filelist.")
    parser.add_argument('-sp', '--singlePoint', type=str, help="Indicates the 'single point' subroutine for"
                                                               " the listed file(s)")
    parser.add_argument('-b', '--bench', type=str, help="Indicates the 'benchmark' subroutine, for creating"
                                                        " a single point benchmark")
    parser.add_argument('-ch', '--checkpoint', action='store_true', help="Enables checkpoint functionality "
                                                                         "for the job creation subroutines.")
    parser.add_argument('-cu','--cube', type=str, help="Indicates the gimmeCubes functionality on a given "
                                                       "Gaussian16 checkpoint file.")
    parser.add_argument('-st','--stalk', action='store_true', help="Activates job stalking.")
    parser.add_argument('-ex', '--excel', type=str, help="Indicates the goodVibesToExcel functionality on a"
                                                         " given GoodVibes output file.")
    parser.add_argument('-ovr', '--override', type=int, help="Indicates an integer override for indexing, used"
                                                             " for accessing single point methods that are not the first"
                                                             " line in a controlled manner. Keep in mind index counting "
                                                             "starts from 0, not from 1.")
    parser.add_argument('-gv', '--goodvibes', action='store_true', help="Activates the CompUtils interactive interface for GoodVibes."
                                                                        " This acts upon all files in the CWD.")
    parser.add_argument('-nbo','--nbo7',action='store_true',help="Enables NBO7 keylist addition for job creation subroutines.")
    parser.add_argument('-re','--rerun',type=str,help="Reruns a failed Gaussian16 job using the failed output"
                                                      " to generate the new input file.")
    parser.add_argument('-form','--formcheck',type=str,help="Activates the Gaussian16 formchk utility without"
                                                            " full passthrough into gimmeCubes.")
    parser.add_argument('-first','--first',action='store_true',help="Activates first-time set-up again.")
    parser.add_argument('-up','--update', action='store_true',help="Prompts a CompUtils update")

    # Figures out what the hell you told it to do
    args = parser.parse_args()

    # Flags that set bools come first
    if args.stalk:
        Catalog.isStalking = True
        willLoop = AskBool("Enable stalk looping (i.e. re-initialize until all jobs terminate)?", "Y")
        if willLoop:
            Catalog.isLooping = True
    if args.checkpoint:
        Catalog.isCheck = True
    if args.nbo7:
        Catalog.isNBO = True
    if args.override:
        Catalog.indexOverride = args.override
        console.print(f"[operation]Registered {args.indexOverride} as the index override.[/operation]")
    if args.first:
        firstTimeSetup()

    if args.run:
        # Compiles the entire list of files to run (built-in 'runall' capabilities)
        jobList = glob.glob(args.run)
        CheckAndBroadcast(len(jobList))
        tempIntent = RunIntent(files=[Path(p) for p in jobList], stalk=Catalog.isStalking, stalkLoop=Catalog.isLooping,
                               checkpoint=Catalog.isCheck, nbo7=Catalog.isNBO, indexOverride=Catalog.indexOverride)
        localStalkingSet: set = set()
        # Builds the molecule object per complex in input
        for job in jobList:
            baseName, extension = grabPaths(job)
            newMolecule = Molecule(job, baseName, 0, 0, 0, extension, baseName)
            runJob(newMolecule, tempIntent, localStalkingSet)
        Catalog.stalkingSet.update(localStalkingSet)

    if args.singlePoint:
        jobList = glob.glob(args.singlePoint)
        tempIntent = SinglePointIntent(files=[Path(p) for p in jobList], stalk=Catalog.isStalking, stalkLoop=Catalog.isLooping,
                                       checkpoint=Catalog.isCheck, nbo7=Catalog.isNBO, indexOverride=Catalog.indexOverride)
        localStalkingSet: set = set()
        for job in jobList:
            baseName, extension = grabPaths(job)
            charge, multiplicity = gaussianChargeFinder(job)
            coordList = getCoords(job,baseName + Defaults.coordExtension)
            newMolecule = Molecule(job, baseName, charge, multiplicity, coordList, extension, baseName)
            genSinglePoint(newMolecule, tempIntent, localStalkingSet)
        Catalog.stalkingSet.update(localStalkingSet)

    if args.bench:
        if Catalog.canBench:
            jobList = glob.glob(args.bench)
            tempIntent = BenchmarkIntent(files=[Path(p) for p in jobList], stalk=Catalog.isStalking, stalkLoop=Catalog.isLooping,
                                         checkpoint=Catalog.isCheck, nbo7=Catalog.isNBO, indexOverride=Catalog.indexOverride)
            localStalkingSet: set = set()
            for job in jobList:
                baseName, extension = grabPaths(job)
                charge, multiplicity = gaussianChargeFinder(job)
                coordList = getCoords(job,baseName + Defaults.coordExtension)
                newMolecule = Molecule(job, baseName, charge, multiplicity, coordList, extension, baseName)
                genBench(newMolecule, tempIntent, localStalkingSet)
            Catalog.stalkingSet.update(localStalkingSet)
        else:
            console.print("[error]Notice: Benchmarking is unavailable without requisite file. Please create your own or download "
                "the template from GitHub.[/error]")

    if args.cube:
        # Needs to run interactively in order to be useful
        cubeList = AskStr("Enter the list of options you want for cube files generated, separated by spaces (e.g. Pot"
            " Den Val Spin or Range)")
        jobList = glob.glob(args.cube)
        # This splits the entered keylist into separate keys, passed into gimmeCubes as an array which can be iterated through
        cubeOptions = cubeList.split(" ")
        tempIntent = CubeIntent(files=[Path(p) for p in jobList], stalk=Catalog.isStalking, stalkLoop=Catalog.isLooping,
                                checkpoint=Catalog.isCheck, nbo7=Catalog.isNBO, indexOverride=Catalog.indexOverride,
                                cubeOptions=list(CubeOption))
        for job in jobList:
            baseName, extension = grabPaths(job)
            newMolecule = Molecule(job, baseName, 0, 0, 0, extension, baseName)
            if extension == ".chk":
                formCheck(newMolecule)
            gimmeCubes(newMolecule, tempIntent)

    if args.formcheck:
        jobList = glob.glob(args.formcheck)
        for job in jobList:
            baseName, extension = grabPaths(job)
            newMolecule = Molecule(job, baseName, 0, 0, 0, extension, baseName)
            formCheck(newMolecule)

    if args.goodvibes:
        console.print("[operation]Interactive GoodVibes interface activated. Please select your keylist from the common ones.[/operation]")
        totalKeyList = " ".join(goodVibesInteractive())
        subprocess.run(f"goodvibes {totalKeyList} *.out", shell=True, check=True)
        console.print("[operation]GoodVibes has terminated. Handing output over to the excel exporter.[/operation]")
        goodVibesProcessor("Goodvibes_output.dat")
        console.print("[good]Enjoy your Excel-formatted GoodVibes output![/good]")

    if args.rerun:
        jobList = glob.glob(args.rerun)
        keylistOrder = AskBool("Is your input structured as 'opt freq FUNCTIONAL' (Y) or 'FUNCTIONAL other keys' (n)? :", "Y")
        if keylistOrder:
            skipIndex = 2
        else:
            skipIndex = 0
        tempIntent = ReRunIntent(files=[Path(p) for p in jobList], stalk=Catalog.isStalking, stalkLoop=Catalog.isLooping,
                                 checkpoint=Catalog.isCheck, nbo7=Catalog.isNBO, indexOverride=Catalog.indexOverride)
        localStalkingSet: set = set()
        for job in jobList:
            baseName, extension = grabPaths(job)
            charge, multiplicity = gaussianChargeFinder(job)
            coordList = getCoords(job,baseName + "_failed" + Defaults.coordExtension)
            newMolecule = Molecule(job, baseName, charge, multiplicity, coordList, extension, baseName)
            genReRun(newMolecule, tempIntent, localStalkingSet)
        Catalog.stalkingSet.update(localStalkingSet)

    if args.update:
        branch = AskStr("Which branch from the GitHub do you want to update with?", "main")
        repoUrl = (
            f"git+https://github.com/christian-d-knox/"
            f"compchem-utilities.git@{branch}"
        )
        console.print(f"[operation]Updating from branch '{branch}'...[/operation]")
        result = subprocess.run(
            ["pip", "install", "--upgrade", "--force-reinstall", "--no-cache-dir", repoUrl], capture_output=False)
        if result.returncode == 0:
            console.print(
                "[good]Update complete! Nex launch of cu will use the new version.[/good]"
            )
        else:
            console.print("[error]Update failed. See pip output.[/error]")


def BuildParser() -> argparse.ArgumentParser:
    """The same argparse setup as commandLineParser, factored out for reuse."""
    parser = argparse.ArgumentParser(description="CompUtils CLI")

    # Action flags (mutually exclusive — one action per invocation)
    actionGroup = parser.add_mutually_exclusive_group()
    actionGroup.add_argument('-r', '--run', type=str, metavar="GLOB", help="Submit jobs as-written.")
    actionGroup.add_argument('-sp', '--singlePoint', type=str, metavar="GLOB", help="Generate + submit single-point calculations.")
    actionGroup.add_argument('-b', '--bench', type=str, metavar="GLOB", help="Generate + submit a benchmark suite.")
    actionGroup.add_argument('-cu', '--cube', type=str, metavar="GLOB", help="Generate cube files via cubegen.")
    actionGroup.add_argument('-re', '--rerun', type=str, metavar="GLOB", help="Regenerate a failed job and re-submit.")
    actionGroup.add_argument('-form', '--formcheck', type=str, metavar="GLOB", help="Run formchk on Gaussian checkpoint files.")
    actionGroup.add_argument('-ex', '--excel', type=str, metavar="FILE", help="Convert GoodVibes output to xlsx.")
    actionGroup.add_argument('-gv', '--goodvibes', action='store_true', help="Run GoodVibes interactively, then convert to xlsx.")
    actionGroup.add_argument('-first','--first', action='store_true', help="Re-run first-time setup.")
    actionGroup.add_argument('-up', '--update', action='store_true', help="Update CompUtils from GitHub.")

    # Modifiers (apply to whichever action was chosen, where relevant)
    parser.add_argument('-st', '--stalk', action='store_true', help="Enable job stalking.")
    parser.add_argument('-ch', '--checkpoint', action='store_true', help="Enable Gaussian checkpoint files.")
    parser.add_argument('-nbo', '--nbo7', action='store_true', help="Enable NBO7 keylist.")
    parser.add_argument('-ovr', '--override', type=int, default=0, help="Index override for benchmark methods (zero-indexed).")
    parser.add_argument('--update-branch', type=str, default="main", help="With --update, install from this branch (default: main).")

    return parser


def ParseCLI(argv: list[str]) -> Intent:
    """Parse CLI args into a validated, finalized Intent.

    Sub-prompts (stalk-loop confirm, cube options, rerun keylist order,
    goodvibes wizard) happen here, populating the draft before validation.
    """
    args  = BuildParser().parse_args(argv)
    draft = IntentDraft()
    pattern: str | None = None

    # Set action and gather any pattern argument
    if   args.run:         draft.action, pattern = Action.RUN,            args.run
    elif args.singlePoint: draft.action, pattern = Action.SINGLE_POINT,   args.singlePoint
    elif args.bench:       draft.action, pattern = Action.BENCHMARK,      args.bench
    elif args.cube:        draft.action, pattern = Action.CUBE,           args.cube
    elif args.rerun:       draft.action, pattern = Action.RERUN,          args.rerun
    elif args.formcheck:   draft.action, pattern = Action.FORM_CHECK,     args.formcheck
    elif args.excel:       draft.action = Action.EXCEL
    elif args.goodvibes:   draft.action = Action.GOODVIBES
    elif args.first:       draft.action = Action.FIRST_TIME_SETUP
    elif args.update:      draft.action = Action.UPDATE
    else:
        console.print("[error]No action specified. Run `cu --help` for usage.[/error]")
        raise SystemExit(2)

    # File-bearing actions: glob and store
    if pattern is not None:
        draft.files = [Path(p) for p in glob.glob(pattern)]

    # Single-file actions
    if args.excel:
        draft.excelInputFile = Path(args.excel)

    # Modifiers
    draft.stalk         = args.stalk
    draft.checkpoint    = args.checkpoint
    draft.nbo7          = args.nbo7
    draft.indexOverride = args.override
    draft.updateBranch  = args.update_branch

    # Sub-prompts — preserved from current CLI behavior
    if draft.stalk:
        draft.stalkLoop = AskBool("Enable stalk looping (i.e. re-initialize until all jobs terminate)?", "Y")

    if draft.action == Action.RERUN:
        keylistOrder = AskBool("Is your input structured as 'opt freq FUNCTIONAL' (Y) or 'FUNCTIONAL other keys' "
                               "(n)?", "Y")
        draft.skipIndex = 2 if keylistOrder else 0

    if draft.action == Action.CUBE:
        keyText = AskStr("Enter the list of options you want for cube files generated, separated by spaces (e.g. Pot Den"
                         " Val Spin or Range)")
        keyList = keyText.split()
        for key in keyList:
            try:
                option = CubeOption(key)
            except ValueError:
                console.print(f"[warning]Unknown cube option: {key} (ignoring)[/warning]")
                continue
            draft.cubeOptions.append(option)
        if CubeOption.RANGE in draft.cubeOptions:
            draft.orbitalRange = AskStr("Enter the range of MOs you want printed (e.g. 10-15)")

    if draft.action == Action.GOODVIBES:
        # Reuse the existing interactive prompts via goodVibesInteractive
        from .analysis import goodVibesInteractive
        keyList = goodVibesInteractive()
        # goodVibesInteractive returns a list of CLI-style flags; parse them back into intent fields.
        # Walking the list is more robust than re-prompting separately.
        i = 0
        while i < len(keyList):
            token = keyList[i]
            if token == "-q":
                draft.quasiharmonic = True
                i += 1
            elif token == "-f":
                draft.freqCutoff = float(keyList[i + 1]); i += 2
            elif token == "-t":
                draft.tempCorrection = float(keyList[i + 1]); i += 2
            elif token == "-c":
                draft.concCorrection = float(keyList[i + 1]); i += 2
            elif token == "-v":
                # -v appears both for the default 1.0 and a custom scale; skip if 1.0
                scale = float(keyList[i + 1])
                if scale != 1.0:
                    draft.vibeScale = scale
                i += 2
            elif token == "--spc":
                draft.singlePointPattern = keyList[i + 1]; i += 2
            else:
                # Anything else goes into extraKeys
                if draft.extraKeys is None:
                    draft.extraKeys = token
                else:
                    draft.extraKeys += " " + token
                i += 1

    # Validate
    errors = draft.Validate()
    if errors:
        for e in errors:
            console.print(f"[error]Error: {e}[/error]")
        raise SystemExit(2)

    return draft.Finalize()