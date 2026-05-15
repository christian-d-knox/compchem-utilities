#!/usr/bin/env python3
# Welcome to the Computational Chemistry Utilities Module Finder!
# This utility serves to make CompUtils HPC AGNOSTIC by finding the list of all necessary modules to load on a cluster
# Written by Christian Drew Knox, for the Peng Liu Research Group
# Last major commit: 2025-08-06


# This will take quite some time to figure out...
import os
import subprocess

print("Your terminal will lock up for a few moments while the script gathers the data it requires.")
command = ['module','spider','gaussian']
result = subprocess.run(command, stdout=subprocess.PIPE)
gaussianModules = result.stdout.decode('utf-8')
print(gaussianModules)