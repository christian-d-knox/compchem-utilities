# Welcome to Computational Chemistry Utilities v0.1
# This package has been crafted through the lovely masochism of Christian Drew Knox, for the Peng Liu Group
# Last major commit to the project was 2024-09-25

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
defaultRAM = "24GB"
defaultWallTime = "24"
defaultCheckpoint = False

# Defines global variables for use in various functions
fileNames = []

def commandLineParser():
    parser = argparse.ArgumentParser(description="The main command line argument parser for flag handling")

    #The various flags for defining the features of this utility
    parser.add_argument('-r', '--run', type=str, help="Indicates the 'run' functionality for the listed file, for submitting jobs to the cluster.")
    parser.add_argument('-sp', '--singlePoint', type=str, help="Indicates the 'single point' functionality for the listed file, to auto-create and submit to the cluster.")
    parser.add_argument('-b', '--bench', type=str, help="Indicates the 'benchmark' functionality, for creating a single point becnhmark on the listed file.")
    parser.add_argument('-st', '--stalk', help="Indicates the 'stalking' functionality, which will watch the jobs currently running based on their outputs, and update the terminal periodically with relevant info.")
    parser.add_argument('-c', '--checkpoint', help="Enables checkpoint functionality for the 'run' routine.")

    args = parser.parse_args()

    if args.checkpoint:
        isCheckpoint = True
    else:
        isCheckpoint = defaultCheckpoint

    if args.run:
        fileNames.append(glob.glob(args.run))
        runJob(isCheckpoint)


def runJob(checkpoint):
   # This routine is for job submission to the cluster
    for x in fileNames
        if not os.path.isfile(fileNames.[x])
            print("You sure that " + fileNames.[x] + " exists???")
            sys.exit(fileNames.[x])
        # The following is more or less copied directly from my modified qg16
        baseName = os.path.splitext(fileNames.[x])[0]
        outputName = baseName + ".out"
        checkName = baseName + ".chk"
        queueName = baseName + ".cmd"

        inputFile = open(fileNames.[x], "r+")
        outputFile = open(outputName, "w")

        while True:
            currentLine = inputFile.readline()

            if not currentLine:
                break

            if len(currentLine) == 0
                break

            currentLine = currentLine.strip()
            subLine = line.split('=')
            firstSubLine = subLine[0].lower()
            if firstSubLine == '%nprocshared'or firstSubLine =='%nproc':
                cpus = int(subLine[1])