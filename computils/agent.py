"""
Agent mode: how one run of cu talks to something that isn't a person at a terminal (an AI agent running
`ssh cluster 'cu ... --json'`, or a script).

Three things every run has, person or not:
  - an exit code (Exit), set from what the run recorded, so a caller knows what happened without reading the prose
  - a Report: what was submitted, written, skipped (and why), asked, and what to run next
  - questions that can be answered in advance (prompts.py: every Ask* has a key; --answer KEY=VALUE)

With --json the report is printed to stdout once, at the end, and every Rich message goes to stderr instead, so the
caller can read either. Without a TTY (or with --json) a question that has no answer stops the run before it acts
(exit 3) instead of waiting for a keyboard that isn't there.

The helpers below (Skip, Warn, Next, Notice, Fail, Stop, Submitted, Written, Result) print what a person would have
seen AND record it, in one call, so a message is never written twice and a skip is never missed by the report.
"""
import json, sys, traceback
from dataclasses import dataclass, field
from enum import IntEnum

import regex
from rich.markup import escape

from .console import console


class Exit(IntEnum):
    """cu's exit codes. 1 and 2 are Python's and argparse's own."""
    DONE     = 0   # everything asked for was done
    CRASHED  = 1   # an unhandled exception: a CompUtils bug (report.error has its last line)
    USAGE    = 2   # bad flags or values, or an invalid --answer
    QUESTION = 3   # stopped before acting, to ask report.question: re-run with --answer KEY=VALUE
    PARTIAL  = 4   # some items done, some skipped (report.skipped says why)
    NOTHING  = 5   # nothing could be done (every item skipped, or nothing matched)
    STOPPED  = 6   # a precondition isn't met: run report.next first, then retry
    HUMAN    = 7   # only a person can do this (setup, a lab profile, a config edit): report.next says what


# How each exit reads in the report
STATUS = {Exit.DONE: "done", Exit.CRASHED: "crashed", Exit.USAGE: "usage", Exit.QUESTION: "question",
          Exit.PARTIAL: "partial", Exit.NOTHING: "nothing", Exit.STOPPED: "stopped", Exit.HUMAN: "human"}


# ─── Exceptions that end a run early (Main catches each) ──────────────

class QuestionNeeded(Exception):
    """A question was reached with nobody to answer it and no --answer for it. Raised before the command acts."""
    def __init__(self, key: str, text: str, kind: str, options: list[str] | None = None, default=None) -> None:
        super().__init__(f"{text} (needs --answer {key}=...)")
        self.question = {"key": key, "text": text, "type": kind, "options": options or [], "default": default}


class UsageError(Exception):
    """Bad flags, values or answers (exit 2). The message says what was wrong."""


class NeedsHuman(Exception):
    """Something only a person can do (exit 7). `then` is what they should run."""
    def __init__(self, message: str, then: str = "") -> None:
        super().__init__(message)
        self.then = then


# ─── Run-wide state (set by Main before anything else; the TUI leaves it alone) ──

jsonOutput = False             # --json: the report on stdout, messages on stderr
dryRun     = False             # --dry-run: inputs and scripts written, nothing submitted (jobs.SubmitJob)
answers: dict[str, str] = {}   # --answer KEY=VALUE, read by prompts.py


@dataclass
class Report:
    command:   str = ""
    submitted: list[dict] = field(default_factory=list)
    written:   list[str]  = field(default_factory=list)
    skipped:   list[dict] = field(default_factory=list)
    results:   list       = field(default_factory=list)
    warnings:  list[str]  = field(default_factory=list)
    next:      list[dict] = field(default_factory=list)
    notices:   list[str]  = field(default_factory=list)
    question:  dict | None = None
    updates:   dict | None = None  # update.DailyCheck: what is newer on GitHub (first report of a day, if anything)
    error:     str  = ""       # what went wrong: a usage error's message, or a crash's last traceback line
    human:     bool = False    # NeedsHuman
    usage:     bool = False    # UsageError / argparse
    stopped:   bool = False    # Stop(): a precondition isn't met
    acted:     bool = False    # Acted(): the command did its work (one that records nothing else, e.g. -init)

    def Exit(self) -> Exit:
        if self.usage:
            return Exit.USAGE
        if self.error:
            return Exit.CRASHED
        if self.question is not None:
            return Exit.QUESTION
        if self.human:
            return Exit.HUMAN
        did = bool(self.submitted or self.written or self.results or self.acted)
        if self.stopped and not did:
            return Exit.STOPPED
        if self.skipped:
            return Exit.PARTIAL if did else Exit.NOTHING
        return Exit.DONE

    def AsDict(self) -> dict:
        code = self.Exit()
        out = {"computils": _Version(), "command": self.command, "exit": int(code), "status": STATUS[code]}
        # Empty fields are left out: every token an agent reads should carry something
        for name in ("submitted", "written", "skipped", "results", "warnings", "question", "next", "notices", "updates",
                     "error"):
            value = getattr(self, name)
            if value:
                out[name] = value
        return out


report = Report()


def Reset(command: str = "") -> None:
    """A fresh report (Main, per run; the TUI's dispatches never read it)."""
    global report
    report = Report(command=command)


def _Version() -> dict:
    from .update import InstalledSource
    source = InstalledSource()
    version = {"commit": source.commit[:7] if source.commit else "local"}
    # The skill's digest, so an agent knows when to regenerate its skill. Never worth failing a report over
    try:
        from .agentskill import SkillDigest
        version["skill"] = SkillDigest()
    except Exception:
        pass
    return version


# ─── Print and record, in one call ────────────────────────────────────

def Skip(item: str, reason: str, message: str | None = None, show: bool = True) -> None:
    """One item (a file, a job, a method variant) wasn't done. Prints `message`, else '<reason>. Skipping <item>.'
    (show=False: a message covering several items was already printed)"""
    if show:
        console.print(f"[error]{escape(message if message is not None else f'{reason}. Skipping {item}.')}[/error]")
    report.skipped.append({"item": str(item), "reason": reason})


def Fail(reason: str) -> None:
    """The command itself couldn't do its work (no item to name): recorded as a skip of the whole command."""
    console.print(f"[error]{escape(reason)}[/error]")
    report.skipped.append({"item": report.command or "command", "reason": reason})


def Warn(text: str, show: bool = True) -> None:
    if show:
        console.print(f"[warning]{escape(text)}[/warning]")
    report.warnings.append(text)


def CommandLine(words) -> str:
    """`cu WORDS`, quoted to run inside `ssh host "…"` as the skill calls it: single quotes, never double ones (they
    would end the ssh command's own, and Windows PowerShell 5.1 strips double quotes inside a program's argument). A
    single quote inside a value is written '\\''."""
    def Quote(word: str) -> str:
        word = str(word)
        if word and regex.fullmatch(r"[\w@%+=:,./-]+", word):
            return word
        return "'" + word.replace("'", "'\\''") + "'"
    return " ".join(["cu", *(Quote(word) for word in words)])


def Next(command: str, why: str, show: bool = True) -> None:
    """A command to run next (an agent follows these; a person reads them)."""
    if show:
        console.print(f"[info]Next: {escape(command)} ({escape(why)})[/info]")
    report.next.append({"command": command, "why": why})


def Stop(reason: str, command: str, why: str) -> None:
    """A precondition isn't met: say so, and what to run first (exit 6 unless something was already done)."""
    console.print(f"[warning]{escape(reason)}[/warning]")
    report.stopped = True
    Next(command, why)


def Human(reason: str, then: str = "", why: str = "A person must run this in a terminal on the cluster.") -> None:
    """Only a person can do this, or decide it (setup, a config edit, waiving a check): say so, and what they should
    run (exit 7)."""
    console.print(f"[error]{escape(reason)}[/error]")
    report.human = True
    if then:
        report.next.append({"command": then, "why": why})


def Notice(severity: str, text: str) -> None:
    """Something that happened outside this command (an environment job that ended, jobs caught up on)."""
    console.print(f"[{severity}]{escape(text)}[/{severity}]")
    report.notices.append(text)


def Submitted(name: str, jobID: str, cluster: str = "", output: str = "", nevaTouch: str = "") -> None:
    entry = {"name": name, "jobId": jobID}
    entry.update({key: value for key, value in (("cluster", cluster), ("output", output), ("nevaTouch", nevaTouch))
                  if value})
    report.submitted.append(entry)


def Written(path) -> None:
    from pathlib import Path
    path = str(Path(path).resolve())
    if path not in report.written:
        report.written.append(path)


def Result(entry) -> None:
    report.results.append(entry)


def Acted() -> None:
    report.acted = True


# ─── Main's side ──────────────────────────────────────────────────────

def Configure(argv: list[str]) -> None:
    """Set from argv before anything prints: --json moves every message to stderr. (--answer and --dry-run are read
    with the other flags, in cli.ParseCLI, before any question can be asked.)"""
    global jsonOutput
    jsonOutput = "--json" in argv
    # -agent's stdout is the skill itself (redirected into SKILL.md): a startup message (a config warning, jobs caught
    # up on) printed there would land above its frontmatter, so messages go to stderr, as with --json
    if jsonOutput or any(arg in ("-agent", "--agent") for arg in argv):
        console.file = sys.stderr


def SetAnswers(pairs: list[str]) -> None:
    for pair in pairs or []:
        key, separator, value = pair.partition("=")
        if not separator or not key.strip():
            raise UsageError(f"--answer takes KEY=VALUE (got '{pair}').")
        answers[key.strip()] = value.strip()


def Crashed() -> None:
    """Record an unhandled exception (Main prints the traceback to stderr first)."""
    report.error = traceback.format_exc().strip().splitlines()[-1]


def Finish() -> int:
    """Print the report (--json) and return the exit code."""
    if jsonOutput:
        sys.stdout.write(json.dumps(report.AsDict(), separators=(",", ":"), default=str) + "\n")
        sys.stdout.flush()
    return int(report.Exit())
