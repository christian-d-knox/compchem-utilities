#!/usr/bin/env python3
# Welcome to the Computational Chemistry Utilities Installer!
# This utility serves to install all potentially important modules that compUtils (and normal work) leverages!
# Written by Christian Drew Knox, for the Peng Liu Research Group
# Last major commit: 2025-07-30

import os

print("Your terminal will lock up for a moment while dependencies are installed. Please be patient.")
os.system("module load python/3.10.13")
print("Correct Python dependency automatically loaded. Do not load a new Python module.")
os.system("pip install numpy")
os.system("pip install pandas")
os.system("pip install matplotlib")
os.system("pip install scikit-learn")
os.system("pip install scipy")
os.system("pip install goodvibes")
os.system("pip install termcolor")
os.system('''sed -i -e '$aalias py3="module load python/3.10.13"' ~/.alias''')
print("Alias for the correct Python version created. Load with terminal alias py3")
os.system('''sed -i -e '$aalias cu="python3 compUtils.py"' ~/.alias''')
print("Alias for compUtils created. Call conveniently with terminal alias cu after loading Python dependency.")
os.system("source ~/.alias")
print("Alias file sourced automatically. Feel free to continue working as normal.")