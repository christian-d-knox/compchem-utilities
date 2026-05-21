import argparse, glob, os, time, subprocess
from .console   import console
from .defaults  import Defaults
from .catalog   import Catalog
from .molecule  import Molecule
from .fileops    import grabPaths, gaussianChargeFinder, formCheck, getCoords
from .jobs      import runJob
from .notify import CheckAndBroadcast
from .prompts import *
from .workflows import genBench, genSinglePoint, genReRun, gimmeCubes
from .analysis  import goodVibesInteractive, goodVibesProcessor
from .wizards   import firstTimeSetup

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
        console.print(f"[operation]Registered {Catalog.indexOverride} as the index override.[/operation]")
    if args.first:
        firstTimeSetup()

    if args.run:
        # Compiles the entire list of files to run (built-in 'runall' capabilities)
        jobList = glob.glob(args.run)
        CheckAndBroadcast(len(jobList))
        # Builds the molecule object per complex in input
        for job in jobList:
            baseName, extension = grabPaths(job)
            newMolecule = Molecule(job, baseName, 0, 0, 0, extension, baseName)
            runJob(newMolecule)

    if args.singlePoint:
        jobList = glob.glob(args.singlePoint)
        for job in jobList:
            baseName, extension = grabPaths(job)
            charge, multiplicity = gaussianChargeFinder(job)
            coordList = getCoords(job,baseName + Defaults.coordExtension)
            newMolecule = Molecule(job, baseName, charge, multiplicity, coordList, extension, baseName)
            genSinglePoint(newMolecule)

    if args.bench:
        if Catalog.canBench:
            jobList = glob.glob(args.bench)
            for job in jobList:
                baseName, extension = grabPaths(job)
                charge, multiplicity = gaussianChargeFinder(job)
                coordList = getCoords(job,baseName + Defaults.coordExtension)
                newMolecule = Molecule(job, baseName, charge, multiplicity, coordList, extension, baseName)
                genBench(newMolecule)
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
        for job in jobList:
            baseName, extension = grabPaths(job)
            newMolecule = Molecule(job, baseName, 0, 0, 0, extension, baseName)
            if extension == ".chk":
                formCheck(newMolecule)
            gimmeCubes(newMolecule, cubeOptions)

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
        for job in jobList:
            baseName, extension = grabPaths(job)
            charge, multiplicity = gaussianChargeFinder(job)
            coordList = getCoords(job,baseName + "_failed" + Defaults.coordExtension)
            newMolecule = Molecule(job, baseName, charge, multiplicity, coordList, extension, baseName)
            genReRun(newMolecule,skipIndex)