#!/usr/bin/env python3
"""
CompUtils Installer.

The one file to download, for Linux (a cluster's login node, a workstation) and
Windows alike. Run it with any Python 3:
  python3 conda-installer.py          (Linux)
  python conda-installer.py           (Windows; or py conda-installer.py)

This is the dev branch's installer: it installs CompUtils from the dev branch.
It finds conda (or installs Miniconda to ~/miniconda3, set to use conda-forge
only), builds or updates the environment from the branch's env file for this
system (compUtils.yml on Linux, compUtilsWindows.yml on Windows), and installs
CompUtils itself. Then, on Linux, it adds the 'con' alias that activates the
environment; on Windows, it writes a 'cu' command (cu.cmd, on your user PATH)
that runs CompUtils from its environment with no activating at all. It asks
nothing, and every step is safe to repeat, so a failed run is fixed by running
it again.

A Windows computer without any Python: install Miniconda first (silently, to
%USERPROFILE%\\miniconda3), then run this file with Miniconda's own python and
--conda-forge-only, so that Miniconda is set to conda-forge as if this
installer had installed it.

The first 'cu' run sets CompUtils up, and asks for your lab's profile (a file
your lab shares privately: its clusters and shared settings) if it has one.

CompUtils' environment is built from conda-forge only (--override-channels,
whatever channels your conda is set up with), so it never needs Anaconda's
package channels or their Terms of Service. This installer never accepts any
terms on your behalf.

Power users:
  --conda-dir PATH   find or install conda here instead of the usual places
  --yml PATH         build the environment from a hand-edited env file
  --conda-forge-only set the conda found to use conda-forge only (as for a
                     Miniconda this installer installs itself)

CompUtils also runs this file itself (cu -env, the TUI's Update Environment) in
a hidden background mode, to refresh, rebuild or install an environment: it
builds '<name>-next' alongside, checks it, and swaps it in once nothing runs
from the old one. That mode is never typed; see Background().

Written for Python 3.6+ and the standard library only: login nodes often have
an old system python3.

Originally written by Christian Drew Knox for the Peng Liu Research Group.
"""
import argparse
import io
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
# This system, as CompUtils' env files name it in their '# @platform' lines (computils/environment.py: THIS_PLATFORM)
WINDOWS = platform.system() == "Windows"
THIS_PLATFORM = "windows" if WINDOWS else "linux"
DEFAULT_CONDA_DIR = HOME / "miniconda3"
KNOWN_CONDA_DIRS = [DEFAULT_CONDA_DIR, HOME / "anaconda3", HOME / "miniforge3"]
MINICONDA_URL = "https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-{arch}.sh"
MINICONDA_ARCHES = {"x86_64": "x86_64", "aarch64": "aarch64", "arm64": "aarch64"}
MINICONDA_WINDOWS_URL = "https://repo.anaconda.com/miniconda/Miniconda3-latest-Windows-x86_64.exe"
# The env file for this system (each names its own environment: compUtils, compUtilsWindows)
YML_NAME = "compUtilsWindows.yml" if WINDOWS else "compUtils.yml"
# Windows: where the 'cu' command (cu.cmd) goes, a per-user programs folder added to the user's PATH
LAUNCHER_DIR = Path(os.environ.get("LOCALAPPDATA") or str(HOME / "AppData" / "Local")) / "Programs" / "CompUtils"
REPO_OWNER = "christian-d-knox"
REPO_NAME = "compchem-utilities"
# This is the dev branch's installer, so it installs dev (main's installer installs main)
BRANCH = "dev"
# The environment's name when the env file doesn't give one
ENV_NAME = "compUtils"
# Where conda's executable sits inside a conda installation (Linux, then Windows)
CONDA_PLACES = ["bin/conda", "Scripts/conda.exe", "condabin/conda.bat"]


def YmlUrl() -> str:
    """The raw-content URL of the branch's env file for this system."""
    return f"https://raw.githubusercontent.com/{REPO_OWNER}/{REPO_NAME}/{BRANCH}/{YML_NAME}"


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


def SafeConsole() -> None:
    """Never let a character the console can't show stop the install: a Windows console (or a log file there) is often
    cp1252, which has no ✓ or ✗. Unknown characters print as '?' instead (Python 3.6 has no reconfigure())."""
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name)
        buffer = getattr(stream, "buffer", None)
        if buffer is None:
            continue
        encoding = stream.encoding or "utf-8"
        try:
            "✓✗".encode(encoding)
            continue
        except (UnicodeEncodeError, LookupError):
            pass
        setattr(sys, name, io.TextIOWrapper(buffer, encoding=encoding, errors="replace", line_buffering=True))


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
    """Install Miniconda into condaDir (on Linux, set up for bash too); return its conda."""
    if WINDOWS:
        return InstallMinicondaWindows(condaDir)
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


def InstallMinicondaWindows(condaDir: Path) -> Path:
    """Install Miniconda for this user only, silently, into condaDir; return its conda. It isn't put on PATH or
    registered as the system's Python: CompUtils' 'cu' command (WriteWindowsLauncher) needs neither."""
    if platform.machine().lower() not in ("amd64", "x86_64"):
        Fail(f"No conda was found, and Miniconda for Windows is 64-bit x86 only (this is {platform.machine()}). "
             f"Install conda yourself, then run the installer again.")
    if " " in str(condaDir):
        Fail(f"Miniconda can't be installed into a folder with a space in its path ({condaDir}). Give another "
             f"folder with --conda-dir.")
    print(f"Installing Miniconda to {condaDir}. This takes a few minutes, with no window.")
    with tempfile.TemporaryDirectory() as scratch:
        installer = Path(scratch) / "miniconda.exe"
        Download(MINICONDA_WINDOWS_URL, installer)
        # The silent installer's own flags; /D must come last and unquoted
        Run([installer, "/InstallationType=JustMe", "/RegisterPython=0", "/AddToPath=0", "/S", f"/D={condaDir}"])
    conda = CondaIn(condaDir)
    if conda is None:
        Fail(f"Miniconda's installer finished, but there's no conda in {condaDir}.")
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
    """Download the branch's env file for this system into scratch (fresh every run, never kept)."""
    target = scratch / YML_NAME
    print(f"Downloading {YML_NAME} from branch '{BRANCH}'...")
    try:
        Download(YmlUrl(), target)
    except urllib.error.HTTPError as error:
        Fail(f"Couldn't download {YML_NAME} from branch '{BRANCH}' ({error.code} {error.reason}).")
    return target


def EnvExists(conda: Path, name: str) -> bool:
    return EnvPrefix(conda, name) is not None


def EnvPrefix(conda: Path, name: str):
    """The folder of the environment called name, or None."""
    output = subprocess.check_output([str(conda), "env", "list", "--json"], universal_newlines=True)
    for env in json.loads(output).get("envs", []):
        if Path(env).name == name:
            return Path(env)
    return None


def ReadYml(yml: Path):
    """The env file's environment name, channels (nodefaults left out) and package specs. Only the flat lists
    compUtils.yml uses are read (no yaml module in the standard library); a nested entry such as '- pip:' stops the
    install. A '# @platform linux|windows' line limits the file to that system: on another, the install stops. CompUtils
    keeps a twin (computils/environment.py's ReadYml)."""
    name, channels, specs, section = ENV_NAME, [], [], None
    for raw in yml.read_text().splitlines():
        comment = raw.strip()[1:].strip() if raw.strip().startswith("#") else ""
        if comment.startswith("@platform") and comment[len("@platform"):].split():
            target = comment[len("@platform"):].split()[0].lower()
            if target != THIS_PLATFORM:
                Fail(f"{yml} is for {target}, and this is {platform.system()}. Use the env file for this system "
                     f"({YML_NAME}).")
            continue
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


def CheckInstall(conda: Path, name: str, launcher=None) -> bool:
    """Whether 'cu' runs inside the environment, and every module imports (and, on Windows, whether the 'cu' command
    runs too). None of it touches the config, so setup is left to the first real run."""
    works = (Run(InEnv(conda, name, "cu", "--help"), check=False) == 0 and
             Run(InEnv(conda, name, *SELF_TEST), check=False) == 0 and
             (launcher is None or Run([launcher, "--help"], check=False) == 0))
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


# ─── Windows: the 'cu' command ─────────────────────────────────────────────────

def LauncherText(conda: Path, name: str, prefix: Path) -> str:
    """cu.cmd: CompUtils run from its environment without activating it. It sets what activation would (the
    environment's folders first on PATH, so its DLLs load; the CONDA_* variables cu -env reads to find conda) and runs
    'python -m computils', never cu.exe: Windows can't replace a running .exe, which would break cu --update."""
    base = conda.parent.parent          # conda is <base>/Scripts/conda.exe or <base>/condabin/conda.bat
    folders = [prefix, prefix / "Library" / "mingw-w64" / "bin", prefix / "Library" / "usr" / "bin",
               prefix / "Library" / "bin", prefix / "Scripts", prefix / "bin"]
    return "\n".join([
        "@echo off",
        "rem CompUtils' cu command, written by conda-installer.py (running it again rewrites this file).",
        "rem It runs CompUtils from its conda environment, so no 'conda activate' is needed.",
        "setlocal",
        f'set "CONDA_PREFIX={prefix}"',
        f'set "CONDA_DEFAULT_ENV={name}"',
        f'set "CONDA_EXE={conda}"',
        f'set "CONDA_PYTHON_EXE={base / "python.exe"}"',
        'set "PATH={};%PATH%"'.format(";".join(str(folder) for folder in folders)),
        f'"{prefix / "python.exe"}" -m computils %*',
        "exit /b %ERRORLEVEL%",
        ""])


def AddToUserPath(folder: Path) -> bool:
    """Add folder to this user's PATH (the registry, as Windows' own settings do; never setx, which cuts PATH at 1024
    characters), and tell open programs so new windows see it. False if it was already there."""
    import ctypes
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_READ | winreg.KEY_WRITE) as key:
        try:
            value, kind = winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            value, kind = "", winreg.REG_EXPAND_SZ
        entries = [entry for entry in value.split(";") if entry.strip()]
        wanted = os.path.normcase(str(folder).rstrip("\\"))
        if any(os.path.normcase(os.path.expandvars(entry).rstrip("\\")) == wanted for entry in entries):
            return False
        if kind not in (winreg.REG_SZ, winreg.REG_EXPAND_SZ):
            kind = winreg.REG_EXPAND_SZ
        winreg.SetValueEx(key, "Path", 0, kind, ";".join(entries + [str(folder)]))
    # WM_SETTINGCHANGE to every top-level window (Explorer re-reads the environment for the windows it opens next)
    result = ctypes.c_ulong()
    ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, "Environment", 0x0002, 5000, ctypes.byref(result))
    return True


def WriteWindowsLauncher(conda: Path, name: str) -> Path:
    """Write cu.cmd for the environment called name into LAUNCHER_DIR and put that folder on this user's PATH."""
    prefix = EnvPrefix(conda, name)
    if prefix is None:
        Fail(f"The environment '{name}' wasn't found, so the 'cu' command couldn't be written.")
    LAUNCHER_DIR.mkdir(parents=True, exist_ok=True)
    launcher = LAUNCHER_DIR / "cu.cmd"
    # cmd reads a batch file in the console's (OEM) code page, and wants CRLF line ends
    with open(str(launcher), "w", encoding="oem", newline="\r\n") as file:
        file.write(LauncherText(conda, name, prefix))
    print(f"✓ Wrote the 'cu' command: {launcher}")
    if AddToUserPath(LAUNCHER_DIR):
        print(f"✓ Added {LAUNCHER_DIR} to your user PATH (new PowerShell and cmd windows will have 'cu').")
    else:
        print(f"✓ {LAUNCHER_DIR} is already on your user PATH.")
    return launcher


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
    parser.add_argument("--conda-forge-only", action="store_true",
                        help="Set the conda found to use conda-forge only, as a Miniconda this installer installs is "
                             "(for a Miniconda installed just before running this).")
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
    SafeConsole()
    args = ParseArgs()
    if args.background is not None:
        Background(args)
        return
    print("=" * 64)
    print(" CompUtils Installer")
    print("=" * 64)

    if platform.system() not in ("Linux", "Windows"):
        Fail(f"This installer supports Linux and Windows (this is {platform.system()}). Build the environment "
             f"yourself from compUtils.yml, then pip-install CompUtils into it.")
    if args.yml is not None and not args.yml.is_file():
        Fail(f"No env file at {args.yml}.")
    print(f"\nInstalling from branch: {BRANCH} (on {platform.system()})")

    installed = False
    launcher = None
    try:
        conda = FindConda(args.conda_dir)
        if conda is None:
            conda = InstallMiniconda(args.conda_dir or DEFAULT_CONDA_DIR)
            installed = True
        else:
            print(f"✓ Using conda at {conda}.")
            if args.conda_forge_only:
                UseCondaForgeOnly(conda)
        with tempfile.TemporaryDirectory() as scratch:
            name = BuildEnv(conda, args.yml or FetchYml(Path(scratch)))
        InstallCompUtils(conda, name)
        if WINDOWS:
            launcher = WriteWindowsLauncher(conda, name)
        works = CheckInstall(conda, name, launcher)
        if not WINDOWS:
            RemoveLegacyCuAlias()
            AddAliases()
    except (subprocess.CalledProcessError, OSError) as error:  # OSError covers failed downloads
        Fail(f"A step failed: {error}\n  Run the installer again; it picks up where it stopped.")

    print("\n" + "=" * 64)
    print(" Installation complete!" if works else " Installed, but 'cu' didn't run (see above).")
    print("=" * 64)
    if WINDOWS:
        print("\nNext steps:")
        print("  1. Open a new PowerShell or cmd window (open ones don't see the new PATH yet).")
        print("  2. Run CompUtils:                cu")
        print("       (the first run walks you through setting it up, and asks for your lab's profile if it has one)")
        print(f"       In a window opened before this install, run it as: {launcher}")
        print()
        print("Notes:")
        print(f"  • 'cu' is {launcher}: it runs CompUtils from the '{name}' environment, no activating needed.")
        print(f"  • Update later with 'cu --update' (it stays on branch '{BRANCH}'),")
        print("    or run this installer again.")
        return
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
