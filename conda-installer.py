#!/usr/bin/env python3
"""
CompUtils Installer.

The one file to download. Run it on the cluster's login node:
  python3 conda-installer.py                  # asks which branch (default: main)
  python3 conda-installer.py --branch dev     # no questions
  python3 conda-installer.py --yes            # no questions, main
  python3 conda-installer.py --profile FILE   # also set up your lab's profile

It finds conda (or installs Miniconda to ~/miniconda3), builds or updates the
'compUtils' environment from the branch's compUtils.yml, installs CompUtils
itself from that branch, and adds the 'con' alias to activate it. Every step
is safe to repeat, so a failed run is fixed by running it again.

A lab profile (a file your lab shares privately: its clusters and shared
settings) is copied to ~/bin/profile.toml and applied by the first 'cu' run.

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
# Anaconda's channels, whose Terms of Service recent Miniconda asks to accept
TOS_CHANNELS = ["https://repo.anaconda.com/pkgs/main", "https://repo.anaconda.com/pkgs/r"]
REPO_OWNER = "christian-d-knox"
REPO_NAME = "compchem-utilities"
DEFAULT_BRANCH = "main"
ENV_NAME = "compUtils"
# Where CompUtils looks for the lab profile copy (its default binDirectory), and the header lines it reads (profile.py)
PROFILE_COPY = HOME / "bin" / "profile.toml"
PROFILE_NOTE = "#@ A copy of your lab profile, kept by CompUtils. Edit the original, then run: cu -profile"


def YmlUrlFor(branch: str) -> str:
    """The raw-content URL of compUtils.yml on a branch."""
    return f"https://raw.githubusercontent.com/{REPO_OWNER}/{REPO_NAME}/{branch}/compUtils.yml"


def PackageUrlFor(branch: str) -> str:
    """What pip installs CompUtils from (cu --update in dispatch.py builds the same URL)."""
    return f"git+https://github.com/{REPO_OWNER}/{REPO_NAME}.git@{branch}"


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
    print("✓ Miniconda installed.")
    return conda


def AcceptTerms(conda: Path) -> None:
    """Accept the Terms of Service of Anaconda's channels, which recent conda asks for before any install."""
    print("Accepting the Terms of Service of Anaconda's package channels.")
    channels = [arg for channel in TOS_CHANNELS for arg in ("--channel", channel)]
    # An older conda has no 'tos' command, and doesn't need one
    Run([conda, "tos", "accept", "--override-channels", *channels], check=False)


def ListBranches() -> list:
    """The repository's branch names, or [] if GitHub can't be reached."""
    url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/branches?per_page=100"
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            return [branch["name"] for branch in json.loads(response.read().decode())]
    except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError):
        return []


def ChooseBranch(given, assumeYes: bool) -> str:
    """The branch to install from: --branch if given, else asked (default: main). Checked against GitHub."""
    branches = ListBranches()
    if not branches:
        print("(Couldn't list the branches from GitHub, so the branch can't be checked.)")
    if given:
        if branches and given not in branches:
            Fail(f"There is no branch '{given}'. Branches: {', '.join(branches)}")
        return given
    if assumeYes:
        return DEFAULT_BRANCH
    if branches:
        print(f"\nBranches: {', '.join(branches)}")
    while True:
        try:
            answer = input(f"Install from which branch? [{DEFAULT_BRANCH}]: ").strip()
        except EOFError:
            return DEFAULT_BRANCH  # no terminal to answer from (piped input)
        branch = answer or DEFAULT_BRANCH
        if not branches or branch in branches:
            return branch
        print(f"There is no branch '{branch}'.")


def FetchYml(branch: str, scratch: Path) -> Path:
    """Download the branch's compUtils.yml into scratch (fresh every run, never kept)."""
    target = scratch / "compUtils.yml"
    print(f"Downloading compUtils.yml from branch '{branch}'...")
    try:
        Download(YmlUrlFor(branch), target)
    except urllib.error.HTTPError as error:
        Fail(f"Couldn't download compUtils.yml from branch '{branch}' ({error.code} {error.reason}).")
    return target


def EnvExists(conda: Path) -> bool:
    output = subprocess.check_output([str(conda), "env", "list", "--json"], universal_newlines=True)
    return any(Path(env).name == ENV_NAME for env in json.loads(output).get("envs", []))


def BuildEnv(conda: Path, yml: Path) -> None:
    """Create the environment, or bring an existing one up to date (packages you added are kept)."""
    verb = "update" if EnvExists(conda) else "create"
    print(f"{'Updating' if verb == 'update' else 'Creating'} the conda environment '{ENV_NAME}' "
          f"(this can take several minutes; your terminal will be busy).")
    Run([conda, "env", verb, "-n", ENV_NAME, "-f", yml])
    print(f"✓ Conda environment '{ENV_NAME}' is ready.")


def InstallCompUtils(conda: Path, branch: str) -> None:
    """Install CompUtils from the branch. Its dependencies come from compUtils.yml (hence --no-deps)."""
    print(f"Installing CompUtils from branch '{branch}'.")
    Run(InEnv(conda, "python", "-m", "pip", "install", "--upgrade", "--force-reinstall",
              "--no-deps", "--no-cache-dir", PackageUrlFor(branch)))


def CheckInstall(conda: Path) -> bool:
    """Whether 'cu' runs inside the environment."""
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


def AskProfile(given, assumeYes: bool):
    """The lab profile file: --profile if given, else asked (Enter for none). None without one."""
    if given is None and not assumeYes:
        try:
            answer = input("Lab profile file, if your lab gave you one [none]: ").strip()
        except EOFError:
            answer = ""
        given = Path(answer) if answer else None
    if given is None:
        return None
    given = given.expanduser().resolve()
    if not given.is_file():
        Fail(f"No lab profile file at {given}.")
    return given


def CopyProfile(profile: Path) -> None:
    """Copy the lab profile where CompUtils finds it (readable by you only: it can hold the bot token).
    The first 'cu' run checks and applies it, and later runs offer the lab's changes to it."""
    header = "\n".join([PROFILE_NOTE, "#@source " + str(profile), "#@pending"]) + "\n"
    text = profile.read_text(encoding="utf-8")
    PROFILE_COPY.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(str(PROFILE_COPY), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as out:
        out.write(header + text)
    os.chmod(str(PROFILE_COPY), 0o600)
    print(f"✓ Lab profile copied to {PROFILE_COPY}.")


# ─── Entry point ───────────────────────────────────────────────────────────────

def ParseArgs():
    parser = argparse.ArgumentParser(description="CompUtils installer.")
    parser.add_argument("--branch", help=f"Git branch to install from (default: ask; '{DEFAULT_BRANCH}' with --yes).")
    parser.add_argument("--yes", action="store_true", help="Ask nothing; use the defaults.")
    parser.add_argument("--conda-dir", type=Path,
                        help=f"Where to find conda, or install Miniconda if it isn't there (default: search, "
                             f"then install to {DEFAULT_CONDA_DIR}).")
    parser.add_argument("--yml", type=Path, help="Build the environment from this env file instead of the branch's.")
    parser.add_argument("--profile", type=Path, help="Your lab's profile file (default: ask; none with --yes).")
    return parser.parse_args()


def Main() -> None:
    args = ParseArgs()
    print("=" * 64)
    print(" CompUtils Installer")
    print("=" * 64)

    if args.yml is not None and not args.yml.is_file():
        Fail(f"No env file at {args.yml}.")
    branch = ChooseBranch(args.branch, args.yes)
    print(f"\nInstalling from branch: {branch}")
    profile = AskProfile(args.profile, args.yes)

    installed = False
    try:
        conda = FindConda(args.conda_dir)
        if conda is None:
            conda = InstallMiniconda(args.conda_dir or DEFAULT_CONDA_DIR)
            installed = True
        else:
            print(f"✓ Using conda at {conda}.")
        AcceptTerms(conda)
        with tempfile.TemporaryDirectory() as scratch:
            BuildEnv(conda, args.yml or FetchYml(branch, Path(scratch)))
        InstallCompUtils(conda, branch)
        works = CheckInstall(conda)
        RemoveLegacyCuAlias()
        AddAliases()
        if profile is not None:
            CopyProfile(profile)
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
    print("       (the first run walks you through setting it up" + (", using your lab profile)" if profile else ")"))
    print()
    print("Notes:")
    print("  • The 'cu' command is installed inside the conda environment.")
    print("    It is only on PATH after the environment is activated.")
    print(f"  • Update later with 'cu --update' (it stays on branch '{branch}'),")
    print("    or run this installer again.")


if __name__ == "__main__":
    Main()
