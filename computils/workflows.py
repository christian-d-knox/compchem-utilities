import time, regex

from .console  import console
from .defaults import Defaults
from .catalog  import Catalog, ParseRouteTemplate
from .fileops import fileCreation, IncrementSuffix, Retarget, ReRunTemplate
from .actions  import CubeOption
from .intent import JobIntent, BenchmarkIntent, ReRunIntent, CubeIntent
from .jobs     import genFile, runJob, slurmHandler, SubmitJob, DRY_RUN
from .         import agent
from .molecule import Molecule
from .prompts import AskStr

# Every job's name, in one place: the workflows below write these, and the TUI's job screens (planned.py) list them
def SinglePointName(molecule: Molecule) -> str:
    """A single point's job name: 8a -> 8a_SP (singlePointExtra). A benchmark's first method's too."""
    return molecule.baseName + Defaults.singlePointExtra

def BenchmarkName(molecule: Molecule, index: int, template) -> str:
    """A benchmark job's name past its first method: 8a-2-B3LYP_SP (the index, the method, SMD when solvated)."""
    isSMD = regex.search("smd", template.base, regex.IGNORECASE)
    methodName = template.method.replace("(", "").replace(")", "")
    return f"{molecule.rootName}-{index}-{methodName}{'SMD' if isSMD else ''}{Defaults.singlePointExtra}"

def ReRunName(molecule: Molecule) -> str:
    """A re-run's job name: 8a -> 8a_re, and a re-run of a re-run increments (8a_re -> 8a_re2, never 8a_re_re)."""
    return IncrementSuffix(molecule.baseName, Defaults.reRunExtra)

# Also runs the first job of a benchmark, which passes its BenchmarkIntent (same fields)
def genSinglePoint(molecule: Molecule, intent: JobIntent, trackedJobs: list) -> None:
    startTime = time.monotonic()

    # The job's name, route template and program: a route given (--route, a Neva-Touch Scheduler chain step's), else
    # Benchmark Methods' entry chosen by -ovr
    template = (ParseRouteTemplate(intent.route) if getattr(intent, "route", None)
                else Catalog.templates[intent.indexOverride])
    Retarget(molecule, SinglePointName(molecule), template)

    # Calls the separate file generation method, feeds directly into runJob. Skip this job if generation failed
    if genFile(molecule, intent):
        runJob(molecule, intent, trackedJobs)
    endTime = time.monotonic()
    totalTime = round(endTime - startTime,2)
    console.print(f"[operation]Total single point time is {totalTime} seconds.[/operation]")

def genBench(molecule: Molecule, intent: BenchmarkIntent, trackedJobs: list) -> None:
    # First, make the original Single Point
    genSinglePoint(molecule, intent, trackedJobs)
    # Since methodFile is defined globally, no need to iterate a line to catch-up after genSinglePoint
    startTime = time.monotonic()
    for index in range(intent.indexOverride + 1, len(Catalog.templates)):
        template = Catalog.templates[index]
        Retarget(molecule, BenchmarkName(molecule, index, template), template)
        # Only this benchmark variant is skipped on failure; the molecule's other methods still run
        if not genFile(molecule, intent):
            continue
        runJob(molecule, intent, trackedJobs)
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
                intent.orbitalRange = AskStr("Enter the range of MOs you want printed (e.g. 10-15)", key="orbital-range")
            label += intent.orbitalRange
            keyWord = f"MO={intent.orbitalRange}"
        elif label in keyWords:
            keyWord = keyWords[label]
        else:
            # e.g. a label renamed in programs.toml no longer matches its CubeOption: skip it rather than crash
            agent.Skip(f"{molecule.baseName} {label}", f"Unknown cube keyword {label}",
                       f"Error: Unknown keyword found in keylist for {molecule.baseName} : {label}")
            continue
        outputName = fileCreation(molecule.baseName, Defaults.cubeExtension, label)
        queueName = fileCreation(molecule.baseName, Defaults.queueExtension, label)

        # cubegen runs on one core ('cubegen 1'): the job asks for one, and one core's memory
        slurmHandler(molecule, queueName, outputName, 1, int(Defaults.memoryRatio + Defaults.memoryBuffer),
                     intent.wallTime, intent.partition, intent.cluster)

        with open(queueName,"a") as queueFile:
            queueFile.writelines(Defaults.gaussianNonVariant)
            # Writes the specifics for running the Density Cube
            queueFile.write(f"cubegen 1 {keyWord} {molecule.fullPath} {outputName} 0\n\n")

        submitted = SubmitJob(queueName)
        if submitted is not None and submitted != DRY_RUN:
            console.print(f"[good]Submitted cube job {molecule.baseName} {cubeOption.value} to the cluster (ID "
                          f"{submitted[0]}).[/good]")
            agent.Submitted(f"{molecule.baseName} {cubeOption.value}", submitted[0], submitted[1], str(outputName))

# Because jobs don't always work the first time
def genReRun(molecule: Molecule, intent: ReRunIntent, trackedJobs: list) -> None:
    template, problem = ReRunTemplate(molecule)
    if template is None:
        agent.Skip(molecule.baseName, problem)
        return
    # The program was set by ReRunTemplate, which a verbatim route's first token may not name
    Retarget(molecule, ReRunName(molecule), template, molecule.extensionType)

    # Calls the separate file generation method, feeds directly into runJob. Skip this job if generation failed
    if genFile(molecule, intent):
        runJob(molecule, intent, trackedJobs)
