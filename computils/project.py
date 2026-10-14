"""Project-root recognition.

A project root is any directory containing a marker directory (Defaults.projectMarker, default `.computils/`).
CompUtils walks upward from the CWD to the nearest marker, the same way git finds `.git/`. Shareable reference files
(jobRelatedKeylists, spinstates.txt) live inside the marker so every subdirectory of the project can find them.
"""
import functools, os, shutil
import tomllib as tom
from pathlib import Path

from .console  import ApplyTheme, console
from .defaults import DELETE, BackUpFile, Defaults, loadToml, writeToml
from .prompts  import AskBool

# Known shareable files. -init offers to move these from the CWD into a new marker (jobRelatedKeylists: keylists.py)
PROJECT_FILES = ["jobRelatedKeylists", "spinstates.txt"]
# Project-level overrides of the global config. Only ever lives inside the marker
PROJECT_CONFIG = "project.toml"

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
            console.print(f"[warning]{configPath} could not be read ({error}). Backing it up to {PROJECT_CONFIG}.bak "
                          f"and regenerating it from the global config.[/warning]")
            if not BackUpFile(configPath):
                return
    writeToml(configPath.parent, PROJECT_CONFIG, Defaults.BuildProjectContent())


def RefreshProjectConfig() -> None:
    """`cu -refresh project`: rewrite the nearest project.toml in the current layout, keeping the overrides that applied
    (LoadProjectConfig). Keys a project can't override, stale keys and invalid values are dropped (they follow global).
    A missing or unparseable file goes through EnsureProjectConfig, since there is nothing to keep. The project's old
    mixedbasis.txt / orcablocks.txt / optmethods.txt and its benchmarkMethods key are merged into its jobRelatedKeylists
    first (keylists.MergeLegacy), so dropping the key loses nothing: project.toml isn't rewritten while any of them
    couldn't be merged."""
    root = FindProjectRoot()
    if root is None:
        console.print("[info]Not inside a project, so there is no project.toml to refresh (`cu -init` creates one).[/info]")
        return
    from .keylists import Levels, MergeLegacy
    if not MergeLegacy(next(level for level in Levels() if level.label == "Project")):
        console.print(f"[error]{ProjectFilePath(root, PROJECT_CONFIG)} was not refreshed, so it keeps its "
                      f"benchmarkMethods.[/error]")
        return
    configPath = ProjectFilePath(root, PROJECT_CONFIG)
    try:
        current = configPath.read_text(encoding="utf-8")
        data = tom.loads(current)
    except (tom.TOMLDecodeError, OSError, UnicodeDecodeError):
        EnsureProjectConfig(root)
        return
    values = {key: Defaults._projectValues[key] for key in Defaults._PROJECT_KEYS if key in Defaults._projectValues}
    content = Defaults.BuildProjectContent(values)
    if current.replace("\r\n", "\n") == content:
        console.print(f"[info]{configPath} is already current.[/info]")
        return
    dropped = ", ".join(key for key in data if key not in values)
    if writeToml(configPath.parent, PROJECT_CONFIG, content):
        console.print(f"[good]Refreshed {configPath}{f' (dropped: {dropped})' if dropped else ''}.[/good]")


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
        # Project files may have been rewritten too (their loaders cache by path)
        from .keylists import ClearCaches
        from .spin import _LoadSpinOverrides
        ClearCaches()
        for loader in (_LoadSpinOverrides, ResolveProjectFile):
            loader.cache_clear()
    LoadProjectConfig()
    Catalog.Load()


def ChangeDirectory(target: Path) -> bool:
    """Move the CWD mid-run (TUI navigation). Returns True if the nearest project root changed.

    The root lookup is re-walked on every move; project.toml and the Catalog are only reloaded when the root itself
    changes (config is Global / Project only, so nothing else depends on the CWD).
    """
    previousRoot = FindProjectRoot()
    os.chdir(target)
    FindProjectRoot.cache_clear()
    ResolveProjectFile.cache_clear()
    if FindProjectRoot() == previousRoot:
        return False
    ReloadConfig(readGlobals=False)
    return True


# Cached so a batch resolves (and prints) once, not once per molecule. Only the project's copy is read (config is
# Global / Project only, never the CWD), and a missing file is never prompted for (the job files are jobRelatedKeylists,
# which has a global level: keylists.py)
@functools.cache
def ResolveProjectFile(fileName: str) -> Path | None:
    root = FindProjectRoot()
    if root is None:
        return None
    sharedFile = ProjectFilePath(root, fileName)
    if sharedFile.is_file():
        console.print(f"[info]Using project file: {sharedFile}[/info]")
        return sharedFile
    return None
