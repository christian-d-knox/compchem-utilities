"""Job stalking: every submitted job is tracked in <binDirectory>/tracked-jobs.json, so any later run (the TUI's Job
Stalker, a bare `cu -st`) can re-hook into it. Polling (PollQueue) is split from rendering (ProgressLines): the CLI
prints the lines, the TUI shows them.

Outputs are read the bookmarked way: a termination line is a bookmark. Reporting data is always the LAST instance, so
it is searched backwards from EOF and never further back than the latest bookmark; reading forward only happens at a
step's start, to learn what that step reports (its route)."""
import copy, json, os, subprocess, sys, time
from dataclasses import dataclass, field, asdict, fields
from pathlib import Path

from .actions  import NTS_SP, NTS_DONE, WAIT_ANY, WAIT_CHAIN, WAIT_ALL
from .console  import console
from .datafile import RecordEndings
from .defaults import Defaults, BackUpFile
from .notify   import NotifyPersonal
from .fileops  import (FileLock, MapFile, HasContent, FindTermination, TerminationVariant, IsInternalStep,
                       ExtractStability, ExtractConvergence, ExtractOutOfSteps, ExtractRouteEcho, ExtractOrcaInput,
                       OrcaRouteLine, JobChecks, CREST_PROGRAM)
from .crest    import CONFORMER_SEARCH

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
# What a job's Neva-Touch Scheduler step did (nevatouch.RunStep), and who took it
SUBMITTED, FAILED, STOPPED, CHAIN_COMPLETE = "Submitted", "Couldn't Submit", "Stopped", "Chain Complete"
# ...and for an opt that ran out of optimizer steps: re-run from its last geometry, or stopped after ntsReRunLimit
RERUN, GAVE_UP = "Re-ran", "Out of Re-runs"
BY_HOOK, BY_STALKER = "hook", "stalker"
# The Neva-Touch Scheduler's log beside each of its jobs (<name>.nts): the hook's output, never the job's own
NTS_LOG = ".nts"


@dataclass
class Stage:
    """One --Link1-- stage of a job: what it was planned to run, its status, its current step's label, and the last
    reports read for it. A report is kept once read, so an opt's convergence still shows during the freq step Gaussian
    adds to it."""
    status:      str              = AWAITING
    label:       str              = ""      # the current step's: ModRed, OptTS, Opt, IRC, Stability, Freq, SP
    reports:     bool             = False   # whether any of its steps optimizes or runs a stability analysis
    convergence: list[int] | None = None    # [criteria met, criteria]
    stable:      bool | None      = None
    planned:     str              = ""      # what the input's route for it runs (JobChecks.kinds), from submission
    outOfSteps:  bool             = False   # its optimization ran out of optimizer steps (read once the step ends)
    report:      str              = ""      # a program's own report (CREST: its phase, then the ensemble it kept)


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
    leftQueue:   bool  = False     # squeue no longer lists it (a Neva-Touch Scheduler job is watched until then)
    cancelled:   bool  = False     # cancelled through CompUtils (CancelJobs): recorded at once, and never followed
    # The Neva-Touch Scheduler (nevatouch.py): the step this job's hook takes once it ends (NTS_SP, NTS_DONE; '' off)
    # and the hook's command (a stalker runs it for a step the hook didn't take), set at submission. The rest is what
    # the step did, written only through UpdateTracked (straight into the store), so no stalker's copy overwrites it
    nevaTouch:     str       = ""
    hookCommand:   list[str] = field(default_factory=list)
    reRuns:        int       = 0       # a re-run's: re-runs so far of the stage it starts at
    firstStage:    int       = 1       # the original job's number for its first stage (stages are numbered from it)
    nextJob:       str       = ""      # the job the step submitted: '8a_SP (ID 123456)'
    stepBy:        str       = ""      # BY_HOOK or BY_STALKER
    stepOutcome:   str       = ""      # SUBMITTED, FAILED, STOPPED or CHAIN_COMPLETE
    stepNote:      str       = ""      # why it couldn't submit
    stepStage:     int       = 0       # a stage that ran out of steps: the one re-run (or given up on)
    stepCount:     int       = 0       # ...and which re-run of that stage this was (over ntsReRunLimit: given up)
    fallbackTried: bool      = False   # a stalker has taken the step (once only)
    # A CREST job running CompUtils' environment's crest: that environment's prefix, so an environment swap waits for it
    # (conda-installer.py: a compute node's process is invisible to the login node's process check)
    runsFrom:      str       = ""

    @property
    def key(self) -> str:
        return f"{self.cluster}:{self.jobID}"

    @property
    def ended(self) -> bool:
        return self.state == ENDED

    @property
    def logPath(self) -> Path:
        """The job's Neva-Touch Scheduler log, beside its output."""
        return Path(self.outputPath).parent / f"{self.name}{NTS_LOG}"

    @classmethod
    def FromRecord(cls, record: dict) -> "TrackedJob":
        known = {entry.name for entry in fields(cls)}
        values = {name: value for name, value in record.items() if name in known}
        # Known fields only, so a store a newer CompUtils wrote still loads
        stageFields = {entry.name for entry in fields(Stage)}
        values["stages"] = [Stage(**{name: value for name, value in stage.items() if name in stageFields})
                            for stage in record.get("stages", [])] or [Stage()]
        return cls(**values)


# Where stalked jobs run: what slurmHandler() wrote this same run (the job's own partition/cluster, else the config's).
# The cluster is only included when the submission header actually sends -M; otherwise just the partition
def jobLocation(partition: str | None = None, cluster: str | None = None) -> str:
    from .jobs import HeaderSends
    parts = [(cluster or Defaults.cluster) if HeaderSends("cluster") else "", partition or Defaults.partition]
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

def Watched(job: TrackedJob) -> bool:
    """Whether a ping still looks at a job: until it has ended, and a Neva-Touch Scheduler job until it has left the
    queue too (its hook runs inside the job, after the termination line: only then is its step known to be taken)."""
    return not job.ended or (bool(job.nevaTouch) and not job.leftQueue)

def HasLiveJobs() -> bool:
    """Whether any tracked job is still watched (the TUI's stalking routine only pings while one is)."""
    return any(Watched(job) for job in LoadTracked())

# The store is shared by every CompUtils run at once: a CLI stalker, the TUI, and a job's Neva-Touch Scheduler hook on
# a compute node. Each read-merge-write holds its lock file (fileops.FileLock)
def _StoreLock():
    return FileLock(_StorePath().with_name(TRACKED_FILE + ".lock"))

def _WriteStore(stored: dict[str, TrackedJob]) -> None:
    path = _StorePath()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps([asdict(job) for job in sorted(stored.values(), key=lambda job: job.submitted)],
                                    indent=1))
    os.replace(temporary, path)

# A CLI stalker and the TUI can run at once, each holding its own copies. A record further along wins (ended beats
# running, a later bookmark beats an earlier one), a notification sent by either one counts for both, and the
# Neva-Touch Scheduler's step fields are always the stored ones (only UpdateTracked writes them)
def _Progress(job: TrackedJob) -> tuple:
    return job.ended, job.stage, job.bookmark, job.stepRead

STEP_FIELDS = ("nextJob", "stepBy", "stepOutcome", "stepNote", "stepStage", "stepCount", "fallbackTried")

def _Sync(job: TrackedJob, stored: TrackedJob) -> None:
    """Brings a held copy's shared fields up to the stored record's."""
    for name in ("notified", "leftQueue", "cancelled", *STEP_FIELDS):
        setattr(job, name, getattr(stored, name))

def _SaveStore(jobs: list[TrackedJob] = (), dropped: set[str] = frozenset()) -> None:
    """Merges these jobs into the store (re-read first) and removes the dropped keys, writing it atomically. The jobs
    given are brought up to date with what other runs wrote (notified, the Neva-Touch Scheduler's step)."""
    with _StoreLock():
        stored = _Prune(_ReadStore())
        for job in jobs:
            old = stored.get(job.key)
            if old is not None:
                kept = job if _Progress(job) >= _Progress(old) else old
                kept.notified = old.notified or job.notified
                kept.leftQueue = old.leftQueue or job.leftQueue
                kept.cancelled = old.cancelled or job.cancelled
                if kept is job:
                    for name in STEP_FIELDS:
                        setattr(job, name, getattr(old, name))
                stored[job.key] = kept
            else:
                stored[job.key] = job
        for key in dropped:
            stored.pop(key, None)
        _WriteStore(stored)
    for job in jobs:
        if job.key in stored and stored[job.key] is not job:
            _Sync(job, stored[job.key])

def UpdateTracked(key: str, change) -> TrackedJob | None:
    """Applies change(record) to one stored record under the lock, and returns the record (None: not tracked). The
    only writer of the Neva-Touch Scheduler's step fields."""
    with _StoreLock():
        stored = _Prune(_ReadStore())
        job = stored.get(key)
        if job is None:
            return None
        change(job)
        _WriteStore(stored)
    return job

def ClaimFallback(job: TrackedJob) -> bool:
    """Takes a job's Neva-Touch Scheduler step for this stalker, once: a compare-and-set under the lock, so two
    stalkers (the TUI and `cu -st`) never both take it. False if it is already taken or done."""
    claimed = []
    def Claim(stored: TrackedJob) -> None:
        if not stored.fallbackTried and not stored.nextJob:
            stored.fallbackTried = True
            claimed.append(True)
    UpdateTracked(job.key, Claim)
    if claimed:
        job.fallbackTried = True
    return bool(claimed)

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
    if job.program == CREST_PROGRAM:
        # One step, whose report is CREST's own (crest.ProgressReport)
        stage = job.stages[job.stage]
        stage.status, stage.label, stage.reports = IN_PROGRESS, CONFORMER_SEARCH, True
        job.stepRead, job.stepOptimizes, job.stepStability = True, False, False
        return True
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
    if job.program == CREST_PROGRAM:
        from .crest import ProgressReport
        stage.report = ProgressReport(data, job.bookmark, end) or stage.report
        return
    if job.stepOptimizes:
        convergence = ExtractConvergence(data, job.bookmark, end)
        if convergence is not None:
            stage.convergence = list(convergence)
    if job.stepStability:
        stable = ExtractStability(data, job.bookmark, end)
        if stable is not None:
            stage.stable = stable
    # Once the step has ended (end: its termination line): whether its optimization ran out of steps
    if job.stepOptimizes and end is not None and ExtractOutOfSteps(data, job.bookmark, end):
        stage.outOfSteps = True

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

def ReadEnding(job: TrackedJob) -> TrackedJob:
    """A copy of a job's record, read through to its end: the stage that ended it (job.stage) and what that stage
    reported (its outOfSteps). The Neva-Touch Scheduler's step reads the job this way, as the stalker does."""
    ending = copy.deepcopy(job)
    if not ending.ended:
        _ReadProgress(ending, final=True)
    return ending


# ─── Polling ──────────────────────────────────────────────────────────

def PollQueue(jobs: list[TrackedJob]) -> list[TrackedJob] | None:
    """One ping: reads the queue, brings every unended job up to date, notifies (once, batched) about the ones that
    ended, and saves. Returns the jobs that ended on this ping, or None if squeue failed (nothing changes then)."""
    live = [job for job in jobs if Watched(job)]
    queue = _ReadQueue(live) if live else {}
    if queue is None:
        return None
    endedNow, leftNow = [], []
    for job in live:
        wasEnded = job.ended
        row = queue.get((job.cluster, job.jobID))
        if row is None:
            job.leftQueue = True
            leftNow.append(job)
            if not job.ended:
                _ReadProgress(job, final=True)
        # An ended Neva-Touch Scheduler job still in the queue is running its hook: squeue's RUNNING must not undo it
        elif not job.ended:
            job.state, job.startAt, job.elapsed = row
            if job.state == "RUNNING":
                _ReadProgress(job)
        # A Neva-Touch Scheduler job's ending is reported once it has left the queue (NotifyEndings holds it till then)
        if job.ended and (not wasEnded or (job.nevaTouch and job in leftNow)):
            endedNow.append(job)
    _FireFallbacks(leftNow)
    NotifyEndings(endedNow)
    RecordEndings(endedNow)
    _SaveStore(jobs)
    return endedNow

def CheckTracked() -> None:
    """Startup check, every run: prunes the list and files the endings of jobs that left the queue since the last
    look, so a stalker's first ping only covers live jobs. One squeue call, and none if nothing is still live; a
    missing squeue (e.g. not on a cluster) is skipped silently. Running jobs aren't read here."""
    jobs = LoadTracked()
    live = [job for job in jobs if Watched(job)]
    if not live:
        return
    queue = _ReadQueue(live)
    if queue is None:
        return
    gone = [job for job in live if (job.cluster, job.jobID) not in queue]
    for job in gone:
        job.leftQueue = True
        if not job.ended:
            _ReadProgress(job, final=True)
    _FireFallbacks(gone)
    NotifyEndings(gone, caughtUp=True)
    RecordEndings(gone)
    if gone:
        _SaveStore(gone)


# ─── The Neva-Touch Scheduler's fallback ──────────────────────────────

def _FallbackDue(job: TrackedJob) -> bool:
    """Whether a job's Neva-Touch Scheduler step is still to be taken after it left the queue: it ended normally (the
    SP) or its last stage ran out of optimizer steps (a re-run), and its hook (which runs inside the job, so it is over
    by now) took no step or couldn't submit: refused (e.g. no sbatch on compute nodes), killed, or never run."""
    normal = bool(job.termination) and job.termination != Defaults.terminationVariants[2]
    return (job.nevaTouch == NTS_SP and job.leftQueue and not job.cancelled
            and (normal or job.stages[job.stage].outOfSteps)
            and bool(job.hookCommand) and not job.nextJob and not job.fallbackTried and job.stepOutcome in ("", FAILED))

def _FireFallbacks(jobs: list[TrackedJob]) -> None:
    """Takes each due step once, through the hook's own command (nevatouch.FireStep), then brings the jobs up to date
    with what it recorded, so its message is never sent again here."""
    due = [job for job in jobs if _FallbackDue(job)]
    if not due:
        return
    from .nevatouch import FireStep
    # The ending the step's message reports is on record first
    _SaveStore(due)
    for job in due:
        if not ClaimFallback(job):
            continue
        console.print(f"[operation]Neva-Touch Scheduler: taking {job.name}'s step (its hook didn't; log "
                      f"{job.logPath.name}).[/operation]")
        FireStep(job)
    stored = _ReadStore()
    for job in due:
        if job.key in stored:
            _Sync(job, stored[job.key])


# ─── Messages ─────────────────────────────────────────────────────────

# How a job ended, as a phrase for messages. The matched line itself only reads well for Gaussian: ORCA's is
# 'terminated normally' ("has finished via terminated normally")
def EndingPhrase(termination: str) -> str:
    return "error termination" if termination == Defaults.terminationVariants[2] else "normal termination"

def _OnLocation(job: TrackedJob) -> str:
    return f" on {job.location}" if job.location else ""

def NevaTouchNote(job: TrackedJob, termination: str | None = None) -> str:
    """What a job's Neva-Touch Scheduler step did, as a sentence for its ending message ('' for a job without one).
    Until the step is recorded, what it is bound to do: a job that didn't end normally stops the chain."""
    if not job.nevaTouch:
        return ""
    termination = job.termination if termination is None else termination
    normal = bool(termination) and termination != Defaults.terminationVariants[2]
    outcome = job.stepOutcome or ((CHAIN_COMPLETE if normal else STOPPED) if job.nevaTouch == NTS_DONE
                                  else "" if normal else STOPPED)
    if outcome == SUBMITTED:
        return f"Neva-Touch Scheduler submitted {job.nextJob}."
    if outcome == RERUN:
        return (f"Neva-Touch Scheduler re-ran stage {job.stepStage} from its last geometry (out of optimizer steps): "
                f"{job.nextJob}, re-run {job.stepCount} of {Defaults.ntsReRunLimit}.")
    if outcome == GAVE_UP:
        reRuns = job.stepCount - 1
        return (f"Neva-Touch Scheduler stopped: stage {job.stepStage} ran out of optimizer steps "
                + (f"on all {reRuns} re-run{'s' if reRuns != 1 else ''}." if reRuns > 0
                   else "(re-runs are off: ntsReRunLimit is 0)."))
    if outcome == FAILED:
        tried = f"re-run of stage {job.stepStage}" if job.stepStage else job.nevaTouch
        return f"Neva-Touch Scheduler couldn't submit the {tried}" + (f": {job.stepNote}." if job.stepNote else ".")
    if outcome == STOPPED:
        return ("Neva-Touch Scheduler: the chain stopped at its last step." if job.nevaTouch == NTS_DONE
                else f"Neva-Touch Scheduler stopped: no {job.nevaTouch} was submitted.")
    if outcome == CHAIN_COMPLETE:
        return "Neva-Touch Scheduler: the chain is complete (Opt → SP)."
    return ""

def EndingMessage(job: TrackedJob, termination: str | None = None) -> str:
    """A job's ending, as one Telegram message: how it ended, then its Neva-Touch Scheduler step. The one builder for
    the stalker and the Neva-Touch Scheduler's hook (termination: the hook's own reading, before any stalker's)."""
    termination = job.termination if termination is None else termination
    head = (f"Job {job.name}{_OnLocation(job)} was cancelled (cu -cancel)." if job.cancelled
            else f"Job {job.name}{_OnLocation(job)} has finished via {EndingPhrase(termination)}." if termination
            else f"Job {job.name}{_OnLocation(job)} left the queue without a termination line.")
    # A program's own report of its ending (CREST: the conformers it kept)
    report = job.stages[job.stage].report if termination and job.program == CREST_PROGRAM else ""
    note = NevaTouchNote(job, termination)
    return " ".join(part for part in (head, report, note) if part)

def NotifyEndings(jobs: list[TrackedJob], caughtUp: bool = False) -> None:
    """One Telegram message for every job here not yet notified (split only at Telegram's length limit). A single
    ending reads as it always has."""
    # Another stalker (the CLI and the TUI can run at once) may already have sent one
    stored = _ReadStore() if jobs else {}
    for job in jobs:
        if job.key in stored and stored[job.key].notified:
            job.notified = True
    # A Neva-Touch Scheduler job's ending is its hook's to report: only once it has left the queue (the hook is over)
    # is it this stalker's, and only if the hook's message didn't go out. A step another stalker has claimed is that
    # stalker's to report, once its run is over
    pending = [job for job in jobs if job.ended and not job.notified and (not job.nevaTouch or job.leftQueue)
               and not (job.nevaTouch and job.fallbackTried and job.stepBy != BY_STALKER)]
    if not pending:
        return
    if len(pending) == 1:
        NotifyPersonal(EndingMessage(pending[0]))
    else:
        header = (f"Caught up on {len(pending)} finished jobs:" if caughtUp else f"{len(pending)} jobs have finished:")
        lines = [f"• {job.name}{_OnLocation(job)}: "
                 + (EndingPhrase(job.termination) if job.termination else "left the queue without a termination line")
                 + (f". {NevaTouchNote(job)}" if NevaTouchNote(job) else "")
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
    if stage.outOfSteps:
        lines.append("Ran out of optimizer steps.")
    if stage.report:
        lines.append(stage.report)
    if not lines and stage.status in (IN_PROGRESS, COMPLETE) and stage.label and not stage.reports:
        lines.append("No reporting criteria.")
    return lines

def StageLines(job: TrackedJob) -> list[str]:
    """'Stage N (Status) Planned: report' for every stage, reached or not (Planned: what its route runs, from the input
    at submission); a second report goes on its own line below."""
    lines = []
    # A re-run starting at a later stage keeps the original job's numbers
    for number, stage in enumerate(job.stages, job.firstStage):
        reports = _StageReport(stage)
        lines.append(f"Stage {number} ({stage.status})" + (f" {stage.planned}" if stage.planned else "")
                     + (f": {reports[0]}" if reports else ""))
        lines.extend(f"    {report}" for report in reports[1:])
    return lines

def StagePosition(job: TrackedJob) -> str:
    """'2/3' with the step ('1/2, Freq') once it is read, else what the stage was planned to run ('2/3, Stability')."""
    label = job.stages[job.stage].label or job.stages[job.stage].planned
    return f"{job.firstStage + job.stage}/{job.firstStage - 1 + len(job.stages)}" + (f", {label}" if label else "")

def HeadLine(job: TrackedJob) -> tuple[str, str]:
    """(the job's one-line status, its console style)."""
    count = len(job.stages)
    stages = f"{count} stage{'s' if count != 1 else ''}"
    if job.ended:
        if job.cancelled:
            return f"Job {job.name} was cancelled (cu -cancel).", "warning"
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
    if job.nevaTouch:
        lines.append(NevaTouchLine(job))
    return lines

def NevaTouchLine(job: TrackedJob) -> str:
    """'Neva-Touch Scheduler: Then SP' (or Submitted 8a_SP (ID 123456), ...) for a job with a step."""
    if job.stepOutcome == SUBMITTED:
        state = f"Submitted {job.nextJob}"
    elif job.stepOutcome == RERUN:
        state = f"Re-ran Stage {job.stepStage} as {job.nextJob}, {job.stepCount} of {Defaults.ntsReRunLimit}"
    elif job.stepOutcome == GAVE_UP:
        state = f"Stopped (Out of Re-runs on Stage {job.stepStage})"
    elif job.stepOutcome == FAILED:
        state = f"Couldn't Submit the {f'Re-run of Stage {job.stepStage}' if job.stepStage else job.nevaTouch}"
    elif job.stepOutcome == STOPPED:
        state = "Stopped (Chain Not Complete)" if job.nevaTouch == NTS_DONE else f"Stopped (No {job.nevaTouch})"
    elif job.stepOutcome == CHAIN_COMPLETE:
        state = "Chain Complete"
    else:
        state = "Last Step of the Chain" if job.nevaTouch == NTS_DONE else f"Then {job.nevaTouch}" + (
            f" (Re-run {job.reRuns} of {Defaults.ntsReRunLimit} of Stage {job.firstStage})" if job.reRuns else "")
    return f"Neva-Touch Scheduler: {state}" + (" (Fired by the Stalker)" if job.stepBy == BY_STALKER else "")


# ─── Reports (cu -st --json) ──────────────────────────────────────────

def JobRecord(job: TrackedJob) -> dict:
    """One tracked job for a report: what HeadLine and ProgressLines say, as fields. Empty values are left out."""
    head, _ = HeadLine(job)
    record = {"name": job.name, "jobId": job.jobID, "cluster": job.cluster, "output": job.outputPath,
              "state": job.state, "headline": head, "termination": job.termination, "elapsed": job.elapsed,
              "expectedStart": job.startAt if job.state == "PENDING" else "",
              "stages": [{key: value for key, value in (
                  ("number", number), ("status", stage.status), ("planned", stage.planned), ("step", stage.label),
                  ("convergence", {"met": stage.convergence[0], "total": stage.convergence[1]}
                                  if stage.convergence is not None else None),
                  ("stable", stage.stable), ("outOfSteps", stage.outOfSteps or None), ("report", stage.report))
                  if value not in ("", None)}
                         for number, stage in enumerate(job.stages, job.firstStage)],
              "nevaTouch": NevaTouchLine(job) if job.nevaTouch else "", "nextJob": job.nextJob,
              "cancelled": job.cancelled or None}
    return {key: value for key, value in record.items() if value not in ("", None, [])}


def Snapshot(jobs: list[TrackedJob]) -> list[TrackedJob]:
    """One ping (a stalker's, so endings are notified and saved as usual), then every job printed and recorded: what
    `cu -st --json` gives instead of a loop that would hold an agent's shell."""
    from . import agent
    if not jobs:
        console.print("[info]No tracked jobs.[/info]")
        return []
    if PollQueue(jobs) is None:
        agent.Warn("Could not read the queue (squeue failed): these are the jobs as last read.")
    for job in jobs:
        _PrintJob(job)
        agent.Result(JobRecord(job))
    return jobs


def MatchJobs(names: list[str], jobs: list[TrackedJob]) -> dict[str, TrackedJob]:
    """The tracked jobs the names pick (each an ID, a name, 'cluster:ID', or 'ended': every ended one), by key, in
    submission order. A name that picks nothing is skipped (agent.Skip). Shared by --dismiss and --wait."""
    from . import agent
    chosen = {}
    for name in names:
        matches = ([job for job in jobs if job.ended] if name.lower() == "ended"
                   else [job for job in jobs if name in (job.jobID, job.name, job.key)])
        if not matches and name.lower() != "ended":
            agent.Skip(name, "No tracked job has that ID or name")
        chosen.update({job.key: job for job in matches})
    return chosen


def CancelJobs(jobs: list[TrackedJob]) -> tuple[list[TrackedJob], list[tuple[TrackedJob, str]]]:
    """Cancels tracked jobs (scancel, one call per job, so one already gone doesn't hide the rest) and records each at
    once: cancelled, read to its end (the running stage Stopped, the rest Not Reached), saved, notified as any ending
    is (the notification settings decide), and its data file written. The Neva-Touch Scheduler takes no step after a
    cancelled job. The one canceller: `cu -cancel` and the Job Stalker's c. Returns (cancelled, [(job, why not)])."""
    cancelled, failures = [], []
    for job in jobs:
        command = ["scancel", *(["-M", job.cluster] if job.cluster else []), job.jobID]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=60)
            problem = "" if result.returncode == 0 else (result.stderr.strip() or f"scancel exited {result.returncode}")
        except (OSError, subprocess.SubprocessError) as error:
            problem = f"scancel couldn't run: {error}"
        if problem:
            failures.append((job, problem))
            continue
        job.cancelled = True
        if not job.ended:
            _ReadProgress(job, final=True)
        cancelled.append(job)
    if cancelled:
        _SaveStore(cancelled)
        NotifyEndings(cancelled)
        RecordEndings(cancelled)
    return cancelled, failures


def Cancel(names: list[str]) -> None:
    """`cu -cancel`: cancels the named live jobs (IDs, names, or CLUSTER:ID), asked first (key confirm-cancel)."""
    from . import agent
    from .prompts import AskBool
    live = []
    for job in MatchJobs(names, LoadTracked()).values():
        if job.ended:
            agent.Skip(job.name, "Already ended")
        else:
            live.append(job)
    if not live:
        return
    listed = ", ".join(f"{job.name} (ID {job.jobID}, {job.state.title()})" for job in live)
    if not AskBool(f"Cancel {listed}?", "n", key="confirm-cancel"):
        for job in live:
            agent.Skip(job.name, "Not cancelled", show=False)
        console.print("[info]Nothing cancelled.[/info]")
        return
    cancelled, failures = CancelJobs(live)
    for job, problem in failures:
        agent.Skip(job.name, f"scancel refused it: {problem}")
    if cancelled:
        console.print(f"[good]Cancelled {', '.join(job.name for job in cancelled)}.[/good]")
        agent.Result({"cancelled": [job.name for job in cancelled]})


def Dismiss(names: list[str]) -> None:
    """Stops tracking the named jobs (IDs, names, or 'ended': every ended one), as the TUI's d / D. A live job is asked
    about first (key dismiss-live)."""
    from . import agent
    from .prompts import AskBool
    chosen = MatchJobs(names, LoadTracked())
    live = [job.name for job in chosen.values() if Watched(job)]
    if live and not AskBool(f"{', '.join(live)} {'is' if len(live) == 1 else 'are'} still in the queue. Stop tracking "
                            f"{'it' if len(live) == 1 else 'them'} anyway?", "n", key="dismiss-live"):
        for name in live:
            agent.Skip(name, "Still live (not dismissed)", show=False)
        chosen = {key: job for key, job in chosen.items() if not Watched(job)}
    if chosen:
        DismissJobs(set(chosen))
        console.print(f"[good]No longer tracking {', '.join(job.name for job in chosen.values())}.[/good]")
        agent.Result({"dismissed": [job.name for job in chosen.values()]})
    elif not names or all(name.lower() == "ended" for name in names):
        console.print("[info]Nothing to dismiss.[/info]")


# ─── Waiting (cu -st --wait): an agent sleeps until a job ends ────────
#
# An agent runs `cu -st --wait JOB… --json` as a background command and ends its turn: its harness (Antigravity, Claude
# Code) wakes it when the command exits, so it spends nothing while jobs run. The wait is an ordinary stalker (PollQueue:
# Telegram, the Neva-Touch Scheduler's fallbacks), that exits at the first ping where a waited job has ended.

WAIT_CHECK = 10   # seconds between looks, while sleeping, at the store and at whether the agent is still connected
# Why a sleep between pings ended
WAIT_GONE, WAIT_TIMER, WAIT_STORE = "gone", "timer", "store"

def FollowUps(job: TrackedJob, jobs: list[TrackedJob]) -> list[TrackedJob]:
    """The tracked job a job's Neva-Touch Scheduler step submitted (its SP, or a re-run), if any: the one whose
    'name (ID id)' is the job's nextJob."""
    return [other for other in jobs if job.nextJob and job.nextJob == f"{other.name} (ID {other.jobID})"]

def _StepTaken(job: TrackedJob) -> bool:
    """Whether a job's Neva-Touch Scheduler step is on record (its hook writes it within seconds of the job's end,
    while the job is still in the queue). Couldn't Submit doesn't count: a stalker's fallback takes the step again once
    the job has left the queue, and its result is the one to report."""
    return job.ended and bool(job.nevaTouch) and job.stepOutcome not in ("", FAILED)

def _WaitDone(job: TrackedJob) -> bool:
    """Whether a wait is over for a job: it's no longer watched, or its Neva-Touch Scheduler step is already taken."""
    return not Watched(job) or _StepTaken(job) or job.cancelled

def _ResolveChain(rootKey: str, byKey: dict[str, TrackedJob],
                  endedSnapshots: dict[str, TrackedJob]) -> list[TrackedJob]:
    """Follows a waited root job through any Neva-Touch Scheduler follow-ups (re-runs and SPs) in submission order.
    Returns [] if the root itself was dismissed before finishing."""
    current = byKey.get(rootKey) or endedSnapshots.get(rootKey)
    if current is None:
        return []
    chain, seen = [current], {current.key}
    allJobs = [*byKey.values(), *(job for key, job in endedSnapshots.items() if key not in byKey)]
    while current.stepOutcome in (SUBMITTED, RERUN) and current.nextJob:
        nxt = next((other for other in allJobs
                    if f"{other.name} (ID {other.jobID})" == current.nextJob and other.key not in seen), None)
        if nxt is None:
            break
        chain.append(nxt)
        seen.add(nxt.key)
        current = nxt
    return chain

def _ChainDone(chain: list[TrackedJob]) -> bool:
    """Whether a chain has reached a terminal state (its current tail job is done and either ended the chain,
    stopped/failed, wasn't an NTS job, or had its next step dismissed)."""
    return bool(chain) and _WaitDone(chain[-1])

def _JobFailed(job: TrackedJob) -> bool:
    """Whether a finished job/chain tail warrants reading its output with `cu -info`."""
    return not job.cancelled and (not job.termination or job.termination == Defaults.terminationVariants[2]
                                  or job.stepOutcome in (GAVE_UP, FAILED, STOPPED))

def _DeduplicateJobs(jobs: list[TrackedJob]) -> list[TrackedJob]:
    seen, unique = set(), []
    for job in jobs:
        if job.key not in seen:
            seen.add(job.key)
            unique.append(job)
    return unique

def _StoreStamp() -> int:
    """When the store was last written (0: not there). The Neva-Touch Scheduler's hooks run on compute nodes and share
    only the filesystem with a wait on the login node, so this is how a wait hears of their steps at once."""
    try:
        return _StorePath().stat().st_mtime_ns
    except OSError:
        return 0

def _OutputClosed() -> bool:
    """Whether whoever reads this run's stdout has gone (the agent's ssh connection closed). sshd sends a command run
    without a terminal no hangup signal, so a wait would otherwise sleep on, orphaned, on the login node. The read end
    of a closed pipe or socket shows as POLLERR / POLLHUP on ours. Linux only (Windows has no poll): never closed."""
    try:
        import select
        poller = select.poll()
        poller.register(sys.stdout.fileno(), 0)
        return any(events & (select.POLLERR | select.POLLHUP) for _, events in poller.poll(0))
    except (AttributeError, ValueError, OSError):
        return False

def _Sleep(seconds: float, stamp: int) -> str:
    """Sleeps until the next ping (WAIT_TIMER), looking every WAIT_CHECK seconds at whether the store has been written
    since `stamp` (WAIT_STORE: something, maybe a hook, recorded a change) and whether the agent is still there
    (WAIT_GONE: stop waiting)."""
    end = time.monotonic() + seconds
    while (left := end - time.monotonic()) > 0:
        if _OutputClosed():
            return WAIT_GONE
        if _StoreStamp() != stamp:
            return WAIT_STORE
        time.sleep(min(WAIT_CHECK, left))
    return WAIT_GONE if _OutputClosed() else WAIT_TIMER

def Wait(names: list[str], mode: str = WAIT_CHAIN) -> list[TrackedJob]:
    """`cu -st --wait [JOB…] [--wait-mode any|chain|all]`: pings now and every stalkFrequency minutes until the
    wait condition for `mode` is met:
      - `chain` (default): any waited job's full Neva-Touch Scheduler chain reaches a terminal state;
      - `any`: any individual job ends (even an intermediate stage that submits a follow-up);
      - `all`: every job/chain in the wait list reaches a terminal state (normal or error).
    A Neva-Touch Scheduler step wakes the sleep as soon as its hook records it (a look at the store every WAIT_CHECK
    seconds; the queue is read only when needed for the report). Gives up after trackedJobHours, or quietly once
    nobody reads its output."""
    from . import agent
    jobs = LoadTracked()
    waited = list(MatchJobs(names, jobs)) if names else [job.key for job in jobs if not _WaitDone(job)]
    if not waited:
        agent.Fail("Nothing to wait for: no tracked job is still in the queue." if not names
                   else "Nothing to wait for: none of those jobs is tracked.")
        return []
    modeText = {
        WAIT_CHAIN: "chain (until a Neva-Touch Scheduler chain finishes)",
        WAIT_ANY:   "any (until any individual job ends)",
        WAIT_ALL:   "all (until every waited job and chain finishes)",
    }[mode]
    console.print(f"[operation]Waiting for {len(waited)} job{'s' if len(waited) != 1 else ''} (mode: {modeText}; "
                  f"a ping every {Defaults.stalkFrequency} minutes; a Neva-Touch Scheduler step as soon as it is "
                  f"recorded).[/operation]")
    start, nextPing, reason = time.monotonic(), 0.0, ""
    ended: list[TrackedJob] = []
    endedSnapshots: dict[str, TrackedJob] = {}
    chains: dict[str, list[TrackedJob]] = {}
    doneKeys: list[str] = []
    while True:
        # Each look re-reads the store, so another stalker's, or a hook's, reading counts too
        jobs = LoadTracked()
        pinged = time.monotonic() >= nextPing
        if pinged:
            if PollQueue(jobs) is None:
                console.print("[warning]Could not read the queue (squeue failed). Trying again at the next "
                              "ping.[/warning]")
            else:
                # PollQueue's fallback may have submitted new follow-up jobs; reload so _ResolveChain sees them
                jobs = LoadTracked()
            nextPing = time.monotonic() + Defaults.stalkFrequency * 60
        byKey = {job.key: job for job in jobs}
        if mode == WAIT_ANY:
            ended = [byKey[key] for key in waited if key in byKey and _WaitDone(byKey[key])]
            trigger = bool(ended)
            allDismissed = not any(key in byKey for key in waited)
        else:
            chains = {key: _ResolveChain(key, byKey, endedSnapshots) for key in waited}
            for chain in chains.values():
                for job in chain:
                    if _WaitDone(job):
                        endedSnapshots[job.key] = job
            doneKeys = [key for key in waited if chains[key] and _ChainDone(chains[key])]
            dismissedCount = sum(1 for key in waited if not chains[key])
            trigger = bool(doneKeys) if mode == WAIT_CHAIN else (
                len(doneKeys) + dismissedCount == len(waited) and bool(doneKeys))
            allDismissed = dismissedCount == len(waited)
        if trigger and not pinged:
            # Seen in the store (a hook's step): one ping first, so the report's queue states are current
            nextPing = 0.0
            continue
        if trigger:
            reason = "ended"
        elif allDismissed:
            reason = "dismissed"
        elif time.monotonic() - start >= Defaults.trackedJobHours * 3600:
            reason = "time limit"
        if reason:
            break
        # Taken after this look's own write (a ping saves the store), so only someone else's write wakes the sleep. The
        # next ping keeps its time whatever wakes the sleep, so other stalkers' writes never put it off
        woke = _Sleep(nextPing - time.monotonic(), _StoreStamp())
        if woke == WAIT_GONE:
            # Nobody is left to read the report: say nothing, and let the run end
            sys.stdout = console.file = open(os.devnull, "w")
            return []
        if woke == WAIT_TIMER:
            # The ping is due (a sleep can end a hair early)
            nextPing = 0.0

    if mode == WAIT_ANY:
        shown = [byKey[key] for key in waited if key in byKey]
        # The jobs a Neva-Touch Scheduler step submitted are recorded by their hook, maybe after this ping's read
        latest = LoadTracked()
        waitedSet = set(waited)
        followUps = [other for job in ended for other in FollowUps(job, latest) if other.key not in waitedSet]
        reportJobs = _DeduplicateJobs(shown + followUps)
        # A job whose hook has taken its step is done here even while it's still in the queue (its hook finishing)
        remaining = [job for job in reportJobs if not _WaitDone(job)]
        endedEntries = [{"name": job.name, "jobId": job.jobID, "message": EndingMessage(job)} for job in ended]
    else:
        endedChains = [chains[key] for key in doneKeys]
        ended = _DeduplicateJobs([chain[-1] for chain in endedChains])
        doneSet = set(doneKeys)
        remaining = _DeduplicateJobs([chains[key][-1] for key in waited if chains[key] and key not in doneSet])
        reportJobs = _DeduplicateJobs([job for key in waited for job in chains[key]])
        endedEntries = []
        for chain in endedChains:
            tail = chain[-1]
            entry: dict = {"name": tail.name, "jobId": tail.jobID, "message": EndingMessage(tail)}
            if len(chain) > 1:
                entry["chain"] = [job.jobID for job in chain]
            endedEntries.append(entry)

    if reason == "ended":
        unit = "job" if mode == WAIT_ANY else "chain"
        console.print(f"[operation]{len(ended)} {unit}{'s' if len(ended) != 1 else ''} ended:[/operation]")
    elif reason == "dismissed":
        console.print("[warning]The jobs being waited for are no longer tracked (dismissed).[/warning]")
    else:
        console.print(f"[warning]Stopped waiting after {Defaults.trackedJobHours} hours; the jobs are still "
                      f"running.[/warning]")
    waitResult = {key: value for key, value in (
        ("mode", mode),
        ("reason", reason),
        ("ended", endedEntries),
        ("waiting", [job.jobID for job in remaining])) if value}
    agent.Result({"wait": waitResult})
    for job in reportJobs:
        _PrintJob(job)
        agent.Result(JobRecord(job))
    for job in ended:
        if not _JobFailed(job):
            continue
        agent.Next(agent.CommandLine(["-info", job.outputPath, "--json"]),
                   f"{job.name} didn't end normally: read what its output says before deciding what to do.")
    if remaining:
        waitArgs = ["-st", "--wait", *(job.jobID for job in remaining)]
        if mode != WAIT_CHAIN:
            waitArgs += ["--wait-mode", mode]
        waitArgs.append("--json")
        why = (f"{len(remaining)} job{'s' if len(remaining) != 1 else ''} still running: run it as a background "
               f"command and end your turn to be woken "
               + ("once the whole batch finishes." if mode == WAIT_ALL else "at the next ending."))
        agent.Next(agent.CommandLine(waitArgs), why)
    return ended


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

        # If all jobs for stalking are done, finish execution and release the terminal (a Neva-Touch Scheduler job once
        # it has left the queue: its step is then known to be taken)
        if not any(Watched(job) for job in jobs):
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
