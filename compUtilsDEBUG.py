#!/usr/bin/env python3
# Welcome to Computational Chemistry Utilities v0.1
# This package has been crafted through pain and suffering
# Last major commit to the project was 2024-09-26

# Imports the various libraries needed for main() and each function()
import os
import posix
import posixpath
import sys
import subprocess
import argparse
import glob

# Set your defaults HERE
binDirectory = "/ihome/pliu/cdk67/bin/"
defaultSinglePoint = "M062X/6-311+G(d,p) scrf=(smd,solvent=TetraHydroFuran)" # Soon to be obsolete
defaultCPU = 12
defaultWallTime = "24"
# Change partition to pliu for SMP if preferred
defaultPartition = "smp"

# Defines global variables for use in various functions
fileNames = []
fullPaths = []

print("I loaded!!")

# Defines all the terminal flags the program can accept
def commandLineParser():
    parser = argparse.ArgumentParser(description="The main command line argument parser for flag handling")

    #The various flags for defining the features of this utility
    parser.add_argument('-r', '--run', type=str, help="Indicates the 'run' subroutine for the listed file(s)")
    parser.add_argument('-sp', '--singlePoint', type=str, help="Indicates the 'single point' subroutine for the listed file(s)")
    parser.add_argument('-b', '--bench', type=str, help="Indicates the 'benchmark' subroutine, for creating a single point becnhmark on the listed file(s)")
    parser.add_argument('-st', '--stalk', help="Indicates the 'stalking' subroutine, which will watch the jobs currently running based on their outputs, and update the terminal periodically")
    parser.add_argument('-c', '--checkpoint', action='store_true', help="Enables checkpoint functionality for the 'run' routine")
    parser.add_argument('-i', '--install', help="Indicates the 'install' subroutine, to install any dependencies CompUtils relies upon")

    # Figures out what the hell you told it to do
    args = parser.parse_args()

    # Run flag handling
    if args.run:
        print("I saw the run flag!")
        # Compiles the entire list of files to run (built-in 'runall' capabilities)
        fileNames = glob.glob(args.run)
        print("So you want me to run on these files?" + str(fileNames[-1]))
        runJob(grabPaths(fileNames))

    if args.singlePoint:
        print("I saw the single point flag!")
        # Compiles the entire list of files to run
        fileNames = glob.glob(args.singlePoint)
        print("So you want me to run on these files?" + str(fileNames[-1]))
        genSinglePoint(grabPaths(fileNames))

# This subroutine allows for easily compiling file paths for multiple methods
def grabPaths(fileList):
    fullPaths = [os.path.join(os.getcwd(), file) for file in fileList]
    # Basic-tier error-handling for nonexistent files
    missingFiles = [file for file in fileList if not os.path.isfile(os.path.join(os.getcwd(), file))]
    if missingFiles:
        print(f"Could not locate: {', '.join(missingFiles)}")
    return fullPaths

def genSinglePoint(fileList):
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
        baseName = os.path.basename(fileList[x])

        # This chunk helps handle the special case of doing DLPNO in ORCA *specifically*
        methodLine = methodFile.readline()
        methodSubs = methodLine.strip().split("/")
        methodFirstSub = methodSubs[0]

        if methodFirstSub != "DLPNO-CCSD(T)":
            outputName = baseName + ".gjf"
            # More G16 shit
        else:
            outputName = baseName + ".inp"
            # More ORCA shit




# Houkmol XYZ file Generator
# Originally scripted in AWK by Jan Lanbowski, translated to PERL and hacked by Paul Ha-Yeon Cheong
# Translated to Python using AI and then fixed by Christian Drew Knox (as such, is not thoroughly documented)
def getCoords(fileName, outputFile):
    print("I'm in getCoords now!")
    atSymbol = {
        1: 'H', 2: 'He', 3: 'Li', 4: 'Be', 5: 'B', 6: 'C', 7: 'N', 8: 'O', 9: 'F', 10: 'Ne',
        11: 'Na', 12: 'Mg', 13: 'Al', 14: 'Si', 15: 'P', 16: 'S', 17: 'Cl', 18: 'Ar', 19: 'K',
        20: 'Ca', 21: 'Sc', 22: 'Ti', 23: 'V', 24: 'Cr', 25: 'Mn', 26: 'Fe', 27: 'Co', 28: 'Ni',
        29: 'Cu', 30: 'Zn', 31: 'Ga', 32: 'Ge', 33: 'As', 34: 'Se', 35: 'Br', 36: 'Kr',

        42: 'Mo', 44: 'Ru', 45: 'Rh', 46: 'Pd', 47: 'Ag', 48: 'Cd', 50: 'Sn', 51: 'Sb',
        53: 'I', 54: 'Xe', 77: 'Ir', 78: 'Pt', 79: 'Au', 80: 'Hg', 81: 'Tl', 82: 'Pb',
        83: 'Bi'
    }

    at = {}
    X = {}
    Y = {}
    z = {}
    j = 0
    i = 0
    energy = None

    def getLine0(file):
        line = file.readline()
        if line:
            return line.strip()
        return None

    with open(os.path.abspath(fileName), 'r') as file:
        for line in file:
            line = line.strip()  # strip record separator
            Fld = line.split()

            if len(Fld) > 2 and Fld[1] == 'Standard' and Fld[2] == 'orientation:':
                for _ in range(5):
                    getLine0(file)
                while len(Fld) > 1:
                    i += 1
                    at[i] = Fld[2]
                    if len(Fld) == 6:
                        X[i] = Fld[3]
                        Y[i] = Fld[4]
                        z[i] = Fld[5]
                    else:
                        X[i] = Fld[4]
                        Y[i] = Fld[5]
                        z[i] = Fld[6]
                    line = getLine0(file)
                    if line:
                        Fld = line.split()
                    else:
                        break

            if len(Fld) > 2 and Fld[1] == 'SCF' and Fld[2] == 'Done:':
                energy = Fld[5]

            if len(Fld) > 3 and Fld[3] == 'Threshold':
                getLine0(file)
                j += 1

    # print(i)
    # print('Point ', j, ' Energy= ', energy)
    for k in range(1, i + 1):
        outputFile.write(f"{atSymbol[at[k]]}, {X[k]}, {Y[k]}, {z[k]}\n")

# This routine is for job submission to the cluster
def runJob(fileList):
    print("SUPPOSEDLY I'm in the run routine now!!")
    # Iterates over the entirety of the array passed into the subroutine
    for x in range(len(fileList)):
        # The following is more or less copied directly from my modified qg16
        # Sets up all the basic filenames for the rest of submission
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
            print("Yo this shit's BLANK, dawg.")
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
            #os.system("rm -f " + queueName)

        # Checks for ORCA-type submission
        subLine = currentLine.split(' ')
        firstSubLine = subLine[0].lower()
        # ORCA submission
        if firstSubLine == '%pal':
            cpus = int(subLine[2])
            print("Successfully read " + str(cpus) + " cores from " + fileList[x])

            # Looks for memory in ORCA file
            currentLine = inputFile.readline()
            currentLine = currentLine.strip()
            subLine = currentLine.split(' ')
            firstSubLine = subLine[0].lower()
            if firstSubLine == '%maxcore':
                ram = int(subLine[1]) * cpus / 1000
                jobRam = int(ram + 2)
                print("Successfully read memory and will submit with " + str(ram) + " + 2 GB")
            # Sets the RAM to a default amount
            else:
                ram = cpus * 2
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
            outputFile.write("module load openmpi/3.1.4 orca/4.2.0\n")
            outputFile.write("\n")
            outputFile.write("# Copy files to SLURM_SCRATCH\n")
            outputFile.write("files=(" + fileList[x] + ")\n")
            outputFile.write("for i in ${files[@]}; do\n")
            outputFile.write("    cp $SLURM_SUBMIT_DIR/$i $SLURM_SCRATCH/$i\n")
            outputFile.write("done\n")
            outputFile.write("\n")
            outputFile.write("# cd to the SCRATCH space\n")
            outputFile.write("cd $SLURM_SCRATCH\n")
            outputFile.write("\n")
            outputFile.write("# run the job, $(which orca) is necessary\n")
            outputFile.write("$(which orca) " + fileList[x] + "\n")
            outputFile.write("\n")
            outputFile.write("# finally, copy back gbw and prop files\n")
            outputFile.write("cp $SLURM_SCRATCH/*.{gbw,prop} $SLURM_SUBMIT_DIR\n")
            outputFile.write("\n")
            outputFile.close()

            os.system("sbatch " + queueName)
            #os.system("rm -f " + queueName)

commandLineParser()