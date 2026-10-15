"""The Neva-Touch Scheduler: a job's next step, taken once the job ends, so the main pipeline (Opt → SP) runs on its own.

An NTS job's SLURM script ends with a hook (HookLines): once the program has ended, the job itself runs
`cu --neva-touch STEP OUTPUT JOBID` in its folder, so the folder's Global/Project config applies as for any cu run.
RunStep takes the step: SP submits the single point (exactly what `cu -sp OUTPUT` makes) when the job ended normally,
and the single point's own hook (Done) reports the chain complete. Whoever takes a step sends its one message.

The hook is the job's own; a stalker only steps in after the job has left the queue (the hook is over by then) for a
step it didn't take: refused (e.g. no sbatch on compute nodes), killed, or never run. It runs the same command
(FireStep), as a run of its own in the job's folder, so a stalker's own CWD and config are never touched."""
import os, shlex, subprocess, sys, time, traceback
from pathlib import Path

from rich.markup import escape

from .actions  import NTS_SP, NTS_DONE
from .console  import console
from .defaults import Defaults
from .fileops  import ExtractFrom, ExtractTermination, TERMINATION_TAIL
from .intent   import NevaTouchIntent, SinglePointIntent
from .notify   import NotifyPersonal
from .stalk    import (TrackedJob, Stage, LoadTracked, UpdateTracked, EndingMessage, SUBMITTED, FAILED, STOPPED,
                       CHAIN_COMPLETE, BY_HOOK, BY_STALKER)

# The job ID in a hook's command: the shell fills it in, so the hook names its own job (its record); a stalker's copy
# of the command has the real ID
JOB_ID = "$SLURM_JOB_ID"
# How long a stalker waits for a step it takes (seconds)
FIRE_TIMEOUT = 600
# How long a hook waits for its job's record, which the submitting run saves just after sbatch (seconds)
RECORD_WAIT = 10


def HookCommand(step: str, output: Path, cpus: int | None = None, memoryRatio: float | None = None) -> list[str]:
    """The hook's command: this CompUtils (its own interpreter, so neither PATH nor `module purge` matters), the step,
    the job's output, its ID, and the resources of the job the step submits (None: the config's)."""
    command = [sys.executable, "-m", "computils", "--neva-touch", step, str(output), JOB_ID]
    if cpus is not None:
        command += ["--cpus", str(cpus)]
    if memoryRatio is not None:
        command += ["--memory", f"{memoryRatio:g}"]
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

def RunStep(intent: NevaTouchIntent) -> None:
    """Takes a job's Neva-Touch Scheduler step, records what it did on the job, and sends the step's one message. A
    hook whose submission failed sends nothing: the step is a stalker's to take, and its message with it."""
    output = Path(intent.output).resolve()
    stepBy = BY_HOOK if os.environ.get("SLURM_JOB_ID") == intent.jobID else BY_STALKER
    termination = ExtractFrom(output, ExtractTermination, -TERMINATION_TAIL, empty="")
    normal = bool(termination) and termination != Defaults.terminationVariants[2]
    console.print(f"[operation]{time.strftime('%Y-%m-%d %H:%M:%S')}: Neva-Touch Scheduler, {intent.step} step for "
                  f"{output.name} (job {intent.jobID}, by the {stepBy}): {termination or 'no termination line'}."
                  f"[/operation]")
    job = _FindJob(intent.jobID, output, wait=stepBy == BY_HOOK)

    outcome, nextJob, note = STOPPED, "", ""
    if intent.step == NTS_DONE:
        outcome = CHAIN_COMPLETE if normal else STOPPED
    elif normal:
        submitted = _SubmitSinglePoint(intent, output)
        if submitted is not None:
            outcome, nextJob = SUBMITTED, f"{submitted.name} (ID {submitted.jobID})"
        else:
            outcome, note = FAILED, f"see {output.stem}.nts"

    def Record(record: TrackedJob) -> None:
        record.stepBy, record.stepOutcome, record.nextJob, record.stepNote = stepBy, outcome, nextJob, note

    if job is None:
        console.print(f"[warning]Job {intent.jobID} isn't tracked; its step is reported without a record.[/warning]")
        job = TrackedJob(intent.jobID, "", output.stem, str(output), "", "", time.time(), [Stage()],
                         nevaTouch=intent.step)
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
