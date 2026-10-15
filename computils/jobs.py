import os, regex, subprocess, time
from dataclasses import dataclass, field
from pathlib import Path
from .console  import console
from rich.markup import escape
from .defaults import Defaults
from .catalog  import RenderRoute, RouteTemplate, ROUTE_LEAK_PATTERN
from .fileops  import (fileCreation, HasContent, ExtractResources, MapFile, StageCount, MixedBasis, BasisEntry,
                       NormalizeElement, MoleculeElements, BASIS_PATTERN, InputRoutes, JobChecks, ExtractOrcaInput,
                       StageProblem)
from .keylists import Keylist, Where, MIXED_BASIS, OPTIMIZATION, ORCA_BLOCKS
from .molecule import Molecule
from .intent import JobIntent
from .stalk    import TrackedJob, Stage, jobLocation, TrackJobs, NTS_LOG
from .nevatouch import HookCommand, HookLines, JOB_ID

# The optimization methods (name -> route, in order): jobRelatedKeylists' Optimization Methods from the nearest level
# that has them, or the unsaved text from the TUI's config editor ({level path: text}); {} without any
def OptMethods(texts: dict[Path, str] | None = None) -> dict[str, str]:
    return Keylist(OPTIMIZATION, texts)

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

# Writes only the Mixed Basis entries for elements this molecule contains.
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
# previews. Mixed basis and ORCA blocks come from jobRelatedKeylists (keylists.Keylist: the nearest level with the
# section); texts ({level path: unsaved text}, from the TUI's config editor) stands in for those files. Never prompts
def JobFileProblem(route: str, tags: list[str], extensionType: str, elements: set[str],
                   texts: dict[Path, str] | None = None) -> str:
    # {tag} and [group] syntax is parsed out in Catalog.Load and must never reach an input file
    if regex.search(ROUTE_LEAK_PATTERN, route):
        return "Malformed [ ] Group or Unknown % Directive in the Route"
    if extensionType == Defaults.gaussianExtension and _UsesMixedBasis(route):
        _, _, missing = _FilterMixedBasis(Keylist(MIXED_BASIS, texts), elements)
        if missing:
            return f"Mixed Basis Has No Basis for {' '.join(sorted(missing))} ({Where(MIXED_BASIS)})"
    if extensionType == Defaults.orcaExtension and tags:
        blocks = Keylist(ORCA_BLOCKS, texts)
        missingTags = [tag for tag in tags if tag not in blocks]
        if missingTags:
            return f"ORCA Blocks Has No {', '.join(missingTags)} ({Where(ORCA_BLOCKS)})"
    return ""

# ─── Stages (Gaussian --Link1--; ORCA's wait for its `Continue` usage) ─
# Each stage after the first is chained to the one before through the checkpoint (ChainStage, the rules in one place)

# What follows a stage's route: the title, charge/multiplicity and coordinates (stage 1); the title and the charge line
# (its own geom=check); or nothing (Geom=AllCheck: all of them come from the checkpoint)
FULL, CHARGE, NONE = "full", "charge", "none"
# Guesses that make their own orbitals: a stage asking for one keeps it and is not given 'read'
GENERATED_GUESSES = {"core", "huckel", "indo", "am1", "harris", "sad", "cards"}

@dataclass
class RenderedStage:
    route: str                  # the stage's route, chaining keywords added (Gen/GenECP is switched when written)
    tags: list[str]             # its orcablocks tags
    molecule: str = FULL        # FULL, CHARGE or NONE
    chkBasis: bool = False      # the basis (and ECP) come from the checkpoint: no Gen sections are written
    newChk: bool = False        # %newchk: starts a new checkpoint, %OldChk-copied from the one so far
    notes: list[str] = field(default_factory=list)     # what CompUtils added, in words (the previews say it)

def _KeywordAt(tokens: list[str], keyword: str) -> int | None:
    """The index of a route keyword written bare, keyword=... or keyword(...), or None."""
    return next((index for index, token in enumerate(tokens)
                 if regex.fullmatch(rf"(?i){keyword}(?:[=(].*)?", token)), None)

def _KeywordOptions(token: str) -> list[str]:
    """'guess=(read,mix)' -> ['read', 'mix']; 'geom=allcheck' -> ['allcheck']; a bare keyword -> []."""
    _, _, options = token.partition("=") if "=" in token else token.partition("(")
    return [option.strip().lower() for option in regex.split(r"[(),]", options) if option.strip()]

def _NamesBasis(tokens: list[str]) -> bool:
    """Whether a Gaussian route names a basis: 'method/basis', or a basis token (Gen and GenECP included)."""
    return any("/" in token or regex.match(BASIS_PATTERN, token) for token in tokens)

def ChainStage(route: str, genInCheckpoint: bool) -> tuple[str, str, bool, list[str]]:
    """A Gaussian stage after the first, read from the checkpoint the stages share: (route, molecule section, ChkBasis,
    notes). Explicit on purpose (TUI_DESIGN 4.12): every rule is said in the notes where a preview shows the stage.
    1. Geometry: Geom=AllCheck (geometry, charge and multiplicity from the checkpoint), unless the stage writes its own
       geom= (geom=check: the title and charge line are written, for a stage that changes them).
    2. Orbitals: 'read' joins the stage's guess options (guess=mix -> guess=(read,mix)), unless it names a guess
       generator (GENERATED_GUESSES).
    3. ChkBasis, from the user's own route, never assumed: one the stage writes is honoured (no Gen sections); a stage
       naming no basis at all after a Gen/GenECP stage gets one (Gaussian would otherwise fall back to STO-3G); a stage
       writing Gen/GenECP itself gets its sections again."""
    tokens, notes = route.split(), []
    geom = _KeywordAt(tokens, "geom")
    if geom is None:
        tokens.append("geom=allcheck")
        molecule = NONE
        notes.append("Geom=AllCheck: Geometry, Charge and Multiplicity From the Checkpoint")
    else:
        options = _KeywordOptions(tokens[geom])
        molecule = NONE if "allcheck" in options else CHARGE if "check" in options else FULL
    guess = _KeywordAt(tokens, "guess")
    if guess is None:
        tokens.append("guess=read")
        notes.append("Guess=Read: Orbitals From the Checkpoint")
    else:
        options = _KeywordOptions(tokens[guess])
        if "read" not in options and not set(options) & GENERATED_GUESSES:
            tokens[guess] = f"guess=({','.join(['read', *options])})"
            notes.append(f"Guess=(Read,{','.join(option.capitalize() for option in options)}): Orbitals From the "
                         f"Checkpoint")
    chkBasis = any(token.lower() == "chkbasis" for token in tokens)
    if not chkBasis and genInCheckpoint and not _NamesBasis(tokens):
        tokens.append("ChkBasis")
        chkBasis = True
        notes.append("ChkBasis: the Gen Basis From the Checkpoint (This Stage Names No Basis)")
    return " ".join(tokens), molecule, chkBasis, notes

def RenderStages(template: RouteTemplate, molecule: Molecule) -> list[RenderedStage]:
    """Every stage of a molecule's job, rendered (RenderRoute: spin groups, U/RO) and, for Gaussian, chained through
    the checkpoint (ChainStage). One stage for a method without stages."""
    stages, genInCheckpoint = [], False
    for number, stage in enumerate(template.stages, 1):
        route, tags = RenderRoute(stage, molecule)
        if number == 1 or molecule.extensionType != Defaults.gaussianExtension:
            rendered = RenderedStage(route, tags)
        else:
            route, section, chkBasis, notes = ChainStage(route, genInCheckpoint)
            if stage.newChk:
                notes.insert(0, "%newchk: a New Checkpoint, Copied From the One So Far (%OldChk)")
            rendered = RenderedStage(route, tags, section, chkBasis, stage.newChk, notes)
        genInCheckpoint = _UsesMixedBasis(rendered.route) or (rendered.chkBasis and genInCheckpoint)
        stages.append(rendered)
    return stages

def StagesProblem(template: RouteTemplate, molecule: Molecule, elements: set[str],
                  texts: dict[Path, str] | None = None, programOf: dict | None = None) -> str:
    """Why a job with this template would be skipped ('' if it can be written): its stages (StageProblem), then each
    rendered stage's JobFileProblem. The one check behind genFile, shared with the TUI's previews."""
    problem = StageProblem(template, programOf)
    if problem:
        return problem
    stages = RenderStages(template, molecule)
    for number, stage in enumerate(stages, 1):
        problem = JobFileProblem(stage.route, stage.tags, molecule.extensionType, elements, texts)
        if problem:
            return f"Stage {number}: {problem}" if len(stages) > 1 else problem
    return ""

def PlannedKinds(data, program: str) -> list[str]:
    """What each stage of an input runs (JobChecks' kinds: 'ModRed', 'Stability', 'OptTS+Freq'), for the stalker's
    planned stages. data: the input's map."""
    inputText = ExtractOrcaInput(data) if program == Defaults.orcaExtension else ""
    return [JobChecks(route, program, inputText).kinds if route else "" for route in InputRoutes(data, program)]

# Separate method for input file generation to improve code efficiency. Path to input and the route template are
# previously stored in molecule (fileops.Retarget).
# Returns False if this job can't be generated (e.g. an element with no Mixed Basis entry), so the caller skips only
# this job.
def genFile(molecule: Molecule, intent: JobIntent) -> bool:
    # Checked BEFORE opening the input, so a failure never leaves a half-written file: every stage's route card,
    # rendered for this molecule's spin state (base + matching [spin groups] + U/RO reference, then the chaining)
    problem = StagesProblem(molecule.template, molecule, MoleculeElements(molecule.coordinateList))
    if problem:
        console.print(f"[error]{escape(problem)}. Skipping {molecule.baseName}.[/error]")
        return False
    text = JobInputText(molecule, intent.checkpoint, intent.nbo7, intent.cpus, intent.memoryRatio)
    with open(molecule.fullPath, 'w') as jobInput:
        jobInput.write(text)
    return True

# The memory per core a job gets unless told otherwise (GB): DLPNO in ORCA needs more
def DefaultMemoryRatio(route: str, extensionType: str) -> float:
    if extensionType == Defaults.orcaExtension and "DLPNO" in route:
        return Defaults.highMemoryRatio
    return Defaults.memoryRatio

# The text of a molecule's job input, in the program molecule.extensionType names (genFile writes it; the TUI's input
# builders preview it). cpus and memoryRatio (GB per core) default to the config's. A missing Mixed Basis entry or ORCA
# block is left out (JobFileProblem is what reports it). extraInput: one optional text per stage, written after that
# stage's molecule section (e.g. ModRed lines). oldChk: a checkpoint stage 1 starts from (%OldChk; for the scheduler)
def JobInputText(molecule: Molecule, checkpoint: bool = False, nbo7: bool = False, cpus: int | None = None,
                 memoryRatio: float | None = None, extraInput: list[str] | None = None, oldChk: str = "") -> str:
    route, blockTags = RenderRoute(molecule.template, molecule)
    elements = MoleculeElements(molecule.coordinateList)
    cpus = Defaults.CPU if cpus is None else cpus
    memoryRatio = DefaultMemoryRatio(route, molecule.extensionType) if memoryRatio is None else memoryRatio
    match molecule.extensionType:
        case Defaults.gaussianExtension:
            stages = RenderStages(molecule.template, molecule)
            blockTags = [tag for stage in stages for tag in stage.tags]
            if blockTags:
                console.print(f"[warning]Block tags {blockTags} only apply to ORCA jobs. Ignoring them for "
                              f"{molecule.baseName}.[/warning]")
            # A job with stages always writes its checkpoint: it is how the stages pass on what they made
            chained = len(stages) > 1
            checkpointName = f"{molecule.baseName}.chk"
            sections = []
            for number, stage in enumerate(stages, 1):
                route = stage.route
                # Link 0 commands don't carry across --Link1--: every stage writes its own
                link0 = [f"%nprocshared={cpus}", f"%mem={int(cpus * memoryRatio)}GB"]
                if stage.newChk:
                    link0.append(f"%oldchk={checkpointName}")
                    checkpointName = f"{molecule.baseName}_{number}.chk"
                elif number == 1 and oldChk:
                    link0.append(f"%oldchk={oldChk}")
                if checkpoint or chained or oldChk:
                    link0.append(f"%chk={checkpointName}")
                basisSection, ecpSection = "", ""
                usesMixedBasis = _UsesMixedBasis(route) and not stage.chkBasis
                if usesMixedBasis:
                    # The Mixed Basis section lists every element the project may need; keep only this molecule's
                    basisSection, ecpSection, _ = _FilterMixedBasis(Keylist(MIXED_BASIS), elements)
                    # GenECP with an empty ECP section (or Gen with ECP elements) fails in Gaussian
                    switchedRoute = _SetMixedBasisKeyword(route, bool(ecpSection))
                    if switchedRoute != route:
                        reason = "ECP elements present" if ecpSection else "no ECP elements"
                        console.print(f"[info]{molecule.baseName}{f' (stage {number})' if chained else ''}: using "
                                      f"{'GenECP' if ecpSection else 'Gen'} ({reason}).[/info]")
                        route = switchedRoute
                # A stage's input sections, each ended by a blank line: route, title, molecule, extra input, basis, ECP.
                # If the methodLine from benchmarking.txt is garbage, the calculation will fail. Not my fault.
                parts = ["# " + route + "\n"]
                if stage.molecule != NONE:
                    parts.append("Useless Comment line\n")
                    # Accessing the stored coordinate list is significantly faster in run-time than prior crappy
                    # implementation
                    parts.append(f"{molecule.charge} {molecule.multiplicity}\n" +
                                 ("".join(molecule.coordinateList) if stage.molecule == FULL else ""))
                extra = (extraInput[number - 1] if extraInput and number <= len(extraInput) else "").strip("\r\n")
                if extra:
                    parts.append(extra + "\n")
                # Mixed basis entries for this molecule's elements (jobRelatedKeylists' Mixed Basis)
                if usesMixedBasis:
                    parts.append(basisSection)
                    if ecpSection:
                        parts.append(ecpSection)
                sections.append("\n".join(link0) + "\n" + "\n".join(parts) + "\n")
            text = "--Link1--\n".join(sections)
            # New NBO7 section (the last stage's)
            text += f"{Defaults.nboKeylist} FILE={molecule.baseName} ARCHIVE $END" if nbo7 else "\n"
            return text

        case Defaults.orcaExtension:
            # Tagged blocks from ORCA Blocks, in route-card tag order (JobFileProblem checked they all exist)
            selectedBlocks = []
            if blockTags:
                availableBlocks = Keylist(ORCA_BLOCKS)
                selectedBlocks = [availableBlocks[tag] for tag in blockTags if tag in availableBlocks]
            # Then the template's own literal blocks (an ORCA re-run carries the blocks its job ran with)
            selectedBlocks += molecule.template.blocks
            # Writes the standard ORCA formatted opening: the job's CPU and RAM (%maxcore is MB per core)
            text = f"%pal nprocs {cpus}\nend\n%maxcore {int(memoryRatio * 1000)}"
            # If the methodLine from benchmarking.txt is garbage, the calculation will fail. Not my fault.
            text += "\n! " + route + "\n"
            for block in selectedBlocks:
                text += block if block.endswith("\n") else block + "\n"
            text += "\n"
            # ORCA is smart enough to read from an XYZ directly
            text += f"* xyz {molecule.charge} {molecule.multiplicity} \n"
            text += "".join(molecule.coordinateList)
            return text + "\n*"
    return ""

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

# The environment sbatch runs in. Inside a job (the Neva-Touch Scheduler's hook submits from one) the job's own SLURM_*
# and SBATCH_* variables are left out, or the new job would inherit the old one's allocation settings
def _SubmitEnvironment() -> dict[str, str] | None:
    if "SLURM_JOB_ID" not in os.environ:
        return None
    return {name: value for name, value in os.environ.items()
            if name == "SLURM_CONF" or not name.startswith(("SLURM_", "SBATCH_"))}

# --parsable prints just 'ID' or 'ID;cluster'. Output is captured: sbatch must never write to the TUI's screen. Returns
# (job ID, cluster), or None (after saying why) if sbatch refused the job: the caller skips only that job
def SubmitJob(queueName: Path) -> tuple[str, str] | None:
    global submittedJobs
    try:
        result = subprocess.run(["sbatch", "--parsable", str(queueName)], capture_output=True, text=True,
                                env=_SubmitEnvironment())
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

# This routine is for job submission to the cluster. Every submitted job is tracked (recorded at once, and returned in
# trackedJobs), so any later stalker can re-hook into it
def runJob(molecule: Molecule, intent: JobIntent, trackedJobs: list) -> None:
    # Sets up all the basic filenames for the rest of submission
    outputName = fileCreation(molecule.baseName, Defaults.outputExtension)
    queueName = fileCreation(molecule.baseName, Defaults.queueExtension)
    # The Neva-Touch Scheduler's hook: the step this job takes once it ends, with the next job's resources
    outputPath = Path(outputName).resolve()
    hookCommand = (HookCommand(intent.nevaTouch, outputPath, intent.nextCpus, intent.nextMemoryRatio)
                   if intent.nevaTouch else [])
    hook = (HookLines(hookCommand, outputPath.parent, outputPath.parent / fileCreation(molecule.baseName, NTS_LOG))
            if hookCommand else "")

    # Empty file guard
    if not HasContent(molecule.fullPath):
        console.print(f"[error]Job file {molecule.baseName} is empty or blank. Skipping submission.[/error]")
        return

    # Extract CPU and RAM from the input file via mmap regex, and the --Link1-- stages it runs, each with what it runs
    # (for the stalker's planned stages)
    with MapFile(molecule.fullPath) as data:
        cpus, jobRam = ExtractResources(data, molecule.extensionType)
        stageCount = StageCount(data, molecule.extensionType)
        planned = PlannedKinds(data, molecule.extensionType)

    slurmHandler(molecule, queueName, outputName, cpus, jobRam)

    match molecule.extensionType:
        case Defaults.gaussianExtension:
            program = "Gaussian16"
            with open(queueName, 'a') as outputFile:
                outputFile.writelines(Defaults.gaussianNonVariant)
                outputFile.write(f"\ng16 < {molecule.fullPath}\n\n")
                outputFile.write(hook)

        case Defaults.orcaExtension:
            program = "ORCA"
            resultFiles = " ".join(str(fileCreation(molecule.baseName, suffix)) for suffix in Defaults.orcaResultSuffixes)
            with open(queueName, 'a') as outputFile:
                outputFile.writelines(Defaults.orcaNonVariant)
                outputFile.write(ORCA_RUN_SCRIPT.format(
                    inputName=fileCreation(molecule.baseName, Defaults.orcaExtension), resultFiles=resultFiles,
                    resultsFolder=fileCreation(molecule.baseName, "", Defaults.scratchFolderExtra)))
                outputFile.write(hook)

        # Only Gaussian and ORCA inputs are submitted
        case _:
            return

    submitted = SubmitJob(queueName)
    if submitted is None:
        console.print(f"[error]Skipping {molecule.baseName}.[/error]")
        return
    jobID, cluster = submitted
    console.print(f"[good]Submitted job {molecule.baseName} to {program} (ID {jobID})[/good]")
    tracked = TrackedJob(jobID, cluster, molecule.baseName, str(outputPath), jobLocation(), molecule.extensionType,
                         time.time(), [Stage(planned=planned[index] if index < len(planned) else "")
                                       for index in range(stageCount)],
                         nevaTouch=intent.nevaTouch,
                         hookCommand=[jobID if word == JOB_ID else word for word in hookCommand])
    # Recorded now, not after the batch: a quick job's Neva-Touch Scheduler hook looks for its record
    TrackJobs([tracked])
    trackedJobs.append(tracked)

