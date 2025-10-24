This README is essentially a Quick-Start guide, and is NOT a substitute for the Manual. Please read it if you have amy
questions about how the program works, how its flags operate, etc.

The bare minimum requirement for CompUtils to operate properly are the compUtils.py , compUtils.yml , and conda-installer.py

Place all three files in your ~/bin/ folder, load a Python3 module, and execute the conda installer. This will automatically
install the version of Miniconda3 I use internally, along with the actual compUtils conda environment, and create aliases
for accessing both quickly (along with sourcing your alias file for ease-of-use).

Simply load the compUtils conda environment, then use the convenient alias to access CompUtils. Previous versions required
more leg work from the end-user, but it has been narrowed down and simplified as much as possible.

It is STRONGLY recommended you either create your own programs.txt and benchmarking.txt in your ~/bin/ folder to customize
CompUtils in a more convenient way, or download the templates from the DEPENDENCY FILES/ folder on GitHub and modify them. 
The same can be said for mixedbasis.txt in your current working directory, if running with a mixed basis in Gaussian16 is desired.

CompUtils is currently configured to write submission scripts according to the SLURM version and quirks of H2P. With some
elbow grease, it can be re-configured for other HPC clusters.