"""Updating CompUtils from GitHub: the one place for it (`cu --update` and the TUI's Update CompUtils).

The branch is remembered by pip itself: every install from GitHub records the branch it asked for (direct_url.json), so a
reinstall from another branch becomes the one later updates use, and the record can't go stale. Before installing, the
installed commit is compared with the branch's latest on GitHub. A local install (pip install -e ., or running from a
checkout) is never replaced by GitHub's: it is updated where it lives (git pull).

DailyCheck is the automatic side: the first --json report of a day on a machine says what is newer (an agent relays it
to its human), so nobody, person or agent, ever has to ask. When a lab profile names an agent plugin, it also reminds
the agent, every day, to update it on the human's own computer (plugin.py): a private repository no cluster can see."""
import datetime
import importlib.metadata
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from rich.markup import escape

from .console import console

REPO_OWNER, REPO_NAME = "christian-d-knox", "compchem-utilities"
PACKAGE = "compchem-utilities"
# Until the first release dev is the only branch (then main); used when no install record names one
FALLBACK_BRANCH = "dev"
API_TIMEOUT = 10
# The daily check (DailyCheck) rides on a report someone is waiting for: it gives up on GitHub sooner
CHECK_TIMEOUT = 4
CHECK_FILE = "update-check.json"
_timeout = API_TIMEOUT


def PackageUrl(branch: str) -> str:
    """What pip installs CompUtils from (conda-installer.py builds the same URL)."""
    return f"git+https://github.com/{REPO_OWNER}/{REPO_NAME}.git@{branch}"


@dataclass
class Source:
    """Where the running CompUtils was installed from."""
    branch:    str
    commit:    str  = ""     # the full SHA pip recorded ('' if unknown)
    localPath: str  = ""     # set for a local install (editable, a folder, or a checkout pip doesn't know)
    editable:  bool = False  # a local install pip made editable (pip install -e)
    pipRecord: bool = True   # False: running from a checkout pip never installed (PYTHONPATH)


@dataclass
class Commit:
    sha:  str
    date: str                # YYYY-MM-DD


def InstalledSource() -> Source:
    """What pip recorded about this install (direct_url.json)."""
    try:
        record = json.loads(importlib.metadata.distribution(PACKAGE).read_text("direct_url.json") or "{}")
    except importlib.metadata.PackageNotFoundError:
        # Not installed by pip at all: running from a checkout
        return Source(FALLBACK_BRANCH, localPath=str(Path(__file__).resolve().parent.parent), pipRecord=False)
    except ValueError:
        record = {}
    if "dir_info" in record:
        url = record.get("url", "")
        if url.startswith("file:"):
            import urllib.parse, urllib.request
            path = urllib.request.url2pathname(urllib.parse.urlparse(url).path)
        else:
            path = url
        return Source(FALLBACK_BRANCH, localPath=path or "a local folder",
                      editable=bool(record["dir_info"].get("editable")))
    vcs = record.get("vcs_info", {})
    return Source(vcs.get("requested_revision") or FALLBACK_BRANCH, vcs.get("commit_id", ""))


def _Get(url: str, accept: str = "") -> str:
    """The text at url. Raises urllib.error.URLError / OSError / ValueError."""
    import urllib.request
    request = urllib.request.Request(url, headers={"User-Agent": "CompUtils", **({"Accept": accept} if accept else {})})
    with urllib.request.urlopen(request, timeout=_timeout) as response:
        return response.read().decode()


def _Reason(error: Exception) -> str:
    import urllib.error
    if isinstance(error, urllib.error.HTTPError):
        return f"GitHub answered {error.code} {error.reason}"
    return str(getattr(error, "reason", error))


def _GitHub(path: str):
    """A GitHub API response (JSON), or a str saying why there isn't one."""
    import urllib.error
    try:
        return json.loads(_Get(f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/{path}",
                               "application/vnd.github+json"))
    except (urllib.error.URLError, OSError, ValueError) as error:
        return _Reason(error)


def RepoFiles(ref: str) -> list[str] | str:
    """The names of the files at the repository's root at ref (a branch or commit), or why they couldn't be read."""
    import urllib.parse
    answer = _GitHub(f"contents?ref={urllib.parse.quote(ref)}")
    return answer if isinstance(answer, str) else [entry["name"] for entry in answer if entry.get("type") == "file"]


def RawFile(ref: str, path: str) -> tuple[bool, str]:
    """(True, the text of the repository's file at ref), or (False, why it couldn't be read). Raw files don't count
    against the API's hourly limit."""
    import urllib.error
    try:
        return True, _Get(f"https://raw.githubusercontent.com/{REPO_OWNER}/{REPO_NAME}/{ref}/{path}")
    except (urllib.error.URLError, OSError, ValueError) as error:
        return False, _Reason(error)


def LatestCommit(branch: str) -> Commit | str:
    """The branch's latest commit on GitHub, or why it couldn't be read."""
    answer = _GitHub(f"commits/{branch}")
    if isinstance(answer, str):
        missing = answer.startswith(("GitHub answered 404", "GitHub answered 422"))
        return f"no branch named {branch}" if missing else answer
    return Commit(answer["sha"], answer["commit"]["committer"]["date"][:10])


def Branches() -> list[str] | str:
    """The repository's branches, or why they couldn't be read."""
    answer = _GitHub("branches?per_page=100")
    return answer if isinstance(answer, str) else [branch["name"] for branch in answer]


def UpToDate(source: Source, latest: Commit | str) -> bool:
    return isinstance(latest, Commit) and bool(source.commit) and source.commit == latest.sha


def SourceLines(source: Source, latest: Commit | str) -> list[str]:
    """Installed vs GitHub, then the verdict (the CLI prints these; the TUI's pop-up shows them)."""
    installed = f"{source.branch} @ {source.commit[:7]}" if source.commit else f"{source.branch} (commit unknown)"
    lines = [f"Installed: {installed}"]
    if isinstance(latest, str):
        return lines + ["", f"Couldn't reach GitHub ({latest})."]
    lines += [f"GitHub:    {source.branch} @ {latest.sha[:7]} ({latest.date})", ""]
    return lines + ["Up to date." if UpToDate(source, latest) else "A newer version is available."]


def DailyCheck(remindPlugin: bool = True) -> dict | None:
    """What is newer on GitHub than this install, checked at most once per calendar day on this machine (the date is
    in <binDirectory>/update-check.json, written before checking, so a slow or failed check still counts): the report's
    `updates` (Main, --json runs only), or None (checked today already). The code and environment news is left out for
    a local install, a background environment job running, or GitHub unreachable; never acts: updating CompUtils is
    the person's call. `plugin` (plugin.Reminder, when a lab profile names one) is in every day's first report,
    unless remindPlugin is False (the plugin's own update run): the agent updates its plugin by itself (plugin.py)."""
    from .defaults import Defaults
    folder = Path(Defaults.binDirectory).expanduser()
    if not folder.is_dir():
        return None
    record, today = folder / CHECK_FILE, datetime.date.today().isoformat()
    try:
        if json.loads(record.read_text(encoding="utf-8")).get("date") == today:
            return None
    except (OSError, ValueError, AttributeError):
        pass
    try:
        temporary = record.with_name(record.name + ".tmp")
        temporary.write_text(json.dumps({"date": today}), encoding="utf-8")
        os.replace(temporary, record)
    except OSError:
        # Unrecorded, it would check on every run: better not at all
        return None
    from .plugin import Reminder
    plugin = Reminder() if remindPlugin else None
    reminder = {"plugin": plugin} if plugin else {}
    source = InstalledSource()
    if source.localPath:
        return reminder or None
    global _timeout
    _timeout = CHECK_TIMEOUT
    try:
        return {**(_Updates(source) or {}), **reminder} or None
    finally:
        _timeout = API_TIMEOUT


def _Updates(source: Source) -> dict | None:
    """The code's and the running environment's news on the installed branch, and the one command that brings both
    (an environment refresh installs the branch's latest code too, so it comes first: environment.CodeUpdateProblem's
    rule)."""
    from . import environment
    name = environment.RunningName()
    # An environment job brings the code up to date by itself when it's done
    if name is not None and environment.RunningJob(name) is not None:
        return None
    latest = LatestCommit(source.branch)
    if isinstance(latest, str):
        return None
    updates, command, why = {}, "", ""
    if not UpToDate(source, latest):
        installed = source.commit[:7] or "unknown"
        updates["code"] = {"branch": source.branch, "installed": installed, "latest": latest.sha[:7]}
        command = "cu --update"
        why = f"Branch {source.branch} has newer CompUtils code on GitHub ({installed} -> {latest.sha[:7]})."
    if name is not None:
        # Read at the commit just found (one fewer GitHub call than a branch, and the same files)
        variants = environment.ReadVariants(source, latest.sha)
        variant = (None if isinstance(variants, str) else
                   next((variant for variant in variants.found if variant.name == name), None))
        status = environment.CheckStatus(variant) if variant is not None else None
        if status is not None and not status.current:
            mode = "rebuild" if status.needsRebuild else "refresh"
            updates["environment"] = {"name": name, "verdict": status.Verdict()}
            command = f"cu -env {mode}"
            why = (f"The {name} environment needs a {mode} (status: {status.Verdict()}). It runs in the background "
                   f"and brings CompUtils' code on branch {source.branch} up to date too.")
    if not updates:
        return None
    return {**updates, "command": command, "why": why}


def LocalInstallMessage(source: Source) -> str:
    return (f"CompUtils is installed from a local folder ({source.localPath}), so it isn't replaced from GitHub. "
            f"Update it there (git pull).")


def Install(branch: str) -> bool:
    """Reinstalls CompUtils from the branch on GitHub (the same install conda-installer.py runs). pip's output is
    captured (it must never write to the TUI's screen) and shown only if the install failed."""
    console.print(f"[operation]Updating from branch '{escape(branch)}'...[/operation]")
    result = subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade", "--force-reinstall", "--no-deps",
                             "--no-cache-dir", PackageUrl(branch)], capture_output=True, text=True)
    if result.returncode == 0:
        console.print(f"[good]Updated from branch '{escape(branch)}'.[/good]")
        return True
    console.print(f"[error]The update failed. pip said:[/error]\n{escape((result.stdout + result.stderr).strip())}")
    return False
