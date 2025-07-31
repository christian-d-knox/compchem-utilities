#!/usr/bin/env python3
# Welcome to Computational Chemistry Utilities (Rewritten) v0.X
# This package has been crafted lovingly through pain and suffering
# Last major commit to the project was 2025-07-30

# Imports the various libraries needed for main() and each function()
import os
import argparse
import glob
import time
import subprocess
from termcolor import cprint

# Set your defaults HERE
binDirectory = "/ihome/pliu/cdk67/bin/"
defaultCPU = 12
defaultWallTime = "24"
defaultMemoryRatio = 2
defaultDLPNORatio = 6
defaultCluster = "smp"
# Change partition to pliu for SMP if preferred
defaultPartition = "smp"
defaultSinglePointExtra = "_SP"
coordExtension = ".xyz"
defaultGaussianExtension = ".gjf"
defaultOrcaExtension = ".inp"
defaultQChemExtension = ".in"
# Hardcoded default targets. Edit CAREFULLY.
defaultMethodNames = ["B3LYP","M062X","M06","M06L","B2PLYP","wB97XD","DLPNO-CCSD(T)"]
defaultTargetProgram = ["G16","G16","G16","G16","G16","G16","O"]
defaultMethod = "M062X"
defaultMethodLine = "M062X 6-311+G(d,p)"

# Defines global variables for use in various functions
fileNames = []
fullPaths = []
totalJobList = []
totalOutputs = []
methodNames = []
targetProgram = []
isStalking = False
isCustomTarget = True
canBench = True
isDefaultMethod = False
methodList = []
fileExtension = ""
coordinateScrapeTime = 0

# Actually perform some operations globally for more code efficiency
if os.path.isfile(os.path.join(binDirectory, "benchmarking.txt")):
    methodFile = open(os.path.join(binDirectory, "benchmarking.txt"), "r")
else:
    canBench = False
    isDefaultMethod = True
    cprint("Notice: Could not find benchmarking.txt in ~/bin/.", "light_red")
    cprint("Benchmarking functionality is unavailable without requisite file. Please create your own or download the template from GitHub.", "light_red")
if os.path.isfile(os.path.join(binDirectory, "programs.txt")):
    with open(os.path.join(binDirectory, "programs.txt"), 'r') as programFile:
        for targetLine in programFile:
            currentSubs = targetLine.strip().split(" ")
            methodNames.append(currentSubs[0])
            targetProgram.append(currentSubs[1])
        methodList = methodNames
else:
    isCustomTarget = False
    methodList = defaultMethodNames
    targetProgram = defaultTargetProgram
    cprint("Notice: Could not find programs.txt in ~/bin/.", "light_red")
    cprint("Defaulting to hardcoded method targets.", "light_red")

# Defines all the terminal flags the program can accept
def commandLineParser():
    parser = argparse.ArgumentParser(description="The main command line argument parser for flag handling")

    #The various flags for defining the features of this utility
    parser.add_argument('-r', '--run', type=str, help="Indicates the 'run' subroutine for the listed file(s)")
    parser.add_argument('-sp', '--singlePoint', type=str, help="Indicates the 'single point' subroutine for the listed file(s)")
    parser.add_argument('-b', '--bench', type=str, help="Indicates the 'benchmark' subroutine, for creating a single point becnhmark on the listed file(s)")
    parser.add_argument('-c', '--checkpoint', action='store_true', help="Enables checkpoint functionality for the 'run' routine")
    parser.add_argument('-t','--test',type=str,help="Activates whatever function I'm trying to test.")

    # Figures out what the hell you told it to do
    args = parser.parse_args()

    # Run flag handling
    if args.run:
        # Compiles the entire list of files to run (built-in 'runall' capabilities)
        fileNames = glob.glob(args.run)
        runJob(grabPaths(fileNames))

    if args.singlePoint:
        # Compiles the entire list of files to run
        fileNames = glob.glob(args.singlePoint)
        genSinglePoint(grabPaths(fileNames))

    if args.bench and canBench:
        fileNames = glob.glob(args.bench)
        genBench(grabPaths(fileNames))
    elif args.bench and not canBench:
        cprint("Notice: Benchmarking is unavailable without requisite file. Please create your own or download the template from GitHub.", "light_red")

    if args.test:
        fileNames = glob.glob(args.test)
        for fileName in fileNames:
            outputFileName = fileCreation(grabPaths(fileName),coordExtension,"")
            coordinateScraper(fileName,outputFileName)


# NEW!! Finally handle filename creation in one place to stop the infinite copypasta
def fileCreation(baseFile,extensionType,extra):
    baseName, extension = os.path.splitext(baseFile)
    baseName = os.path.basename(baseName)
    if not len(extra) == 0:
        fullFile = baseName + extra + extensionType
    else:
        fullFile = baseName + extensionType
    return fullFile

# NEW!! Finally PROPERLY executes the PERL script and pipes output into Python for post-processing
def coordinateScraper(fileName, outputFileName):
    startTime = time.time()
    scrapedCoords = subprocess.run(['pg2xyz.sh',fileName], capture_output=True, text=True, check=True)
    coordList = scrapedCoords.stdout
    coordList = coordList.splitlines()
    with open(outputFileName, 'w') as outputFile:
        outputFile.write(str(len(coordList)) + "\nPointless Comment Line\n")
        for line in coordList:
            outputFile.write(line + "\n")
    endTime = time.time()
    coordinateScrapeTime = round(endTime-startTime,2)
    cprint("Time taken to scrape coordinates by PERL script is " + str(coordinateScrapeTime) + " seconds.", "light_cyan")

# NEW!! Handles extensions so I don't have to copypasta this
def extensionGetter(method):
    programTarget = ""
    for x in range(len(methodList)):
        if method == methodList[x]:
            programTarget = targetProgram[x]
    match programTarget:
        case "G16":
            fileExtension = defaultGaussianExtension
        case "O":
            fileExtension = defaultOrcaExtension
        case "Q":
            fileExtension = defaultQChemExtension
        case _:
            cprint("Notice: Intended method is not specified in programs file nor hardcoded. Defaulting to Gaussian16.", "light_red")
            fileExtension = defaultGaussianExtension
    return fileExtension

# NEW!! Gaussian16 Charge Finder in its own method
def gaussianChargeFinder(geometryFile):
    chargeLine = ""
    chargeSub = chargeLine.strip().split(" ")
    chargeFirstSub = chargeSub[0]

    # This iterator finds the charge and multiplicity automatically, no more need to specify them.
    while chargeFirstSub != "Charge":
        chargeLine = geometryFile.readline()
        chargeSub = chargeLine.strip().split(" ")
        chargeFirstSub = chargeSub[0]

    # This chunk handles the special case where a stupid non-breaking space is used for neutral charges?
    if chargeSub[2] == '':
        del chargeSub[2]
    charge = chargeSub[2]
    multiplicity = chargeSub[5]
    inputChargeLine = charge + " " + multiplicity
    return inputChargeLine

# This subroutine allows for easily compiling file paths for multiple methods
def grabPaths(fileList):
    fullPaths = [os.path.join(os.getcwd(), file) for file in fileList]
    # Basic-tier error-handling for nonexistent files
    missingFiles = [file for file in fileList if not os.path.isfile(os.path.join(os.getcwd(), file))]
    if missingFiles:
        cprint(f"Could not locate: {', '.join(missingFiles)}", "light_red")
    return fullPaths

def genBench(fileList):
    for x in range(len(fileList)):
        # First, make the original Single Point
        genSinglePoint(fileList[x])
        # NEW!! Since methodFile is defined globally, no need to iterate a line to catch-up after genSinglePoint
        # Copy over code from genSinglePoint
        geometryFile = open(fileList[x])
        chargeSubs = gaussianChargeFinder(geometryFile).split()
        charge = chargeSubs[0]
        multiplicity = chargeSubs[1]

        coordFile = fileCreation(fileList[x],coordExtension,"")
        coordFullPath = os.path.abspath(coordFile)

        for methodLine in methodFile:
            methodSubs = methodLine.strip().split(" ")
            methodFirstSub = methodSubs[0]
            extension = extensionGetter(methodFirstSub)
            inputFile = fileCreation(fileList[x],extension,methodFirstSub)
            runJob(genFile(inputFile, coordFullPath, methodLine, charge, multiplicity))

def genSinglePoint(fileList):
    for x in range(len(fileList)):
        startTime = time.time()
        # NEW!! Just open the damn file
        geometryFile = open(fileList[x])

        # Handles the creation of the output crap
        if not isDefaultMethod:
            methodLine = methodFile.readline()
            methodFirstSub = methodLine.strip().split(" ")[0]
        else:
            methodLine = defaultMethodLine
            methodFirstSub = defaultMethod

        inputFile = fileCreation(fileList[x], extensionGetter(methodFirstSub), defaultSinglePointExtra)
        coordFile = fileCreation(fileList[x],coordExtension,"")
        coordFullPath = os.path.abspath(coordFile)

        # Passes the XYZ into getCoords
        #getCoords(fileList[x], coordFullPath)
        coordinateScraper(fileList[x],coordFullPath)
        chargeSubs = gaussianChargeFinder(geometryFile).split()
        charge = chargeSubs[0]
        multiplicity = chargeSubs[1]
        # Calls the separate file generation method, feeds directly into runJob
        genFile(inputFile, coordFullPath, methodLine, charge, multiplicity)
        endTime = time.time()
        totalTime = round(endTime - startTime,2)
        singlePointTime = totalTime - coordinateScrapeTime
        cprint("Total single point time is " + str(totalTime) + " seconds.", "light_cyan")
        cprint("Corrected time for non-coordinate code is " + str(singlePointTime) + " seconds.", "light_cyan")

# Separate method for input file generation to improve code efficiency, returns the name of the input
def genFile(inputFile, coordFile, methodLine, charge, multiplicity):
    baseName, extension = os.path.splitext(inputFile)
    extension = extension.strip().split(".")[-1]
    outputFullPath = os.path.abspath(inputFile)
    outputList = []
    match extension:
        case "gjf":
            # Opens the XYZ Files that was just created in order to read-in coordinates and the actual job file
            with open(coordFile, 'r') as jobCoords, open(outputFullPath, 'w') as jobInput:
                # Sets the job's CPU and RAM
                jobCPU = str(defaultCPU)
                jobMem = str(defaultCPU * defaultMemoryRatio)
                # Writes the standard Gaussian16 formatted opening
                jobInput.write("%nprocshared=" + jobCPU + "\n%mem=" + jobMem +"GB")
                # If the methodLine from benchmarking.txt is garbage, the calculation will fail. Not my fault.
                jobInput.write("\n# " + methodLine + "\nUseless Comment line\n\n")
                jobInput.write(charge + " " + multiplicity + "\n")
                # Iterates through the XYZ to scrape the coordinates (getCoords isn't efficient to call repeatedly,
                # and this is actually *much* more useful)
                for line in jobCoords:
                    # Skips the atoms and blank line in the XYZ because that will break shit
                    if len(line.split()) > 3:
                        jobInput.write(line)
                jobInput.write("\n\n")

        case "inp":
            # Opens the job file
            with open(outputFullPath, 'w') as jobInput:
                # Sets the job's CPU and RAM
                jobCPU = str(defaultCPU)
                if methodLine == "DLPNO-CCSD(T)":
                    jobMem = str(defaultCPU * defaultDLPNORatio * 1000)
                else:
                    jobMem = str(defaultCPU * defaultMemoryRatio * 1000)
                # Writes the standard ORCA formatted opening
                jobInput.write("%pal nprocs " + jobCPU + "\nend" + "\n%maxcore " + jobMem)
                # If the methodLine from benchmarking.txt is garbage, the calculation will fail. Not my fault.
                jobInput.write("\n! " + methodLine)
                # ORCA is smart enough to read from an XYZ directly
                jobInput.write(f"* xyzfile {charge} {multiplicity} {coordFile} *")

        case "in":
            # Put crap here later
            thing = 0
    outputList.append(outputFullPath)
    return outputList

# This routine is for job submission to the cluster
def runJob(fileList):
    totalJobs = 0
    # Iterates over the entirety of the array passed into the subroutine
    for x in range(len(fileList)):
        # The following is more or less copied directly from my modified qg16
        # Sets up all the basic filenames for the rest of submission
        baseName, extension = os.path.splitext(fileList[x])
        baseName = os.path.basename(baseName)
        extension = extension.strip().split(".")[-1]
        outputName = baseName + ".out"
        queueName = baseName + ".cmd"

        with open(fileList[x], 'r+') as inputFile, open(queueName, 'w') as outputFile:
            #while True:
            # Reads the first line of the file
            currentLine = inputFile.readline()
            currentLine = currentLine.strip()

            # Craps out if the first link doesn't exist, or is entirely blank
            if not currentLine:
                cprint(f"Job file " + baseName + " is empty. Terminating submission attempt.", "light_red")
                break
            if len(currentLine) == 0:
                cprint(f"First line of job file " + baseName + " is blank. Terminating submission attempt.", "light_red")
                break

            ## This chunk analyzes the first line of the input to help determine what gets submitted where
            ## Analysis is ENTIRELY BASED on the first line, PLEASE specify the cores accordingly
            #currentLine = currentLine.strip()
            #subLine = currentLine.split('=')
            #firstSubLine = subLine[0].lower()

            match extension:
                case "gjf":
                    subLine = currentLine.split('=')
                    firstSubLine = subLine[0].lower()
                    if firstSubLine == '%nprocshared'or firstSubLine =='%nproc':
                        cpus = int(subLine[1])
                        print("Successfully read " + str(cpus) + " cores from " + baseName + ".gjf")

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
                            ram = cpus * defaultMemoryRatio
                            jobRam = ram + 2
                            print("Couldn't find RAM amount and will submit with " + str(ram) + " + 2 GB instead")
                    else:
                        cpus = defaultCPU
                        ram = cpus * defaultMemoryRatio
                        jobRam = ram + 2

                    # Writes the CMD for submission
                    outputFile.write("#!/usr/bin/env bash\n")
                    outputFile.write("#SBATCH --job-name=" + str(baseName) + "\n")
                    outputFile.write("#SBATCH --output=" + outputName + "\n")
                    outputFile.write("#SBATCH --ntasks-per-node=" + str(cpus) + "\n")
                    outputFile.write("#SBATCH --mem=" + str(jobRam) + "GB\n")
                    outputFile.write("#SBATCH --time=" + str(defaultWallTime) + ":00:00\n")
                    outputFile.write("#SBATCH --cluster=" + defaultCluster + "\n")
                    outputFile.write("#SBATCH --partition=" + defaultPartition + "\n")
                    outputFile.write("#SBATCH --nodes=1\n")

                    outputFile.write("\nmodule purge\nmodule load gaussian\n\n")
                    outputFile.write("export GAUSS_SCRDIR=$SLURM_SCRATCH\n")
                    outputFile.write("ulimit -s unlimited\n")
                    outputFile.write("export LC_COLLATE=C\n")
                    outputFile.write("\ng16 < " + fileList[x] + "\n\n")
                    outputFile.close()

                    os.system("sbatch " + queueName)
                    os.remove(queueName)
                    cprint(f"Submitted job " + baseName + " to Gaussian16", "light_green")

                case "inp":
                    subLine = currentLine.split()
                    firstSubLine = subLine[0].lower()
                    # ORCA submission
                    if firstSubLine == '%pal':
                        cpus = int(subLine[2])
                        print("Successfully read " + str(cpus) + " cores from " + baseName + ".inp")

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
                        cpus = defaultCPU
                        ram = cpus * defaultMemoryRatio
                        jobRam = ram + 2

                    # Writes the CMD for job submission
                    outputFile.write("#!/bin/bash\n")
                    outputFile.write("#SBATCH --job-name=" + str(baseName) + "\n")
                    outputFile.write("#SBATCH --output=" + outputName + "\n")
                    outputFile.write("#SBATCH --nodes=1\n")
                    outputFile.write("#SBATCH --mem=" + str(jobRam) + "GB\n")
                    outputFile.write("#SBATCH --ntasks-per-node=" + str(cpus) + "\n")
                    outputFile.write("#SBATCH --time=" + str(defaultWallTime) + ":00:00\n")
                    outputFile.write("#SBATCH --cluster=" + defaultCluster + "\n")
                    outputFile.write("#SBATCH --partition=" + defaultPartition + "\n")

                    outputFile.write("\n# Load the module\nmodule purge\n")
                    # New!! Now runs in ORCA 6.0.0 instead of 4.2.0 .
                    outputFile.write("module load gcc/10.2.0 gcc/4.8.5 openmpi/4.1.1 orca/6.0.0\n\n")
                    outputFile.write("# Copy files to SLURM_SCRATCH\n")
                    outputFile.write("files=(" + str(baseName+".inp") + ")\n")
                    outputFile.write("for i in ${files[@]}; do\n")
                    outputFile.write("    cp $SLURM_SUBMIT_DIR/$i $SLURM_SCRATCH/$i\n")
                    outputFile.write("done\n\n")
                    outputFile.write("# cd to the SCRATCH space\n")
                    outputFile.write("cd $SLURM_SCRATCH\n\n")
                    outputFile.write("# run the job, $(which orca) is necessary\n")
                    outputFile.write("$(which orca) " + str(baseName+".inp") + "\n\n")
                    outputFile.write("# finally, copy back gbw and prop files\n")
                    outputFile.write("cp $SLURM_SCRATCH/*.{gbw,prop} $SLURM_SUBMIT_DIR\n\n")
                    outputFile.close()

                    os.system("sbatch " + queueName)
                    os.remove(queueName)
                    cprint(f"Submitted job " + baseName + " to ORCA 6.0.0", "light_green")

                case "in":
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
                            print("Successfully read " + str(cpus) + " cores from " + baseName + ".in")
                            # Sets the job to run with a default amount of RAM. Does not update the input because neither of us could be bothered to care.
                    else:
                        cpus = defaultCPU
                        ram = cpus * defaultMemoryRatio
                        jobRam = ram + 2
                        print("Couldn't find RAM amount and will submit with " + str(ram) + " + 2 GB instead")

                    # Writes queueFile for submission to the cluster
                    outputFile.write("#!/usr/bin/env bash\n")
                    outputFile.write("#SBATCH --job-name=" + baseName + "\n")
                    outputFile.write("#SBATCH --output=" + outputName + "\n")
                    outputFile.write("#SBATCH --ntasks-per-node=" + str(cpus) + "\n")
                    outputFile.write("#SBATCH --mem=" + str(jobRam) + "\n")
                    outputFile.write("#SBATCH --time=" + str(defaultWallTime) + ":00:00\n")
                    outputFile.write("#SBATCH --cluster=" + defaultCluster + "\n")
                    outputFile.write("#SBATCH --partition=" + defaultPartition + "\n")
                    outputFile.write("#SBATCH --nodes=1\n\n")

                    outputFile.write("module purge\n")
                    outputFile.write("module load qchem/6.3.0-pliu\n\n\n")
                    # This crap was in the original and I have no clue if it's still necessary
                    # output.write("export QCSCRATCH=$LOCAL\n")
                    # output.write("export QC=/ihome/pliu/xiq23/qchem/qchem_for_peng_20151014\n")
                    # output.write("export PATH=$PATH:$QC/bin\n")
                    # output.write("export QCAUX=/ihome/pliu/xiq23/qchem/qcaux4\n")
                    outputFile.write("# Change to working directory\n")
                    outputFile.write("cp $SLURM_SUBMIT_DIR/" + fileList[x] + " $SLURM_SCRATCH\n")
                    outputFile.write("cd $SLURM_SCRATCH\n\n")
                    # No clue what this line is and if it's needed, it's not in the Q-Chem documentation
                    outputFile.write("df -h\n")

                    # Re-wrote the script in order to make this line *actually* match the Q-Chem documentation
                    # Re-wrote to allow nthreads to match ncpu since each thread only runs on 1 CPU (see Q-Chem manual)
                    # Do NOT specify outfile, it LITERALLY breaks shit for some reason
                    outputFile.write("qchem -slurm -nt " + str(cpus) + " " + fileList[x] + " " + "\n")
                    outputFile.write("du -h\n\n")
                    outputFile.close()

                    os.system("sbatch " + queueName)
                    os.remove(queueName)
                    cprint(f"Submitted job " + baseName + " to Q-Chem 6.3", "light_green")

commandLineParser()