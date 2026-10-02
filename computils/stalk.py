"""Job stalking: every submitted job is tracked in <binDirectory>/tracked-jobs.json, so any later run (the TUI's Job
Stalker, a bare `cu -st`) can re-hook into it. Polling (PollQueue) is split from rendering (ProgressLines): the CLI
prints the lines, the TUI shows them.

Outputs are read the bookmarked way: a termination line is a bookmark. Reporting data is always the LAST instance, so
it is searched backwards from EOF and never further back than the latest bookmark; reading forward only happens at a
step's start, to learn what that step reports (its route)."""
import json, os, subprocess, time
from dataclasses import dataclass, field, asdict, fields
from pathlib import Path

from .console  import console
from .defaults import Defaults, BackUpFile
from .notify   import NotifyPersonal
from .fileops  import (MapFile, HasContent, FindTermination, TerminationVariant, IsInternalStep, ExtractStability,
                       ExtractConvergence, ExtractRouteEcho, ExtractOrcaInput, OrcaRouteLine, JobChecks)

TRACKED_FILE = "tracked-jobs.json"
# JOBID|JOBNAME|STATE|START_TIME|CURRENT_DURATION for each of the user's jobs. '|'-separated and unpadded, so long job
# names aren't cut to a column width and a field containing spaces can't shift the others
QUEUE_COMMAND = ["squeue", "-h", "--me", "--format=%i|%j|%T|%S|%M"]
# Telegram's message length limit
MESSAGE_LIMIT = 4096

# A stage's status, as ProgressLines writes it
AWAITING, IN_PROGRESS, COMPLETE, ERROR, STOPPED, NOT_REACHED = (
    "Awaiting Link", "In Progress", "Complete", "Error", "Stopped", "Not Reached")
# A job's state before its first ping, and once it has ended (otherwise squeue's: PENDING, RUNNING, ...)
AWAITING_PING, ENDED = "AWAITING PING", "ENDED"


@dataclass
class Stage:
    """One --Link1-- stage of a job: its status, its current step's label, and the last reports read for it. A report
    is kept once read, so an opt's convergence still shows during the freq step Gaussian adds to it."""
    status:      str              = AWAITING
    label:       str              = ""      # the current step's: Opt, Stability, Freq, SP
    reports:     bool             = False   # whether any of its steps optimizes or runs a stability analysis
    convergence: list[int] | None = None    # [criteria met, criteria]
    stable:      bool | None      = None


@dataclass
class TrackedJob:
    jobID:       str
    cluster:     str               # '' unless the submission header sends -M
    name:        str
    outputPath:  str               # absolute
    location:    str               # jobLocation() at submission
    program:     str               # the input's extension
    submitted:   float
    stages:      list[Stage]
    # Reading state: the current stage, the bookmark (offset just past the latest termination line) and whether the
    # current step's route has been read yet, with what it asks for
    stage:          int  = 0
    bookmark:       int  = 0
    stepRead:       bool = False
    stepOptimizes:  bool = False
    stepStability:  bool = False
    # From squeue
    state:       str   = AWAITING_PING
    startAt:     str   = ""
    elapsed:     str   = ""
    # Ending
    termination: str   = ""
    endedAt:     float = 0.0
    notified:    bool  = False

    @property
    def key(self) -> str:
        return f"{self.cluster}:{self.jobID}"

    @property
    def ended(self) -> bool:
        return self.state == ENDED

    @classmethod
    def FromRecord(cls, record: dict) -> "TrackedJob":
        known = {entry.name for entry in fields(cls)}
        values = {name: value for name, value in record.items() if name in known}
        values["stages"] = [Stage(**stage) for stage in record.get("stages", [])] or [Stage()]
        return cls(**values)


# Where stalked jobs run: jobs are submitted this same run by slurmHandler(), so its Defaults values are exact.
# The cluster is only included when the submission header actually sends -M; otherwise just the partition
def jobLocation() -> str:
    submitsCluster = any("-M" in line for line in Defaults.submissionList)
    parts = [Defaults.cluster if submitsCluster else "", Defaults.partition]
    return " / ".join(part for part in parts if part)


# ─── The tracked-jobs store ───────────────────────────────────────────

def _StorePath() -> Path:
    return Path(Defaults.binDirectory).expanduser() / TRACKED_FILE

def _ReadStore() -> dict[str, TrackedJob]:
    path = _StorePath()
    if not path.is_file():
        return {}
    try:
        records = json.loads(path.read_text())
        jobs = [TrackedJob.FromRecord(record) for record in records]
    except (ValueError, TypeError, KeyError):
        console.print(f"[warning]{path} couldn't be read; starting a new tracked-jobs list.[/warning]")
        BackUpFile(path)
        return {}
    return {job.key: job for job in jobs}

def _Prune(jobs: dict[str, TrackedJob]) -> dict[str, TrackedJob]:
    """Drops ended jobs older than trackedJobHours."""
    cutoff = time.time() - Defaults.trackedJobHours * 3600
    return {key: job for key, job in jobs.items() if not (job.ended and job.endedAt < cutoff)}

def LoadTracked() -> list[TrackedJob]:
    """Every tracked job, in submission order (ended ones past trackedJobHours dropped)."""
    return sorted(_Prune(_ReadStore()).values(), key=lambda job: job.submitted)

def HasLiveJobs() -> bool:
    """Whether any tracked job hasn't ended yet (the TUI's stalking routine only pings while one hasn't)."""
    return any(not job.ended for job in LoadTracked())

# A CLI stalker and the TUI can run at once, each holding its own copies. A record further along wins (ended beats
# running, a later bookmark beats an earlier one), and a notification sent by either one counts for both
def _Progress(job: TrackedJob) -> tuple:
    return job.ended, job.stage, job.bookmark, job.stepRead

def _SaveStore(jobs: list[TrackedJob] = (), dropped: set[str] = frozenset()) -> None:
    """Merges these jobs into the store (re-read first) and removes the dropped keys, writing it atomically."""
    stored = _Prune(_ReadStore())
    for job in jobs:
        old = stored.get(job.key)
        if old is not None and _Progress(old) > _Progress(job):
            old.notified = old.notified or job.notified
            continue
        if old is not None:
            job.notified = job.notified or old.notified
        stored[job.key] = job
    for key in dropped:
        stored.pop(key, None)
    path = _StorePath()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps([asdict(job) for job in sorted(stored.values(), key=lambda job: job.submitted)],
                                    indent=1))
    os.replace(temporary, path)

def TrackJobs(jobs: list[TrackedJob]) -> None:
    if jobs:
        _SaveStore(jobs)

def DismissJobs(keys: set[str]) -> None:
    _SaveStore(dropped=keys)


# ─── Reading the queue ────────────────────────────────────────────────

def _ReadQueue(jobs: list[TrackedJob]) -> dict[tuple[str, str], list[str]] | None:
    """{(cluster, job ID): [state, start time, current duration]} for the user's queued jobs, or None if squeue failed.
    Each cluster jobs were submitted to with -M is asked in a call of its own, and the jobs without one on the default
    cluster in another (adding -M would hide the default cluster's jobs). A call's rows are filed under the cluster it
    asked: squeue only labels them with 'CLUSTER: name' lines when headers are shown, and -h hides them."""
    clusters = sorted({job.cluster for job in jobs if job.cluster})
    calls = ([("", QUEUE_COMMAND)] if any(not job.cluster for job in jobs) else []) + \
            [(cluster, QUEUE_COMMAND + ["-M", cluster]) for cluster in clusters]
    queue = {}
    for cluster, command in calls:
        try:
            result = subprocess.run(command, capture_output=True, text=True)
        except FileNotFoundError:
            return None
        if result.returncode != 0:
            return None
        for line in result.stdout.splitlines():
            fields = line.split("|")
            if len(fields) == 5:
                queue[(cluster, fields[0].strip())] = fields[2:]
    return queue


# ─── Reading an output, bookmark by bookmark ──────────────────────────

def _ReadStep(job: TrackedJob, data) -> bool:
    """Reads forward from the bookmark to the current step's route, and so what it reports. False if it isn't
    written yet."""
    if job.program == Defaults.orcaExtension:
        inputText = ExtractOrcaInput(data)
        route = OrcaRouteLine(inputText)
        if not route:
            return False
    else:
        inputText, route = "", ExtractRouteEcho(data, job.bookmark)
        if route is None:
            return False
    checks = JobChecks(route, job.program, inputText)
    stage = job.stages[job.stage]
    stage.status, stage.label = IN_PROGRESS, checks.label
    stage.reports = stage.reports or checks.optimizes or checks.stability
    job.stepRead, job.stepOptimizes, job.stepStability = True, checks.optimizes, checks.stability
    return True

def _ReadReports(job: TrackedJob, data, end: int | None = None) -> None:
    """The current step's reporting data: backwards from end (EOF, or a termination line), never past the bookmark."""
    stage = job.stages[job.stage]
    if job.stepOptimizes:
        convergence = ExtractConvergence(data, job.bookmark, end)
        if convergence is not None:
            stage.convergence = list(convergence)
    if job.stepStability:
        stable = ExtractStability(data, job.bookmark, end)
        if stable is not None:
            stage.stable = stable

def _End(job: TrackedJob, termination: str) -> None:
    job.state, job.termination, job.endedAt = ENDED, termination, time.time()
    current = job.stages[job.stage]
    if termination == Defaults.terminationVariants[2]:
        current.status = ERROR
    elif not termination:
        # Cancelled or out of time: a stage that had started was stopped, one that hadn't was never reached
        current.status = STOPPED if current.status == IN_PROGRESS else NOT_REACHED
    for stage in job.stages[job.stage + 1:]:
        stage.status = NOT_REACHED

def _ReadProgress(job: TrackedJob, final: bool = False) -> None:
    """Brings a job's reading state up to date. final: the job has left the queue, so whatever ending the output has
    is its ending (none at all: it was cancelled, ran out of time, or never wrote one)."""
    path = Path(job.outputPath)
    # A running job's current stage has started, even before its route is written
    if not final and job.stages[job.stage].status == AWAITING:
        job.stages[job.stage].status = IN_PROGRESS
    if not HasContent(path):
        if final:
            _End(job, "")
        return
    with MapFile(path) as data:
        while not job.ended:
            if not job.stepRead and not _ReadStep(job, data):
                break
            if FindTermination(data, job.bookmark) is None:
                _ReadReports(job, data)
                break
            # A step has ended since the bookmark. Several may have, if the job outran the pings: take the first
            termLine = FindTermination(data, job.bookmark, reverse=False)
            termination = TerminationVariant(termLine)
            internal = IsInternalStep(data, termLine) if job.program == Defaults.gaussianExtension else False
            if internal is None and not final and termination != Defaults.terminationVariants[2]:
                # Gaussian hasn't written the next line yet, so this can't be told from its own freq step: next ping
                _ReadReports(job, data, termLine.start())
                break
            # Finalize the step from its termination back to the old bookmark, then move the bookmark past it
            _ReadReports(job, data, termLine.start())
            lineEnd = data.find(b"\n", termLine.end())
            job.bookmark = len(data) if lineEnd < 0 else lineEnd + 1
            job.stepRead = False
            if internal:
                continue
            if termination == Defaults.terminationVariants[2]:
                _End(job, termination)
                break
            job.stages[job.stage].status = COMPLETE
            if job.stage + 1 >= len(job.stages):
                _End(job, termination)
                break
            job.stage += 1
            job.stages[job.stage].status = IN_PROGRESS
    if final and not job.ended:
        _End(job, "")


# ─── Polling ──────────────────────────────────────────────────────────

def PollQueue(jobs: list[TrackedJob]) -> list[TrackedJob] | None:
    """One ping: reads the queue, brings every unended job up to date, notifies (once, batched) about the ones that
    ended, and saves. Returns the jobs that ended on this ping, or None if squeue failed (nothing changes then)."""
    live = [job for job in jobs if not job.ended]
    queue = _ReadQueue(live) if live else {}
    if queue is None:
        return None
    endedNow = []
    for job in live:
        row = queue.get((job.cluster, job.jobID))
        if row is not None:
            job.state, job.startAt, job.elapsed = row
            if job.state == "RUNNING":
                _ReadProgress(job)
        else:
            _ReadProgress(job, final=True)
        if job.ended:
            endedNow.append(job)
    NotifyEndings(endedNow)
    _SaveStore(jobs)
    return endedNow

def CheckTracked() -> None:
    """Startup check, every run: prunes the list and files the endings of jobs that left the queue since the last
    look, so a stalker's first ping only covers live jobs. One squeue call, and none if nothing is still live; a
    missing squeue (e.g. not on a cluster) is skipped silently. Running jobs aren't read here."""
    jobs = LoadTracked()
    live = [job for job in jobs if not job.ended]
    if not live:
        return
    queue = _ReadQueue(live)
    if queue is None:
        return
    gone = [job for job in live if (job.cluster, job.jobID) not in queue]
    for job in gone:
        _ReadProgress(job, final=True)
    NotifyEndings(gone, caughtUp=True)
    if gone:
        _SaveStore(gone)


# ─── Messages ─────────────────────────────────────────────────────────

# How a job ended, as a phrase for messages. The matched line itself only reads well for Gaussian: ORCA's is
# 'terminated normally' ("has finished via terminated normally")
def EndingPhrase(termination: str) -> str:
    return "error termination" if termination == Defaults.terminationVariants[2] else "normal termination"

def _OnLocation(job: TrackedJob) -> str:
    return f" on {job.location}" if job.location else ""

def NotifyEndings(jobs: list[TrackedJob], caughtUp: bool = False) -> None:
    """One Telegram message for every job here not yet notified (split only at Telegram's length limit). A single
    ending reads as it always has."""
    # Another stalker (the CLI and the TUI can run at once) may already have sent one
    stored = _ReadStore() if jobs else {}
    for job in jobs:
        if job.key in stored and stored[job.key].notified:
            job.notified = True
    pending = [job for job in jobs if job.ended and not job.notified]
    if not pending:
        return
    if len(pending) == 1:
        job = pending[0]
        NotifyPersonal(f"Job {job.name}{_OnLocation(job)} has finished via {EndingPhrase(job.termination)}."
                       if job.termination else f"Job {job.name}{_OnLocation(job)} left the queue without a termination line.")
    else:
        header = (f"Caught up on {len(pending)} finished jobs:" if caughtUp else f"{len(pending)} jobs have finished:")
        lines = [f"• {job.name}{_OnLocation(job)}: "
                 + (EndingPhrase(job.termination) if job.termination else "left the queue without a termination line")
                 for job in pending]
        message = header
        for line in lines:
            if len(message) + 1 + len(line) > MESSAGE_LIMIT:
                NotifyPersonal(message)
                message = header.rstrip(":") + " (continued):"
            message += "\n" + line
        NotifyPersonal(message)
    for job in pending:
        job.notified = True

def _StageReport(stage: Stage) -> list[str]:
    """A stage's reports, one per line ('' when there's nothing to say yet)."""
    lines = []
    if stage.convergence is not None:
        lines.append(f"Converged on {stage.convergence[0]} out of {stage.convergence[1]} criteria.")
    if stage.stable is not None:
        lines.append("The wave function has stabilized." if stage.stable else "The wave function has not stabilized.")
    if not lines and stage.status in (IN_PROGRESS, COMPLETE) and stage.label and not stage.reports:
        lines.append("No reporting criteria.")
    return lines

def StageLines(job: TrackedJob) -> list[str]:
    """'Stage N (Status): report' for every stage; a second report goes on its own line below."""
    lines = []
    for number, stage in enumerate(job.stages, 1):
        reports = _StageReport(stage)
        lines.append(f"Stage {number} ({stage.status})" + (f": {reports[0]}" if reports else ""))
        lines.extend(f"    {report}" for report in reports[1:])
    return lines

def StagePosition(job: TrackedJob) -> str:
    """'2/3' (with the step, e.g. '1/2, Freq', once it is known)."""
    label = job.stages[job.stage].label
    return f"{job.stage + 1}/{len(job.stages)}" + (f", {label}" if label else "")

def HeadLine(job: TrackedJob) -> tuple[str, str]:
    """(the job's one-line status, its console style)."""
    count = len(job.stages)
    stages = f"{count} stage{'s' if count != 1 else ''}"
    if job.ended:
        if not job.termination:
            return (f"Job {job.name} left the queue without a termination line (cancelled, out of time, or no output "
                    f"written).", "warning")
        style = "error" if job.termination == Defaults.terminationVariants[2] else "good"
        return f"Job {job.name} has encountered {EndingPhrase(job.termination)}", style
    if job.state == "PENDING":
        return f"Job {job.name} is currently pending ({stages}). Expected start time is {job.startAt}", "warning"
    if job.state == "RUNNING":
        return f"Job {job.name} is currently running (Stage {StagePosition(job)}).", "info"
    if job.state == AWAITING_PING:
        return f"Job {job.name} is awaiting its first ping ({stages}).", "info"
    return f"Job {job.name} is {job.state.lower()} ({stages}).", "info"

def ProgressLines(job: TrackedJob) -> list[str]:
    """Everything reported for a job below its head line: the duration while running, then every stage. The one line
    builder for the CLI and the TUI's Details pane."""
    lines = []
    if job.state == "RUNNING" and not job.ended:
        lines.append(f"Current duration is {job.elapsed}")
        if not HasContent(Path(job.outputPath)):
            lines.append("Its output hasn't been written yet.")
    if job.state != "PENDING" and job.state != AWAITING_PING:
        lines.extend(StageLines(job))
    return lines


# ─── The CLI stalker ──────────────────────────────────────────────────

def _PrintJob(job: TrackedJob) -> None:
    head, style = HeadLine(job)
    console.print(f"[{style}]{head}[/{style}]")
    for line in ProgressLines(job):
        console.print(f"[{style}]    {line}[/{style}]")

# Follows tracked jobs through the queue: progress while they run, then how each one ended (reported once, and again in
# the final summary)
def jobStalking(jobs: list[TrackedJob], duration: int, frequency: int, loop: bool) -> None:
    if not jobs:
        console.print("[info]No tracked jobs to stalk.[/info]")
        return
    startTime = time.monotonic()
    while True:
        endedNow = PollQueue(jobs)
        if endedNow is None:
            console.print("[warning]Could not read the queue (squeue failed). Trying again at the next ping.[/warning]")
        else:
            for job in jobs:
                if not job.ended or job in endedNow:
                    _PrintJob(job)

        # If all jobs for stalking are done, finish execution and release the terminal
        if all(job.ended for job in jobs):
            console.print("[operation]All jobs tagged for stalking have finished.[/operation]")
            break
        # Timeout: start another round when looping, otherwise stop
        if time.monotonic() - startTime >= duration * 60:
            if not loop:
                console.print("[warning]Job stalking terminated by timeout. Your jobs are still running.[/warning]")
                console.print("[info]Consider editing the default stalk duration and frequency if your jobs regularly "
                              "timeout. `cu -st` picks them back up.[/info]")
                break
            startTime = time.monotonic()

        lastPing = time.strftime("%a %I:%M:%S", time.localtime())
        console.print(f"[operation]Waiting {frequency * 60} seconds to ping the queue again. Last ping at {lastPing}"
                      " local time.[/operation]")
        time.sleep(frequency * 60)

    # Final summary: every ended job together, plus any still queued at a timeout
    console.print("[operation]Stalking summary:[/operation]")
    for job in jobs:
        if job.ended:
            _PrintJob(job)
    for job in jobs:
        if not job.ended:
            console.print(f"[info]Job {job.name} is still in the queue.[/info]")
