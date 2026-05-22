import os, regex, subprocess
from pathlib import Path
from .console  import console
from .defaults import Defaults
from .catalog  import Catalog
from .fileops   import fileCreation, extensionGetter, grabPaths
from .molecule import Molecule

# Separate method for input file generation to improve code efficiency. No longer returns anything as path to input is
# previously stored in molecule
def genFile(molecule: object, index: int) -> None:
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
                if Catalog.isCheck:
                    jobInput.write(f"\n%chk={molecule.baseName}.chk")
                # If the methodLine from benchmarking.txt is garbage, the calculation will fail. Not my fault.
                jobInput.write("\n# " + Catalog.fullMethodLine[index].replace("\n","") + "\n\nUseless Comment line\n\n")
                jobInput.write(f"{molecule.charge} {molecule.multiplicity}\n")
                # New mixed basis checking
                for keyWord in Catalog.fullMethodLine[index].split():
                    if keyWord in Defaults.mixedBasisVariants:
                        mixedBasis = True
                        break
                # Accessing the stored coordinate list is significantly faster in run-time than prior crappy implementation
                for line in molecule.coordinateList:
                    jobInput.write(line)
                # Adds in mixed basis info from local file
                if Path("mixedbasis.txt").is_file() and mixedBasis:
                    jobInput.write("\n")
                    with open("mixedbasis.txt") as mixedBasisFile:
                        for line in mixedBasisFile:
                            jobInput.write(line)
                elif mixedBasis and not Path("mixedbasis.txt").is_file():
                    console.print("[error]Mixed basis detected but requirements not found. Aborting.[/error]")
                    return
                jobInput.write("\n")
                # New NBO7 section
                if Catalog.isNBO:
                    jobInput.write(f"{Defaults.nboKeylist} FILE={molecule.baseName} ARCHIVE $END")
                else:
                    jobInput.write("\n")

        case Defaults.orcaExtension:
            # Opens the job file
            with open(inputFile, 'w') as jobInput:
                # Sets the job's CPU and RAM
                jobCPU = str(Defaults.CPU)
                if "DLPNO" in Catalog.fullMethodLine[index]:
                    jobMem = str(Defaults.highMemoryRatio * 1000)
                else:
                    jobMem = str(Defaults.memoryRatio * 1000)
                # Writes the standard ORCA formatted opening
                jobInput.write(f"%pal nprocs {jobCPU}\nend\n%maxcore {jobMem}")
                # If the methodLine from benchmarking.txt is garbage, the calculation will fail. Not my fault.
                jobInput.write("\n! " + Catalog.fullMethodLine[index].replace("\n","") + "\n\n")
                # ORCA is smart enough to read from an XYZ directly
                jobInput.write(f"* xyz {molecule.charge} {molecule.multiplicity} \n")
                for line in molecule.coordinateList:
                    jobInput.write(line)
                jobInput.write("\n*")

        case Defaults.qChemExtension:
            pass

# Reorganized! Now handles SLURM commands independently because of HPC cluster agnosticism
def slurmHandler(molecule: object, queueName: Path, outputName: Path, firstFiveLines: list[str]) -> None:
    coresLine, ramLine, cpus, jobRam = "", "", 0, 0
    match molecule.extensionType:
        case Defaults.gaussianExtension:
            for line in firstFiveLines:
                if regex.search(Defaults.coreLineVariants[0], line) or regex.search(Defaults.coreLineVariants[1], line):
                    coresLine = line
                if regex.search(Defaults.ramLineVariants[0], line):
                    ramLine = line
            if len(coresLine) == 0:
                cpus = Defaults.CPU
                console.print("[error]Couldn't find CPU count in input file. Submitting instead according to Defaults.[/error]")
            else:
                cpus = coresLine.strip().split("=")[1]
            if len(ramLine) == 0:
                ram = cpus * Defaults.memoryRatio
                jobRam = ram + Defaults.memoryBuffer
                console.print("[error]Couldn't find RAM count in input file. Submitting instead according to Defaults.[/error]")
            else:
                ram = int(ramLine.strip().split("=")[1].replace("GB", ""))
                jobRam = ram + Defaults.memoryBuffer

        case Defaults.orcaExtension:
            for line in firstFiveLines:
                if regex.search(Defaults.coreLineVariants[2], line):
                    coresLine = line
                if regex.search(Defaults.ramLineVariants[1], line):
                    ramLine = line
            if len(coresLine) == 0:
                cpus = Defaults.CPU
                console.print("[error]Couldn't find CPU count in input file. Submitting instead according to Defaults.[/error]")
            else:
                cpus = coresLine.strip().split()[2]
            if len(ramLine) == 0:
                ram = cpus * Defaults.memoryRatio
                jobRam = ram + Defaults.memoryBuffer
                console.print("[error]Couldn't find RAM count in input file. Submitting instead according to Defaults.[/error]")
            else:
                ram = int(ramLine.strip().split()[1]) / 1000
                jobRam = int(int(cpus) * ram + Defaults.memoryBuffer)

        case Defaults.qChemExtension:
            for line in firstFiveLines:
                if regex.search(Defaults.coreLineVariants[0], line) or regex.search(Defaults.coreLineVariants[1], line):
                    coresLine = line
                if regex.search(Defaults.ramLineVariants[0], line):
                    ramLine = line
            if len(coresLine) == 0:
                cpus = Defaults.CPU
                console.print("[error]Couldn't find CPU count in input file. Submitting instead according to Defaults.[/error]")
            else:
                cpus = coresLine.strip().split("=")[1]
            if len(ramLine) == 0:
                ram = cpus * Defaults.memoryRatio
                jobRam = ram + Defaults.memoryBuffer
                console.print("[error]Couldn't find RAM count in input file. Submitting instead according to Defaults.[/error]")
            else:
                ram = int(ramLine.strip().split("=")[1].replace("GB", ""))
                jobRam = ram + Defaults.memoryBuffer

        case _:
            cpus = Defaults.CPU
            jobRam = cpus * Defaults.memoryRatio + Defaults.memoryBuffer

    with open(queueName, 'w') as outputFile:
        for line in Defaults.submissionList:
            if regex.search("-J", line):
                outputFile.write(f"{line} {molecule.baseName}\n")
            elif regex.search("-o", line):
                outputFile.write(f"{line} {outputName}\n")
            elif regex.search("--ntasks", line):
                outputFile.write(f"{line}{cpus}\n")
            elif regex.search("--mem", line):
                outputFile.write(f"{line}{jobRam}GB\n")
            elif regex.search("-t", line):
                outputFile.write(f"{line} {Defaults.wallTime}:00:00\n")
            elif regex.search("-p", line):
                outputFile.write(f"{line} {Defaults.partition}\n")
            elif regex.search("-M", line):
                outputFile.write(f"{line} {Defaults.cluster}\n")
            else:
                outputFile.write(f"{line}\n")

# This routine is for job submission to the cluster
def runJob(molecule: object):
    # Sets up all the basic filenames for the rest of submission
    outputName = molecule.baseName + Defaults.outputExtension
    queueName = molecule.baseName + Defaults.queueExtension

    # A potential minor speed uplift would be the closing(mmap()) implementation used basically everywhere else, since
    # I've learned just how fast it is. Probably not necessary, though
    with open(molecule.fullPath, 'r+') as inputFile:
        firstFiveLines = []
        # Reads the first line of the file
        currentLine = inputFile.readline().strip()
        firstFiveLines.append(currentLine)

        # Craps out if the first line doesn't exist, or is entirely blank
        if not currentLine:
            console.print(f"[error]Job file {molecule.baseName} is empty. Terminating submission attempt.[/error]")
        if len(currentLine) == 0:
            console.print(f"[error]First line of job file {molecule.baseName} is blank. Terminating submission attempt.[/error]")

        for index in range(0,4):
            firstFiveLines.append(inputFile.readline().strip())

        slurmHandler(molecule, queueName, outputName, firstFiveLines)

        match molecule.extensionType:
            case Defaults.gaussianExtension:
                with open(queueName, 'a') as outputFile:
                    for nonVariantLine in Defaults.gaussianNonVariant:
                        outputFile.write(nonVariantLine)
                    outputFile.write(f"\ng16 < {molecule.fullPath}\n\n")

                subprocess.run(["sbatch", queueName], check=True)
                #os.remove(queueName)
                console.print(f"[good]Submitted job {molecule.baseName} to Gaussian16[/good]")
                if Catalog.isStalking:
                    molecule.fullPath = molecule.baseName + Defaults.outputExtension
                    Catalog.stalkingSet.add((molecule.baseName,molecule.fullPath))

            case Defaults.orcaExtension:
                with open(queueName, 'a') as outputFile:
                    # Now runs in ORCA 6.0.1 instead of 4.2.0
                    for index in range(0,3):
                        outputFile.write(Defaults.orcaNonVariant[index])
                    outputFile.write(f"files=({molecule.baseName + Defaults.orcaExtension})\n")
                    for index in range(3,8):
                        outputFile.write(Defaults.orcaNonVariant[index])
                    outputFile.write(f"$(which orca) {molecule.baseName + Defaults.orcaExtension}\n\n")
                    for index in range(8,10):
                        outputFile.write(Defaults.orcaNonVariant[index])

                subprocess.run(["sbatch", queueName], check=True)
                #os.remove(queueName)
                console.print(f"[good]Submitted job {molecule.baseName} to ORCA 6.0.1[/good]")
                if Catalog.isStalking:
                    molecule.fullPath = molecule.baseName + Defaults.outputExtension
                    Catalog.stalkingSet.add((molecule.baseName,molecule.fullPath))

            # This will need updated to the modern architecture at some point
            case Defaults.qChemExtension:
                with open(queueName, 'a') as outputFile:
                    outputFile.write("module purge\nmodule load qchem/6.3.0-pliu\n\n\n")
                    # This crap was in the original and I have no clue if it's still necessary
                    # output.write("export QCSCRATCH=$LOCAL\n")
                    # output.write("export QC=/ihome/pliu/xiq23/qchem/qchem_for_peng_20151014\n")
                    # output.write("export PATH=$PATH:$QC/bin\n")
                    # output.write("export QCAUX=/ihome/pliu/xiq23/qchem/qcaux4\n")
                    outputFile.write("# Change to working directory\n")
                    outputFile.write(f"cp $SLURM_SUBMIT_DIR/{molecule.fullPath} $SLURM_SCRATCH\n")
                    outputFile.write("cd $SLURM_SCRATCH\n\n")
                    # No clue what this line is and if it's needed, it's not in the Q-Chem documentation
                    outputFile.write("df -h\n")

                    # Re-wrote the script in order to make this line *actually* match the Q-Chem documentation
                    # Re-wrote to allow nthreads to match ncpu since each thread only runs on 1 CPU (see Q-Chem manual)
                    # Do NOT specify outfile, it LITERALLY breaks shit for some reason
                    #outputFile.write("qchem -slurm -nt " + str(cpus) + " " + molecule.fullPath + " " + "\n")
                    outputFile.write("du -h\n\n")

                subprocess.run(["sbatch", queueName], check=True)
                #os.remove(queueName)
                console.print(f"[good]Submitted job {molecule.baseName} to Q-Chem 6.3[/good]")
                if Catalog.isStalking:
                    molecule.fullPath = molecule.baseName + Defaults.outputExtension
                    Catalog.stalkingSet.add((molecule.baseName,molecule.fullPath))