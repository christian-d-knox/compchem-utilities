"""The Neva-Touch Scheduler: a job's next step, taken once the job ends, so the main pipeline (Opt → SP) runs on its own.

An NTS job's SLURM script ends with a hook (HookLines): once the program has ended, the job itself runs
`cu --neva-touch STEP OUTPUT JOBID` in its folder, so the folder's Global/Project config applies as for any cu run.
RunStep takes the step: SP submits the single point (exactly what `cu -sp OUTPUT` makes) when the job ended normally,
and the single point's own hook (Done) reports the chain complete. An opt that ran out of optimizer steps is re-run
instead: its own input from the stage that ran out onward, with its last geometry (jobs.RestartInputText), up to
ntsReRunLimit times per stage, its hook the same SP step. Whoever takes a step sends its one message.

The hook is the job's own; a stalker only steps in after the job has left the queue (the hook is over by then) for a
step it didn't take: refused (e.g. no sbatch on compute nodes), killed, or never run. It runs the same command
(FireStep), as a run of its own in the job's folder, so a stalker's own CWD and config are never touched."""
import os, shlex, subprocess, sys, time, traceback
from pathlib import Path

from rich.markup import escape

from .actions  import NTS_SP, NTS_DONE
from .console  import console
from .defaults import Defaults
from .fileops  import (ExtractFrom, ExtractTermination, ExtractProgram, StageCount, IncrementSuffix, fileCreation,
                       TERMINATION_TAIL)
from .intent   import NevaTouchIntent, SinglePointIntent, RunIntent
from .notify   import NotifyPersonal
from .stalk    import (TrackedJob, Stage, LoadTracked, TrackJobs, UpdateTracked, EndingMessage, ReadEnding, SUBMITTED,
                       FAILED, STOPPED, CHAIN_COMPLETE, RERUN, GAVE_UP, BY_HOOK, BY_STALKER, NTS_LOG)

# The job ID in a hook's command: the shell fills it in, so the hook names its own job (its record); a stalker's copy
# of the command has the real ID
JOB_ID = "$SLURM_JOB_ID"
# How long a stalker waits for a step it takes (seconds)
FIRE_TIMEOUT = 600
# How long a hook waits for its job's record, which the submitting run saves just after sbatch (seconds)
RECORD_WAIT = 10


def HookCommand(step: str, output: Path, cpus: int | None = None, memoryRatio: float | None = None, reRuns: int = 0,
                firstStage: int = 1) -> list[str]:
    """The hook's command: this CompUtils (its own interpreter, so neither PATH nor `module purge` matters), the step,
    the job's output, its ID, the resources of the job the step submits (None: the config's) and, for a re-run, its
    count and the original stage it starts at (so a hook without its record still counts right)."""
    command = [sys.executable, "-m", "computils", "--neva-touch", step, str(output), JOB_ID]
    if cpus is not None:
        command += ["--cpus", str(cpus)]
    if memoryRatio is not None:
        command += ["--memory", f"{memoryRatio:g}"]
    if reRuns:
        command += ["--reruns", str(reRuns)]
    if firstStage != 1:
        command += ["--first-stage", str(firstStage)]
    return command

def HookLines(command: list[str], folder: Path, log: Path) -> str:
    """The end of an NTS job's script, after its program line. The program's exit status stays the job's. The hook runs
    in a subshell (ORCA's EXIT trap copies results from the scratch folder it is in), and everything it prints goes to
    the log: SLURM -o is the job's own output, which the stalker, GoodVibes and cclib read."""
    words = " ".join(f'"{word}"' if word == JOB_ID else shlex.quote(word) for word in command)
    return ("status=$?\n"
            f"# Neva-Touch Scheduler: this chain's next step, once the program has ended. Its messages go to {log.name},"
            " never into the job's own output\n"
            f"( cd {shlex.quote(str(folder))} && {words} ) >> {shlex.quote(str(log))} 2>&1\n"
            "exit $status\n")


# ─── Taking a step (the hook, or a stalker's FireStep) ────────────────

def _FindJob(jobID: str, output: Path, wait: bool) -> TrackedJob | None:
    """The job's record: by ID, the one with this output first (two clusters can share an ID). A hook may run before
    the submitting run has saved it, so the hook waits a little."""
    for _ in range(RECORD_WAIT * 2 if wait else 1):
        matches = [job for job in LoadTracked() if job.jobID == jobID]
        if matches:
            return next((job for job in matches if Path(job.outputPath) == output), matches[0])
        if wait:
            time.sleep(0.5)
    return None

def _SubmitSinglePoint(intent: NevaTouchIntent, output: Path) -> TrackedJob | None:
    """The single point of a finished opt, through the one execution path (what `cu -sp OUTPUT` runs), with the
    resources the hook carries; its own hook ends the chain. None if it wasn't submitted (genFile or sbatch said why)."""
    from .dispatch import Dispatch
    spIntent = SinglePointIntent(files=[output], nevaTouch=NTS_DONE, cpus=intent.cpus, memoryRatio=intent.memoryRatio)
    try:
        submitted = Dispatch(spIntent)
    except Exception:
        console.print(traceback.format_exc(), markup=False, highlight=False)
        return None
    return submitted[0] if submitted else None

def _ReRunName(output: Path) -> str:
    """The re-run's name, as `cu -re` names one (8a -> 8a_re -> 8a_re2), past any job that has run here (has an output).
    An input alone is rewritten, as `cu -re` does: e.g. one a refused hook wrote, which the fallback then submits."""
    name = IncrementSuffix(output.stem, Defaults.reRunExtra)
    while (output.parent / fileCreation(name, Defaults.outputExtension)).exists():
        name = IncrementSuffix(name, Defaults.reRunExtra)
    return name

def _SubmitReRun(intent: NevaTouchIntent, output: Path, stage: int, originalStage: int,
                 count: int) -> tuple[TrackedJob | None, str]:
    """An opt that ran out of optimizer steps, re-run from its last geometry: its own input from the stage that ran out
    (jobs.RestartInputText) through every stage after it, submitted as written (what `cu -r` runs) with this step's
    hook again, its count and the original stage it starts at. (the job, '') or (None, why it wasn't submitted)."""
    from .dispatch import Dispatch, _LoadMolecule
    from .jobs     import RestartInputText
    # The last geometry, read as `cu -re` reads it (and written to <name>_failed.xyz)
    molecule = _LoadMolecule(output, "_failed")
    if molecule is None:
        return None, f"no geometry in {output.name}"
    program = molecule.sourceProgram
    name = _ReRunName(output)
    text, problem = RestartInputText(output.with_suffix(program), program, stage, molecule, output.stem, name)
    if problem:
        return None, problem
    inputPath = fileCreation(name, program)
    inputPath.write_text(text)
    console.print(f"[info]Wrote {inputPath}: stage {originalStage} onward, from the last geometry of {output.name}."
                  f"[/info]")
    reRun = RunIntent(files=[inputPath], nevaTouch=NTS_SP, nextCpus=intent.cpus, nextMemoryRatio=intent.memoryRatio,
                      reRuns=count, firstStage=originalStage)
    try:
        submitted = Dispatch(reRun)
    except Exception:
        console.print(traceback.format_exc(), markup=False, highlight=False)
        return None, f"see {output.stem}{NTS_LOG}"
    return (submitted[0], "") if submitted else (None, f"see {output.stem}{NTS_LOG}")

def _Untracked(intent: NevaTouchIntent, output: Path) -> TrackedJob:
    """A stand-in record for a job that isn't tracked, so it is still read (its stages counted from its input) and its
    step reported."""
    program = ExtractFrom(output, ExtractProgram, output.suffix, empty="")
    inputPath = output.with_suffix(program) if program else output
    stages = ExtractFrom(inputPath, StageCount, program, empty=1) if program else 1
    return TrackedJob(intent.jobID, "", output.stem, str(output), "", program, time.time(),
                      [Stage() for _ in range(max(1, stages))], nevaTouch=intent.step, reRuns=intent.reRuns,
                      firstStage=intent.firstStage)

def RunStep(intent: NevaTouchIntent) -> None:
    """Takes a job's Neva-Touch Scheduler step, records what it did on the job, and sends the step's one message. A
    hook whose submission failed sends nothing: the step is a stalker's to take, and its message with it.
    SP: a stage that ran out of optimizer steps (whatever the termination line: ORCA can still end normally) is re-run
    from its last geometry, up to ntsReRunLimit times per stage; otherwise a normal ending gets its single point."""
    output = Path(intent.output).resolve()
    stepBy = BY_HOOK if os.environ.get("SLURM_JOB_ID") == intent.jobID else BY_STALKER
    termination = ExtractFrom(output, ExtractTermination, -TERMINATION_TAIL, empty="")
    normal = bool(termination) and termination != Defaults.terminationVariants[2]
    job = _FindJob(intent.jobID, output, wait=stepBy == BY_HOOK)
    tracked = job is not None
    if not tracked:
        console.print(f"[warning]Job {intent.jobID} isn't tracked; its step is reported without a record.[/warning]")
        job = _Untracked(intent, output)
    # The stage that ended the job (0-based, the job's own), and whether it ran out of steps: the stalker's reading,
    # saved as a ping would, so the Job Stalker shows how it ended before its next ping
    ending = ReadEnding(job) if job.program else job
    if tracked and ending is not job:
        TrackJobs([ending])
    endedIn, outOfSteps = ending.stage, ending.stages[ending.stage].outOfSteps
    console.print(f"[operation]{time.strftime('%Y-%m-%d %H:%M:%S')}: Neva-Touch Scheduler, {intent.step} step for "
                  f"{output.name} (job {intent.jobID}, by the {stepBy}): {termination or 'no termination line'}"
                  f"{f', stage {intent.firstStage + endedIn} ran out of optimizer steps' if outOfSteps else ''}."
                  f"[/operation]")

    outcome, nextJob, note, stepStage, stepCount = STOPPED, "", "", 0, 0
    if intent.step == NTS_DONE:
        outcome = CHAIN_COMPLETE if normal else STOPPED
    elif outOfSteps:
        # Counted per stage: a failure in the stage this job re-runs adds to its count; a later stage starts its own
        stepStage = intent.firstStage + endedIn
        stepCount = intent.reRuns + 1 if endedIn == 0 else 1
        if stepCount > max(0, Defaults.ntsReRunLimit):
            outcome = GAVE_UP
        else:
            submitted, problem = _SubmitReRun(intent, output, endedIn + 1, stepStage, stepCount)
            if submitted is not None:
                outcome, nextJob = RERUN, f"{submitted.name} (ID {submitted.jobID})"
            else:
                outcome, note = FAILED, problem
    elif normal:
        submitted = _SubmitSinglePoint(intent, output)
        if submitted is not None:
            outcome, nextJob = SUBMITTED, f"{submitted.name} (ID {submitted.jobID})"
        else:
            outcome, note = FAILED, f"see {output.stem}{NTS_LOG}"

    def Record(record: TrackedJob) -> None:
        record.stepBy, record.stepOutcome, record.nextJob, record.stepNote = stepBy, outcome, nextJob, note
        record.stepStage, record.stepCount = stepStage, stepCount

    Record(job)
    if outcome == FAILED and stepBy == BY_HOOK:
        sent = False
        console.print("[warning]Nothing was submitted; the next stalker to see this job takes the step "
                      "(its message with it).[/warning]")
    else:
        message = EndingMessage(job, termination)
        sent = NotifyPersonal(message)
        console.print(f"[info]{'Sent' if sent else 'Not sent (a stalker sends it once the job leaves the queue)'}: "
                      f"{escape(message)}[/info]", highlight=False)

    def Store(record: TrackedJob) -> None:
        Record(record)
        record.notified = record.notified or sent
    UpdateTracked(job.key, Store)


def FireStep(job: TrackedJob) -> None:
    """A stalker takes a job's step its hook didn't (stalk._FireFallbacks, once claimed): the hook's own command, as a
    run of its own in the job's folder, its output added to the job's log. If that run couldn't record anything (the
    folder is gone, it crashed), the failure is recorded here, for the stalker's message."""
    folder, log, problem = Path(job.outputPath).parent, job.logPath, ""
    if not folder.is_dir():
        problem = f"its folder {folder} is gone"
    elif not Path(job.outputPath).is_file():
        problem = f"its output {Path(job.outputPath).name} is gone"
    else:
        try:
            with open(log, "a", encoding="utf-8") as logFile:
                logFile.write(f"\n# {time.strftime('%Y-%m-%d %H:%M:%S')}: taken by a stalker (the hook submitted "
                              f"nothing)\n")
                logFile.flush()
                result = subprocess.run(job.hookCommand, cwd=folder, stdin=subprocess.DEVNULL, stdout=logFile,
                                        stderr=subprocess.STDOUT, timeout=FIRE_TIMEOUT)
            if result.returncode != 0:
                problem = f"the step stopped with an error (see {log.name})"
        except subprocess.TimeoutExpired:
            problem = f"the step took over {FIRE_TIMEOUT // 60} minutes (see {log.name})"
        except OSError as error:
            problem = str(error)
    if not problem:
        return

    def Failed(record: TrackedJob) -> None:
        # Only if the step itself recorded nothing
        if record.stepBy != BY_STALKER:
            record.stepBy, record.stepOutcome, record.stepNote = BY_STALKER, FAILED, problem
    UpdateTracked(job.key, Failed)
