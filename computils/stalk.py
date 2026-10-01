import subprocess, time
from pathlib import Path

from .console  import console
from .defaults import Defaults
from .notify   import NotifyPersonal
from .fileops  import MapFile, HasContent, ExtractFrom, ExtractTermination, ExtractStability, ExtractConvergence

# JOBNAME|STATE|START_TIME|CURRENT_DURATION for each of the user's jobs. '|'-separated and unpadded, so long job names
# aren't cut to a column width and a field containing spaces can't shift the others
QUEUE_COMMAND = ["squeue", "-h", "--me", "--format=%j|%T|%S|%M"]


# Where stalked jobs run: jobs are submitted this same run by slurmHandler(), so its Defaults values are exact.
# The cluster is only included when the submission header actually sends -M; otherwise just the partition
def jobLocation() -> str:
    submitsCluster = any("-M" in line for line in Defaults.submissionList)
    parts = [Defaults.cluster if submitsCluster else "", Defaults.partition]
    return " / ".join(part for part in parts if part)

def _ReadQueue() -> dict[str, list[str]] | None:
    """{job name: [state, start time, current duration]} for the user's queued jobs, or None if squeue failed."""
    result = subprocess.run(QUEUE_COMMAND, capture_output=True, text=True)
    if result.returncode != 0:
        return None
    rows = (line.split("|") for line in result.stdout.splitlines())
    return {fields[0]: fields[1:] for fields in rows if len(fields) == 4}

def _ReportRunning(name: str, outputPath: Path, duration: str) -> None:
    # The output file can take a moment to appear after the job starts
    if not HasContent(outputPath):
        console.print(f"[info]Job {name} is currently running; its output hasn't been written yet. Current duration is "
                      f"{duration}[/info]")
        return
    with MapFile(outputPath) as inFile:
        stabilityInsert, convergence = ExtractStability(inFile), ExtractConvergence(inFile)
    if convergence is None:
        status = "is currently running. Convergence criterion header not found."
    else:
        # Gaussian has 4 criteria; ORCA 4 or 5
        status = f"is currently running, and has converged on {convergence[0]} out of {convergence[1]} criteria."
    console.print(f"[info]Job {name} {status}\n    {stabilityInsert} Current duration is {duration}[/info]")

# How a job ended, as a phrase for messages. The matched line itself only reads well for Gaussian: ORCA's is
# 'terminated normally' ("has finished via terminated normally")
def EndingPhrase(termination: str) -> str:
    return "error termination" if termination == Defaults.terminationVariants[2] else "normal termination"

def _ReportFinished(name: str, termination: str) -> None:
    if not termination:
        console.print(f"[warning]Job {name} left the queue without a termination line (cancelled, out of time, or no "
                      f"output written).[/warning]")
    elif termination == Defaults.terminationVariants[2]:
        console.print(f"[error]Job {name} has encountered {EndingPhrase(termination)}[/error]")
    else:
        console.print(f"[good]Job {name} has encountered {EndingPhrase(termination)}[/good]")

# Follows this run's jobs through the queue: progress while they run, then how each one ended (reported once, and
# again in the final summary). Jobs are matched to the queue by their full name
def jobStalking(jobSet: set, duration: int, frequency: int, loop: bool) -> None:
    waiting = {name: Path(outputPath) for name, outputPath in jobSet}
    finished = {}   # job name -> termination ('' if it left the queue without one)
    location = jobLocation()
    locationText = f" on {location}" if location else ""
    startTime = time.monotonic()
    while True:
        queue = _ReadQueue()
        if queue is None:
            console.print("[warning]Could not read the queue (squeue failed). Trying again at the next ping.[/warning]")
        else:
            for name, outputPath in list(waiting.items()):
                if name in queue:
                    state, startAt, current = queue[name]
                    if state == "PENDING":
                        console.print(f"[warning]Job {name} is currently pending. Expected start time is {startAt}[/warning]")
                    elif state == "RUNNING":
                        _ReportRunning(name, outputPath, current)
                    # Any other state (COMPLETING, CONFIGURING, ...) is checked again at the next ping
                    continue
                # Gone from the queue: find how it ended
                termination = ExtractFrom(outputPath, ExtractTermination, empty="")
                finished[name] = termination
                del waiting[name]
                _ReportFinished(name, termination)
                NotifyPersonal(f"Job {name}{locationText} has finished via {EndingPhrase(termination)}." if termination
                               else f"Job {name}{locationText} left the queue without a termination line.")

        # If all jobs for stalking are done, finish execution and release the terminal
        if not waiting:
            console.print("[operation]All jobs tagged for stalking have finished.[/operation]")
            break
        # Timeout: start another round when looping, otherwise stop
        if time.monotonic() - startTime >= duration * 60:
            if not loop:
                console.print("[warning]Job stalking terminated by timeout. Your jobs are still running.[/warning]")
                console.print("[info]Consider editing the default stalk duration and frequency if your jobs regularly "
                              "timeout.[/info]")
                break
            startTime = time.monotonic()

        lastPing = time.strftime("%a %I:%M:%S", time.localtime())
        console.print(f"[operation]Waiting {frequency * 60} seconds to ping the queue again. Last ping at {lastPing}"
                      " local time.[/operation]")
        time.sleep(frequency * 60)

    # Final summary: every job's termination together, plus any still queued at a timeout
    console.print("[operation]Stalking summary:[/operation]")
    for name, termination in finished.items():
        _ReportFinished(name, termination)
    for name in waiting:
        console.print(f"[info]Job {name} is still in the queue.[/info]")
