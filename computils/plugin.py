"""An agent plugin on the human's own computer (`cu --update-plugin`): CompUtils' side of keeping it current.

A lab may give its agents a plugin of its own, kept as a git clone of a private repository, so only the human's own
computer, with their GitHub sign-in, can see its updates. CompUtils knows nothing of any plugin until a lab profile
names one (`agentPluginFolder`, `agentPluginUpdater`, `agentPluginRepo`, `agentPluginBranch`, and
`agentPluginProfile` when the plugin carries the lab profile itself): then the cluster's daily
check (update.DailyCheck) reminds the agent every day, with this command and those values as its flags, and the agent
runs it locally. The work is the plugin's own updater (the profile's `agentPluginUpdater`, a script in the clone that
prints one JSON record), so CompUtils and the plugin can never disagree on how it updates: this runs it and puts what
it says in the report, and whether the plugin's lab profile is newer than the cluster's or this computer's
(profile.py: the relay)."""
import json
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from rich.markup import escape

from . import agent
from .console import console

UPDATER_TIMEOUT = 30
# scp/ssh to the cluster for the lab profile relay: never a password prompt (BatchMode), never hanging
SSH_TIMEOUT = 60
SSH_OPTIONS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=20"]
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
    # The lab profile's path in the plugin, and the cluster's side of the relay: the digest of its newest profile and
    # where a newer one is copied to (the reminder's, from the cluster; '' elsewhere)
    profile: str = ""
    profileDigest: str = ""
    profileDest: str = ""
    # The cluster's ssh alias (as `cu -agent --ssh-host` recorded it there) and cu's path on it, so this computer copies
    # and applies a newer profile itself
    clusterHost: str = ""
    clusterCu: str = ""

    @classmethod
    def Configured(cls, folder: str | None = None, updater: str | None = None, repo: str | None = None,
                   branch: str | None = None, profile: str | None = None, profileDigest: str | None = None,
                   profileDest: str | None = None, clusterHost: str | None = None,
                   clusterCu: str | None = None) -> "Plugin":
        from .defaults import Defaults
        return cls(folder or Defaults.agentPluginFolder, updater or Defaults.agentPluginUpdater,
                   repo or Defaults.agentPluginRepo, branch or Defaults.agentPluginBranch,
                   profile or Defaults.agentPluginProfile, profileDigest or "", profileDest or "", clusterHost or "",
                   clusterCu or "")

    def Words(self) -> list[str]:
        """The flags that name it, for the reminder's command (built where the profile is: the cluster)."""
        words = ["--update-plugin"]
        for flag, value in (("--plugin-dir", self.folder), ("--plugin-updater", self.updater),
                            ("--plugin-repo", self.repo), ("--plugin-branch", self.branch),
                            ("--plugin-profile", self.profile), ("--profile-digest", self.profileDigest),
                            ("--profile-dest", self.profileDest), ("--cluster-host", self.clusterHost),
                            ("--cluster-cu", self.clusterCu)):
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
    if plugin.profile:
        from .profile import CurrentDigest, RelayPath
        plugin.profileDest = str(RelayPath(create=True))
        plugin.profileDigest = CurrentDigest() or "none"
        from .agentskill import CuPath, RememberedHost
        plugin.clusterHost, plugin.clusterCu = RememberedHost(), CuPath()
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
        _CheckProfile(plugin, record)
        agent.Result({"plugin": record})
        console.print(f"[good]The plugin was updated ({escape(record['before'])} -> {escape(record['after'])})."
                      "[/good]")
        if record.get("reload"):
            agent.Warn("These changed files are read only when the agent's app starts: " +
                       ", ".join(record["reload"]) + ". The human reloads it (e.g. Ctrl+R, Reload Window) to load "
                       "them.")
    else:
        _CheckProfile(plugin, record)
        agent.Result({"plugin": record})
        console.print(f"[good]The plugin is up to date ({escape(record.get('after', '?'))}).[/good]")


def _CheckProfile(plugin: Plugin, record: dict) -> None:
    """The lab profile the plugin carries (agentPluginProfile): whether it differs from the cluster's newest (the
    reminder's digest) or from the one applied on this computer. A newer one is copied to the cluster (scp, which
    applies nothing: the cluster offers it from there too), then the human is asked (`apply-lab-profile`) before it is
    applied on the cluster (ssh) and here. record.profile {file, dest, cluster, local, copied, applied}."""
    if not plugin.profile:
        return
    from .profile import AppliedDigest, DeclineLocal, Digest, LocalDeclined
    path = plugin.Path() / plugin.profile
    try:
        digest = Digest(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        agent.Warn(f"The plugin has no lab profile at {plugin.profile} (agentPluginProfile).")
        return
    cluster = bool(plugin.profileDigest) and plugin.profileDigest != digest
    local = AppliedDigest() not in ("", digest) and LocalDeclined() != digest
    if not (cluster or local):
        return
    copied = cluster and _CopyUp(plugin, path)
    profile = record["profile"] = {"file": str(path), "dest": plugin.profileDest, "cluster": cluster, "local": local,
                                   "copied": copied, "applied": []}
    if cluster and not copied:
        agent.Warn("The plugin's lab profile is newer than the cluster's: copy it up (scp) and ask the human before "
                   "applying it there.")
        if not local:
            return
    where = " and ".join(place for place, differs in (("the cluster", copied), ("this computer", local)) if differs)
    from .prompts import AskBool
    if not AskBool(f"Your lab profile has changed (the plugin's copy). Apply it on {where} now?", "y",
                   key="apply-lab-profile"):
        # The cluster's relay file is the newer one now, so tomorrow's reminder doesn't ask again; this computer
        # remembers the declined version
        if local:
            DeclineLocal(digest)
        console.print("[info]Kept the current lab profile. Run cu -profile with the new file to apply it later."
                      "[/info]")
        return
    if copied and _ApplyRemote(plugin):
        profile["applied"].append("cluster")
    if local:
        from .profile import ApplyProfile
        if ApplyProfile(path):
            profile["applied"].append("local")


def _CopyUp(plugin: Plugin, path: Path) -> bool:
    """scp the plugin's profile to the cluster's relay file (only into the folder the cluster made for it)."""
    if not (plugin.clusterHost and plugin.profileDest):
        return False
    try:
        # From its own folder: a Windows path's drive letter would read as a host to scp
        result = subprocess.run(["scp", "-q", *SSH_OPTIONS, path.name, f"{plugin.clusterHost}:{plugin.profileDest}"],
                                cwd=path.parent, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                timeout=SSH_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as error:
        agent.Warn(f"Couldn't copy the lab profile to {plugin.clusterHost}: {error}")
        return False
    if result.returncode != 0:
        agent.Warn(f"Couldn't copy the lab profile to {plugin.clusterHost}: {result.stderr.strip()[-300:]}")
        return False
    console.print(f"[good]Copied the plugin's lab profile to {escape(plugin.clusterHost)}.[/good]")
    return True


def _ApplyRemote(plugin: Plugin) -> bool:
    """`cu -profile RELAY_FILE --json` on the cluster, over ssh; its report's exit code says whether it applied."""
    if not plugin.clusterCu:
        agent.Warn("Copied, but cu's path on the cluster is unknown: run cu -profile on the cluster.")
        return False
    command = f"{shlex.quote(plugin.clusterCu)} -profile {shlex.quote(plugin.profileDest)} --json"
    try:
        result = subprocess.run(["ssh", *SSH_OPTIONS, plugin.clusterHost, command], stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=SSH_TIMEOUT)
        report = json.loads(result.stdout.strip().splitlines()[-1])
    except (OSError, subprocess.TimeoutExpired, ValueError, IndexError):
        agent.Warn(f"Couldn't apply the lab profile on {plugin.clusterHost}: run cu -profile there.")
        return False
    if report.get("exit") != 0:
        agent.Warn(f"The cluster didn't apply the lab profile: {report.get('error') or report.get('status')}.")
        return False
    console.print(f"[good]Applied the lab profile on {escape(plugin.clusterHost)}.[/good]")
    return True


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
