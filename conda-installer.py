#!/usr/bin/env python3
"""
CompUtils Installer.

The one file to download. Run it on the cluster's login node:
  python3 conda-installer.py

This is the dev branch's installer: it installs CompUtils from the dev branch.
It finds conda (or installs Miniconda to ~/miniconda3, set to use conda-forge
only), builds or updates the 'compUtils' environment from the branch's
compUtils.yml, installs CompUtils itself, and adds the 'con' alias to activate
it. It asks nothing, and every step is safe to repeat, so a failed run is fixed
by running it again.

The first 'cu' run sets CompUtils up, and asks for your lab's profile (a file
your lab shares privately: its clusters and shared settings) if it has one.

CompUtils' environment is built from conda-forge only (--override-channels,
whatever channels your conda is set up with), so it never needs Anaconda's
package channels or their Terms of Service. This installer never accepts any
terms on your behalf.

Power users:
  --conda-dir PATH   find or install conda here instead of the usual places
  --yml PATH         build the environment from a hand-edited env file

CompUtils also runs this file itself (cu -env, the TUI's Update Environment) in
a hidden background mode, to refresh, rebuild or install an environment: it
builds '<name>-next' alongside, checks it, and swaps it in once nothing runs
from the old one. That mode is never typed; see Background().

Written for Python 3.6+ and the standard library only: login nodes often have
an old system python3.

Originally written by Christian Drew Knox for the Peng Liu Research Group.
"""
import argparse
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

# ─── Configuration ─────────────────────────────────────────────────────────────
HOME = Path.home()
DEFAULT_CONDA_DIR = HOME / "miniconda3"
KNOWN_CONDA_DIRS = [DEFAULT_CONDA_DIR, HOME / "anaconda3", HOME / "miniforge3"]
MINICONDA_URL = "https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-{arch}.sh"
MINICONDA_ARCHES = {"x86_64": "x86_64", "aarch64": "aarch64", "arm64": "aarch64"}
REPO_OWNER = "christian-d-knox"
REPO_NAME = "compchem-utilities"
# This is the dev branch's installer, so it installs dev (main's installer installs main)
BRANCH = "dev"
# The environment's name when the env file doesn't give one
ENV_NAME = "compUtils"
# Where conda's executable sits inside a conda installation (Linux, then Windows)
CONDA_PLACES = ["bin/conda", "Scripts/conda.exe", "condabin/conda.bat"]


def YmlUrl() -> str:
    """The raw-content URL of the branch's compUtils.yml."""
    return f"https://raw.githubusercontent.com/{REPO_OWNER}/{REPO_NAME}/{BRANCH}/compUtils.yml"


def PackageUrl() -> str:
    """What pip installs CompUtils from (computils/update.py's PackageUrl builds the same URL)."""
    return f"git+https://github.com/{REPO_OWNER}/{REPO_NAME}.git@{BRANCH}"


# ─── Helpers ───────────────────────────────────────────────────────────────────

def Run(args, check: bool = True, env=None) -> int:
    """Run a command (a list, no shell), printing it first."""
    print("\n+ " + " ".join(str(arg) for arg in args), flush=True)
    return subprocess.run([str(arg) for arg in args], check=check, env=env).returncode


def InEnv(conda: Path, name: str, *args) -> list:
    """A command run inside the named environment, its output shown as it happens."""
    return [conda, "run", "--no-capture-output", "-n", name, *args]


def InPrefix(conda: Path, prefix: Path, *args) -> list:
    """A command run inside the environment at prefix (activated, so Windows finds its DLLs)."""
    return [conda, "run", "--no-capture-output", "-p", prefix, *args]


def CondaIn(folder: Path):
    """The conda executable of the conda installation at folder, or None."""
    for place in CONDA_PLACES:
        if (folder / place).exists():
            return folder / place
    return None


def Download(url: str, target: Path) -> None:
    """Save url to target (urllib, so wget isn't needed). Raises urllib.error.URLError."""
    with urllib.request.urlopen(url, timeout=60) as response, open(str(target), "wb") as out:
        shutil.copyfileobj(response, out)


def Fail(message: str) -> None:
    print(f"\n✗ {message}", file=sys.stderr)
    sys.exit(1)


# ─── Installation steps ────────────────────────────────────────────────────────

def FindConda(condaDir) -> Path:
    """
    The conda executable to use, or None. The user's --conda-dir comes first
    (and alone: it is where conda goes if it isn't there yet), then an active
    or module-loaded conda, then conda on PATH, then the usual home folders.
    """
    if condaDir is not None:
        return CondaIn(condaDir)
    found = os.environ.get("CONDA_EXE") or shutil.which("conda")
    if found and Path(found).exists():
        return Path(found)
    for folder in KNOWN_CONDA_DIRS:
        conda = CondaIn(folder)
        if conda is not None:
            return conda
    return None


def InstallMiniconda(condaDir: Path) -> Path:
    """Install Miniconda into condaDir and set it up for bash; return its conda."""
    arch = MINICONDA_ARCHES.get(platform.machine())
    if platform.system() != "Linux" or arch is None:
        Fail(f"No conda was found, and this installer can only install Miniconda on 64-bit "
             f"Linux (this is {platform.system()} {platform.machine()}). Install conda "
             f"yourself, then run the installer again.")

    print(f"Installing Miniconda to {condaDir}. Your terminal will be busy for a moment.")
    with tempfile.TemporaryDirectory() as scratch:
        installer = Path(scratch) / "miniconda.sh"
        Download(MINICONDA_URL.format(arch=arch), installer)
        Run(["bash", installer, "-b", "-u", "-p", condaDir])
    conda = condaDir / "bin" / "conda"
    # Registers conda in ~/.bashrc for future shells (this one is handled by calling conda directly)
    Run([conda, "init", "bash"])
    UseCondaForgeOnly(conda)
    print("✓ Miniconda installed.")
    return conda


def UseCondaForgeOnly(conda: Path) -> None:
    """Set a Miniconda this installer just installed to use conda-forge only. Anaconda's own channels (and the Terms
    of Service they ask to be accepted) are then never used; CompUtils' environment comes from conda-forge anyway.
    An existing conda's settings are never changed."""
    print("Setting the new conda to use conda-forge only.")
    # Adding a channel to a fresh config writes the implicit 'defaults' after it, which is then removed (a failure there
    # means it was never set, which is fine)
    Run([conda, "config", "--add", "channels", "conda-forge"])
    Run([conda, "config", "--remove", "channels", "defaults"], check=False)
    Run([conda, "config", "--set", "channel_priority", "strict"])


def FetchYml(scratch: Path) -> Path:
    """Download the branch's compUtils.yml into scratch (fresh every run, never kept)."""
    target = scratch / "compUtils.yml"
    print(f"Downloading compUtils.yml from branch '{BRANCH}'...")
    try:
        Download(YmlUrl(), target)
    except urllib.error.HTTPError as error:
        Fail(f"Couldn't download compUtils.yml from branch '{BRANCH}' ({error.code} {error.reason}).")
    return target


def EnvExists(conda: Path, name: str) -> bool:
    output = subprocess.check_output([str(conda), "env", "list", "--json"], universal_newlines=True)
    return any(Path(env).name == name for env in json.loads(output).get("envs", []))


def ReadYml(yml: Path):
    """The env file's environment name, channels (nodefaults left out) and package specs. Only the flat lists
    compUtils.yml uses are read (no yaml module in the standard library); a nested entry such as '- pip:' stops the
    install. CompUtils keeps a twin (computils/environment.py's ReadYml), which also reads '# @platform' lines."""
    name, channels, specs, section = ENV_NAME, [], [], None
    for raw in yml.read_text().splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if not line[0].isspace():
            section, _, value = line.partition(":")
            section = section.strip()
            if section == "name" and value.strip():
                name = value.strip()
            continue
        item = line.strip()
        if not item.startswith("- "):
            continue
        item = item[2:].strip()
        if item.endswith(":"):
            Fail(f"{yml} has a nested entry ('{item}'), which this installer can't read. Use a flat list of packages.")
        if section == "channels" and item != "nodefaults":
            channels.append(item)
        elif section == "dependencies":
            specs.append(item)
    if not channels or not specs:
        Fail(f"{yml} lists no channels or no dependencies.")
    return name, channels, specs


def ChannelArgs(channels) -> list:
    """Only the env file's channels (--override-channels): see BuildEnv."""
    return ["--override-channels"] + [arg for channel in channels for arg in ("-c", channel)]


def BuildEnv(conda: Path, yml: Path) -> str:
    """
    Create the environment, or bring an existing one up to date (packages you
    added are kept). Only the env file's channels are used (--override-channels):
    conda's Terms of Service check reads the channels conda is configured with,
    not an env file's, so 'conda env create' would ask about Anaconda's channels
    even though nothing comes from them. With conda-forge alone there is
    nothing to accept. Returns the environment's name.
    """
    name, channels, specs = ReadYml(yml)
    verb = "install" if EnvExists(conda, name) else "create"
    print(f"{'Updating' if verb == 'install' else 'Creating'} the conda environment '{name}' from "
          f"{', '.join(channels)} (this can take several minutes; your terminal will be busy).")
    Run([conda, verb, "-n", name, *ChannelArgs(channels), "-y", *specs])
    print(f"✓ Conda environment '{name}' is ready.")
    return name


def PipInstall(package: str, editable: bool = False) -> list:
    """The pip arguments that install CompUtils. Its dependencies come from the env file (hence --no-deps)."""
    return ["python", "-m", "pip", "install", "--upgrade", "--force-reinstall", "--no-deps", "--no-cache-dir",
            *(["-e"] if editable else []), package]


def InstallCompUtils(conda: Path, name: str) -> None:
    """Install CompUtils from the branch."""
    print(f"Installing CompUtils from branch '{BRANCH}'.")
    Run(InEnv(conda, name, *PipInstall(PackageUrl())))


# Imports every CompUtils module and checks GoodVibes, writing no config: what proves an environment works
SELF_TEST = ["python", "-m", "computils", "--self-test"]


def CheckInstall(conda: Path, name: str) -> bool:
    """Whether 'cu' runs inside the environment, and every module imports. Neither touches the config, so setup is
    left to the first real run."""
    works = (Run(InEnv(conda, name, "cu", "--help"), check=False) == 0 and
             Run(InEnv(conda, name, *SELF_TEST), check=False) == 0)
    print("✓ 'cu' runs." if works else "✗ 'cu' did not run; see the output above.")
    return works


def AddAliases() -> None:
    """Add the 'con' alias for env activation.

    'cu' itself needs no alias: it is installed as a pip entry-point inside the
    conda env, so it's on PATH once the env is activated.
    """
    alias_line = f'alias con="conda activate {ENV_NAME}"'
    alias_file = HOME / ".alias"

    existing = alias_file.read_text() if alias_file.exists() else ""
    if alias_line in existing:
        print(f"✓ Alias 'con' already present in {alias_file}.")
    else:
        with open(str(alias_file), "a") as f:
            if existing and not existing.endswith("\n"):
                f.write("\n")
            f.write(alias_line + "\n")
        print(f"✓ Added alias 'con' to {alias_file}.")

    # Ensure ~/.bashrc sources ~/.alias
    bashrc = HOME / ".bashrc"
    source_line = "source ~/.alias"
    bashrc_content = bashrc.read_text() if bashrc.exists() else ""
    if source_line in bashrc_content:
        print(f"✓ {bashrc} already sources ~/.alias.")
    else:
        with open(str(bashrc), "a") as f:
            f.write(f"\n{source_line}\n")
        print(f"✓ Added '{source_line}' to {bashrc}.")


def RemoveLegacyCuAlias() -> None:
    """Remove any leftover 'alias cu=...' from a previous install.

    Old installers wrote `alias cu="python3 ~/bin/compUtils.py"` to
    ~/.alias. With the new packaging, 'cu' is a real command on PATH;
    keeping the alias would shadow the entry-point and break things.
    """
    alias_file = HOME / ".alias"
    if not alias_file.exists():
        return

    lines = alias_file.read_text().splitlines(keepends=True)
    new_lines = [ln for ln in lines if not ln.lstrip().startswith("alias cu=")]
    if len(new_lines) != len(lines):
        alias_file.write_text("".join(new_lines))
        print("✓ Removed legacy 'alias cu=...' from ~/.alias.")


# ─── Background mode (run by CompUtils: cu -env, the TUI's Update Environment) ─
#
# Never typed. CompUtils starts it detached on conda's own Python (never the
# environment's, which it may replace) and reads its progress from the state
# file. Nothing here asks anything. The environment in use is never touched
# until a replacement is built and checked:
#   install  conda create at the prefix, CompUtils, self-test (nothing to swap)
#   refresh  clone the environment to <prefix>-next (your own packages kept),
#            add what the env file asks for, CompUtils, self-test, swap
#   rebuild  create <prefix>-next fresh from the env file, CompUtils, self-test,
#            swap
# CompUtils is --package: the installed branch (its latest, so the code comes
# up to date with the environment; pip records the branch, which cu --update
# follows) or a local install's folder.
# The swap waits until nothing on this machine runs from the environment, then
# renames it aside (instant, reversible), clones -next into its place and
# checks that; a failure renames the old one back. While it swaps, a guard file
# (<prefix>.swapping) tells job scripts' Neva-Touch Scheduler hooks to wait.

DONE, FAILED = "Done", "Failed"
WAITING = "Waiting for CompUtils to Close"
WAIT_SECONDS = 30


class State:
    """The job's state file, read and rewritten whole (CompUtils reads it; its keys are kept)."""
    def __init__(self, path: Path):
        self.path = path

    def Read(self) -> dict:
        try:
            return json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}

    def Set(self, **changes) -> None:
        state = self.Read()
        state.update(changes)
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_text(json.dumps(state, indent=2))
        os.replace(str(temporary), str(self.path))

    def Phase(self, phase: str) -> None:
        print(f"\n=== {phase} ===", flush=True)
        self.Set(phase=phase)


def RemoveEnv(conda: Path, prefix: Path) -> None:
    """Remove an environment by its path (conda's records too); a folder conda doesn't know is deleted."""
    if not prefix.exists():
        return
    Run([conda, "remove", "--all", "-y", "-p", prefix], check=False)
    if prefix.exists():
        shutil.rmtree(str(prefix), ignore_errors=True)


def Executables():
    """(pid, executable path) of every process this user can see on this machine: /proc on Linux, the process list on
    Windows (ctypes; Windows lets a folder be renamed and deleted under a running program, so it must be asked)."""
    if os.path.isdir("/proc"):
        for pid in os.listdir("/proc"):
            if pid.isdigit():
                try:
                    yield int(pid), os.readlink("/proc/{}/exe".format(pid))
                except OSError:
                    continue
        return
    if os.name != "nt":
        return
    import ctypes
    from ctypes import wintypes
    psapi, kernel = ctypes.WinDLL("psapi"), ctypes.WinDLL("kernel32")
    pids, used = (wintypes.DWORD * 8192)(), wintypes.DWORD()
    if not psapi.EnumProcesses(pids, ctypes.sizeof(pids), ctypes.byref(used)):
        return
    for pid in pids[:used.value // ctypes.sizeof(wintypes.DWORD)]:
        handle = kernel.OpenProcess(0x1000, False, pid)          # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            continue
        buffer, size = ctypes.create_unicode_buffer(1024), wintypes.DWORD(1024)
        if kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            yield pid, buffer.value
        kernel.CloseHandle(handle)


def InUse(prefix: Path) -> bool:
    """Whether a process of this user on this machine runs from the environment (its executable is inside it)."""
    inside, me = os.path.normcase(os.path.realpath(str(prefix))) + os.sep, os.getpid()
    for pid, executable in Executables():
        if pid != me and os.path.normcase(os.path.realpath(executable)).startswith(inside):
            return True
    return False


class JobFailed(Exception):
    pass


def InstalledCommit(prefix: Path):
    """The commit pip recorded for CompUtils in the environment at prefix (its direct_url.json), or None (not
    installed from GitHub, or no record)."""
    for pattern in ("lib/python*/site-packages/compchem_utilities-*.dist-info/direct_url.json",
                    "Lib/site-packages/compchem_utilities-*.dist-info/direct_url.json"):
        for path in prefix.glob(pattern):
            try:
                return json.loads(path.read_text()).get("vcs_info", {}).get("commit_id") or None
            except (OSError, ValueError, AttributeError):
                return None
    return None


def Install(conda: Path, prefix: Path, args, state: State) -> None:
    """CompUtils (--package: the installed branch, so the code is brought up to date with the environment, or a local
    install's folder; nothing for a checkout on --pythonpath), then the self-test: every module imports and GoodVibes
    runs. The commit installed goes in the state, for CompUtils' report."""
    if args.package:
        if Run(InPrefix(conda, prefix, *PipInstall(args.package, args.editable)), check=False) != 0:
            raise JobFailed("pip couldn't install CompUtils")
    # Never the caller's PYTHONPATH: the test must read the new environment's code (or the checkout it was given)
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    if args.pythonpath:
        env["PYTHONPATH"] = args.pythonpath
    if Run(InPrefix(conda, prefix, *SELF_TEST), check=False, env=env) != 0:
        raise JobFailed("the self-test failed (see the log)")
    commit = InstalledCommit(prefix)
    if commit:
        state.Set(codeCommit=commit)


def SwapIn(conda: Path, prefix: Path, built: Path, args, state: State) -> None:
    """Replace the environment at prefix with the one built beside it."""
    aside = Path(str(prefix) + ".old")
    guard = Path(str(prefix) + ".swapping")
    RemoveEnv(conda, aside)
    while True:
        if InUse(prefix):
            if state.Read().get("phase") != WAITING:
                state.Phase(WAITING)
            time.sleep(WAIT_SECONDS)
            continue
        state.Phase("Swapping")
        guard.write_text(str(os.getpid()))
        try:
            os.rename(str(prefix), str(aside))
            break
        except OSError:
            # Something inside is held open after all: wait, as for a process
            guard.unlink()
            if state.Read().get("phase") != WAITING:
                state.Phase(WAITING)
            time.sleep(WAIT_SECONDS)
    try:
        if Run([conda, "create", "-y", "--offline", "-p", prefix, "--clone", built], check=False) != 0:
            raise JobFailed("conda couldn't copy the new environment into place")
        # The clone moved CompUtils' own files: install it again, so its command points at the final place
        Install(conda, prefix, args, state)
    except Exception:
        print("\nThe swap failed: putting the old environment back.", flush=True)
        shutil.rmtree(str(prefix), ignore_errors=True)
        try:
            os.rename(str(aside), str(prefix))
        except OSError as error:
            raise RollbackFailed("the swap failed, and the old environment couldn't be put back ({}): rename {} to {}"
                                 .format(error, aside, prefix))
        raise
    finally:
        if guard.exists():
            guard.unlink()
    shutil.rmtree(str(aside), ignore_errors=True)
    RemoveEnv(conda, built)


class RollbackFailed(JobFailed):
    pass


def Background(args) -> None:
    state = State(args.background)
    lock = args.background.with_suffix(".lock")
    state.Set(pid=os.getpid(), host=socket.gethostname())
    prefix = args.prefix
    built = prefix if args.mode == "install" else Path(str(prefix) + "-next")
    conda = FindConda(args.conda_dir)
    # Whether built is this job's to remove on a failure (never an environment that was there before it)
    ours = False
    try:
        if conda is None:
            raise JobFailed("conda wasn't found")
        name, channels, specs = ReadYml(args.yml)
        if args.mode != "install":
            # The code the environment has now, so CompUtils can report the update (Install records the new one)
            state.Set(codeBefore=InstalledCommit(prefix))
        state.Phase("Building")
        if args.mode == "install" and prefix.exists():
            raise JobFailed("{} already exists".format(prefix))
        if args.mode != "install" and not (prefix / "conda-meta").is_dir():
            raise JobFailed("there's no environment at {}".format(prefix))
        # A -next left by an earlier job that died
        RemoveEnv(conda, built)
        ours = True
        if args.mode == "refresh":
            # A copy of the environment as it is (the package cache, so no channel is asked), then what's missing
            if Run([conda, "create", "-y", "--offline", "-p", built, "--clone", prefix], check=False) != 0:
                raise JobFailed("conda couldn't copy the environment")
            if Run([conda, "install", "-y", "-p", built, *ChannelArgs(channels), *specs], check=False) != 0:
                raise JobFailed("conda couldn't install the packages")
        elif Run([conda, "create", "-y", "-p", built, *ChannelArgs(channels), *specs], check=False) != 0:
            raise JobFailed("conda couldn't create the environment")
        state.Phase("Installing CompUtils")
        Install(conda, built, args, state)
        if args.mode != "install":
            SwapIn(conda, prefix, built, args, state)
        state.Set(phase=DONE, finished=time.time())
        print("\n✓ Done.", flush=True)
    except BaseException as error:
        # Fail() (an unreadable env file) exits; its message is already in the log
        reason = (str(error) if isinstance(error, JobFailed) else "it stopped (see the log)"
                  if isinstance(error, SystemExit) else repr(error))
        print("\n✗ " + reason, flush=True)
        current = state.Read().get("phase", "")
        # The environment in use is as it was (a failed swap put it back): what was built goes. Unless the old one
        # couldn't be put back, when the built one is the only working copy
        if ours and not isinstance(error, RollbackFailed):
            RemoveEnv(conda, built)
        state.Set(phase=FAILED, failedDuring=current, error=reason, finished=time.time())
    finally:
        if lock.exists():
            lock.unlink()


# ─── Entry point ───────────────────────────────────────────────────────────────

def ParseArgs():
    parser = argparse.ArgumentParser(description=f"CompUtils installer (installs the {BRANCH} branch).")
    parser.add_argument("--conda-dir", type=Path,
                        help=f"Where to find conda, or install Miniconda if it isn't there (default: search, "
                             f"then install to {DEFAULT_CONDA_DIR}).")
    parser.add_argument("--yml", type=Path, help="Build the environment from this env file instead of the branch's.")
    # The background mode CompUtils runs (never typed; see Background)
    hidden = argparse.SUPPRESS
    parser.add_argument("--background", type=Path, metavar="STATE", help=hidden)
    parser.add_argument("--mode", choices=("install", "refresh", "rebuild"), help=hidden)
    parser.add_argument("--prefix", type=Path, help=hidden)
    parser.add_argument("--package", help=hidden)
    parser.add_argument("--editable", action="store_true", help=hidden)
    parser.add_argument("--pythonpath", help=hidden)
    args = parser.parse_args()
    if args.background is not None and (args.mode is None or args.prefix is None or args.yml is None):
        parser.error("--background needs --mode, --prefix and --yml")
    return args


def Main() -> None:
    args = ParseArgs()
    if args.background is not None:
        Background(args)
        return
    print("=" * 64)
    print(" CompUtils Installer")
    print("=" * 64)

    if args.yml is not None and not args.yml.is_file():
        Fail(f"No env file at {args.yml}.")
    print(f"\nInstalling from branch: {BRANCH}")

    installed = False
    try:
        conda = FindConda(args.conda_dir)
        if conda is None:
            conda = InstallMiniconda(args.conda_dir or DEFAULT_CONDA_DIR)
            installed = True
        else:
            print(f"✓ Using conda at {conda}.")
        with tempfile.TemporaryDirectory() as scratch:
            name = BuildEnv(conda, args.yml or FetchYml(Path(scratch)))
        InstallCompUtils(conda, name)
        works = CheckInstall(conda, name)
        RemoveLegacyCuAlias()
        AddAliases()
    except (subprocess.CalledProcessError, OSError) as error:  # OSError covers failed downloads
        Fail(f"A step failed: {error}\n  Run the installer again; it picks up where it stopped.")

    print("\n" + "=" * 64)
    print(" Installation complete!" if works else " Installed, but 'cu' didn't run (see above).")
    print("=" * 64)
    print("\nNext steps:")
    print("  1. Restart your shell, or run:   exec bash")
    if installed:
        print("       (needed once, so the new conda is set up in your shell)")
    print("  2. Activate the environment:     con")
    print(f"       (or equivalently:           conda activate {name})")
    print("  3. Run CompUtils:                cu")
    print("       (the first run walks you through setting it up, and asks for your lab's profile if it has one)")
    print()
    print("Notes:")
    print("  • The 'cu' command is installed inside the conda environment.")
    print("    It is only on PATH after the environment is activated.")
    print(f"  • Update later with 'cu --update' (it stays on branch '{BRANCH}'),")
    print("    or run this installer again.")


if __name__ == "__main__":
    Main()
