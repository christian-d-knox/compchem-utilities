"""The agent plugin on the human's own computer (`cu --update-plugin`): CompUtils' side of keeping it current.

The lab's agent plugin is a git clone of a private repository, so only the human's own computer, with
their GitHub sign-in, can see its updates. The cluster's daily check (update.DailyCheck) reminds the agent every day,
and the agent runs this command locally. The work is the plugin's own updater (scripts/update.py, which its
PreInvocation hook runs too), so the two ways of updating can never drift apart: this runs it and puts what it says in
the report."""
import json
import subprocess
import sys
from pathlib import Path

from rich.markup import escape

from . import agent
from .console import console

PLUGIN_NAME = "your-plugin"
UPDATER = Path("scripts") / "update.py"
UPDATER_TIMEOUT = 30
CLONE_URL = "https://github.com/your-lab/your-plugin.git"
COMMAND = "cu --update-plugin"
# What the plugin's updater reports, and what each problem needs: a retry tomorrow (the daily reminder), or a person
RETRY_KINDS = ("unreachable",)
# Each problem's fix, and the command a person runs for it (FOLDER: the plugin's; '' none)
FIXES = {
    "missing":       ("Install the plugin as a git clone", "CLONE"),
    "not-clone":     ("Move the old folder aside, then install the plugin as a git clone", "CLONE"),
    "no-git":        ("Install Git (Windows: git-scm.com; Mac: xcode-select --install), then sign in to GitHub once",
                      ""),
    "no-upstream":   ("Check the plugin's branch out again", "git -C FOLDER checkout main"),
    "local-changes": ("Keep or discard the plugin's local changes", "git -C FOLDER status"),
    "diverged":      ("Keep the clone's own commits elsewhere, then reset it to GitHub's branch",
                      "git -C FOLDER log --oneline @{u}..HEAD"),
    "sign-in":       ("Sign in to GitHub once in a terminal, with an account that can read the plugin's repository",
                      "git -C FOLDER fetch"),
}


def DefaultFolder() -> Path:
    """Where Antigravity reads a user's plugins (the App and the CLI alike; Windows: %USERPROFILE%\\.gemini\\...)."""
    return Path.home() / ".gemini" / "config" / "plugins" / PLUGIN_NAME


def CloneCommand(folder: Path) -> str:
    return f"git clone -b main {CLONE_URL} '{folder}'"


def UpdatePlugin(folder: Path | None = None) -> None:
    """Runs the plugin's own updater (`--now --json`) and records what it did: the record in results (exit 0), a retry
    for tomorrow when GitHub couldn't be reached (exit 5), anything else for the human (exit 7)."""
    folder = (folder or DefaultFolder()).expanduser()
    updater = folder / UPDATER
    if not updater.is_file():
        kind = "not-clone" if folder.is_dir() else "missing"
        _Human(kind, f"There's no {PLUGIN_NAME} plugin with an updater at {folder}.", folder)
        return
    try:
        result = subprocess.run([sys.executable, str(updater), "--now", "--json"], cwd=folder,
                                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=UPDATER_TIMEOUT)
        record = json.loads(result.stdout.strip().splitlines()[-1])
    except subprocess.TimeoutExpired:
        agent.Fail(f"The plugin's updater didn't finish in {UPDATER_TIMEOUT} s; the next daily reminder tries again.")
        return
    except (OSError, ValueError, IndexError):
        agent.Fail(f"The plugin's updater at {updater} gave no record it could read.")
        return
    kind = record.get("kind")
    if kind in RETRY_KINDS:
        agent.Fail(f"{record['problem']} The next daily reminder tries again.")
    elif kind:
        _Human(kind, record.get("problem", ""), folder)
    elif record.get("updated"):
        agent.Result({"plugin": record})
        console.print(f"[good]The {PLUGIN_NAME} plugin was updated ({escape(record['before'])} -> "
                      f"{escape(record['after'])}).[/good]")
        if record.get("reload"):
            agent.Warn("These changed files are read only when Antigravity starts: " + ", ".join(record["reload"]) +
                       ". The human presses Ctrl+R (Reload Window) to load them.")
    else:
        agent.Result({"plugin": record})
        console.print(f"[good]The {PLUGIN_NAME} plugin is up to date ({escape(record.get('after', '?'))}).[/good]")


def _Human(kind: str, problem: str, folder: Path) -> None:
    """A problem only the human can fix: what it is in warnings (the report's record of it), and their command."""
    fix, command = FIXES.get(kind, ("Fix the plugin's clone", ""))
    command = CloneCommand(folder) if command == "CLONE" else command.replace("FOLDER", f"'{folder}'")
    text = f"{problem} {fix}."
    agent.Warn(text, show=False)
    agent.Human(text, command, "For the human to run on their own computer, if they choose to.")
