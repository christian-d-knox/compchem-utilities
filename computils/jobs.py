import os, functools, regex, subprocess, time
from pathlib import Path
from .console  import console
from rich.markup import escape
from .defaults import Defaults
from .catalog  import RenderRoute, ROUTE_LEAK_PATTERN
from .fileops  import (fileCreation, ExtractFrom, ExtractFromText, HasContent, ExtractResources, ExtractOrcaBlocks,
                       MapFile, StageCount,
                       ExtractMixedBasis, MixedBasis, BasisEntry, NormalizeElement, MoleculeElements)
from .molecule import Molecule
from .project  import ResolveProjectFile, FindProjectRoot, ProjectFilePath
from .intent import JobIntent
from .stalk    import TrackedJob, Stage, jobLocation

# Parsed once per run, however many jobs use it
@functools.cache
def _LoadOrcaBlocks(blocksPath: Path) -> dict[str, str]:
    return ExtractFrom(blocksPath, ExtractOrcaBlocks, empty={})

@functools.cache
def _LoadMixedBasis(basisPath: Path) -> MixedBasis:
    return ExtractFrom(basisPath, ExtractMixedBasis, empty=MixedBasis([], []))

# Route tokens that request a mixed basis, including the slash form (B3LYP/GenECP)
def _IsMixedBasisKeyword(word: str) -> bool:
    return word.lower() in {variant.lower() for variant in Defaults.mixedBasisVariants}

def _UsesMixedBasis(route: str) -> bool:
    return any(_IsMixedBasisKeyword(word) for word in regex.findall(r"[^\s/]+", route))

# Swaps Gen <-> GenECP in the route, keeping the user's capitalization style (gen / GEN / Gen)
def _SetMixedBasisKeyword(route: str, needsEcp: bool) -> str:
    def Styled(original: str) -> str:
        target = "GenECP" if needsEcp else "Gen"
        if original.islower():
            return target.lower()
        if original.isupper():
            return target.upper()
        return target
    return regex.sub(r"[^\s/]+", lambda word: Styled(word.group()) if _IsMixedBasisKeyword(word.group())
                     else word.group(), route)

# Writes only the master mixedbasis.txt entries for elements this molecule contains.
# Returns (basis section, ECP section, elements with no basis entry)
def _FilterMixedBasis(master: MixedBasis, elements: set[str]) -> tuple[str, str, set[str]]:
    def Section(entries: list[BasisEntry]) -> str:
        text = ""
        for entry in entries:
            kept = entry.tokens if entry.byCenter else [token for token in entry.tokens
                                                          if NormalizeElement(token) in elements]
            if kept:
                text += " ".join(kept) + " 0\n" + entry.body
        return text
    covered = {element for entry in master.basis for element in entry.elements}
    # Center-number groups could cover anything, so coverage can't be checked
    missing = set() if any(entry.byCenter for entry in master.basis) else elements - covered
    return Section(master.basis), Section(master.ecp), missing

# Why genFile would skip a job, or '' if it can be generated. The one check shared by genFile and the TUI's route
# preview, which passes required=False so a missing project file never prompts. texts ({file name: unsaved text}, from
# the TUI's config editor) stands in for those project files
def JobFileProblem(route: str, tags: list[str], extensionType: str, elements: set[str], required: bool = True,
                   texts: dict[str, str] | None = None) -> str:
    # {tag} and [group] syntax is parsed out in Catalog.Load and must never reach an input file
    if regex.search(ROUTE_LEAK_PATTERN, route):
        return "Malformed [ ] Group in benchmarkMethods"
    if extensionType == Defaults.gaussianExtension and _UsesMixedBasis(route):
        master = _ProjectData("mixedbasis.txt", ExtractMixedBasis, _LoadMixedBasis, MixedBasis([], []), required, texts)
        if master is None:
            return _MissingProjectFile("mixedbasis.txt")
        _, _, missing = _FilterMixedBasis(master, elements)
        if missing:
            return f"mixedbasis.txt Has No Basis for {' '.join(sorted(missing))}"
    if extensionType == Defaults.orcaExtension and tags:
        blocks = _ProjectData("orcablocks.txt", ExtractOrcaBlocks, _LoadOrcaBlocks, {}, required, texts)
        if blocks is None:
            return _MissingProjectFile("orcablocks.txt")
        missingTags = [tag for tag in tags if tag not in blocks]
        if missingTags:
            return f"orcablocks.txt Has No {', '.join(missingTags)}"
    return ""

def _ProjectData(fileName: str, extractor, loader, empty, required: bool, texts: dict[str, str] | None):
    # A project file's parsed content: its unsaved text if given, else the file (None if there is no file)
    if texts and fileName in texts:
        return ExtractFromText(texts[fileName], extractor, empty=empty)
    path = ResolveProjectFile(fileName, required)
    return None if path is None else loader(path)

def _MissingProjectFile(fileName: str) -> str:
    root = FindProjectRoot()
    return f"{fileName} Not Found (Checked CWD and {ProjectFilePath(root, fileName) if root else 'No Project Root'})"

# Separate method for input file generation to improve code efficiency. Path to input and the route template are
# previously stored in molecule (fileops.Retarget).
# Returns False if this job can't be generated (e.g. missing mixedbasis.txt), so the caller skips only this job.
def genFile(molecule: Molecule, intent: JobIntent) -> bool:
    inputFile = molecule.fullPath
    # Route card rendered for this molecule's spin state: base + matching [spin groups] + U/RO reference
    route, blockTags = RenderRoute(molecule.template, molecule)
    elements = MoleculeElements(molecule.coordinateList)
    # Checked BEFORE opening the input, so a failure never leaves a half-written file
    problem = JobFileProblem(route, blockTags, molecule.extensionType, elements, True)
    if problem:
        console.print(f"[error]{escape(problem)}. Skipping {molecule.baseName}.[/error]")
        return False
    match molecule.extensionType:
        case Defaults.gaussianExtension:
            if blockTags:
                console.print(f"[warning]Block tags {blockTags} only apply to ORCA jobs. Ignoring them for "
                              f"{molecule.baseName}.[/warning]")
            basisSection, ecpSection = "", ""
            usesMixedBasis = _UsesMixedBasis(route)
            if usesMixedBasis:
                # The master file lists every element the project may need; keep only this molecule's
                master = _LoadMixedBasis(ResolveProjectFile("mixedbasis.txt", True))
                basisSection, ecpSection, _ = _FilterMixedBasis(master, elements)
                # GenECP with an empty ECP section (or Gen with ECP elements) fails in Gaussian
                switchedRoute = _SetMixedBasisKeyword(route, bool(ecpSection))
                if switchedRoute != route:
                    reason = "ECP elements present" if ecpSection else "no ECP elements"
                    console.print(f"[info]{molecule.baseName}: using {'GenECP' if ecpSection else 'Gen'} "
                                  f"({reason}).[/info]")
                    route = switchedRoute
            # No longer accesses the XYZ file due to Molecule coordinateList property
            with open(inputFile, 'w') as jobInput:
                # Sets the job's CPU and RAM
                jobCPU = str(Defaults.CPU)
                jobMem = str(int(Defaults.CPU * Defaults.memoryRatio))
                # Writes the standard Gaussian16 formatted opening
                jobInput.write("%nprocshared=" + jobCPU + "\n%mem=" + jobMem + "GB")
                if intent.checkpoint:
                    jobInput.write(f"\n%chk={molecule.baseName}.chk")
                # If the methodLine from benchmarking.txt is garbage, the calculation will fail. Not my fault.
                jobInput.write("\n# " + route + "\n\nUseless Comment line\n\n")
                jobInput.write(f"{molecule.charge} {molecule.multiplicity}\n")
                # Accessing the stored coordinate list is significantly faster in run-time than prior crappy implementation
                jobInput.writelines(molecule.coordinateList)
                # Mixed basis entries for this molecule's elements, from the CWD or project root mixedbasis.txt
                if usesMixedBasis:
                    jobInput.write("\n" + basisSection)
                    if ecpSection:
                        jobInput.write("\n" + ecpSection)
                jobInput.write("\n")
                # New NBO7 section
                if intent.nbo7:
                    jobInput.write(f"{Defaults.nboKeylist} FILE={molecule.baseName} ARCHIVE $END")
                else:
                    jobInput.write("\n")

        case Defaults.orcaExtension:
            # Tagged blocks from orcablocks.txt, in route-card tag order (JobFileProblem checked they all exist)
            selectedBlocks = []
            if blockTags:
                availableBlocks = _LoadOrcaBlocks(ResolveProjectFile("orcablocks.txt", True))
                selectedBlocks = [availableBlocks[tag] for tag in blockTags]
            # Then the template's own literal blocks (an ORCA re-run carries the blocks its job ran with)
            selectedBlocks += molecule.template.blocks
            # Opens the job file
            with open(inputFile, 'w') as jobInput:
                # Sets the job's CPU and RAM
                jobCPU = str(Defaults.CPU)
                if "DLPNO" in route:
                    jobMem = str(int(Defaults.highMemoryRatio * 1000))
                else:
                    jobMem = str(int(Defaults.memoryRatio * 1000))
                # Writes the standard ORCA formatted opening
                jobInput.write(f"%pal nprocs {jobCPU}\nend\n%maxcore {jobMem}")
                # If the methodLine from benchmarking.txt is garbage, the calculation will fail. Not my fault.
                jobInput.write("\n! " + route + "\n")
                for block in selectedBlocks:
                    jobInput.write(block if block.endswith("\n") else block + "\n")
                jobInput.write("\n")
                # ORCA is smart enough to read from an XYZ directly
                jobInput.write(f"* xyz {molecule.charge} {molecule.multiplicity} \n")
                jobInput.writelines(molecule.coordinateList)
                jobInput.write("\n*")
    return True

# Reorganized! Now handles SLURM commands independently because of HPC cluster agnosticism
def slurmHandler(molecule: Molecule, queueName: Path, outputName: Path, cpus: int, jobRam: int) -> None:
    # What each header line gets filled with, by the flag it contains. Checked in this order; first match wins
    fills = [("-J", f" {molecule.baseName}"), ("-o", f" {outputName}"), ("--ntasks", f"{cpus}"), ("--mem", f"{jobRam}GB"),
             ("-t", f" {Defaults.wallTime}:00:00"), ("-p", f" {Defaults.partition}"), ("-M", f" {Defaults.cluster}")]
    with open(queueName, 'w') as outputFile:
        for line in Defaults.submissionList:
            fill = next((value for flag, value in fills if flag in line), "")
            outputFile.write(f"{line}{fill}\n")

# An ORCA job's plumbing, after orcaNonVariant's environment: run in node scratch, then copy the result files into
# <job name>-scratch/ however the job ends. At the wall time SLURM sends SIGTERM (then SIGKILL ~30 s later), so the TERM
# trap exits and the EXIT trap still copies. The .out itself comes back through SLURM -o
ORCA_RUN_SCRIPT = """\
# Run in the node's scratch space
cp "$SLURM_SUBMIT_DIR/{inputName}" "$SLURM_SCRATCH/"
cd "$SLURM_SCRATCH"

# Copy the results back however the job ends: normal end, ORCA error, or the wall time
results="$SLURM_SUBMIT_DIR/{resultsFolder}"
CopyBack() {{
    mkdir -p "$results"
    for file in {resultFiles}; do
        if [ -f "$file" ]; then cp "$file" "$results/"; fi
    done
}}
trap CopyBack EXIT
trap 'exit 143' TERM

# $(which orca) is necessary: ORCA needs its full path to run in parallel
$(which orca) {inputName}
"""

# Every job reaches the queue through here, so the group broadcast can count the whole invocation (dispatch)
submittedJobs = 0

# --parsable prints just 'ID' or 'ID;cluster'. Output is captured: sbatch must never write to the TUI's screen. Returns
# (job ID, cluster), or None (after saying why) if sbatch refused the job: the caller skips only that job
def SubmitJob(queueName: Path) -> tuple[str, str] | None:
    global submittedJobs
    try:
        result = subprocess.run(["sbatch", "--parsable", str(queueName)], capture_output=True, text=True)
    except FileNotFoundError:
        console.print(f"[error]sbatch isn't available here, so {queueName} wasn't submitted.[/error]")
        return None
    if result.returncode != 0:
        console.print(f"[error]sbatch refused {queueName}: {escape(result.stderr.strip())}[/error]")
        return None
    #os.remove(queueName)
    submittedJobs += 1
    jobID, _, cluster = result.stdout.strip().splitlines()[-1].partition(";")
    return jobID.strip(), cluster.strip()

# This routine is for job submission to the cluster. Every submitted job is tracked (trackedJobs, recorded by
# dispatch), so any later stalker can re-hook into it
def runJob(molecule: Molecule, intent: JobIntent, trackedJobs: list) -> None:
    # Sets up all the basic filenames for the rest of submission
    outputName = fileCreation(molecule.baseName, Defaults.outputExtension)
    queueName = fileCreation(molecule.baseName, Defaults.queueExtension)

    # Empty file guard
    if not HasContent(molecule.fullPath):
        console.print(f"[error]Job file {molecule.baseName} is empty or blank. Skipping submission.[/error]")
        return

    # Extract CPU and RAM from the input file via mmap regex, and how many --Link1-- stages it runs (for the stalker)
    with MapFile(molecule.fullPath) as data:
        cpus, jobRam = ExtractResources(data, molecule.extensionType)
        stageCount = StageCount(data, molecule.extensionType)

    slurmHandler(molecule, queueName, outputName, cpus, jobRam)

    match molecule.extensionType:
        case Defaults.gaussianExtension:
            program = "Gaussian16"
            with open(queueName, 'a') as outputFile:
                outputFile.writelines(Defaults.gaussianNonVariant)
                outputFile.write(f"\ng16 < {molecule.fullPath}\n\n")

        case Defaults.orcaExtension:
            program = "ORCA"
            resultFiles = " ".join(str(fileCreation(molecule.baseName, suffix)) for suffix in Defaults.orcaResultSuffixes)
            with open(queueName, 'a') as outputFile:
                outputFile.writelines(Defaults.orcaNonVariant)
                outputFile.write(ORCA_RUN_SCRIPT.format(
                    inputName=fileCreation(molecule.baseName, Defaults.orcaExtension), resultFiles=resultFiles,
                    resultsFolder=fileCreation(molecule.baseName, "", Defaults.scratchFolderExtra)))

        # Only Gaussian and ORCA inputs are submitted
        case _:
            return

    submitted = SubmitJob(queueName)
    if submitted is None:
        console.print(f"[error]Skipping {molecule.baseName}.[/error]")
        return
    jobID, cluster = submitted
    console.print(f"[good]Submitted job {molecule.baseName} to {program} (ID {jobID})[/good]")
    trackedJobs.append(TrackedJob(jobID, cluster, molecule.baseName, str(Path(outputName).resolve()), jobLocation(),
                                  molecule.extensionType, time.time(), [Stage() for _ in range(stageCount)]))

