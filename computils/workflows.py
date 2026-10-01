import time, regex

from .console  import console
from .defaults import Defaults
from .catalog  import Catalog
from .fileops import fileCreation, IncrementSuffix, Retarget, ReRunTemplate
from .actions  import CubeOption
from .intent import JobIntent, BenchmarkIntent, ReRunIntent, CubeIntent
from .jobs     import genFile, runJob, slurmHandler, SubmitJob
from .molecule import Molecule
from .prompts import AskStr

# Also runs the first job of a benchmark, which passes its BenchmarkIntent (same fields)
def genSinglePoint(molecule: Molecule, intent: JobIntent, stalkingSet: set) -> None:
    startTime = time.monotonic()

    # The job's name, route template and program, from the method chosen by -ovr
    Retarget(molecule, molecule.baseName + Defaults.singlePointExtra, Catalog.templates[intent.indexOverride])

    # Calls the separate file generation method, feeds directly into runJob. Skip this job if generation failed
    if genFile(molecule, intent):
        runJob(molecule, intent, stalkingSet)
    endTime = time.monotonic()
    totalTime = round(endTime - startTime,2)
    console.print(f"[operation]Total single point time is {totalTime} seconds.[/operation]")

def genBench(molecule: Molecule, intent: BenchmarkIntent, stalkingSet: set) -> None:
    # First, make the original Single Point
    genSinglePoint(molecule, intent, stalkingSet)
    # Since methodFile is defined globally, no need to iterate a line to catch-up after genSinglePoint
    startTime = time.monotonic()
    for index in range(intent.indexOverride + 1, len(Catalog.templates)):
        template = Catalog.templates[index]
        isSMD = regex.search("smd", template.base, regex.IGNORECASE)
        methodName = template.method.replace("(", "").replace(")", "")
        filemaskExtra = f"-{index}-{methodName}{'SMD' if isSMD else ''}{Defaults.singlePointExtra}"
        Retarget(molecule, molecule.rootName + filemaskExtra, template)
        # Only this benchmark variant is skipped on failure; the molecule's other methods still run
        if not genFile(molecule, intent):
            continue
        runJob(molecule, intent, stalkingSet)
    endTime = time.monotonic()
    totalTime = round(endTime - startTime,2)
    console.print(f"[operation]Total time for non-SP benchmark generation is: {totalTime} seconds.[/operation]")

# Better, interactive implementation of my own gimmeCubesv3
def gimmeCubes(molecule: Molecule, intent: CubeIntent) -> None:
    # cubegen keyword for each cube label (the labels come from programs.toml)
    keyWords = {Defaults.spinCube: "Spin=SCF", Defaults.denCube: "Density=SCF", Defaults.potCube: "Potential=SCF",
                Defaults.valenceCube: "MO=Valence"}
    for cubeOption in intent.cubeOptions:
        label = cubeOption.value # Taken from its Enum
        if cubeOption == CubeOption.RANGE:
            if not intent.orbitalRange:
                intent.orbitalRange = AskStr("Enter the range of MOs you want printed (e.g. 10-15)")
            label += intent.orbitalRange
            keyWord = f"MO={intent.orbitalRange}"
        elif label in keyWords:
            keyWord = keyWords[label]
        else:
            # e.g. a label renamed in programs.toml no longer matches its CubeOption: skip it rather than crash
            console.print(f"[error]Error: Unknown keyword found in keylist for {molecule.baseName} : {label}[/error]")
            continue
        outputName = fileCreation(molecule.baseName, Defaults.cubeExtension, label)
        queueName = fileCreation(molecule.baseName, Defaults.queueExtension, label)

        slurmHandler(molecule, queueName, outputName, Defaults.CPU,
                     int(Defaults.CPU * Defaults.memoryRatio + Defaults.memoryBuffer))

        with open(queueName,"a") as queueFile:
            queueFile.writelines(Defaults.gaussianNonVariant)
            # Writes the specifics for running the Density Cube
            queueFile.write(f"cubegen 1 {keyWord} {molecule.fullPath} {outputName} 0\n\n")

        SubmitJob(queueName)
        console.print(f"[good]Submitted cube job {molecule.baseName} {cubeOption.value} to the cluster.[/good]")

# Because jobs don't always work the first time
def genReRun(molecule: Molecule, intent: ReRunIntent, stalkingSet: set) -> None:
    template, problem = ReRunTemplate(molecule)
    if template is None:
        console.print(f"[error]{problem}. Skipping {molecule.baseName}.[/error]")
        return
    # A re-run of a re-run increments instead of stacking: mol_re -> mol_re2, not mol_re_re. The program was set by
    # ReRunTemplate, which a verbatim route's first token may not name
    Retarget(molecule, IncrementSuffix(molecule.baseName, Defaults.reRunExtra), template, molecule.extensionType)

    # Calls the separate file generation method, feeds directly into runJob. Skip this job if generation failed
    if genFile(molecule, intent):
        runJob(molecule, intent, stalkingSet)
