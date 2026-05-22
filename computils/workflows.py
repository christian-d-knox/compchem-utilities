import os, time, regex, subprocess
from contextlib import closing
from mmap import mmap, ACCESS_READ

from .console  import console
from .defaults import Defaults
from .catalog  import Catalog
from .fileops   import *
from .jobs     import genFile, runJob, slurmHandler
from .molecule import Molecule
from .prompts import AskStr


def genBench(molecule: object) -> None:
    # First, make the original Single Point
    genSinglePoint(molecule)
    # Since methodFile is defined globally, no need to iterate a line to catch-up after genSinglePoint
    startTime = time.monotonic()
    if Catalog.indexOverride != 0:
        indexShift = Catalog.indexOverride + 1
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
        genFile(molecule,index)
        runJob(molecule)
    endTime = time.monotonic()
    totalTime = round(endTime - startTime,2)
    console.print(f"[operation]Total time for non-SP benchmark generation is: {totalTime} seconds.[/operation]")

def genSinglePoint(molecule: object) -> None:
    startTime = time.monotonic()

    # Update molecule properties
    molecule.extensionType = extensionGetter(Catalog.methodLine[0])
    inputFile = fileCreation(molecule.baseName, molecule.extensionType, Defaults.singlePointExtra)
    molecule.fullPath = inputFile
    molecule.baseName = molecule.baseName + Defaults.singlePointExtra
    if Catalog.indexOverride != 0:
        index = Catalog.indexOverride
    else:
        index = 0

    # Calls the separate file generation method, feeds directly into runJob
    genFile(molecule, index)
    runJob(molecule)
    endTime = time.monotonic()
    totalTime = round(endTime - startTime,2)
    console.print(f"[operation]Total single point time is {totalTime} seconds.[/operation]")

# Better, interactive implementation of my own gimmeCubesv3
def gimmeCubes(molecule: object, cubeKeyList: list[str]) -> None:
    keyWord, queueName, outputName = "", "", ""
    for cubeKey in cubeKeyList:
        match cubeKey:
            case Defaults.spinCube:
                outputName = fileCreation(molecule.baseName, Defaults.cubeExtension, cubeKey)
                queueName = fileCreation(molecule.baseName, Defaults.queueExtension, cubeKey)
                keyWord = "Spin=SCF"
            case Defaults.denCube:
                outputName = fileCreation(molecule.baseName, Defaults.cubeExtension, cubeKey)
                queueName = fileCreation(molecule.baseName, Defaults.queueExtension, cubeKey)
                keyWord = "Density=SCF"
            case Defaults.potCube:
                outputName = fileCreation(molecule.baseName, Defaults.cubeExtension, cubeKey)
                queueName = fileCreation(molecule.baseName, Defaults.queueExtension, cubeKey)
                keyWord = "Potential=SCF"
            case Defaults.valenceCube:
                outputName = fileCreation(molecule.baseName, Defaults.cubeExtension, cubeKey)
                queueName = fileCreation(molecule.baseName, Defaults.queueExtension, cubeKey)
                keyWord = "MO=Valence"
            case "Range":
                orbitalRange = AskStr("Enter the range of MOs you want printed (e.g. 10-15)")
                outputName = fileCreation(molecule.baseName, Defaults.cubeExtension, cubeKey + orbitalRange)
                queueName = fileCreation(molecule.baseName, Defaults.queueExtension, cubeKey + orbitalRange)
                keyWord = f"MO={orbitalRange}"
            case _:
                console.print(f"[error]Error: Unknown keyword found in keylist for {molecule.baseName} : {cubeKey}[/error]")

        slurmHandler(molecule,queueName,outputName,[])

        with open(queueName,"a") as queueFile:
            for nonVariantLine in Defaults.gaussianNonVariant:
                queueFile.write(nonVariantLine)

            # Writes the specifics for running the Density Cube
            queueFile.write(f"cubegen 1 {keyWord} {molecule.fullPath} {outputName} 0\n\n")

        subprocess.run(["sbatch", queueName], check=True)
        #os.remove(queueName)
        console.print(f"[good]Submitted cube job {molecule.baseName} {cubeKey} to the cluster.[/good]")

# Because jobs don't always work the first time
def genReRun(molecule,skipIndex):
    #with open(molecule.fullPath,"r") as inputFile:
    #    with closing(mmap(inputFile.fileno(),0,access=ACCESS_READ)) as data:
    #        preTable = "Will use up to"
    #        preBytes = preTable.encode()
    #        originalMethod = regex.search(preBytes,data)
    #        pointer = originalMethod.ends()
    #        data.seek(pointer[0])
    #        data.read(2)
    #        for index in range(3):
    #            data.readline()
    #        originalMethod = data.readline().decode()
    with MapFile(molecule.fullPath) as inFile:
        originalMethod = ExtractPriorMethod(inFile)

    Catalog.fullMethodLine[0] = originalMethod.replace("#","").strip()
    Catalog.methodLine[0] = originalMethod.replace("#","").strip().split()[skipIndex]
    molecule.extensionType = extensionGetter(Catalog.methodLine[0])
    inputFile = fileCreation(molecule.baseName, molecule.extensionType, Defaults.reRunExtra)
    molecule.fullPath = inputFile
    molecule.baseName = molecule.baseName + Defaults.reRunExtra

    # Calls the separate file generation method, feeds directly into runJob
    genFile(molecule, 0)
    runJob(molecule)