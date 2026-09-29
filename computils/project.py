"""Project-root recognition.

A project root is any directory containing a marker directory (Defaults.projectMarker, default `.computils/`).
CompUtils walks upward from the CWD to the nearest marker, the same way git finds `.git/`. Shareable reference files
(e.g. mixedbasis.txt) live inside the marker so every subdirectory of the project can find them.
"""
import functools, os, shutil
import tomllib as tom
from pathlib import Path

from .console  import ApplyTheme, console
from .defaults import DELETE, Defaults, loadToml, writeToml
from .prompts  import AskBool, AskChoice

# Known shareable files. -init (and the auto-prompt) offer to move these from the CWD into a new marker.
PROJECT_FILES = ["mixedbasis.txt", "orcablocks.txt", "spinstates.txt"]
# Project-level overrides of the global config. Only ever lives inside the marker
PROJECT_CONFIG = "project.toml"

# Batch guard: the create-project prompt is offered at most once per run
_hasPrompted = False


# CWD never changes during a run, so the upward walk only needs to happen once
@functools.cache
def FindProjectRoot() -> Path | None:
    here = Path.cwd().resolve()
    for candidate in (here, *here.parents):
        if (candidate / Defaults.projectMarker).is_dir():
            return candidate
    return None


def ProjectFilePath(root: Path, fileName: str) -> Path:
    return root / Defaults.projectMarker / fileName


# Shared by -init and the auto-prompt
def CreateProjectRoot(target: Path, offerMove: bool = True) -> Path | None:
    markerDir = target / Defaults.projectMarker
    try:
        markerDir.mkdir(exist_ok=True)
    except OSError as error:
        console.print(f"[error]Could not create {markerDir}: {error}[/error]")
        return None
    console.print(f"[good]Project root created: {target}[/good]")
    FindProjectRoot.cache_clear()
    ResolveProjectFile.cache_clear()
    EnsureProjectConfig(target)

    if offerMove:
        for fileName in PROJECT_FILES:
            localFile = Path(fileName)
            if localFile.is_file() and AskBool(f"Move ./{fileName} into {markerDir} to share it project-wide?", "y"):
                shutil.move(localFile, markerDir / fileName)
                console.print(f"[operation]Moved {fileName} -> {markerDir / fileName}[/operation]")
    return target


def EnsureProjectConfig(root: Path) -> None:
    """Generate project.toml from the global config if it is missing or unparseable. A valid file is never touched."""
    configPath = ProjectFilePath(root, PROJECT_CONFIG)
    if configPath.is_file():
        try:
            with open(configPath, "rb") as file:
                tom.load(file)
            console.print(f"[info]{configPath} already exists; left unchanged.[/info]")
            return
        except (tom.TOMLDecodeError, OSError, UnicodeDecodeError) as error:
            backupPath = configPath.with_name(PROJECT_CONFIG + ".bak")
            console.print(f"[warning]{configPath} could not be read ({error}). Backing it up to {backupPath.name} "
                          f"and regenerating it from the global config.[/warning]")
            try:
                shutil.move(configPath, backupPath)
            except OSError as moveError:
                console.print(f"[error]Could not back up {configPath}: {moveError}. Leaving it as-is.[/error]")
                return
    writeToml(configPath.parent, PROJECT_CONFIG, Defaults.BuildProjectContent())


def LoadProjectConfig() -> None:
    """Overlay the project's project.toml (if any) onto the global Defaults. Must run before Catalog.Load()."""
    root = FindProjectRoot()
    if root is None:
        return
    configPath = ProjectFilePath(root, PROJECT_CONFIG)
    if not configPath.is_file():
        return
    data = loadToml(configPath.parent, PROJECT_CONFIG)
    overridden = Defaults.ApplyProjectOverrides(data, configPath)
    # Only real deviations are reported, so a freshly generated snapshot is silent
    if overridden:
        console.print(f"[info]Project config ({configPath}) overrides: {', '.join(overridden)}[/info]")


def SaveProjectConfig(root: Path, updates: dict) -> bool:
    """Write project overrides into <marker>/project.toml in place (Defaults.DELETE removes one: follow global)."""
    configPath = ProjectFilePath(root, PROJECT_CONFIG)
    if configPath.is_file():
        return Defaults._PatchFile(configPath, updates, Defaults.ProjectComments())
    kept = {key: value for key, value in updates.items() if value is not DELETE}
    return writeToml(configPath.parent, PROJECT_CONFIG, Defaults.BuildProjectContent(kept))


def ReloadConfig(readGlobals: bool = True) -> None:
    """Re-apply the config mid-run, in Main()'s order: the global files (unless only the project changed, e.g. the TUI
    moved to another project), then project.toml, then the Catalog derived from them."""
    from .catalog import Catalog
    Defaults.ResetProjectOverrides()
    if readGlobals:
        Defaults.Load()
        ApplyTheme(Defaults.colorMode)
    LoadProjectConfig()
    Catalog.Load()


def ChangeDirectory(target: Path) -> bool:
    """Move the CWD mid-run (TUI navigation). Returns True if the nearest project root changed.

    The root/file lookups are re-walked on every move, since a subfolder can hold its own CWD copy of a project file.
    project.toml and the Catalog are only reloaded when the root itself changes.
    """
    previousRoot = FindProjectRoot()
    os.chdir(target)
    FindProjectRoot.cache_clear()
    ResolveProjectFile.cache_clear()
    if FindProjectRoot() == previousRoot:
        return False
    ReloadConfig(readGlobals=False)
    return True


def PromptCreateProject(missingFile: str) -> Path | None:
    global _hasPrompted
    if _hasPrompted:
        return None
    _hasPrompted = True

    here = Path.cwd().resolve()
    console.print(f"[warning]{missingFile} not found, and {here} is not inside a project.[/warning]")
    if not AskBool("Create a project root so reference files can be shared across subdirectories?", "y"):
        return None
    # Offer the CWD and its parents, excluding the filesystem root
    candidates = [here, *here.parents][:-1] or [here]
    choice = AskChoice("Choose the project root", [str(path) for path in candidates])
    if choice is None:
        return None
    root = CreateProjectRoot(candidates[choice], offerMove=False)
    if root is not None:
        console.print(f"[info]Place {missingFile} in {root / Defaults.projectMarker} and re-run.[/info]")
    return root


# Cached so a batch resolves (and prints) once, not once per molecule
@functools.cache
def ResolveProjectFile(fileName: str, required: bool = True) -> Path | None:
    # Optional files (required=False) never trigger the create-project prompt
    root = FindProjectRoot()
    # A CWD copy overrides the project copy
    localFile = Path(fileName)
    if localFile.is_file():
        if root is None:
            console.print(f"[info]Tip: run `cu -init` to share {fileName} across subdirectories.[/info]")
        return localFile

    if root is not None:
        sharedFile = ProjectFilePath(root, fileName)
        if sharedFile.is_file():
            console.print(f"[info]Using project file: {sharedFile}[/info]")
            return sharedFile
        return None

    if required:
        PromptCreateProject(fileName)
    return None
