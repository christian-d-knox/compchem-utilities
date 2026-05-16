# !/usr/bin/env python3
"""
CompUtils Installer.

Installs Miniconda (if not already present) and creates the conda
environment defined in compUtils.yml. The environment file's embedded
pip section auto-installs the compchem-utilities package from GitHub,
so the 'cu' command is available after the env is activated.

Branch selection:
  By default, the installer pulls compUtils.yml from the 'main' branch.
  Use --branch BRANCHNAME to install from a different branch, or run
  the installer without --branch and answer the interactive prompt.

Run:
  python3 conda-installer.py                  # prompts for branch
  python3 conda-installer.py --branch main    # install from main
  python3 conda-installer.py --branch dev     # install from dev

Originally written by Christian Drew Knox for the Peng Liu Research Group.
This revision adds branch selection and fixes issues with the previous
version where `source` calls inside `os.system` did not propagate state
to subsequent commands.
"""
import argparse
import subprocess
import sys
from pathlib import Path

# ─── Configuration ─────────────────────────────────────────────────────────────
HOME = Path.home()
MINICONDA_DIR = HOME / "miniconda3"
MINICONDA_URL = (
    "https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh"
)
CONDA_SH = MINICONDA_DIR / "etc" / "profile.d" / "conda.sh"
REPO_OWNER = "christian-d-knox"
REPO_NAME = "compchem-utilities"
DEFAULT_BRANCH = "main"


def YmlUrlFor(branch: str) -> str:
    """Build the raw-content URL for compUtils.yml on a given branch."""
    return (
        f"https://raw.githubusercontent.com/{REPO_OWNER}/{REPO_NAME}/"
        f"{branch}/compUtils.yml"
    )


# ─── Helpers ───────────────────────────────────────────────────────────────────

def Shell(command: str, *, check: bool = True) -> int:
    """Run a shell command in a regular subshell. Prints before running."""
    print(f"\n+ {command}")
    return subprocess.run(command, shell=True, check=check).returncode


def CondaShell(*commands: str, check: bool = True) -> int:
    """
    Run one or more commands in a bash shell with conda sourced.

    Each call is a fresh shell, so we have to source conda.sh inside it.
    Multiple commands are joined with `&&` so they execute in sequence
    inside the same shell — necessary because state from one command
    (e.g. an activated env) must be visible to the next.
    """
    activate = f"source {CONDA_SH}"
    full_command = " && ".join([activate, *commands])
    print(f"\n+ bash -c '{full_command}'")
    return subprocess.run(
        ["bash", "-c", full_command],
        check=check,
    ).returncode


# ─── Installation steps ────────────────────────────────────────────────────────

def InstallMiniconda() -> None:
    """Install Miniconda if it isn't already present at MINICONDA_DIR."""
    if CONDA_SH.exists():
        print(f"✓ Miniconda already installed at {MINICONDA_DIR}.")
        return

    print("Installing Miniconda. Your terminal will be busy for a moment.")
    installer_sh = HOME / "miniconda_installer.sh"

    Shell(f"mkdir -p {MINICONDA_DIR}")
    Shell(f"wget -q --show-progress {MINICONDA_URL} -O {installer_sh}")
    Shell(f"bash {installer_sh} -b -u -p {MINICONDA_DIR}")
    Shell(f"rm {installer_sh}")

    # Register conda for future shells (modifies ~/.bashrc). This does NOT
    # help the current shell — that's what CondaShell() handles.
    CondaShell("conda init --all")
    print("✓ Miniconda installed.")


def LocateYml(branch: str) -> Path:
    """
    Return the path to compUtils.yml for the chosen branch.

    If a compUtils.yml sits alongside this installer script, it's used
    as-is (so a user can hand-edit it before running). Otherwise, the
    yml for the chosen branch is downloaded from GitHub.
    """
    local_yml = Path(__file__).parent / "compUtils.yml"
    if local_yml.exists():
        print(
            f"✓ Using compUtils.yml found alongside installer.\n"
            f"  (If you want the latest from branch '{branch}', delete this\n"
            f"  file and re-run the installer.)"
        )
        return local_yml

    url = YmlUrlFor(branch)
    print(f"Downloading compUtils.yml from branch '{branch}'...")
    Shell(f"wget -q {url} -O {local_yml}")
    return local_yml


def CreateEnv(branch: str) -> None:
    """Create the conda environment defined in the branch's compUtils.yml."""
    yml = LocateYml(branch)
    print(
        "Creating conda environment 'compUtils' (this can take several "
        "minutes — your terminal will be busy)."
    )
    CondaShell(f"conda env create -f {yml}")
    print("✓ Conda environment 'compUtils' created.")


def AddAliases() -> None:
    """Add the 'con' alias for env activation.

    The 'cu' alias from the previous installer is intentionally not
    recreated — 'cu' is now installed as a pip entry-point inside the
    conda env, so it's on PATH automatically once the env is activated.
    """
    alias_line = 'alias con="conda activate compUtils"'
    alias_file = HOME / ".alias"

    existing = alias_file.read_text() if alias_file.exists() else ""
    if alias_line in existing:
        print(f"✓ Alias 'con' already present in {alias_file}.")
    else:
        with open(alias_file, "a") as f:
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
        with open(bashrc, "a") as f:
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

def ChooseBranch() -> str:
    """
    Determine which branch to install from.

    1. If --branch was passed on the command line, use that.
    2. Otherwise, prompt the user interactively (default: main).
    """
    parser = argparse.ArgumentParser(
        description="CompUtils installer.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--branch",
        help=(
            f"Git branch to install from "
            f"(default: prompt; falls back to '{DEFAULT_BRANCH}')."
        ),
        default=None,
    )
    args = parser.parse_args()

    if args.branch:
        return args.branch

    try:
        response = input(
            f"\nInstall from which branch? [{DEFAULT_BRANCH}]: "
        ).strip()
    except EOFError:
        # Non-interactive (e.g. piped input); fall back to default.
        return DEFAULT_BRANCH
    return response if response else DEFAULT_BRANCH


def Main() -> None:
    print("=" * 64)
    print(" CompUtils Installer")
    print("=" * 64)

    branch = ChooseBranch()
    print(f"\nInstalling from branch: {branch}")

    try:
        InstallMiniconda()
        CreateEnv(branch)
        RemoveLegacyCuAlias()
        AddAliases()
    except subprocess.CalledProcessError as e:
        print(f"\n✗ A step failed: {e}", file=sys.stderr)
        print(
            "  You may need to re-run the installer or run the failing "
            "step manually.",
            file=sys.stderr,
        )
        sys.exit(1)

    print("\n" + "=" * 64)
    print(" Installation complete!")
    print("=" * 64)
    print("\nNext steps:")
    print("  1. Restart your shell, or run:   exec bash")
    print("  2. Activate the environment:     con")
    print("       (or equivalently:           conda activate compUtils)")
    print("  3. Test the install:             cu --help")
    print()
    print("Notes:")
    print("  • The 'cu' command is installed inside the conda environment.")
    print("    It is only on PATH after the environment is activated.")
    print("  • Your TOML configs and benchmarking.txt / programs.txt in")
    print("    ~/bin/ are untouched and remain in use.")


if __name__ == "__main__":
    Main()