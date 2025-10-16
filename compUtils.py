#!/usr/bin/env python3
# Welcome to Computational Chemistry Utilities!
# Now bigger, harder, faster, and stronger than ever before!
# This package has been crafted lovingly through untold pain and suffering
# Last major commit to the project was 2025-10-15 (previously 2025-10-10)
# Last minor commit to the project was 2025-10-7

# Imports the various libraries needed for main() and each function()
import os
import argparse
import glob
import time
import subprocess
from termcolor import cprint
#import numpy
#import pandas
import regex
from contextlib import closing
from mmap import mmap, ACCESS_READ

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
    mixedBasisVariants = ["Gen", "GenECP", "gen", "genecp"]
    # Cube Keylists
    potCube = "Pot"
    denCube = "Den"
    valenceCube = "Val"
    spinCube = "Spin"
    stalkDuration = 120
    stalkFrequency = 5
    gaussianNonVariant = ["#SBATCH --nodes=1\n","\nmodule purge\nmodule load gaussian\n\n","export GAUSS_SCRDIR=$SLURM_SCRATCH\nulimit -s unlimited\nexport LC_COLLATE=C\n"]
    orcaNonVariant = ["\n# Load the module\nmodule purge\n","module load orca/6.0.1\n\n","# Copy files to SLURM_SCRATCH\n","for i in ${files[@]}; do\n","    cp $SLURM_SUBMIT_DIR/$i $SLURM_SCRATCH/$i\ndone\n\n","# cd to the SCRATCH space\n","cd $SLURM_SCRATCH\n\n","# run the job, $(which orca) is necessary\n","# finally, copy back gbw and prop files\n","cp $SLURM_SCRATCH/*.{gbw,prop} $SLURM_SUBMIT_DIR\n\n"]
    qChemNonVariant = []
    coreLineVariants = ["%nproc","%nprocshared","%pal"]
    ramLineVariants = ["%mem","%maxcore"]
    terminationVariants = ["normal termination","terminated normally","error termination"]
    memoryBuffer = 2

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
# Paired together for program identification
methodList = []
targetProgram = []
stalkingSet = set()

# NEW!! Attempting to keep track of everything related to a job in one central location
class Molecule:
    def __init__(self, fullPath, baseName, charge, multiplicity, coordinateList, extensionType, rootName):
        self.fullPath = fullPath
        self.baseName = baseName
        self.charge = charge
        self.multiplicity = multiplicity
        self.coordinateList = coordinateList
        self.extensionType = extensionType
        self.rootName = rootName

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
    parser.add_argument('-t','--test', type=str, help="Activates whatever function I'm trying to test.")
    parser.add_argument('-cu','--cube', type=str, help="Indicates the gimmeCubes functionality on a given Gaussian16 checkpoint file.")
    parser.add_argument('-st','--stalk', action='store_true', help="Activates job stalking.")

    # Figures out what the hell you told it to do
    args = parser.parse_args()

    global isStalking

    # Stalking flag first
    if args.stalk:
        isStalking = True

    # Run flag handling
    if args.run:
        # Compiles the entire list of files to run (built-in 'runall' capabilities)
        jobList = glob.glob(args.run)
        for job in jobList:
            baseName, extension = grabPaths(job)
            newMolecule = Molecule(job, baseName, 0, 0, 0, extension, baseName)
            runJob(newMolecule)

    if args.singlePoint:
        # Compiles the entire list of files to run
        jobList = glob.glob(args.singlePoint)
        for job in jobList:
            baseName, extension = grabPaths(job)
            charge, multiplicity = gaussianChargeFinder(job)
            coordList = getCoords(job,baseName + Defaults.coordExtension)
            newMolecule = Molecule(job, baseName, charge, multiplicity, coordList, extension, baseName)
            genSinglePoint(newMolecule)

    if args.bench:
        if canBench:
            jobList = glob.glob(args.bench)
            for job in jobList:
                baseName, extension = grabPaths(job)
                charge, multiplicity = gaussianChargeFinder(job)
                coordList = getCoords(job,baseName + Defaults.coordExtension)
                newMolecule = Molecule(job, baseName, charge, multiplicity, coordList, extension, baseName)
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
            newMolecule = Molecule(job, baseName, 0, 0, 0, extension, baseName)
            gimmeCubes(newMolecule, cubeOptions)

# Finally handle filename creation in one place to stop the infinite copypasta
def fileCreation(baseName, extensionType, extra):
    if not len(extra) == 0:
        fullFile = baseName + extra + extensionType
    else:
        fullFile = baseName + extensionType
    return fullFile

# A new fully pythonic solution to coordinate scraping, agnostic of the PERL bullshit on H2P
def getCoords(fileName, outputFileName):
    coordinateList = []
    atSymbol = {
        1: 'H', 2: 'He', 3: 'Li', 4: 'Be', 5: 'B', 6: 'C', 7: 'N', 8: 'O', 9: 'F', 10: 'Ne',
        11: 'Na', 12: 'Mg', 13: 'Al', 14: 'Si', 15: 'P', 16: 'S', 17: 'Cl', 18: 'Ar', 19: 'K',
        20: 'Ca', 21: 'Sc', 22: 'Ti', 23: 'V', 24: 'Cr', 25: 'Mn', 26: 'Fe', 27: 'Co', 28: 'Ni',
        29: 'Cu', 30: 'Zn', 31: 'Ga', 32: 'Ge', 33: 'As', 34: 'Se', 35: 'Br', 36: 'Kr',

        42: 'Mo', 44: 'Ru', 45: 'Rh', 46: 'Pd', 47: 'Ag', 48: 'Cd', 50: 'Sn', 51: 'Sb',
        53: 'I', 54: 'Xe', 77: 'Ir', 78: 'Pt', 79: 'Au', 80: 'Hg', 81: 'Tl', 82: 'Pb',
        83: 'Bi'
    }

    # Initialize local empty lists
    at = []
    X = []
    Y = []
    Z = []

    with open(fileName, 'r+') as inFile, open(outputFileName, 'w') as outputFile:
        with closing(mmap(inFile.fileno(), 0, access=ACCESS_READ)) as data:
            tableHeader = "                         Standard orientation:                         "
            tableBytes = tableHeader.encode()
            finalTableHeader = regex.search(tableBytes, data, regex.REVERSE)
            pointer = finalTableHeader.ends()
            data.seek(pointer[0])
            data.read(2)
            for index in range(4):
                data.readline()
            line = data.readline().decode().strip()
            while len(line.split()) > 2:
                # Extracts the Atomic Number, and X Y Z coordinates into their respective lists
                at.append(str(line.split()[1]))
                X.append(str(line.split()[3]))
                Y.append(str(line.split()[4]))
                Z.append(str(line.split()[5]))
                line = data.readline().decode().strip()

        outputFile.write(str(len(at))+"\nPointless Comment Line\n")
        for k in range(len(at)):
            # Ensures the list elements are integers for dictionary pairing
            at[k] = int(at[k])
            # Translates from Atomic Number to Atomic Symbol, along with ensuring all elements of each list are strings
            # Build the entire line to be written, and ensure it's properly formatted
            coordLine = str(atSymbol[at[k]]) + "   " + str(X[k]) + "   " + str(Y[k]) + "   " + str(Z[k]) + "\n"
            coordLine = coordLine.replace(' ', ' ')
            outputFile.write(coordLine)
            coordinateList.append(coordLine)
    return coordinateList

# Handles extensions so I don't have to copypasta this
def extensionGetter(method):
    programTarget = ""
    global fileExtension
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
    chargeFirstSub = chargeLine.strip().split(" ")[0]
    with open(geometryFile, 'r') as geomFile:
        # This iterator finds the charge and multiplicity automatically, no more need to specify them.
        while chargeFirstSub != "Charge":
            chargeLine = geomFile.readline()
            chargeFirstSub = chargeLine.strip().split(" ")[0]

        chargeSub = chargeLine.strip().split(" ")
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
        return baseName, extension
    else:
        cprint("Could not locate: " + fileName, "light_red")
        return None, None

def genBench(molecule):
    # First, make the original Single Point
    genSinglePoint(molecule)
    # Since methodFile is defined globally, no need to iterate a line to catch-up after genSinglePoint
    startTime = time.time()
    for index in range(1, len(methodLine)):
        molecule.extensionType = extensionGetter(methodLine[index])
        inputFile = fileCreation(molecule.rootName, molecule.extensionType, f"-{index}-" + methodLine[index].replace("(","").replace(")","") + Defaults.singlePointExtra)
        molecule.fullPath = inputFile
        molecule.baseName = molecule.rootName + f"-{index}-" + methodLine[index].replace("(","").replace(")","") + Defaults.singlePointExtra
        genFile(molecule,index)
        runJob(molecule)
    endTime = time.time()
    totalTime = round(endTime-startTime,2)
    cprint("Total time for non-SP benchmark generation is: " + str(totalTime) + " seconds.", "light_cyan")

def genSinglePoint(molecule):
    startTime = time.time()

    # Update molecule properties
    molecule.extensionType = extensionGetter(methodLine[0])
    inputFile = fileCreation(molecule.baseName, molecule.extensionType, Defaults.singlePointExtra)
    molecule.fullPath = inputFile
    molecule.baseName = molecule.baseName + Defaults.singlePointExtra

    # Calls the separate file generation method, feeds directly into runJob
    genFile(molecule, 0)
    runJob(molecule)
    endTime = time.time()
    totalTime = round(endTime - startTime,2)
    cprint("Total single point time is " + str(totalTime) + " seconds.", "light_cyan")

# Separate method for input file generation to improve code efficiency. No longer returns anything as path to input is previously stored
def genFile(molecule, index):
    inputFile = molecule.fullPath
    mixedBasis = False
    match molecule.extensionType:
        case Defaults.gaussianExtension:
            # No longer accesses the XYZ file due to Molecule coordinateList property
            with open(inputFile, 'w') as jobInput:
                # Sets the job's CPU and RAM
                jobCPU = str(Defaults.CPU)
                jobMem = str(Defaults.CPU * Defaults.memoryRatio)
                # Writes the standard Gaussian16 formatted opening
                jobInput.write("%nprocshared=" + jobCPU + "\n%mem=" + jobMem + "GB")
                # If the methodLine from benchmarking.txt is garbage, the calculation will fail. Not my fault.
                jobInput.write("\n# " + fullMethodLine[index] + "\nUseless Comment line\n\n")
                jobInput.write(molecule.charge + " " + molecule.multiplicity + "\n")
                # Iterates through the XYZ to scrape the coordinates (getCoords isn't efficient to call repeatedly,
                # and this is actually *much* more useful)
                for keyWord in fullMethodLine[index].split():
                    if keyWord in Defaults.mixedBasisVariants:
                        mixedBasis = True
                        break
                for line in molecule.coordinateList:
                    jobInput.write(line)
                if os.path.isfile("mixedbasis.txt") and mixedBasis:
                    jobInput.write("\n")
                    with open("mixedbasis.txt") as mixedBasisFile:
                        for line in mixedBasisFile:
                            jobInput.write(line)
                elif mixedBasis and not os.path.isfile("mixedbasis.txt"):
                    cprint("Mixed basis information not found. Aborting.", "light_red")
                    return
                jobInput.write("\n\n")

        case Defaults.orcaExtension:
            # Opens the job file
            with open(inputFile, 'w') as jobInput:
                # Sets the job's CPU and RAM
                jobCPU = str(Defaults.CPU)
                if methodLine == "DLPNO-CCSD(T)":
                    jobMem = str(Defaults.highMemoryRatio * 1000)
                else:
                    jobMem = str(Defaults.memoryRatio * 1000)
                # Writes the standard ORCA formatted opening
                jobInput.write("%pal nprocs " + jobCPU + "\nend" + "\n%maxcore " + jobMem)
                # If the methodLine from benchmarking.txt is garbage, the calculation will fail. Not my fault.
                jobInput.write("\n! " + fullMethodLine[index] + "\n")
                # ORCA is smart enough to read from an XYZ directly
                jobInput.write(f"* xyz {molecule.charge} {molecule.multiplicity} \n")
                for line in molecule.coordinateList:
                    jobInput.write(line)
                jobInput.write("\n*")

        case Defaults.qChemExtension:
            pass

# This routine is for job submission to the cluster
def runJob(molecule):
    # Iteration is now done in commandLineParser()
    # Sets up all the basic filenames for the rest of submission
    outputName = molecule.baseName + Defaults.outputExtension
    queueName = molecule.baseName + Defaults.queueExtension
    global isStalking
    global stalkingSet
    with open(molecule.fullPath, 'r+') as inputFile:
        coresLine = ""
        ramLine = ""
        firstFiveLines = []
        # Reads the first line of the file
        currentLine = inputFile.readline().strip()
        firstFiveLines.append(currentLine)

        # Craps out if the first line doesn't exist, or is entirely blank
        if not currentLine:
            cprint(f"Job file " + molecule.baseName + " is empty. Terminating submission attempt.", "light_red")
        if len(currentLine) == 0:
            cprint(f"First line of job file " + molecule.baseName + " is blank. Terminating submission attempt.", "light_red")

        for index in range(0,4):
            firstFiveLines.append(inputFile.readline().strip())

        match molecule.extensionType:
            case Defaults.gaussianExtension:
                # NEW!! Uses regex to search for substrings in any order in the top 5 lines of input. No more stickler formatting!
                for line in firstFiveLines:
                    if regex.search(Defaults.coreLineVariants[0],line) or regex.search(Defaults.coreLineVariants[1],line):
                        coresLine = line
                    if regex.search(Defaults.ramLineVariants[0],line):
                        ramLine = line
                if len(coresLine) == 0:
                    cpus = Defaults.CPU
                    cprint("Couldn't find CPU count in input file. Submitting instead according to Defaults.","light_red")
                else:
                    cpus = coresLine.strip().split("=")[1]
                if len(ramLine) == 0:
                    ram = cpus * Defaults.memoryRatio
                    jobRam = ram + Defaults.memoryBuffer
                    cprint("Couldn't find RAM count in input file. Submitting instead according to Defaults.","light_red")
                else:
                    ram = int(ramLine.strip().split("=")[1].replace("GB",""))
                    jobRam = ram + Defaults.memoryBuffer

                with open(queueName, 'w') as outputFile:
                    # Writes the CMD for submission
                    outputFile.write("#!/bin/bash -l\n")
                    outputFile.write("#SBATCH --job-name=" + str(molecule.baseName) + "\n")
                    outputFile.write("#SBATCH --output=" + outputName + "\n")
                    outputFile.write("#SBATCH --ntasks-per-node=" + str(cpus) + "\n")
                    outputFile.write("#SBATCH --mem=" + str(jobRam) + "GB\n")
                    outputFile.write("#SBATCH --time=" + Defaults.wallTime + ":00:00\n")
                    outputFile.write("#SBATCH --cluster=" + Defaults.cluster + "\n")
                    outputFile.write("#SBATCH --partition=" + Defaults.partition + "\n")
                    for nonVariantLine in Defaults.gaussianNonVariant:
                        outputFile.write(nonVariantLine)
                    outputFile.write("\ng16 < " + molecule.fullPath + "\n\n")

                os.system("sbatch " + queueName)
                #os.remove(queueName)
                cprint(f"Submitted job " + molecule.baseName + " to Gaussian16", "light_green")
                if isStalking:
                    molecule.fullPath = molecule.baseName + Defaults.outputExtension
                    stalkingSet.add((molecule.baseName,molecule.fullPath))

            case Defaults.orcaExtension:
                # NEW!! Uses regex to search for substrings in any order in the top 5 lines of input. No more stickler formatting!
                for line in firstFiveLines:
                    if regex.search(Defaults.coreLineVariants[2], line):
                        coresLine = line
                    if regex.search(Defaults.ramLineVariants[1], line):
                        ramLine = line
                if len(coresLine) == 0:
                    cpus = Defaults.CPU
                    cprint("Couldn't find CPU count in input file. Submitting instead according to Defaults.",
                           "light_red")
                else:
                    cpus = coresLine.strip().split()[2]
                if len(ramLine) == 0:
                    ram = cpus * Defaults.memoryRatio
                    jobRam = ram + Defaults.memoryBuffer
                    cprint("Couldn't find RAM count in input file. Submitting instead according to Defaults.",
                           "light_red")
                else:
                    ram = int(ramLine.strip().split()[1])/1000
                    jobRam = int(int(cpus)*ram + Defaults.memoryBuffer)

                with open(queueName, 'w') as outputFile:
                    # Writes the CMD for job submission
                    outputFile.write("#!/bin/bash -l\n")
                    outputFile.write("#SBATCH --job-name=" + str(molecule.baseName) + "\n")
                    outputFile.write("#SBATCH --output=" + outputName + "\n")
                    outputFile.write("#SBATCH --nodes=1\n")
                    outputFile.write("#SBATCH --mem=" + str(jobRam) + "GB\n")
                    outputFile.write("#SBATCH --ntasks-per-node=" + str(cpus) + "\n")
                    outputFile.write("#SBATCH --time=" + Defaults.wallTime + ":00:00\n")
                    outputFile.write("#SBATCH --cluster=" + Defaults.cluster + "\n")
                    outputFile.write("#SBATCH --partition=" + Defaults.partition + "\n")

                    # Now runs in ORCA 6.0.0 instead of 4.2.0 .
                    for index in range(0,3):
                        outputFile.write(Defaults.orcaNonVariant[index])
                    outputFile.write("files=(" + str(molecule.baseName + Defaults.orcaExtension) + ")\n")
                    for index in range(3,8):
                        outputFile.write(Defaults.orcaNonVariant[index])
                    outputFile.write("$(which orca) " + str(molecule.baseName + Defaults.orcaExtension) + "\n\n")
                    for index in range(8,10):
                        outputFile.write(Defaults.orcaNonVariant[index])

                os.system("sbatch " + queueName)
                #os.remove(queueName)
                cprint(f"Submitted job " + molecule.baseName + " to ORCA 6.0.1", "light_green")
                if isStalking:
                    molecule.fullPath = molecule.baseName + Defaults.outputExtension
                    stalkingSet.add((molecule.baseName,molecule.fullPath))

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
                    outputFile.write("#!/bin/bash -l\n")
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
                if isStalking:
                    molecule.fullPath = molecule.baseName + Defaults.outputExtension
                    stalkingSet.add((molecule.baseName,molecule.fullPath))

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
            queueFile.write("#!/bin/bash -l\n")
            queueFile.write("#SBATCH --job-name=" + molecule.baseName + "\n")
            queueFile.write("#SBATCH --output=" + outputName + "\n")
            queueFile.write("#SBATCH --ntasks-per-node=" + str(Defaults.CPU) + "\n")
            queueFile.write("#SBATCH --mem=" + str(jobRam) + "\n")
            queueFile.write("#SBATCH --time=" + Defaults.wallTime + ":00:00\n")
            queueFile.write("#SBATCH --cluster=" + Defaults.cluster + "\n")
            queueFile.write("#SBATCH --partition=" + Defaults.partition + "\n")
            for nonVariantLine in Defaults.gaussianNonVariant:
                queueFile.write(nonVariantLine)

            # Writes the specifics for running the Density Cube
            queueFile.write("cubegen 1 " + keyWord + " " + molecule.fullPath + " " + outputName + " 0""\n\n")

        os.system("sbatch " + queueName)
        os.remove(queueName)
        cprint(f"Submitted cube job " + molecule.baseName + " " + cubeKey + " to the cluster.", "light_green")

# For realsies this time
def jobStalking(jobSet, duration, frequency):
    startTime = time.time()
    # Prints queue in format of JOBNAME STATUS NODE/REASON START_TIME CURRENT_DURATION
    command = ["squeue -h --me --format='%25j %10T %18R %S %20M'"]
    finishedJobs = []
    while (time.time() - startTime) < duration * 60:
        stalkStatus = set()
        for job in jobSet:
            stalkStatus.add(job[0])
        stalker = subprocess.run(command, shell=True, capture_output=True)
        result = stalker.stdout.splitlines()
        for index in range(len(result)):
            # noinspection PyShadowingNames
            line = result[index].decode("utf-8")
            # noinspection PyTypeChecker
            result[index] = line
            # Adds job basename to stalkStatus for comparison
            stalkStatus.add(result[index].split()[0])

        for index in range(len(result)):
            match result[index].split()[1]:
                case "PENDING":
                    cprint("Job " + str(result[index].split()[0]) + " is currently pending. Expected start time is " + str(result[index].split()[3]), "light_yellow")
                    stalkStatus.remove(result[index].split()[0])
                case "RUNNING":
                    for job in jobSet:
                        convergeCriteria = "Unknown"
                        if job[0] == result[index].split()[0]:
                            if os.path.isfile(job[1]) and os.path.getsize(job[1]) > 0:
                                with open(job[1],'r+') as file:
                                    with closing(mmap(file.fileno(),0,access=ACCESS_READ)) as data:
                                        tableHeader = "         Item               Value     Threshold  Converged?"
                                        tableBytes = tableHeader.encode()
                                        finalTableHeader = regex.search(tableBytes, data, regex.REVERSE)
                                        if len(finalTableHeader.group().decode()) != 0:
                                            convergeCriteria = 0
                                            pointer = finalTableHeader.ends()
                                            data.seek(pointer[0])
                                            data.read(2)
                                            convergeMet = []
                                            for outdex in range(0,4):
                                                convergeLine = data.readline().decode()
                                                convergeMet.append(convergeLine.split()[4])
                                                convergeCriteria = convergeMet.count("YES")
                            cprint("Job " + str(result[index].split()[0]) + " is currently running, and has converged on " + str(convergeCriteria) + " out of 4 criteria. Current duration is " + str(result[index].split()[4]), "light_magenta")
                            stalkStatus.remove(result[index].split()[0])
                            break

        jobCopy = jobSet.copy()

        for job in jobCopy:
            if job[0] in stalkStatus and os.path.isfile(job[1]):
                with open(job[1], "r+") as file:
                    with closing(mmap(file.fileno(), 0, access=ACCESS_READ)) as data:
                        for termination in Defaults.terminationVariants:
                            termBytes = termination.encode()
                            termLine = regex.search(termBytes, data, regex.IGNORECASE)
                            if termLine is not None:
                                finishedJobs.append((job[0], termination))
                                break
                jobSet.remove(job)

        for job in finishedJobs:
            if job[1] == Defaults.terminationVariants[0] or job[1] == Defaults.terminationVariants[1]:
                cprint("Job " + str(job[0]) + " has encountered " + Defaults.terminationVariants[0],"light_green")
            if job[1] == Defaults.terminationVariants[2]:
                cprint("Job " + str(job[0]) + " has encountered " + Defaults.terminationVariants[2], "light_red")

        if len(jobSet) == 0:
            cprint("All jobs tagged for stalking have finished.","light_cyan")
            break

        cprint("Waiting " + str(frequency*60) + " seconds to ping the queue again.","light_blue")
        time.sleep(frequency * 60)

    if (time.time() - startTime) > duration * 60:
        cprint("Job stalking terminated by timeout. Your jobs are still running.","light_red")
        cprint("Consider editing the default stalk duration and frequency if your jobs regularly timeout.","light_red")

commandLineParser()

if isStalking:
    jobStalking(stalkingSet, Defaults.stalkDuration, Defaults.stalkFrequency)
