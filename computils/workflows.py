import os, time, regex, subprocess
from contextlib import closing
from mmap import mmap, ACCESS_READ

from .console  import console
from .defaults import Defaults
from .catalog  import Catalog
from .fileops import extensionGetter, fileCreation, MapFile, ExtractPriorMethod
from .intent import BenchmarkIntent, SinglePointIntent, ReRunIntent, CubeIntent
from .jobs     import genFile, runJob, slurmHandler
from .molecule import Molecule
from .prompts import AskStr

def genSinglePoint(molecule: Molecule, intent: SinglePointIntent, stalkingSet: set) -> None:
    startTime = time.monotonic()

    # Update molecule properties
    molecule.extensionType = extensionGetter(Catalog.methodLine[0])
    inputFile = fileCreation(molecule.baseName, molecule.extensionType, Defaults.singlePointExtra)
    molecule.fullPath = inputFile
    molecule.baseName = molecule.baseName + Defaults.singlePointExtra
    if intent.indexOverride != 0:
        index = intent.indexOverride
    else:
        index = 0

    # Calls the separate file generation method, feeds directly into runJob
    genFile(molecule, index, intent)
    runJob(molecule, intent, stalkingSet)
    endTime = time.monotonic()
    totalTime = round(endTime - startTime,2)
    console.print(f"[operation]Total single point time is {totalTime} seconds.[/operation]")

def genBench(molecule: Molecule, intent: BenchmarkIntent, stalkingSet: set) -> None:
    spIntent = SinglePointIntent(files=intent.files, stalk=intent.stalk, stalkLoop=intent.stalkLoop,
                                 checkpoint=intent.checkpoint, nbo7=intent.nbo7, indexOverride=intent.indexOverride)
    # First, make the original Single Point
    genSinglePoint(molecule, spIntent, stalkingSet)
    # Since methodFile is defined globally, no need to iterate a line to catch-up after genSinglePoint
    startTime = time.monotonic()
    if intent.indexOverride != 0:
        indexShift = intent.indexOverride + 1
    else:
        indexShift = 1
    for index in range(indexShift, len(Catalog.methodLine)):
        molecule.extensionType = extensionGetter(Catalog.methodLine[index])
        isSMD = regex.search("smd", Catalog.fullMethodLine[index], regex.IGNORECASE)
        if isSMD:
            filemaskExtra = f"-{index}-" + str(Catalog.methodLine[index].replace("(", "").replace(")", "")) + f"SMD{Defaults.singlePointExtra}"
        else:
            filemaskExtra = f"-{index}-" + str(Catalog.methodLine[index].replace("(", "").replace(")", "")) + f"{Defaults.singlePointExtra}"
        inputFile = fileCreation(molecule.rootName, molecule.extensionType, filemaskExtra)
        molecule.fullPath = inputFile
        molecule.baseName = (molecule.rootName + filemaskExtra)
        genFile(molecule, index, intent)
        runJob(molecule, intent, stalkingSet)
    endTime = time.monotonic()
    totalTime = round(endTime - startTime,2)
    console.print(f"[operation]Total time for non-SP benchmark generation is: {totalTime} seconds.[/operation]")

# Better, interactive implementation of my own gimmeCubesv3
def gimmeCubes(molecule: Molecule, intent: CubeIntent) -> None:
    keyWord, queueName, outputName = "", "", ""
    for cubeOption in intent.cubeOptions:
        match cubeOption.value: # Taken from its Enum
            case Defaults.spinCube:
                outputName = fileCreation(molecule.baseName, Defaults.cubeExtension, cubeOption.value)
                queueName = fileCreation(molecule.baseName, Defaults.queueExtension, cubeOption.value)
                keyWord = "Spin=SCF"
            case Defaults.denCube:
                outputName = fileCreation(molecule.baseName, Defaults.cubeExtension, cubeOption.value)
                queueName = fileCreation(molecule.baseName, Defaults.queueExtension, cubeOption.value)
                keyWord = "Density=SCF"
            case Defaults.potCube:
                outputName = fileCreation(molecule.baseName, Defaults.cubeExtension, cubeOption.value)
                queueName = fileCreation(molecule.baseName, Defaults.queueExtension, cubeOption.value)
                keyWord = "Potential=SCF"
            case Defaults.valenceCube:
                outputName = fileCreation(molecule.baseName, Defaults.cubeExtension, cubeOption.value)
                queueName = fileCreation(molecule.baseName, Defaults.queueExtension, cubeOption.value)
                keyWord = "MO=Valence"
            case "Range":
                orbitalRange = AskStr("Enter the range of MOs you want printed (e.g. 10-15)")
                outputName = fileCreation(molecule.baseName, Defaults.cubeExtension, cubeOption.value + intent.orbitalRange)
                queueName = fileCreation(molecule.baseName, Defaults.queueExtension, cubeOption.value + intent.orbitalRange)
                keyWord = f"MO={intent.orbitalRange}"
            case _:
                console.print(f"[error]Error: Unknown keyword found in keylist for {molecule.baseName} : {cubeOption.value}[/error]")

        slurmHandler(molecule, queueName, outputName,[])

        with open(queueName,"a") as queueFile:
            for nonVariantLine in Defaults.gaussianNonVariant:
                queueFile.write(nonVariantLine)

            # Writes the specifics for running the Density Cube
            queueFile.write(f"cubegen 1 {keyWord} {molecule.fullPath} {outputName} 0\n\n")

        subprocess.run(["sbatch", queueName], check=True)
        #os.remove(queueName)
        console.print(f"[good]Submitted cube job {molecule.baseName} {cubeOption.value} to the cluster.[/good]")

# Because jobs don't always work the first time
def genReRun(molecule: Molecule, intent: ReRunIntent, stalkingSet: set) -> None:
    with MapFile(molecule.fullPath) as inFile:
        originalMethod = ExtractPriorMethod(inFile)

    Catalog.fullMethodLine[0] = originalMethod.replace("#","").strip()
    Catalog.methodLine[0] = originalMethod.replace("#","").strip().split()[intent.skipIndex]
    molecule.extensionType = extensionGetter(Catalog.methodLine[0])
    inputFile = fileCreation(molecule.baseName, molecule.extensionType, Defaults.reRunExtra)
    molecule.fullPath = inputFile
    molecule.baseName = molecule.baseName + Defaults.reRunExtra

    # Calls the separate file generation method, feeds directly into runJob
    genFile(molecule, 0, intent)
    runJob(molecule, intent, stalkingSet)