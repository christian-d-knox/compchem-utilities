#!/usr/bin/env python3
# Welcome to Computational Chemistry Utilities!
# Now bigger, harder, faster, and stronger than ever before!
# This package has been hand-crafted lovingly through untold pain and suffering
# Last major commit to the project was 2026-05-13 (previously 2025-10-27)
# Last minor commit to the project was 2025-12-29

import os, argparse, glob, subprocess, regex, time, json#, sys    # Only necessary for occasional troubleshooting
from termcolor import cprint
import pandas#, numpy   # Will implement eventually (probably)
from contextlib import closing
from mmap import mmap, ACCESS_READ
import tomllib as tom
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

# Defines global variables for use in various functions
fileNames, fullPaths, totalJobList, totalOutputs, methodLine, fullMethodLine = [], [], [], [], [], []
isStalking, isLooping, isCheck, isNBO, isCustomTarget, canBench = False, False, False, False, True, True
indexOverride = 0
fileExtension = ""
# Paired together for program identification and input processing
methodList, targetProgram, booleanStrings = [], [], ["y","n"]
stalkingSet = set()



# Globally checks for benchmarking and programs data, limiting functionality and alerting user
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
    cprint("Benchmarking functionality is unavailable without requisite file. Please create your own or download "
           "the template from GitHub.", "light_red")

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

#print(sys.orig_argv)