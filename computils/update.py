"""Updating CompUtils from GitHub: the one place for it (`cu --update` and the TUI's Update CompUtils).

The branch is remembered by pip itself: every install from GitHub records the branch it asked for (direct_url.json), so a
reinstall from another branch becomes the one later updates use, and the record can't go stale. Before installing, the
installed commit is compared with the branch's latest on GitHub. A local install (pip install -e ., or running from a
checkout) is never replaced by GitHub's: it is updated where it lives (git pull)."""
import importlib.metadata
import json
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from rich.markup import escape

from .console import console

REPO_OWNER, REPO_NAME = "christian-d-knox", "compchem-utilities"
PACKAGE = "compchem-utilities"
# Until the first release dev is the only branch (then main); used when no install record names one
FALLBACK_BRANCH = "dev"
API_TIMEOUT = 10


def PackageUrl(branch: str) -> str:
    """What pip installs CompUtils from (conda-installer.py builds the same URL)."""
    return f"git+https://github.com/{REPO_OWNER}/{REPO_NAME}.git@{branch}"


@dataclass
class Source:
    """Where the running CompUtils was installed from."""
    branch:    str
    commit:    str = ""      # the full SHA pip recorded ('' if unknown)
    localPath: str = ""      # set for a local install (editable, a folder, or a checkout pip doesn't know)


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
        return Source(FALLBACK_BRANCH, localPath=str(Path(__file__).resolve().parent.parent))
    except ValueError:
        record = {}
    if "dir_info" in record:
        return Source(FALLBACK_BRANCH, localPath=record.get("url", "").removeprefix("file://") or "a local folder")
    vcs = record.get("vcs_info", {})
    return Source(vcs.get("requested_revision") or FALLBACK_BRANCH, vcs.get("commit_id", ""))


def _GitHub(path: str):
    """A GitHub API response (JSON), or a str saying why there isn't one."""
    request = urllib.request.Request(f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/{path}",
                                     headers={"User-Agent": "CompUtils", "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(request, timeout=API_TIMEOUT) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as error:
        return f"GitHub answered {error.code} {error.reason}"
    except (urllib.error.URLError, OSError, ValueError) as error:
        return str(getattr(error, "reason", error))


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
