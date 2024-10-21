#!/usr/bin/env python3
# Welcome to Computational Chemistry Utilities v0.9
# This package has been crafted through pain and suffering
# Last major commit to the project was 2024-10-16

# Imports the various libraries needed for main() and each function()
import os
import argparse
import glob
import time

# Set your defaults HERE
binDirectory = "/ihome/pliu/cdk67/bin/"
defaultCPU = 12
defaultWallTime = "24"
# Change partition to pliu for SMP if preferred
defaultPartition = "smp"
defaultStalk = 300 # Unit is in Seconds

# Defines global variables for use in various functions
fileNames = []
fullPaths = []
totalJobList = []
totalOutputs = []
isStalking = False

print("I loaded!!")

# Defines all the terminal flags the program can accept
def commandLineParser():
    parser = argparse.ArgumentParser(description="The main command line argument parser for flag handling")

    #The various flags for defining the features of this utility
    parser.add_argument('-r', '--run', type=str, help="Indicates the 'run' subroutine for the listed file(s)")
    parser.add_argument('-sp', '--singlePoint', type=str, help="Indicates the 'single point' subroutine for the listed file(s)")
    parser.add_argument('-b', '--bench', type=str, help="Indicates the 'benchmark' subroutine, for creating a single point becnhmark on the listed file(s)")
    parser.add_argument('-st', '--stalk', action='store_true' , help="Indicates the 'stalking' subroutine, which will watch the jobs currently running based on their outputs, and update the terminal periodically")
    parser.add_argument('-c', '--checkpoint', action='store_true', help="Enables checkpoint functionality for the 'run' routine")
    parser.add_argument('-i', '--install', help="Indicates the 'install' subroutine, to install any dependencies CompUtils relies upon")
    parser.add_argument('-t', '--test', help="Temporary testing method")

    # Figures out what the hell you told it to do
    args = parser.parse_args()

    isStalking = args.stalk

    # Run flag handling
    if args.run:
        print("I saw the run flag!")
        # Compiles the entire list of files to run (built-in 'runall' capabilities)
        fileNames = glob.glob(args.run)
        print("So you want me to run on these files?" + str(fileNames[-1]))
        runJob(grabPaths(fileNames), isStalking)

    if args.singlePoint:
        print("I saw the single point flag!")
        # Compiles the entire list of files to run
        fileNames = glob.glob(args.singlePoint)
        print("So you want me to run on these files?" + str(fileNames[-1]))
        genSinglePoint(grabPaths(fileNames), isStalking)

    if args.bench:
        print("Benchmarking time! ... Ugh...")
        fileNames = glob.glob(args.bench)
        print("So you want me to run on these files?" + str(fileNames[-1]))
        genBench(grabPaths(fileNames), isStalking)

# This subroutine allows for easily compiling file paths for multiple methods
def grabPaths(fileList):
    fullPaths = [os.path.join(os.getcwd(), file) for file in fileList]
    # Basic-tier error-handling for nonexistent files
    missingFiles = [file for file in fileList if not os.path.isfile(os.path.join(os.getcwd(), file))]
    if missingFiles:
        print(f"Could not locate: {', '.join(missingFiles)}")
    return fullPaths

def genBench(fileList, isStalking):
    for x in range(len(fileList)):
        print("Time to make some benchmarks!")
        # First, make the original Single Point
        genSinglePoint(fileList)
        # Copy over code from genSinglePoint
        methodFile = open(os.path.join(binDirectory, "benchmarking.txt"), "r")
        methodFile.readline()
        geometryFile = open(fileList[x])
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
        baseName, extension = os.path.splitext(fileList[x])
        baseName = os.path.basename(baseName)
        coordFile = baseName + ".xyz"
        coordFullPath = os.path.abspath(coordFile)

        for methodLine in methodFile:
            methodSubs = methodLine.strip().split(" ")
            methodFirstSub = methodSubs[0]
            tempName = baseName + "_" + methodFirstSub
            runJob(genFile(tempName, coordFullPath, methodLine, methodFirstSub, charge, multiplicity), isStalking)

def genSinglePoint(fileList, isStalking):
    print("Ideally, I'm making a single point file now")
    for x in range(len(fileList)):
        # First checks for benchmarking.txt in the /bin
        if os.path.isfile(os.path.join(binDirectory, "benchmarking.txt")):
            print("Found the methods file!")
            methodFile = open(os.path.join(binDirectory, "benchmarking.txt"), "r")
            geometryFile = open(fileList[x])
        else:
            print("Couldn't find your methods!")
            return

        # Handles the creation of the output crap
        baseName, extension = os.path.splitext(fileList[x])
        baseName = os.path.basename(baseName)
        baseName = baseName + "_SP"
        tempName = baseName.replace("_SP","")
        coordFile = tempName + ".xyz"

        # This chunk helps handle the special case of doing DLPNO in ORCA *specifically*
        methodLine = methodFile.readline()
        methodSubs = methodLine.strip().split(" ")
        methodFirstSub = methodSubs[0]

        chargeLine = ""
        chargeSub = chargeLine.strip().split(" ")
        chargeFirstSub = chargeSub[0]

        # This iterator finds the charge and multiplicity automatically, no more need to specify them.
        while chargeFirstSub != "Charge":
            chargeLine = geometryFile.readline()
            chargeSub = chargeLine.strip().split(" ")
            chargeFirstSub = chargeSub[0]
        print("I found the Charge and Multiplicity!")
        print(chargeSub)

        # This chunk handles the special case where a stupid non-breaking space is used for neutral charges?
        if chargeSub[2] == '':
            del chargeSub[2]
        print(chargeSub)
        charge = chargeSub[2]
        print("Charge is " + str(charge))
        multiplicity = chargeSub[5]
        print("Multiplicity is " + str(multiplicity))
        # Write the charge and multiplicity to the XYZ file, then closes it
        # PROBLEMS occur when the file is not closed before passing it
        coordFullPath = os.path.abspath(coordFile)

        # Passes the XYZ into getCoords
        getCoords(fileList[x], coordFullPath)
        print("I wrote the coordinates to an XYZ file for your convenience!")

        print(methodFirstSub)

        # Calls the separate file generation method, feeds directly into runJob
        runJob(genFile(baseName, coordFullPath, methodLine, methodFirstSub, charge, multiplicity), isStalking)

# Separate method for input file generation to improve code efficiency, returns the name of the input
def genFile(baseName, coordFile, methodLine, methodFirstSub, charge, multiplicity):
    # For Gaussian16 jobs
    if methodFirstSub != "DLPNO-CCSD(T)":
        print("G16 Submission!")
        outputName = baseName + ".gjf"
        outputFullPath = os.path.abspath(outputName)

        # Opens the XYZ Files that was just created in order to read-in coordinates and the actual job file
        with open(coordFile, 'r') as jobCoords, open(outputFullPath, 'w') as jobInput:
            # Sets the job's CPU and RAM
            jobCPU = str(defaultCPU)
            jobMem = str(defaultCPU * 2)
            # Writes the standard Gaussian16 formatted opening
            jobInput.write("%nprocshared=" + jobCPU)
            jobInput.write("\n%mem=" + jobMem +"GB")
            # If the methodLine from benchmarking.txt is garbage, the calculation will fail
            jobInput.write("\n# " + methodLine)
            # Gaussian16 will fail without a comment line
            jobInput.write("\nUseless Comment line\n\n")
            jobInput.write(charge + " " + multiplicity + "\n")
            # Iterates through the XYZ to scrape the coordinates (getCoords isn't efficient to call repeatedly,
            # and this is actually *much* more useful)
            for line in jobCoords:
                # Skips the atoms and blank line in the XYZ because that will break shit
                if len(line.split()) > 3:
                    jobInput.write(line)
            jobInput.write("\n\n")
        jobCoords.close()
        jobInput.close()

    # Handles the special case of doing DLPNO in ORCA
    else:
        print("ORCA submission!")
        outputName = baseName + ".inp"
        outputFullPath = os.path.abspath(outputName)

        # Opens the job file
        with open(outputFullPath, 'w') as jobInput:
            # Sets the job's CPU and RAM
            jobCPU = str(defaultCPU)
            jobMem = str(6000)
            # Writes the standard ORCA formatted opening
            jobInput.write("%pal nprocs " + jobCPU)
            jobInput.write("\nend")
            jobInput.write("\n%maxcore " + jobMem)
            # If the methodLine from benchmarking.txt is garbage, the calculation will fail
            jobInput.write("\n! " + methodLine)
            # ORCA is smart enough to read from an XYZ directly
            jobInput.write(f"* xyzfile {charge} {multiplicity} {coordFile} *")
        jobInput.close()
    print(outputFullPath)
    outputList = [outputFullPath]
    return outputList

# Houkmol XYZ file Generator
# Originally scripted in AWK by Jan Lanbowski, translated to PERL and hacked by Paul Ha-Yeon Cheong
# Completely (painstakingly) re-written by Christian Drew Knox in Python
def getCoords(fileName, outputFileName):
    print("I'm in getCoords now!")

    # Atomic Symbol dictionary for file creation
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

    # This opens both the *.out and the *.xyz files for reading and writing
    with open(os.path.abspath(fileName), 'r') as inFile, open(os.path.abspath(outputFileName), 'w') as outputFile:
        # Seek to the END of the *.out
        inFile.seek(0, 2)
        position = inFile.tell()
        # Initialize the loop termination condition
        foundCoords = False

        # Iterate through the file BACKWARDS to find the final instance of the atomic coordinates
        while foundCoords != True and position > 0:
            position -= 1
            if position >= 0:
                inFile.seek(position)
            line = inFile.readline()
            line = line.strip()
            lineSubs = line.split(" ")
            # All coordinate sections have a header like this (Input orientation?)
            if lineSubs[0] == 'Standard' and lineSubs[1] == 'orientation:':
                print("Found the coordinates!")
                # Signals for loop termination once coordinates are extracted
                foundCoords = True
                # Skips the stupid lines between header and data
                for _ in range(4):
                    inFile.readline()
                # Pre-emptively read the first data line before the loop. Doing it the other way around breaks it.
                line = inFile.readline()
                line = line.strip()
                lineSubs = line.split()
                while len(lineSubs) > 2:
                    #print(len(lineSubs))
                    #print(lineSubs)
                    # Extracts the Atomic Number, and X Y Z coordinates into their respective lists
                    at.append(str(lineSubs[1]))
                    #print("Atom number is " + str(lineSubs[1]))
                    X.append(str(lineSubs[3]))
                    #print("X coord is " + str(lineSubs[3]))
                    Y.append(str(lineSubs[4]))
                    #print("Y coord is " + str(lineSubs[4]))
                    Z.append(str(lineSubs[5]))
                    #print("Z coord is " + str(lineSubs[5]))
                    # Reads the next line of data within the loop
                    line = inFile.readline()
                    line = line.strip()
                    lineSubs = line.split()
                print("Successfully iterated through the whole file")
            elif line:
                # Skips lines regardless of if they contain any text or not
                position -= 1
                inFile.seek(position)

        print(f"Output is {outputFileName}")
        # This loops through the entire coordinate list to write to the XYZ file
        outputFile.write(str(len(at))+"\nPointless Comment Line")
        for k in range(len(at)):
            # Ensures the list elements are integers for dictionary pairing
            at[k] = int(at[k])
            # Translates from Atomic Number to Atomic Symbol, along with ensuring all elements of each list are strings
            atomSym = str(atSymbol[at[k]])
            xCoord = str(X[k])
            yCoord = str(Y[k])
            zCoord = str(Z[k])
            # Build the entire line to be written, and ensure it's properly formatted
            expectedOutput = atomSym + "   " + xCoord + "   " + yCoord + "   " + zCoord
            expectedOutput = expectedOutput.replace(' ', ' ')
            #print(expectedOutput)
            outputFile.write("\n " + expectedOutput)
        # Close XYZ upon termination
        outputFile.close()

# This routine is for job submission to the cluster
def runJob(fileList, isStalking):
    print("SUPPOSEDLY I'm in the run routine now!!")
    totalJobs = 0
    # Iterates over the entirety of the array passed into the subroutine
    for x in range(len(fileList)):
        # The following is more or less copied directly from my modified qg16
        # Sets up all the basic filenames for the rest of submission
        print(fileList[x])
        baseName, extension = os.path.splitext(fileList[x])
        baseName = os.path.basename(baseName)
        outputName = baseName + ".out"
        checkName = baseName + ".chk"
        queueName = baseName + ".cmd"

        inputFile = open(fileList[x], "r+")
        print("I opened " + fileList[x] + "!!")
        outputFile = open(queueName, "w")
        print("I opened " + queueName + "!!")

        #while True:
        # Reads the first line of the file
        currentLine = inputFile.readline()
        print("I just read " + currentLine + "!!")

        # Craps out if the first link doesn't exist, or is entirely blank
        if not currentLine:
            print("Is there even a line??")
            break
        if len(currentLine) == 0:
            print("Yo this line's BLANK, dawg.")
            break

        # This chunk analyzes the first line of the input to help determine what gets submitted where
        # Analysis is ENTIRELY BASED on the first line, PLEASE specify the cores accordingly
        currentLine = currentLine.strip()
        print("Stripped line is " + currentLine + "!!")
        subLine = currentLine.split('=')
        print("Subline is " + str(subLine) + "!!")
        firstSubLine = subLine[0].lower()
        print("First subline is " + str(firstSubLine) + "!!")

        # Gaussian16 submission
        if firstSubLine == '%nprocshared'or firstSubLine =='%nproc':
            cpus = int(subLine[1])
            print("Successfully read " + str(cpus) + " cores from " + fileList[x])

            # Looks for memory in input file
            currentLine = inputFile.readline()
            currentLine = currentLine.strip()
            subLine = currentLine.split('=')
            firstSubLine = subLine[0].lower()
            if firstSubLine == '%mem':
                ram = int(subLine[1].replace("GB", ""))
                jobRam = ram + 2
                print("Successfully read memory and will submit with " + str(ram) + " + 2 GB")
            # Sets the job to run with a default amount of RAM. Does not update the input because neither of us could be bothered to care.
            else:
                ram = cpus * 2
                jobRam = ram + 2
                print("Couldn't find RAM amount and will submit with " + str(ram) + " + 2 GB instead")
            inputFile.close()

            # Writes the CMD for submission
            outputFile.write("#!/usr/bin/env bash\n")
            outputFile.write("#SBATCH --job-name=" + str(baseName) + "\n")
            outputFile.write("#SBATCH --output=" + outputName + "\n")
            outputFile.write("#SBATCH --ntasks-per-node=" + str(cpus) + "\n")
            outputFile.write("#SBATCH --mem=" + str(jobRam) + "GB\n")
            outputFile.write("#SBATCH --time=" + str(defaultWallTime) + ":00:00\n")
            outputFile.write("#SBATCH --cluster=smp\n")
            outputFile.write("#SBATCH --partition=" + defaultPartition + "\n")
            outputFile.write("#SBATCH --nodes=1\n")

            outputFile.write("\n")
            outputFile.write("module purge\n")
            outputFile.write("module load gaussian\n")
            outputFile.write("\n")
            outputFile.write("export GAUSS_SCRDIR=$SLURM_SCRATCH\n")
            outputFile.write("ulimit -s unlimited\n")
            outputFile.write("export LC_COLLATE=C\n")
            outputFile.write("\n")
            outputFile.write("g16 < " + fileList[x] + "\n")
            outputFile.write("\n")
            outputFile.close()

            os.system("sbatch " + queueName)
            if isStalking:
                totalJobs += 1
                totalJobList.append(queueName)

        # Checks for ORCA-type submission
        subLine = currentLine.split()
        firstSubLine = subLine[0].lower()
        # ORCA submission
        if firstSubLine == '%pal':
            cpus = int(subLine[2])
            print("Successfully read " + str(cpus) + " cores from " + fileList[x])

            # Looks for memory in ORCA file
            currentLine = inputFile.readline()
            # Due to input re-writing, need to read the *third* line instead of the second
            currentLine = inputFile.readline()
            currentLine = currentLine.strip()
            subLine = currentLine.split()
            firstSubLine = subLine[0].lower()
            if firstSubLine == '%maxcore':
                ramTemp = int(subLine[1])
                print(str(ramTemp) + " MB")
                ramConvert = int(ramTemp / 1000 * 6)
                print(str(ramConvert) + " GB")
                jobRam = int(ramConvert + 2)
                print("Successfully read memory and will submit with " + str(ramConvert) + " + 2 GB")
            # Sets the RAM to a default amount
            else:
                ram = cpus * 6
                jobRam = int(ram + 2)
                print("Couldn't find RAM amount and will submit with " + str(ram) + " + 2 GB instead")

            inputFile.close()

            # Writes the CMD for job submission
            outputFile.write("#!/bin/bash\n")
            outputFile.write("#SBATCH --job-name=" + str(baseName) + "\n")
            outputFile.write("#SBATCH --output=" + outputName + "\n")
            outputFile.write("#SBATCH --nodes=1\n")
            outputFile.write("#SBATCH --mem=" + str(jobRam) + "GB\n")
            outputFile.write("#SBATCH --ntasks-per-node=" + str(cpus) + "\n")
            outputFile.write("#SBATCH --time=" + str(defaultWallTime) + ":00:00\n")
            outputFile.write("#SBATCH --cluster=smp\n")
            outputFile.write("#SBATCH --partition=" + defaultPartition + "\n")

            outputFile.write("\n")
            outputFile.write("# Load the module\n")
            outputFile.write("module purge\n")
            # New!! Now runs in ORCA 6.0 instead of 4.2.0 .
            outputFile.write("module load gcc/10.2.0 gcc/4.8.5 openmpi/4.1.1 orca/6.0.0\n")
            outputFile.write("\n")
            outputFile.write("# Copy files to SLURM_SCRATCH\n")
            outputFile.write("files=(" + str(baseName+".inp") + ")\n")
            outputFile.write("for i in ${files[@]}; do\n")
            outputFile.write("    cp $SLURM_SUBMIT_DIR/$i $SLURM_SCRATCH/$i\n")
            outputFile.write("done\n")
            outputFile.write("\n")
            outputFile.write("# cd to the SCRATCH space\n")
            outputFile.write("cd $SLURM_SCRATCH\n")
            outputFile.write("\n")
            outputFile.write("# run the job, $(which orca) is necessary\n")
            outputFile.write("$(which orca) " + str(baseName+".inp") + "\n")
            outputFile.write("\n")
            outputFile.write("# finally, copy back gbw and prop files\n")
            outputFile.write("cp $SLURM_SCRATCH/*.{gbw,prop} $SLURM_SUBMIT_DIR\n")
            outputFile.write("\n")
            outputFile.close()

            os.system("sbatch " + queueName)
            if isStalking:
                totalJobs += 1
                totalJobList.append(queueName)
    if isStalking:
        jobStalk(totalJobs, totalJobList)

# This method is still buggy, and does not work properly.
def jobStalk(jobCount, fileList):
    print("Time to stalk jobs! Total count is: " + str(jobCount))
    finishedJobs = 0
    errorJobs = 0
    runningJobs = 0
    queuedJobs = jobCount
    runningList = []
    errorList = []

    # Constructs the list of total outputs to be checking against
    for x in range(len(fileList)):
        baseName, extension = os.path.splitext(fileList[x])
        baseName = os.path.basename(baseName)
        outputName = baseName + ".out"
        totalOutputs.append(outputName)

    # This loop is intended to run until all jobs are completed
    while finishedJobs < jobCount:
        print(fileList)
        # Ideally, this chunk checks if the output for a specific cmd exists. If so, the job has moved from the
        # queue to running, and deletes the cmd. In a perfect world, this continues to happen until no more
        # cmds exist and all jobs are running (or finished)
        if len(fileList) > 0:
            for y in range(len(fileList)):
                if os.path.isfile(totalOutputs[y]):
                    if os.path.isfile(fileList[y]):
                        file = fileList[y]
                        fileList.remove(file)
                        runningList.append(file)
                        queuedJobs -= 1
                        runningJobs += 1
                        os.remove(file)
        # When the output *does* exist, this chunk iterates backwards a maximum of 30 times (because sometimes
        # a Gaussian Error termination line isn't directly at the bottom) looking for *any* termination statement
        # If error, it is added to a different list, and if normal, added to the finished list
        if runningJobs > 0:
            for z in range(len(runningList)):
                with open(runningList[z], 'r') as jobCheck:
                    jobCheck.seek(0,2)
                    position = jobCheck.tell()
                    iterations = 0
                    while iterations < 30:
                        position -= 1
                        jobCheck.seek(position)
                        line = jobCheck.readline().strip()
                        subLines = line.split()
                        if subLines[1] == "TERMINATED" or subLines[1] == "termination":
                            if subLines[0] != "Error" or subLines[3] != "error":
                                finishedJobs += 1
                                runningList.remove(z)
                                break
                        else:
                            errorJobs += 1
                            runningList.remove(z)
                            errorList.append(z)
                            break
                        iterations += 1
                        position -= 1
        # Print the output of the loop every main stalking interval
        print("Jobs queued: " + str(queuedJobs))
        print("Jobs running: " + str(runningJobs))
        print("Jobs finished: " + str(finishedJobs))
        print("Error terminations: " + str(errorJobs))
        print("Specific error jobs: " + str(errorList))
        # Waits the stalking interval before checking jobs again (re-starting the loop)
        time.sleep(defaultStalk)

commandLineParser()