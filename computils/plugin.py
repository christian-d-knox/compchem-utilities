"""An agent plugin on the human's own computer (`cu --update-plugin`): CompUtils' side of keeping it current.

A lab may give its agents a plugin of its own, kept as a git clone of a private repository, so only the human's own
computer, with their GitHub sign-in, can see its updates. CompUtils knows nothing of any plugin until a lab profile
names one (`agentPluginFolder`, `agentPluginUpdater`, `agentPluginRepo`, `agentPluginBranch`): then the cluster's daily
check (update.DailyCheck) reminds the agent every day, with this command and those values as its flags, and the agent
runs it locally. The work is the plugin's own updater (the profile's `agentPluginUpdater`, a script in the clone that
prints one JSON record), so CompUtils and the plugin can never disagree on how it updates: this runs it and puts what
it says in the report."""
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from rich.markup import escape

from . import agent
from .console import console

UPDATER_TIMEOUT = 30
# What the plugin's updater reports, and what each problem needs: a retry tomorrow (the daily reminder), or a person
RETRY_KINDS = ("unreachable",)
# Each problem's fix, and the command a person runs for it (FOLDER: the plugin's, BRANCH its branch; '' none)
FIXES = {
    "missing":       ("Install the plugin as a git clone", "CLONE"),
    "not-clone":     ("Move the old folder aside, then install the plugin as a git clone", "CLONE"),
    "no-git":        ("Install Git (Windows: git-scm.com; Mac: xcode-select --install), then sign in to GitHub once",
                      ""),
    "no-upstream":   ("Check the plugin's branch out again", "git -C FOLDER checkout BRANCH"),
    "local-changes": ("Keep or discard the plugin's local changes", "git -C FOLDER status"),
    "diverged":      ("Keep the clone's own commits elsewhere, then reset it to GitHub's branch",
                      "git -C FOLDER log --oneline @{u}..HEAD"),
    "sign-in":       ("Sign in to GitHub once in a terminal, with an account that can read the plugin's repository",
                      "git -C FOLDER fetch"),
}


@dataclass
class Plugin:
    """Where the plugin is and how it updates: the flags, else this computer's config (a lab profile's values)."""
    folder: str = ""
    updater: str = ""
    repo: str = ""
    branch: str = ""

    @classmethod
    def Configured(cls, folder: str | None = None, updater: str | None = None, repo: str | None = None,
                   branch: str | None = None) -> "Plugin":
        from .defaults import Defaults
        return cls(folder or Defaults.agentPluginFolder, updater or Defaults.agentPluginUpdater,
                   repo or Defaults.agentPluginRepo, branch or Defaults.agentPluginBranch)

    def Words(self) -> list[str]:
        """The flags that name it, for the reminder's command (built where the profile is: the cluster)."""
        words = ["--update-plugin"]
        for flag, value in (("--plugin-dir", self.folder), ("--plugin-updater", self.updater),
                            ("--plugin-repo", self.repo), ("--plugin-branch", self.branch)):
            if value:
                words += [flag, value]
        return words

    def Path(self) -> Path:
        return Path(self.folder).expanduser()


def Reminder() -> dict | None:
    """The report's daily `updates.plugin` (update.DailyCheck): its command and why, or None when no profile names a
    plugin here."""
    plugin = Plugin.Configured()
    if not (plugin.folder and plugin.updater):
        return None
    from .agent import CommandLine
    return {"command": CommandLine(plugin.Words()),
            "why": "A daily reminder, not news: CompUtils can't see the agent plugin's private repository, so the "
                   "plugin is checked, and updated if it's behind, on the human's own computer."}


def UpdatePlugin(plugin: Plugin) -> None:
    """Runs the plugin's own updater (`--now --json`) and records what it did: the record in results (exit 0), a retry
    for tomorrow when GitHub couldn't be reached (exit 5), anything else for the human (exit 7)."""
    if not (plugin.folder and plugin.updater):
        text = ("No agent plugin is set up here: a lab profile names one (agentPluginFolder, agentPluginUpdater), or "
                "the daily reminder's command (updates.plugin) gives its flags.")
        agent.Warn(text, show=False)
        agent.Human(text)
        return
    folder = plugin.Path()
    updater = folder / plugin.updater
    if not updater.is_file():
        kind = "not-clone" if folder.is_dir() else "missing"
        _Human(kind, f"There's no plugin with an updater at {folder}.", plugin)
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
        _Human(kind, record.get("problem", ""), plugin)
    elif record.get("updated"):
        agent.Result({"plugin": record})
        console.print(f"[good]The plugin was updated ({escape(record['before'])} -> {escape(record['after'])})."
                      "[/good]")
        if record.get("reload"):
            agent.Warn("These changed files are read only when the agent's app starts: " +
                       ", ".join(record["reload"]) + ". The human reloads it (e.g. Ctrl+R, Reload Window) to load "
                       "them.")
    else:
        agent.Result({"plugin": record})
        console.print(f"[good]The plugin is up to date ({escape(record.get('after', '?'))}).[/good]")


def _Human(kind: str, problem: str, plugin: Plugin) -> None:
    """A problem only the human can fix: what it is in warnings (the report's record of it), and their command."""
    fix, command = FIXES.get(kind, ("Fix the plugin's clone", ""))
    folder = f"'{plugin.Path()}'"
    if command == "CLONE":
        branch = f"-b {plugin.branch} " if plugin.branch else ""
        command = f"git clone {branch}{plugin.repo} {folder}" if plugin.repo else ""
    elif "BRANCH" in command and not plugin.branch:
        command = ""
    command = command.replace("FOLDER", folder).replace("BRANCH", plugin.branch)
    text = f"{problem} {fix}."
    agent.Warn(text, show=False)
    agent.Human(text, command, "For the human to run on their own computer, if they choose to.")
