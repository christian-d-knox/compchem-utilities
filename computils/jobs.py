import functools, os, regex, subprocess, time
from dataclasses import dataclass, field
from pathlib import Path
from .console  import console
from .defaults import Defaults
from .catalog  import RenderRoute, RouteTemplate, ROUTE_LEAK_PATTERN
from .fileops  import (fileCreation, HasContent, ExtractResources, MapFile, StageCount, MixedBasis, BasisEntry,
                       NormalizeElement, MoleculeElements, BASIS_PATTERN, InputRoutes, JobChecks, ExtractOrcaInput,
                       StageProblem, InputStageTexts, GAUSSIAN_INPUT_ROUTE)
from .keylists import Keylist, Where, MIXED_BASIS, OPTIMIZATION, ORCA_BLOCKS
from .molecule import Molecule
from .intent import JobIntent
from .stalk    import TrackedJob, Stage, jobLocation, TrackJobs, NTS_LOG
from .actions   import NTS_SP
from .nevatouch import HookCommand, HookLines, JOB_ID, ChainPath, WriteChain
from .          import agent

# The optimization methods (name -> route, in order): keylists.cusf's Optimization Methods from the nearest level
# that has them, or the unsaved text from the TUI's config editor ({level path: text}); {} without any
def OptMethods(texts: dict[Path, str] | None = None) -> dict[str, str]:
    return Keylist(OPTIMIZATION, texts)

# Route tokens that request a mixed basis, including the slash form (B3LYP/GenECP)
_ROUTE_SLASH_TOKEN_RE = regex.compile(r"[^\s/]+")
_ROUTE_LEAK_RE = regex.compile(ROUTE_LEAK_PATTERN)


def _IsMixedBasisKeyword(word: str) -> bool:
    return word.lower() in {variant.lower() for variant in Defaults.mixedBasisVariants}

def _UsesMixedBasis(route: str) -> bool:
    return any(_IsMixedBasisKeyword(word) for word in _ROUTE_SLASH_TOKEN_RE.findall(route))

# Swaps Gen <-> GenECP in the route, keeping the user's capitalization style (gen / GEN / Gen)
def _SetMixedBasisKeyword(route: str, needsEcp: bool) -> str:
    def Styled(original: str) -> str:
        target = "GenECP" if needsEcp else "Gen"
        if original.islower():
            return target.lower()
        if original.isupper():
            return target.upper()
        return target
    return _ROUTE_SLASH_TOKEN_RE.sub(lambda word: Styled(word.group()) if _IsMixedBasisKeyword(word.group())
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

def MixedBasisSet(sets: dict[str, MixedBasis], tags: list[str]) -> tuple[MixedBasis | None, str]:
    """The Mixed Basis set a Gaussian stage writes, by its tags: the set a tag names; without one, the unnamed set, or
    the only set there is. An example set ('# @example', keylists.IsExample) is used only when named. (set, '') or
    (None, why not)."""
    from .keylists import IsExample
    from .solvent import SMD_TAG
    named = [tag for tag in tags if tag != SMD_TAG]
    chosen = next((tag for tag in named if tag in sets), None)
    if chosen is not None:
        return sets[chosen], ""
    if named:
        return None, f"Mixed Basis Has No Set {', '.join('@' + tag for tag in named)} ({Where(MIXED_BASIS)})"
    sets = {name: basisSet for name, basisSet in sets.items() if not IsExample(name)}
    if "" in sets:
        return sets[""], ""
    if len(sets) == 1:
        return next(iter(sets.values())), ""
    if not sets:
        return MixedBasis([], []), ""
    return None, (f"Mixed Basis Has Sets {', '.join('@' + name for name in sets)}: Name One in the Route "
                  f"({{name}})")

# Why genFile would skip a job, or '' if it can be generated. The one check shared by genFile and the TUI's route
# previews. Mixed basis and ORCA blocks come from keylists.cusf (keylists.Keylist: the nearest level with the
# section); texts ({level path: unsaved text}, from the TUI's config editor) stands in for those files. Never prompts
def JobFileProblem(route: str, tags: list[str], extensionType: str, elements: set[str],
                   texts: dict[Path, str] | None = None) -> str:
    # {tag} and [group] syntax is parsed out in Catalog.Load and must never reach an input file
    if _ROUTE_LEAK_RE.search(route):
        return "Malformed [ ] Group or Unknown % Directive in the Route"
    # The project's solvent ({smd}): RenderRoute leaves the tag only when it can't be written
    from .solvent import SMD_TAG, Problem as SolventProblem
    if SMD_TAG in tags:
        problem = SolventProblem("orca" if extensionType == Defaults.orcaExtension else "gaussian")
        if problem:
            return problem
    if extensionType == Defaults.gaussianExtension and _UsesMixedBasis(route):
        chosen, problem = MixedBasisSet(Keylist(MIXED_BASIS, texts), tags)
        if chosen is None:
            return problem
        _, _, missing = _FilterMixedBasis(chosen, elements)
        if missing:
            return f"Mixed Basis Has No Basis for {' '.join(sorted(missing))} ({Where(MIXED_BASIS)})"
    if extensionType == Defaults.orcaExtension and tags:
        blocks = Keylist(ORCA_BLOCKS, texts)
        missingTags = [tag for tag in tags if tag not in blocks and tag != SMD_TAG]
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

@functools.lru_cache(maxsize=32)
def _KeywordPattern(keyword: str):
    return regex.compile(rf"(?i){keyword}(?:[=(].*)?")


_OPT_OPTION_SPLIT_RE = regex.compile(r"[(),]")
_BASIS_TOKEN_RE = regex.compile(BASIS_PATTERN)
_CHKBASIS_RE = regex.compile(r"(?i)(?:[^/\s]+/)?chkbas(?:is?)?")


def _KeywordAt(tokens: list[str], keyword: str) -> int | None:
    """The index of a route keyword written bare, keyword=... or keyword(...), or None."""
    pattern = _KeywordPattern(keyword)
    return next((index for index, token in enumerate(tokens) if pattern.fullmatch(token)), None)

def _KeywordOptions(token: str) -> list[str]:
    """'guess=(read,mix)' -> ['read', 'mix']; 'geom=allcheck' -> ['allcheck']; a bare keyword -> []."""
    _, _, options = token.partition("=") if "=" in token else token.partition("(")
    return [option.strip().lower() for option in _OPT_OPTION_SPLIT_RE.split(options) if option.strip()]

def _NamesBasis(tokens: list[str]) -> bool:
    """Whether a Gaussian route names a basis: 'method/basis', or a basis token (Gen and GenECP included)."""
    return any("/" in token or _BASIS_TOKEN_RE.match(token) for token in tokens)

def _ReadsChkBasis(tokens: list[str]) -> bool:
    """Whether a Gaussian route reads its basis from the checkpoint: ChkBasis (or Gaussian's short ChkBas) bare or as
    the basis half of 'method/ChkBas'."""
    return any(_CHKBASIS_RE.fullmatch(token) for token in tokens)

def _ReleaseFrozen(tokens: list[str]) -> bool:
    """Add 'nofreeze' to an optimization's options ('opt' -> 'opt=nofreeze', 'opt=(ts,calcfc)' ->
    'opt=(ts,calcfc,nofreeze)'). False when there's nothing to do: no opt keyword, or nofreeze already written."""
    opt = _KeywordAt(tokens, "opt")
    if opt is None:
        return False
    options = _KeywordOptions(tokens[opt])
    if "nofreeze" in options:
        return False
    options.append("nofreeze")
    tokens[opt] = f"opt={options[0]}" if len(options) == 1 else f"opt=({','.join(options)})"
    return True

def NboReadRoute(route: str) -> str:
    """A Gaussian route that reads the $NBO keylist written after its input (-nbo): pop=nbo7read, joined to its own pop
    options (pop=full -> pop=(full,nbo7read)). An NBO option it already has (pop=nbo, pop=nbo6read) is replaced, since
    -nbo asks for NBO 7 with the keylist."""
    tokens = route.split()
    pop = _KeywordAt(tokens, "pop(?:ulation)?")
    if pop is None:
        return route + " pop=nbo7read"
    options = [option for option in _KeywordOptions(tokens[pop]) if not option.startswith("nbo")] + ["nbo7read"]
    tokens[pop] = f"pop={options[0]}" if len(options) == 1 else f"pop=({','.join(options)})"
    return " ".join(tokens)

def ChainStage(route: str, genInCheckpoint: bool, frozenBy: int = 0) -> tuple[str, str, bool, list[str]]:
    """A Gaussian stage after the first, read from the checkpoint the stages share: (route, molecule section, ChkBasis,
    notes). Explicit on purpose: every rule is said in the notes where a preview shows the stage.
    1. Geometry: Geom=AllCheck (geometry, charge and multiplicity from the checkpoint), unless the stage writes its own
       geom= (geom=check: the title and charge line are written, for a stage that changes them).
    2. Orbitals: 'read' joins the stage's guess options (guess=mix -> guess=(read,mix)), unless it names a guess
       generator (GENERATED_GUESSES).
    3. ChkBasis, from the user's own route, never assumed: one the stage writes is honoured (no Gen sections); a stage
       naming no basis at all after a Gen/GenECP stage gets one (Gaussian would otherwise fall back to STO-3G); a stage
       writing Gen/GenECP itself gets its sections again. ChkBas (Gaussian's short form, e.g. 'ub3lyp/chkbas') counts.
    4. Frozen coordinates: a ModRed stage's frozen coordinates are saved in the checkpoint, and a later stage reading
       its geometry from it keeps them frozen. frozenBy: the last ModRed stage (0: none). An optimization after it
       that reads the checkpoint's geometry (Geom=AllCheck or geom=check) and isn't ModRed itself gets 'nofreeze'
       (opt=(ts,calcfc) -> opt=(ts,calcfc,nofreeze)). A stage writing its own coordinates, a later ModRed stage (its
       own constraints are intended) and stable=opt (not an optimization) are left alone."""
    tokens, notes = route.split(), []
    geom = _KeywordAt(tokens, "geom")
    if geom is None:
        tokens.append("geom=allcheck")
        molecule = NONE
        notes.append("Geom=AllCheck: Geometry, Charge and Multiplicity From the Checkpoint")
    else:
        options = _KeywordOptions(tokens[geom])
        molecule = NONE if "allcheck" in options else CHARGE if "check" in options else FULL
    if frozenBy and molecule != FULL:
        checks = JobChecks(" ".join(tokens), Defaults.gaussianExtension)
        if checks.optimizes and checks.optKind != "ModRed" and _ReleaseFrozen(tokens):
            notes.append(f"NoFreeze: Releases the Coordinates Frozen by Stage {frozenBy}")
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
    chkBasis = _ReadsChkBasis(tokens)
    if not chkBasis and genInCheckpoint and not _NamesBasis(tokens):
        tokens.append("ChkBasis")
        chkBasis = True
        notes.append("ChkBasis: the Gen Basis From the Checkpoint (This Stage Names No Basis)")
    return " ".join(tokens), molecule, chkBasis, notes

def RenderStages(template: RouteTemplate, molecule: Molecule) -> list[RenderedStage]:
    """Every stage of a molecule's job, rendered (RenderRoute: spin groups, U/RO) and, for Gaussian, chained through
    the checkpoint (ChainStage). One stage for a method without stages. What a checkpoint holds is followed from stage
    to stage (%newchk copies it, so it carries over): whether its basis is a Gen one, and the last ModRed stage, whose
    frozen coordinates it keeps."""
    stages, genInCheckpoint, frozenBy = [], False, 0
    gaussian = molecule.extensionType == Defaults.gaussianExtension
    for number, stage in enumerate(template.stages, 1):
        route, tags = RenderRoute(stage, molecule)
        if number == 1 or not gaussian:
            rendered = RenderedStage(route, tags)
        else:
            route, section, chkBasis, notes = ChainStage(route, genInCheckpoint, frozenBy)
            if stage.newChk:
                notes.insert(0, "%newchk: a New Checkpoint, Copied From the One So Far (%OldChk)")
            rendered = RenderedStage(route, tags, section, chkBasis, stage.newChk, notes)
        genInCheckpoint = _UsesMixedBasis(rendered.route) or (rendered.chkBasis and genInCheckpoint)
        if gaussian and JobChecks(rendered.route, Defaults.gaussianExtension).optKind == "ModRed":
            frozenBy = number
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
        agent.Skip(molecule.baseName, problem)
        return False
    text = JobInputText(molecule, intent.checkpoint, intent.nbo7, intent.cpus, intent.memoryRatio)
    with open(molecule.fullPath, 'w') as jobInput:
        jobInput.write(text)
    agent.Written(molecule.fullPath)
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
            # A Gaussian stage's tags name its Mixed Basis set; on a stage with no Gen basis they do nothing
            unused = [tag for stage in stages if not _UsesMixedBasis(stage.route) for tag in stage.tags]
            if unused:
                console.print(f"[warning]Tags {unused} name Mixed Basis sets, but no Gen basis uses them. Ignoring "
                              f"them for {molecule.baseName}.[/warning]")
            # A job with stages always writes its checkpoint: it is how the stages pass on what they made
            chained = len(stages) > 1
            checkpointName = f"{molecule.baseName}.chk"
            sections = []
            for number, stage in enumerate(stages, 1):
                route = stage.route
                # The $NBO keylist goes after the last stage, which is the one told to read it
                if nbo7 and number == len(stages):
                    route = NboReadRoute(route)
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
                    chosen, _ = MixedBasisSet(Keylist(MIXED_BASIS), stage.tags)
                    basisSection, ecpSection, _ = _FilterMixedBasis(chosen or MixedBasis([], []), elements)
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
                # Mixed basis entries for this molecule's elements (keylists.cusf's Mixed Basis)
                if usesMixedBasis:
                    parts.append(basisSection)
                    if ecpSection:
                        parts.append(ecpSection)
                sections.append("\n".join(link0) + "\n" + "\n".join(parts) + "\n")
            text = "--Link1--\n".join(sections)
            # New NBO7 section (the last stage's, whose route reads it: pop=nbo7read)
            text += f"{Defaults.nboKeylist} FILE={molecule.baseName} ARCHIVE $END\n\n" if nbo7 else "\n"
            return text

        case Defaults.orcaExtension:
            # Tagged blocks from ORCA Blocks, in route-card tag order (JobFileProblem checked they all exist); {smd}
            # is the project's solvent's SMD block (solvent.py)
            from .solvent import SMD_TAG, OrcaBlock
            selectedBlocks = []
            if blockTags:
                availableBlocks = Keylist(ORCA_BLOCKS)
                selectedBlocks = [OrcaBlock() if tag == SMD_TAG else availableBlocks[tag] for tag in blockTags
                                  if tag == SMD_TAG or tag in availableBlocks]
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

# ─── Restarting from a failed stage (the Neva-Touch Scheduler's re-run) ─

# A Gaussian section's title and molecule blocks, after its route: blank line(s), the title lines, a blank line, then
# the charge line and any coordinates
_TITLE_AND_MOLECULE = r"(\n[ \t]*\n(?:[ \t]*\n)*)((?:[ \t]*\S[^\n]*\n)+)([ \t]*\n)((?:[ \t]*\S[^\n]*\n)+)"
# ORCA's geometry: '* xyz 0 1' rows '*', or '* xyzfile 0 1 file.xyz'
_ORCA_GEOMETRY = r"(?im)^[ \t]*\*[ \t]*(xyzfile|xyz)[ \t]+(-?\d+)[ \t]+(\d+)[^\n]*\n"
_ORCA_GEOMETRY_END = r"(?m)^[ \t]*\*[ \t]*$"
_GAUSSIAN_INPUT_ROUTE_RE = regex.compile(GAUSSIAN_INPUT_ROUTE)
_TITLE_AND_MOLECULE_RE = regex.compile(_TITLE_AND_MOLECULE)

def _RenamedCheckpoint(line: str, oldName: str, newName: str) -> str:
    """A %chk/%oldchk line with a checkpoint named after the old job renamed for the new one (8a_2.chk -> 8a_re_2.chk)."""
    return regex.sub(rf"(?i)^(%(?:old)?chk=)(.*?){regex.escape(oldName)}(?=[_.])", rf"\g<1>\g<2>{newName}", line)

def RestartInputText(inputPath: Path, program: str, stage: int, molecule: Molecule, oldName: str,
                     newName: str) -> tuple[str, str]:
    """A job's input restarted at one of its stages (1-based): that stage and every one after it, as written, with the
    molecule's coordinates (the failed job's last geometry) in the restarted stage. (text, '') or ('', why it can't).
    Gaussian: the restarted stage writes its own title, charge line and coordinates (a geom=allcheck/check token goes:
    the checkpoint's geometry is the old job's), keeping guess=read and ChkBasis, which read from %oldchk=<the
    checkpoint that stage was writing> when it isn't the first; checkpoints named after the old job are renamed. ORCA
    (one stage): the '* xyz' rows are replaced. Everything else (Link 0, resources, ModRed lines, basis, blocks, NBO)
    is kept as written."""
    if not HasContent(inputPath):
        return "", f"its input {inputPath.name} is gone"
    with MapFile(inputPath) as data:
        stages = InputStageTexts(data, program)
    if not 1 <= stage <= len(stages):
        return "", f"its input {inputPath.name} has no stage {stage}"
    coordinates = "".join(molecule.coordinateList)

    if program == Defaults.orcaExtension:
        text = stages[0]
        geometry = regex.search(_ORCA_GEOMETRY, text)
        if geometry is None:
            return "", f"{inputPath.name} has no '* xyz' or '* xyzfile' geometry"
        if geometry.group(1).lower() == "xyzfile":
            return text[:geometry.start()] + f"* xyz {geometry.group(2)} {geometry.group(3)}\n{coordinates}*\n" + \
                   text[geometry.end():], ""
        end = regex.search(_ORCA_GEOMETRY_END, text, pos=geometry.end())
        if end is None:
            return "", f"{inputPath.name}'s geometry has no closing '*'"
        return text[:geometry.end()] + coordinates + text[end.start():], ""

    sections = []
    for number, section in enumerate(stages[stage - 1:], stage):
        section = section.strip("\n") + "\n"
        routeMatch = _GAUSSIAN_INPUT_ROUTE_RE.search(section)
        if routeMatch is None:
            return "", f"stage {number} of {inputPath.name} has no route"
        link0 = [_RenamedCheckpoint(line, oldName, newName) if not (number == stage and line.lower().startswith("%oldchk"))
                 else line for line in section[:routeMatch.start()].splitlines() if line.strip()]
        route, after = routeMatch.group(1), section[routeMatch.end():]
        if number == stage:
            # The checkpoint this stage was writing holds its last orbitals (and a Gen basis): the new one starts there
            oldCheckpoint = next((line.partition("=")[2].strip() for line in section[:routeMatch.start()].splitlines()
                                  if line.lower().startswith("%chk=")), "")
            if stage > 1 and oldCheckpoint:
                link0 = [line for line in link0 if not line.lower().startswith("%oldchk")]
                position = next((index for index, line in enumerate(link0) if line.lower().startswith("%chk=")), len(link0))
                link0.insert(position, f"%oldchk={oldCheckpoint}")
            tokens = route.split()
            geom = _KeywordAt(tokens, "geom")
            reads = set(_KeywordOptions(tokens[geom])) & {"allcheck", "check"} if geom is not None else set()
            if reads:
                tokens.pop(geom)
                route = " ".join(tokens)
            if "allcheck" not in reads:
                # Its own title and charge line are kept; the coordinates are the last geometry
                written = _TITLE_AND_MOLECULE_RE.match(after)
                if written is None:
                    return "", f"stage {number} of {inputPath.name} has no title and charge line"
                chargeLine = written.group(4).splitlines()[0].strip()
                after = (written.group(1) + written.group(2) + written.group(3) + chargeLine + "\n" + coordinates
                         + after[written.end():])
            else:
                # Everything came from the checkpoint: a title, and the charge line the job read
                rest = after.lstrip(" \t\n")
                after = (f"\n\n{newName}: stage {stage} restarted from the last geometry of {oldName}\n\n"
                         f"{molecule.charge} {molecule.multiplicity}\n{coordinates}" + (f"\n{rest}" if rest else ""))
        sections.append("\n".join(link0) + "\n" + route + after.rstrip("\n") + "\n\n")
    return "--Link1--\n".join(sections), ""

# What each submission header line is filled with, by the flag it contains. Checked in this order, first match wins
# (so a --ntasks-per-node line is a --ntasks one, never -p)
HEADER_FLAGS = [("-J", "name"), ("-o", "output"), ("--ntasks", "cpus"), ("--mem", "memory"), ("-t", "wallTime"),
                ("-p", "partition"), ("-M", "cluster")]

def HeaderKey(line: str) -> str:
    """What a submission header line holds (HEADER_FLAGS), '' for a line filled with nothing."""
    return next((key for flag, key in HEADER_FLAGS if flag in line), "")

def HeaderSends(key: str) -> bool:
    """Whether the active cluster's submission header has a line for this setting (Bridges2's sends no -M), so the
    builders show its row and the job commands take its flag only where it applies."""
    return any(HeaderKey(line) == key for line in Defaults.submissionList)

def WallTimeProblem(value: str) -> str:
    """Why a wall time (whole hours, as wallTime is kept: slurmHandler writes it as H:00:00) can't be used, or ''."""
    return "" if value.isdigit() and int(value) > 0 else "Wall Time Must Be Whole Hours Above 0"

# Reorganized! Now handles SLURM commands independently because of HPC cluster agnosticism. wallTime/partition/cluster:
# this job's own (a builder's rows, the job commands' --wall-time/--partition/--cluster), None for the config's
def slurmHandler(molecule: Molecule, queueName: Path, outputName: Path, cpus: int, jobRam: int,
                 wallTime: str | None = None, partition: str | None = None, cluster: str | None = None) -> None:
    with open(queueName, 'w') as outputFile:
        outputFile.write(SlurmHeaderText(molecule.baseName, outputName, cpus, jobRam, wallTime, partition, cluster))

def SlurmHeaderText(name: str, outputName: Path | str, cpus: int, jobRam: int, wallTime: str | None = None,
                    partition: str | None = None, cluster: str | None = None) -> str:
    """The submission header as slurmHandler writes it (a CREST job's script and its preview build on it too)."""
    fills = {"name": f" {name}", "output": f" {outputName}", "cpus": f"{cpus}", "memory": f"{jobRam}GB",
             "wallTime": f" {wallTime or Defaults.wallTime}:00:00", "partition": f" {partition or Defaults.partition}",
             "cluster": f" {cluster or Defaults.cluster}"}
    return "".join(f"{line}{fills.get(HeaderKey(line), '')}\n" for line in Defaults.submissionList)

# An ORCA or CREST job's plumbing, after its NonVariant environment: run in node scratch, then copy the result files
# into <job name>-scratch/ however the job ends. At the wall time SLURM sends SIGTERM (then SIGKILL ~30 s later), so the
# TERM trap exits and the EXIT trap still copies. The .out itself comes back through SLURM -o
SCRATCH_RUN_SCRIPT = """\
# Run in the node's scratch space
cp {inputs} "$SLURM_SCRATCH/"
cd "$SLURM_SCRATCH"

# Copy the results back however the job ends: normal end, {program} error, or the wall time
results="$SLURM_SUBMIT_DIR/{resultsFolder}"
CopyBack() {{
    mkdir -p "$results"
    for file in {resultFiles}; do
        if [ -f "$file" ]; then cp "$file" "$results/"; fi
    done
}}
trap CopyBack EXIT
trap 'exit 143' TERM

{command}
"""

def ScratchRunScript(inputs: list[str], program: str, resultsFolder: str, resultFiles: list[str], command: str) -> str:
    """The run in node scratch (SCRATCH_RUN_SCRIPT): inputs copied there, command run, resultFiles copied back into
    resultsFolder however the job ends."""
    return SCRATCH_RUN_SCRIPT.format(inputs=" ".join(f'"$SLURM_SUBMIT_DIR/{name}"' for name in inputs),
                                     program=program, resultsFolder=resultsFolder,
                                     resultFiles=" ".join(str(name) for name in resultFiles), command=command)

# Every job reaches the queue through here, so the group broadcast can count the whole invocation (dispatch)
submittedJobs = 0

# The environment sbatch runs in. Inside a job (the Neva-Touch Scheduler's hook submits from one) the job's own SLURM_*
# and SBATCH_* variables are left out, or the new job would inherit the old one's allocation settings
def _SubmitEnvironment() -> dict[str, str] | None:
    if "SLURM_JOB_ID" not in os.environ:
        return None
    return {name: value for name, value in os.environ.items()
            if name == "SLURM_CONF" or not name.startswith(("SLURM_", "SBATCH_"))}

# What SubmitJob returns for a dry run (--dry-run): the script was written, and nothing was submitted or tracked
DRY_RUN = ("", "dry-run")

# --parsable prints just 'ID' or 'ID;cluster'. Output is captured: sbatch must never write to the TUI's screen. Returns
# (job ID, cluster), DRY_RUN, or None (after saying why) if sbatch refused the job: the caller skips only that job
def SubmitJob(queueName: Path) -> tuple[str, str] | None:
    global submittedJobs
    agent.Written(queueName)
    if agent.dryRun:
        console.print(f"[info]Dry run: {queueName} is ready to submit (not submitted).[/info]")
        return DRY_RUN
    try:
        result = subprocess.run(["sbatch", "--parsable", str(queueName)], capture_output=True, text=True,
                                env=_SubmitEnvironment())
    except FileNotFoundError:
        agent.Skip(str(queueName), "sbatch isn't available here", f"sbatch isn't available here, so {queueName} wasn't submitted.")
        return None
    if result.returncode != 0:
        agent.Skip(str(queueName), f"sbatch refused it: {result.stderr.strip()}",
                   f"sbatch refused {queueName}: {result.stderr.strip()}")
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
    outputPath = Path(outputName).resolve()

    # Empty file guard
    if not HasContent(molecule.fullPath):
        agent.Skip(str(molecule.fullPath), "The job file is empty or blank",
                   f"Job file {molecule.baseName} is empty or blank. Skipping submission.")
        return

    # Extract CPU and RAM from the input file via mmap regex, and the --Link1-- stages it runs, each with what it runs
    # (for the stalker's planned stages)
    with MapFile(molecule.fullPath) as data:
        cpus, jobRam = ExtractResources(data, molecule.extensionType, Path(molecule.fullPath).name)
        stageCount = StageCount(data, molecule.extensionType)
        planned = PlannedKinds(data, molecule.extensionType)
        inputText = ExtractOrcaInput(data) if molecule.extensionType == Defaults.orcaExtension else ""
        optimizes = any(JobChecks(route, molecule.extensionType, inputText).optimizes
                        for route in InputRoutes(data, molecule.extensionType))

    # The Neva-Touch Scheduler's hook: the step this job takes once it ends, with the next job's resources. A batch's
    # next job (thenSteps, e.g. -re --then-sp) is written into each job's own chain file, for a job that optimizes: a
    # single point isn't followed by another
    nevaTouch, chain = intent.nevaTouch, intent.chain
    if intent.thenSteps:
        if optimizes:
            chainPath = ChainPath(Path(molecule.fullPath).resolve())
            WriteChain(chainPath, intent.thenSteps)
            nevaTouch, chain = NTS_SP, str(chainPath)
        else:
            nevaTouch, chain = "", None
            console.print(f"[info]{molecule.baseName} doesn't optimize: the Neva-Touch Scheduler has no next job for "
                          "it.[/info]")
    hookCommand = (HookCommand(nevaTouch, outputPath, intent.nextCpus, intent.nextMemoryRatio, intent.reRuns,
                               intent.firstStage, intent.wallTime, intent.partition, intent.cluster, chain)
                   if nevaTouch else [])
    hook = (HookLines(hookCommand, outputPath.parent, outputPath.parent / fileCreation(molecule.baseName, NTS_LOG))
            if hookCommand else "")

    slurmHandler(molecule, queueName, outputName, cpus, jobRam, intent.wallTime, intent.partition, intent.cluster)

    match molecule.extensionType:
        case Defaults.gaussianExtension:
            program = "Gaussian16"
            with open(queueName, 'a') as outputFile:
                outputFile.writelines(Defaults.gaussianNonVariant)
                outputFile.write(f"\ng16 < {molecule.fullPath}\n\n")
                outputFile.write(hook)

        case Defaults.orcaExtension:
            program = "ORCA"
            inputName = fileCreation(molecule.baseName, Defaults.orcaExtension)
            with open(queueName, 'a') as outputFile:
                outputFile.writelines(Defaults.orcaNonVariant)
                outputFile.write(ScratchRunScript(
                    [str(inputName)], "ORCA", str(fileCreation(molecule.baseName, "", Defaults.scratchFolderExtra)),
                    [fileCreation(molecule.baseName, suffix) for suffix in Defaults.orcaResultSuffixes],
                    f"# $(which orca) is necessary: ORCA needs its full path to run in parallel\n$(which orca) {inputName}"))
                outputFile.write(hook)

        # Only Gaussian and ORCA inputs are submitted
        case _:
            agent.Skip(str(molecule.fullPath), f"Only Gaussian ({Defaults.gaussianExtension}) and ORCA "
                                               f"({Defaults.orcaExtension}) inputs can be submitted")
            return

    submitted = SubmitJob(queueName)
    if submitted is None:
        # SubmitJob said why (and recorded the skip)
        console.print(f"[error]Skipping {molecule.baseName}.[/error]")
        return
    if submitted == DRY_RUN:
        return
    jobID, cluster = submitted
    console.print(f"[good]Submitted job {molecule.baseName} to {program} (ID {jobID})[/good]")
    agent.Submitted(molecule.baseName, jobID, cluster, str(outputPath), nevaTouch)
    tracked = TrackedJob(jobID, cluster, molecule.baseName, str(outputPath),
                         jobLocation(intent.partition, intent.cluster), molecule.extensionType,
                         time.time(), [Stage(planned=planned[index] if index < len(planned) else "")
                                       for index in range(stageCount)],
                         nevaTouch=nevaTouch, reRuns=intent.reRuns, firstStage=intent.firstStage,
                         hookCommand=[jobID if word == JOB_ID else word for word in hookCommand])
    # Recorded now, not after the batch: a quick job's Neva-Touch Scheduler hook looks for its record
    TrackJobs([tracked])
    trackedJobs.append(tracked)

