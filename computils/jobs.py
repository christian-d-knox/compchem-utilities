import os, regex, subprocess
from pathlib import Path
from .console  import console
from .defaults import Defaults
from .catalog  import Catalog
from .fileops  import fileCreation, MapFile, ExtractResources
from .molecule import Molecule
from .intent import JobIntent

# Separate method for input file generation to improve code efficiency. No longer returns anything as path to input is
# previously stored in molecule
def genFile(molecule: Molecule, index: int, intent: JobIntent) -> None:
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
                if intent.checkpoint:
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
                if intent.nbo7:
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

# Reorganized! Now handles SLURM commands independently because of HPC cluster agnosticism
def slurmHandler(molecule: Molecule, queueName: Path, outputName: Path, cpus: int, jobRam: int) -> None:
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
def runJob(molecule: Molecule, intent: JobIntent, stalkingSet: set) -> None:
    # Sets up all the basic filenames for the rest of submission
    outputName = fileCreation(molecule.baseName, Defaults.outputExtension)
    queueName = fileCreation(molecule.baseName, Defaults.queueExtension)

    # Empty file guard
    if molecule.fullPath.stat().st_size == 0:
        console.print(f"[error]Job file {molecule.baseName} is empty or blank. Skipping submission.[/error]")
        return

    # Extract CPU and RAM from the input file via mmap regex
    with MapFile(molecule.fullPath) as data:
        cpus, jobRam = ExtractResources(data, molecule.extensionType)

    slurmHandler(molecule, queueName, outputName, cpus, jobRam)

    match molecule.extensionType:
        case Defaults.gaussianExtension:
            with open(queueName, 'a') as outputFile:
                for nonVariantLine in Defaults.gaussianNonVariant:
                    outputFile.write(nonVariantLine)
                outputFile.write(f"\ng16 < {molecule.fullPath}\n\n")

            subprocess.run(["sbatch", queueName], check=True)
            #os.remove(queueName)
            console.print(f"[good]Submitted job {molecule.baseName} to Gaussian16[/good]")
            if intent.stalk:
                molecule.fullPath = fileCreation(molecule.baseName, Defaults.outputExtension)
                stalkingSet.add((molecule.baseName,molecule.fullPath))

        case Defaults.orcaExtension:
            with open(queueName, 'a') as outputFile:
                # Now runs in ORCA 6.0.1 instead of 4.2.0
                for index in range(0,3):
                    outputFile.write(Defaults.orcaNonVariant[index])
                outputFile.write(f"files=({fileCreation(molecule.baseName, Defaults.orcaExtension)})\n")
                for index in range(3,8):
                    outputFile.write(Defaults.orcaNonVariant[index])
                outputFile.write(f"$(which orca) {fileCreation(molecule.baseName, Defaults.orcaExtension)}\n\n")
                for index in range(8,10):
                    outputFile.write(Defaults.orcaNonVariant[index])

            subprocess.run(["sbatch", queueName], check=True)
            #os.remove(queueName)
            console.print(f"[good]Submitted job {molecule.baseName} to ORCA 6.0.1[/good]")
            if intent.stalk:
                molecule.fullPath = fileCreation(molecule.baseName, Defaults.outputExtension)
                stalkingSet.add((molecule.baseName,molecule.fullPath))

