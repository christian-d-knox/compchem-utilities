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

Written for Python 3.6+ and the standard library only: login nodes often have
an old system python3.

Originally written by Christian Drew Knox for the Peng Liu Research Group.
"""
import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
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
ENV_NAME = "compUtils"


def YmlUrl() -> str:
    """The raw-content URL of the branch's compUtils.yml."""
    return f"https://raw.githubusercontent.com/{REPO_OWNER}/{REPO_NAME}/{BRANCH}/compUtils.yml"


def PackageUrl() -> str:
    """What pip installs CompUtils from (cu --update in dispatch.py builds the same URL)."""
    return f"git+https://github.com/{REPO_OWNER}/{REPO_NAME}.git@{BRANCH}"


# ─── Helpers ───────────────────────────────────────────────────────────────────

def Run(args, check: bool = True) -> int:
    """Run a command (a list, no shell), printing it first."""
    print("\n+ " + " ".join(str(arg) for arg in args))
    return subprocess.run([str(arg) for arg in args], check=check).returncode


def InEnv(conda: Path, *args) -> list:
    """A command run inside the environment, its output shown as it happens."""
    return [conda, "run", "--no-capture-output", "-n", ENV_NAME, *args]


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
        conda = condaDir / "bin" / "conda"
        return conda if conda.exists() else None
    found = os.environ.get("CONDA_EXE") or shutil.which("conda")
    if found and Path(found).exists():
        return Path(found)
    for folder in KNOWN_CONDA_DIRS:
        if (folder / "bin" / "conda").exists():
            return folder / "bin" / "conda"
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


def EnvExists(conda: Path) -> bool:
    output = subprocess.check_output([str(conda), "env", "list", "--json"], universal_newlines=True)
    return any(Path(env).name == ENV_NAME for env in json.loads(output).get("envs", []))


def ReadYml(yml: Path):
    """The env file's channels (nodefaults left out) and package specs. Only the flat lists compUtils.yml uses are
    read (no yaml module in the standard library); a nested entry such as '- pip:' stops the install."""
    channels, specs, section = [], [], None
    for raw in yml.read_text().splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if not line[0].isspace():
            section = line.split(":", 1)[0].strip()
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
    return channels, specs


def BuildEnv(conda: Path, yml: Path) -> None:
    """
    Create the environment, or bring an existing one up to date (packages you
    added are kept). Only the env file's channels are used (--override-channels):
    conda's Terms of Service check reads the channels conda is configured with,
    not an env file's, so 'conda env create' would ask about Anaconda's channels
    even though nothing comes from them. With conda-forge alone there is
    nothing to accept.
    """
    channels, specs = ReadYml(yml)
    verb = "install" if EnvExists(conda) else "create"
    print(f"{'Updating' if verb == 'install' else 'Creating'} the conda environment '{ENV_NAME}' from "
          f"{', '.join(channels)} (this can take several minutes; your terminal will be busy).")
    channelArgs = [arg for channel in channels for arg in ("-c", channel)]
    Run([conda, verb, "-n", ENV_NAME, "--override-channels", *channelArgs, "-y", *specs])
    print(f"✓ Conda environment '{ENV_NAME}' is ready.")


def InstallCompUtils(conda: Path) -> None:
    """Install CompUtils from the branch. Its dependencies come from compUtils.yml (hence --no-deps)."""
    print(f"Installing CompUtils from branch '{BRANCH}'.")
    Run(InEnv(conda, "python", "-m", "pip", "install", "--upgrade", "--force-reinstall",
              "--no-deps", "--no-cache-dir", PackageUrl()))


def CheckInstall(conda: Path) -> bool:
    """Whether 'cu' runs inside the environment. 'cu --help' prints its help without touching the config, so setup
    is left to the first real run."""
    works = Run(InEnv(conda, "cu", "--help"), check=False) == 0
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


# ─── Entry point ───────────────────────────────────────────────────────────────

def ParseArgs():
    parser = argparse.ArgumentParser(description=f"CompUtils installer (installs the {BRANCH} branch).")
    parser.add_argument("--conda-dir", type=Path,
                        help=f"Where to find conda, or install Miniconda if it isn't there (default: search, "
                             f"then install to {DEFAULT_CONDA_DIR}).")
    parser.add_argument("--yml", type=Path, help="Build the environment from this env file instead of the branch's.")
    return parser.parse_args()


def Main() -> None:
    args = ParseArgs()
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
            BuildEnv(conda, args.yml or FetchYml(Path(scratch)))
        InstallCompUtils(conda)
        works = CheckInstall(conda)
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
    print(f"       (or equivalently:           conda activate {ENV_NAME})")
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
