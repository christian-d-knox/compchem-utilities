#!/usr/bin/env python3
# Welcome to Computational Chemistry Utilities v0.1
# This package has been crafted through the lovely masochism of Christian Drew Knox, for the Peng Liu Group
# Last major commit to the project was 2024-10-01

# Imports the various libraries needed for main() and each function()
import os
import posix
import posixpath
import sys
import subprocess
import argparse
import glob

# Set your defaults HERE
defaultSinglePoint = "M062X/6-311+G(d,p) scrf=(smd,solvent=TetraHydroFuran)"
defaultCPU = 12
defaultWallTime = "24"
# Change partition to pliu for SMP if preferred
defaultPartition = "smp"

# Defines global variables for use in various functions
fileNames = []
fullPaths = []

# Defines all the terminal flags the program can accept
def commandLineParser():
    parser = argparse.ArgumentParser(description="The main command line argument parser for flag handling")

    #The various flags for defining the features of this utility
    parser.add_argument('-r', '--run', type=str, help="Indicates the 'run' subroutine for the listed file(s)")
    parser.add_argument('-sp', '--singlePoint', type=str, help="Indicates the 'single point' subroutine for the listed file(s)")
    parser.add_argument('-b', '--bench', type=str, help="Indicates the 'benchmark' subroutine, for creating a single point becnhmark on the listed file(s)")
    parser.add_argument('-st', '--stalk', help="Indicates the 'stalking' subroutine, which will watch the jobs currently running based on their outputs, and update the terminal periodically")
    parser.add_argument('-c', '--checkpoint', help="Enables checkpoint functionality for the 'run' routine")
    parser.add_argument('-i', '--install', help="Indicates the 'install' subroutine, to install any dependencies CompUtils relies upon")

    # Figures out what the hell you told it to do
    args = parser.parse_args()

    # Checkpoint flag handling
    if args.checkpoint:
        isCheckpoint = True
    else:
        isCheckpoint = False

    # Run flag handling
    if args.run:
        # Compiles the entire list of files to run (built-in 'runall' capabilities)
        fileNames = glob.glob(args.run)
        runJob(grabPaths(fileNames))

# This subroutine allows for easily compiling file paths for multiple methods
def grabPaths(files):
    fullPaths = [os.path.join(os.getcwd(), file) for file in files]
    # Basic-tier error-handling for nonexistent files
    missingFiles = [file for file in files if not os.path.isfile(os.path.join(os.getcwd(), file))]
    if missingFiles:
        print(f"Could not locate: {', '.join(missingFiles)}")
    return fullPaths

# This routine is for job submission to the cluster
def runJob(fileList):
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
        outputFile = open(queueName, "w")

        #while True:
        # Reads the first line of the file
        currentLine = inputFile.readline()

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
        subLine = currentLine.split('=')
        firstSubLine = subLine[0].lower()

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