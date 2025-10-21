#!/usr/bin/env python3
# Welcome to the Computational Chemistry Utilities Installer!
# This utility serves to install all potentially important modules that compUtils (and normal work) leverages!
# Written by Christian Drew Knox, for the Peng Liu Research Group
# Last major commit: 2025-10-XX

import os

print("Your terminal will lock up for a moment while Miniconda is installed along with the environment. Please be patient.")
os.system("mkdir -p ~/miniconda3")
os.system("wget https://repo.anaconda.com/miniconda/Miniconda3-py310_25.7.0-2-Linux-x86_64.sh -O ~/miniconda3/miniconda.sh")
os.system("bash ~/miniconda3/miniconda.sh -b -u -p ~/miniconda3")
os.system("rm ~/miniconda3/miniconda.sh")
os.system("source ~/miniconda3/bin/activate")
os.system("conda init --all")
os.system("source ~/.bashrc")
os.system('''sed -i -e '$aalias cu="python3 ~/bin/compUtils.py"' ~/.alias''')
os.system('''sed -i -e '$aalias con="conda activate compUtils"' ~/.alias''')
os.system("source ~/.alias")
print("Created aliases 'cu' for compUtils and 'con' for activating conda environment.")
print("Your terminal will now lock up again as the conda environment is installed.")
os.system("conda env create --file compUtils.yml")