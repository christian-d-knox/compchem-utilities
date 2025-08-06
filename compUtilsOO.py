#!/usr/bin/env python3
# Welcome to Computational Chemistry Utilities!
# Now bigger, harder, faster, and stronger than ever before!
# This package has been crafted lovingly through untold pain and suffering
# Last major commit to the project was 2025-08-05

# Imports the various libraries needed for main() and each function()
import os
import argparse
import glob
import time
import subprocess
from termcolor import cprint
import numpy
import pandas


# Set your defaults HERE
class Defaults:
    binDirectory = "/ihome/pliu/cdk67/bin/"
    # Ordinary job defaults
    CPU = 12
    memoryRatio = 2
    highMemoryRatio = 6
    wallTime = "24"
    cluster = "smp"
    partition = "pliu"
    # Filemask and Extension related
    singlePointExtra = "_SP"
    coordExtension = ".xyz"
    gaussianExtension = ".gjf"
    orcaExtension = ".inp"
    qChemExtension = ".in"
    cubeExtension = ".cube"
    queueExtension = ".cmd"
    outputExtension = ".out"
    method = "M062X"
    methodLine = "M062X 6-311+G(d,p)"
    # What runs where. Edit carefully (recommended to use programs.txt instead)
    methodNames = ["B3LYP","M062X","M06","M06L","B2PLYP","wB97XD","DLPNO-CCSD(T)","BLYP"]
    targetProgram = ["G16","G16","G16","G16","G16","G16","O","G16"]
    # Cube Keylists
    potCube = "Pot"
    denCube = "Den"
    valenceCube = "Val"
    spinCube = "Spin"

# Defines global variables for use in various functions
fileNames = []
fullPaths = []
totalJobList = []
totalOutputs = []
isStalking = False
isCustomTarget = True
canBench = True
methodLine = []
fullMethodLine = []
fileExtension = ""
coordinateScrapeTime = 0
# Paired together for program identification
methodList = []
targetProgram = []

# NEW!! Attempting to keep track of everything related to a job in one central location
class Molecule:
    def __init__(self, fullPath, baseName, charge, multiplicity, coordinateList, extensionType):
        self.fullPath = fullPath
        self.baseName = baseName
        self.charge = charge
        self.multiplicity = multiplicity
        self.coordinateList = coordinateList
        self.extensionType = extensionType


# Actually perform some operations globally for more code efficiency
if os.path.isfile(os.path.join(Defaults.binDirectory, "benchmarking.txt")):
    with open(os.path.join(Defaults.binDirectory, "benchmarking.txt"), "r") as methodFile:
        for line in methodFile:
            fullMethodLine.append(line)
            methodLine.append(line.strip().split()[0])
else:
    canBench = False
    fullMethodLine = Defaults.methodLine
    methodLine = Defaults.method
    cprint("Notice: Could not find benchmarking.txt in ~/bin/.", "light_red")
    cprint("Benchmarking functionality is unavailable without requisite file. Please create your own or download the template from GitHub.", "light_red")
if os.path.isfile(os.path.join(Defaults.binDirectory, "programs.txt")):
    with open(os.path.join(Defaults.binDirectory, "programs.txt"), 'r') as programFile:
        for targetLine in programFile:
            currentSubs = targetLine.strip().split(" ")
            methodList.append(currentSubs[0])
            targetProgram.append(currentSubs[1])
else:
    isCustomTarget = False
    methodList = Defaults.methodNames
    targetProgram = Defaults.targetProgram
    cprint("Notice: Could not find programs.txt in ~/bin/.", "light_red")
    cprint("Defaulting to hardcoded method targets.", "light_red")

# Defines all the terminal flags the program can accept
def commandLineParser():
    parser = argparse.ArgumentParser(description="The main command line argument parser for flag handling")

    #The various flags for defining the features of this utility
    parser.add_argument('-r', '--run', type=str, help="Indicates the 'run' subroutine for the listed file(s)")
    parser.add_argument('-sp', '--singlePoint', type=str, help="Indicates the 'single point' subroutine for the listed file(s)")
    parser.add_argument('-b', '--bench', type=str, help="Indicates the 'benchmark' subroutine, for creating a single point becnhmark on the listed file(s)")
    parser.add_argument('-ch', '--checkpoint', action='store_true', help="Enables checkpoint functionality for the 'run' routine")
    parser.add_argument('-t','--test',type=str,help="Activates whatever function I'm trying to test.")
    parser.add_argument('-cu','--cube',type=str,help="Indicates the gimmeCubes functionality on a given Gaussian16 checkpoint file.")

    # Figures out what the hell you told it to do
    args = parser.parse_args()

    # Run flag handling
    if args.run:
        # Compiles the entire list of files to run (built-in 'runall' capabilities)
        jobList = glob.glob(args.run)
        for job in jobList:
            baseName, extension = grabPaths(job)
            newMolecule = Molecule(job, baseName, 0, 0, 0, extension)
            runJob(newMolecule)

    if args.singlePoint:
        # Compiles the entire list of files to run
        jobList = glob.glob(args.singlePoint)
        for job in jobList:
            baseName, extension = grabPaths(job)
            charge, multiplicity = gaussianChargeFinder(job)
            coordList = coordinateScraper(job,baseName + Defaults.coordExtension)
            newMolecule = Molecule(job, baseName, charge, multiplicity, coordList, extension)
            genSinglePoint(newMolecule)

    if args.bench:
        if canBench:
            jobList = glob.glob(args.bench)
            for job in jobList:
                baseName, extension = grabPaths(job)
                charge, multiplicity = gaussianChargeFinder(job)
                coordList = coordinateScraper(job,baseName + Defaults.coordExtension)
                newMolecule = Molecule(job, baseName, charge, multiplicity, coordList, extension)
                genBench(newMolecule)

        else:
            cprint("Notice: Benchmarking is unavailable without requisite file. Please create your own or download the template from GitHub.", "light_red")

    if args.test:
        pass

    if args.cube:
        cubeList = str(input("Enter the list of options you want for cube files generated, separated by spaces (e.g. Pot Den Val Spin): "))
        jobList = glob.glob(args.cube)
        # This splits the entered keylist into separate keys, passed into gimmeCubes as an array which can be iterated through
        cubeOptions = cubeList.split(" ")
        for job in jobList:
            baseName, extension = grabPaths(job)
            newMolecule = Molecule(job, baseName, 0, 0, 0, extension)
            gimmeCubes(newMolecule, cubeOptions)

# Finally handle filename creation in one place to stop the infinite copypasta
def fileCreation(baseName, extensionType, extra):
    if not len(extra) == 0:
        fullFile = baseName + extra + extensionType
    else:
        fullFile = baseName + extensionType
    return fullFile

# Finally PROPERLY executes the PERL script and pipes output into Python for post-processing
def coordinateScraper(fileName, outputFileName):
    startTime = time.time()
    scrapedCoords = subprocess.run(['pg2xyz.sh',fileName], capture_output=True, text=True, check=True)
    coordList = scrapedCoords.stdout
    coordList = coordList.splitlines()
    with open(outputFileName, 'w') as outputFile:
        outputFile.write(str(len(coordList)) + "\nPointless Comment Line\n")
        for index, line in enumerate(coordList):
            modLine = line + "\n"
            outputFile.write(modLine)
            coordList[index] = modLine
    endTime = time.time()
    coordinateScrapeTime = round(endTime-startTime,2)
    cprint("Time taken to scrape coordinates by PERL script is " + str(coordinateScrapeTime) + " seconds.", "light_cyan")
    return coordList

# Handles extensions so I don't have to copypasta this
def extensionGetter(method):
    programTarget = ""
    for x in range(len(methodList)):
        if method == methodList[x]:
            programTarget = targetProgram[x]
    match programTarget:
        case "G16":
            fileExtension = Defaults.gaussianExtension
        case "O":
            fileExtension = Defaults.orcaExtension
        case "Q":
            fileExtension = Defaults.qChemExtension
        case _:
            cprint("Notice: Intended method is not specified in programs file nor hardcoded. Defaulting to Gaussian16.", "light_red")
            fileExtension = Defaults.gaussianExtension
    return fileExtension

# Gaussian16 Charge Finder in its own method
def gaussianChargeFinder(geometryFile):
    chargeLine = ""
    chargeSub = chargeLine.strip().split(" ")
    chargeFirstSub = chargeSub[0]
    with open(geometryFile, 'r') as geomFile:
        # This iterator finds the charge and multiplicity automatically, no more need to specify them.
        while chargeFirstSub != "Charge":
            chargeLine = geomFile.readline()
            chargeSub = chargeLine.strip().split(" ")
            chargeFirstSub = chargeSub[0]

        # This chunk handles the special case where a stupid non-breaking space is used for neutral charges?
        if chargeSub[2] == '':
            del chargeSub[2]
        charge = chargeSub[2]
        multiplicity = chargeSub[5]
    return charge, multiplicity

# This subroutine returns file name and extension for ease-of-use
def grabPaths(fileName):
    if os.path.exists(fileName):
        baseName, extension = os.path.basename(fileName).split(".")
        extension = "." + extension
    else:
        cprint("Could not locate: " + fileName, "light_red")
    return baseName, extension

def genBench(molecule):
    # First, make the original Single Point
    genSinglePoint(molecule)
    # Since methodFile is defined globally, no need to iterate a line to catch-up after genSinglePoint
    startTime = time.time()
    for index in range(1, len(methodLine)):
        molecule.extensionType = extensionGetter(methodLine[index])
        inputFile = fileCreation(molecule.baseName, molecule.extensionType, f"-{index}-" + methodLine[index] + Defaults.singlePointExtra)
        molecule.fullPath = inputFile
        genFile(molecule,index)
        #runJob(molecule)
    endTime = time.time()
    totalTime = round(endTime-startTime,2)
    cprint("Total time for non-SP benchmark generation is: " + str(totalTime) + " seconds.", "light_cyan")

def genSinglePoint(molecule):
    startTime = time.time()

    # Update molecule properties
    molecule.extensionType = extensionGetter(methodLine[0])
    inputFile = fileCreation(molecule.baseName, molecule.extensionType, Defaults.singlePointExtra)
    molecule.fullPath = inputFile

    # Calls the separate file generation method, feeds directly into runJob
    genFile(molecule, 0)
    #runJob(molecule)
    endTime = time.time()
    totalTime = round(endTime - startTime,2)
    singlePointTime = totalTime - coordinateScrapeTime
    cprint("Total single point time is " + str(totalTime) + " seconds.", "light_cyan")
    cprint("Corrected time for non-coordinate code is " + str(singlePointTime) + " seconds.", "light_cyan")

# Separate method for input file generation to improve code efficiency. No longer returns anything as path to input is previously stored
def genFile(molecule, index):
    inputFile = molecule.fullPath
    coordFile = molecule.baseName + Defaults.coordExtension
    match molecule.extensionType:
        case Defaults.gaussianExtension:
            # Opens the XYZ Files that was just created in order to read-in coordinates and the actual job file
            with open(inputFile, 'w') as jobInput:
                # Sets the job's CPU and RAM
                jobCPU = str(Defaults.CPU)
                jobMem = str(Defaults.CPU * Defaults.memoryRatio)
                # Writes the standard Gaussian16 formatted opening
                jobInput.write("%nprocshared=" + jobCPU + "\n%mem=" + jobMem +"GB")
                # If the methodLine from benchmarking.txt is garbage, the calculation will fail. Not my fault.
                jobInput.write("\n# " + fullMethodLine[index] + "\nUseless Comment line\n\n")
                jobInput.write(molecule.charge + " " + molecule.multiplicity + "\n")
                # Iterates through the XYZ to scrape the coordinates (getCoords isn't efficient to call repeatedly,
                # and this is actually *much* more useful)
                for line in molecule.coordinateList:
                    # Skips the atoms and blank line in the XYZ because that will break shit
                    if len(line.split()) > 3:
                        jobInput.write(line)
                jobInput.write("\n\n")

        case Defaults.orcaExtension:
            # Opens the job file
            with open(inputFile, 'w') as jobInput:
                # Sets the job's CPU and RAM
                jobCPU = str(Defaults.CPU)
                if methodLine == "DLPNO-CCSD(T)":
                    jobMem = str(Defaults.CPU * Defaults.highMemoryRatio * 1000)
                else:
                    jobMem = str(Defaults.CPU * Defaults.memoryRatio * 1000)
                # Writes the standard ORCA formatted opening
                jobInput.write("%pal nprocs " + jobCPU + "\nend" + "\n%maxcore " + jobMem)
                # If the methodLine from benchmarking.txt is garbage, the calculation will fail. Not my fault.
                jobInput.write("\n! " + fullMethodLine[index])
                # ORCA is smart enough to read from an XYZ directly
                jobInput.write(f"* xyzfile {molecule.charge} {molecule.multiplicity} {coordFile} *")

        case Defaults.qChemExtension:
            # Put crap here later
            thing = 0

# This routine is for job submission to the cluster
def runJob(molecule):
    totalJobs = 0
    # Iteration is now done in commandLineParser()
    # The following is more or less copied directly from my modified qg16
    # Sets up all the basic filenames for the rest of submission
    outputName = molecule.baseName + Defaults.outputExtension
    queueName = molecule.baseName + Defaults.queueExtension

    with open(molecule.fullPath, 'r+') as inputFile:
        #while True:
        # Reads the first line of the file
        currentLine = inputFile.readline()
        currentLine = currentLine.strip()

        # Craps out if the first link doesn't exist, or is entirely blank
        if not currentLine:
            cprint(f"Job file " + molecule.baseName + " is empty. Terminating submission attempt.", "light_red")
        if len(currentLine) == 0:
            cprint(f"First line of job file " + molecule.baseName + " is blank. Terminating submission attempt.", "light_red")

        match molecule.extensionType:
            case Defaults.gaussianExtension:
                subLine = currentLine.split('=')
                firstSubLine = subLine[0].lower()
                if firstSubLine == '%nprocshared'or firstSubLine =='%nproc':
                    cpus = int(subLine[1])
                    print("Successfully read " + str(cpus) + " cores from " + molecule.baseName + Defaults.gaussianExtension)

                    # Looks for memory in input file
                    currentLine = inputFile.readline().strip()
                    subLine = currentLine.split('=')
                    firstSubLine = subLine[0].lower()
                    if firstSubLine == '%mem':
                        ram = int(subLine[1].replace("GB", ""))
                        jobRam = ram + 2
                        print("Successfully read memory and will submit with " + str(ram) + " + 2 GB")
                    # Sets the job to run with a default amount of RAM. Does not update the input because neither of us could be bothered to care.
                    else:
                        ram = cpus * Defaults.memoryRatio
                        jobRam = ram + 2
                        print("Couldn't find RAM amount and will submit with " + str(ram) + " + 2 GB instead")
                else:
                    cpus = Defaults.CPU
                    ram = cpus * Defaults.memoryRatio
                    jobRam = ram + 2

                with open(queueName, 'w') as outputFile:
                    # Writes the CMD for submission
                    outputFile.write("#!/usr/bin/env bash\n")
                    outputFile.write("#SBATCH --job-name=" + str(molecule.baseName) + "\n")
                    outputFile.write("#SBATCH --output=" + outputName + "\n")
                    outputFile.write("#SBATCH --ntasks-per-node=" + str(cpus) + "\n")
                    outputFile.write("#SBATCH --mem=" + str(jobRam) + "GB\n")
                    outputFile.write("#SBATCH --time=" + Defaults.wallTime + ":00:00\n")
                    outputFile.write("#SBATCH --cluster=" + Defaults.cluster + "\n")
                    outputFile.write("#SBATCH --partition=" + Defaults.partition + "\n")
                    outputFile.write("#SBATCH --nodes=1\n")

                    outputFile.write("\nmodule purge\nmodule load gaussian\n\n")
                    outputFile.write("export GAUSS_SCRDIR=$SLURM_SCRATCH\nulimit -s unlimited\nexport LC_COLLATE=C\n")
                    outputFile.write("\ng16 < " + molecule.f + "\n\n")

                os.system("sbatch " + queueName)
                os.remove(queueName)
                cprint(f"Submitted job " + molecule.baseName + " to Gaussian16", "light_green")

            case Defaults.orcaExtension:
                subLine = currentLine.split()
                firstSubLine = subLine[0].lower()
                # ORCA submission
                if firstSubLine == '%pal':
                    cpus = int(subLine[2])
                    print("Successfully read " + str(cpus) + " cores from " + molecule.baseName + Defaults.orcaExtension)

                    # Looks for memory in ORCA file
                    inputFile.readline()
                    # Due to input re-writing, need to read the *third* line instead of the second
                    currentLine = inputFile.readline().strip()
                    subLine = currentLine.split()
                    firstSubLine = subLine[0].lower()
                    if firstSubLine == '%maxcore':
                        ramTemp = int(subLine[1])
                        print(str(ramTemp) + " MB")
                        ramConvert = int(ramTemp / 1000 * cpus)
                        print(str(ramConvert) + " GB")
                        jobRam = int(ramConvert + 2)
                        print("Successfully read memory and will submit with " + str(ramConvert) + " + 2 GB")
                    # Sets the RAM to a default amount
                    else:
                        ram = cpus * 6
                        jobRam = int(ram + 2)
                        print("Couldn't find RAM amount and will submit with " + str(ram) + " + 2 GB instead")
                else:
                    cpus = Defaults.CPU
                    ram = cpus * Defaults.memoryRatio
                    jobRam = ram + 2

                with open(queueName, 'w') as outputFile:
                    # Writes the CMD for job submission
                    outputFile.write("#!/bin/bash\n")
                    outputFile.write("#SBATCH --job-name=" + str(molecule.baseName) + "\n")
                    outputFile.write("#SBATCH --output=" + outputName + "\n")
                    outputFile.write("#SBATCH --nodes=1\n")
                    outputFile.write("#SBATCH --mem=" + str(jobRam) + "GB\n")
                    outputFile.write("#SBATCH --ntasks-per-node=" + str(cpus) + "\n")
                    outputFile.write("#SBATCH --time=" + Defaults.wallTime + ":00:00\n")
                    outputFile.write("#SBATCH --cluster=" + Defaults.cluster + "\n")
                    outputFile.write("#SBATCH --partition=" + Defaults.partition + "\n")

                    outputFile.write("\n# Load the module\nmodule purge\n")
                    # Now runs in ORCA 6.0.0 instead of 4.2.0 .
                    outputFile.write("module load gcc/10.2.0 gcc/4.8.5 openmpi/4.1.1 orca/6.0.0\n\n")
                    outputFile.write("# Copy files to SLURM_SCRATCH\n")
                    outputFile.write("files=(" + str(molecule.fullPath) + ")\n")
                    outputFile.write("for i in ${files[@]}; do\n")
                    outputFile.write("    cp $SLURM_SUBMIT_DIR/$i $SLURM_SCRATCH/$i\ndone\n\n")
                    outputFile.write("# cd to the SCRATCH space\n")
                    outputFile.write("cd $SLURM_SCRATCH\n\n")
                    outputFile.write("# run the job, $(which orca) is necessary\n")
                    outputFile.write("$(which orca) " + str(molecule.fullPath) + "\n\n")
                    outputFile.write("# finally, copy back gbw and prop files\n")
                    outputFile.write("cp $SLURM_SCRATCH/*.{gbw,prop} $SLURM_SUBMIT_DIR\n\n")

                os.system("sbatch " + queueName)
                os.remove(queueName)
                cprint(f"Submitted job " + molecule.baseName + " to ORCA 6.0.0", "light_green")

            case Defaults.qChemExtension:
                subLine = currentLine.split('=')
                firstSubLine = subLine[0].lower()
                if firstSubLine == '%mem':
                    ram = int(subLine[1].replace("GB", ""))
                    jobRam = ram + 2
                    print("Successfully read memory and will submit with " + str(ram) + " + 2 GB")

                    currentLine = inputFile.readline().strip()
                    subLine = currentLine.split('=')
                    firstSubLine = subLine[0].lower()
                    if firstSubLine == '%nprocshared' or firstSubLine == '%nproc':
                        cpus = int(subLine[1])
                        print("Successfully read " + str(cpus) + " cores from " + molecule.baseName + ".in")
                        # Sets the job to run with a default amount of RAM. Does not update the input because neither of us could be bothered to care.
                else:
                    cpus = Defaults.CPU
                    ram = cpus * Defaults.memoryRatio
                    jobRam = ram + 2
                    print("Couldn't find RAM amount and will submit with " + str(ram) + " + 2 GB instead")

                with open(queueName, 'w') as outputFile:
                    # Writes queueFile for submission to the cluster
                    outputFile.write("#!/usr/bin/env bash\n")
                    outputFile.write("#SBATCH --job-name=" + molecule.baseName + "\n")
                    outputFile.write("#SBATCH --output=" + outputName + "\n")
                    outputFile.write("#SBATCH --ntasks-per-node=" + str(cpus) + "\n")
                    outputFile.write("#SBATCH --mem=" + str(jobRam) + "\n")
                    outputFile.write("#SBATCH --time=" + Defaults.wallTime + ":00:00\n")
                    outputFile.write("#SBATCH --cluster=" + Defaults.cluster + "\n")
                    outputFile.write("#SBATCH --partition=" + Defaults.partition + "\n")
                    outputFile.write("#SBATCH --nodes=1\n\n")

                    outputFile.write("module purge\nmodule load qchem/6.3.0-pliu\n\n\n")
                    # This crap was in the original and I have no clue if it's still necessary
                    # output.write("export QCSCRATCH=$LOCAL\n")
                    # output.write("export QC=/ihome/pliu/xiq23/qchem/qchem_for_peng_20151014\n")
                    # output.write("export PATH=$PATH:$QC/bin\n")
                    # output.write("export QCAUX=/ihome/pliu/xiq23/qchem/qcaux4\n")
                    outputFile.write("# Change to working directory\n")
                    outputFile.write("cp $SLURM_SUBMIT_DIR/" + molecule.fullPath + " $SLURM_SCRATCH\n")
                    outputFile.write("cd $SLURM_SCRATCH\n\n")
                    # No clue what this line is and if it's needed, it's not in the Q-Chem documentation
                    outputFile.write("df -h\n")

                    # Re-wrote the script in order to make this line *actually* match the Q-Chem documentation
                    # Re-wrote to allow nthreads to match ncpu since each thread only runs on 1 CPU (see Q-Chem manual)
                    # Do NOT specify outfile, it LITERALLY breaks shit for some reason
                    outputFile.write("qchem -slurm -nt " + str(cpus) + " " + molecule.fullPath + " " + "\n")
                    outputFile.write("du -h\n\n")

                os.system("sbatch " + queueName)
                os.remove(queueName)
                cprint(f"Submitted job " + molecule.baseName + " to Q-Chem 6.3", "light_green")

# Graciously modified from my very own gimmeCubes
def gimmeCubes(molecule, cubeKeyList):
    keyWord = ""
    queueName = ""
    outputName = ""
    for cubeKey in cubeKeyList:
        match cubeKey:
            case Defaults.spinCube:
                outputName = fileCreation(molecule.baseName, Defaults.cubeExtension, cubeKey)
                queueName = fileCreation(molecule.baseName, Defaults.queueExtension, cubeKey)
                keyWord = "Spin=SCF"
            case Defaults.denCube:
                outputName = fileCreation(molecule.baseName, Defaults.cubeExtension, cubeKey)
                queueName = fileCreation(molecule.baseName, Defaults.queueExtension, cubeKey)
                keyWord = "Density=SCF"
            case Defaults.potCube:
                outputName = fileCreation(molecule.baseName, Defaults.cubeExtension, cubeKey)
                queueName = fileCreation(molecule.baseName, Defaults.queueExtension, cubeKey)
                keyWord = "Potential=SCF"
            case Defaults.valenceCube:
                outputName = fileCreation(molecule.baseName, Defaults.cubeExtension, cubeKey)
                queueName = fileCreation(molecule.baseName, Defaults.queueExtension, cubeKey)
                keyWord = "MO=Valence"
            case _:
                cprint("Error: Unknown keyword found in keylist for " + molecule.baseName + " : " + cubeKey, "light_red")
        with open(queueName,"w") as queueFile:
            jobRam = Defaults.CPU * Defaults.memoryRatio
            queueFile.write("#!/usr/bin/env bash\n")
            queueFile.write("#SBATCH --job-name=" + molecule.baseName + "\n")
            queueFile.write("#SBATCH --output=" + outputName + "\n")
            queueFile.write("#SBATCH --ntasks-per-node=" + str(Defaults.CPU) + "\n")
            queueFile.write("#SBATCH --mem=" + str(jobRam) + "\n")
            queueFile.write("#SBATCH --time=" + Defaults.wallTime + ":00:00\n")
            queueFile.write("#SBATCH --cluster=" + Defaults.cluster + "\n")
            queueFile.write("#SBATCH --partition=" + Defaults.partition + "\n")
            queueFile.write("#SBATCH --nodes=1\n\nmodule purge\n")
            queueFile.write("module load gaussian/16-C.01\n\n")
            queueFile.write("export GAUSS_SCRDIR=$SLURM_SCRATCH\nulimit -s unlimited\nexport LC_COLLATE=C\n\n")

            # Writes the specifics for running the Density Cube
            queueFile.write("cubegen 1 " + keyWord + " " + molecule.fullPath + " " + outputName + " 0""\n\n")

        os.system("sbatch " + queueName)
        os.remove(queueName)
        cprint(f"Submitted cube job " + molecule.baseName + " " + cubeKey + " to the cluster.", "light_green")

commandLineParser()