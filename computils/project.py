"""Project-root recognition.

A project root is any directory containing a marker directory (Defaults.projectMarker, default `.computils/`).
CompUtils walks upward from the CWD to the nearest marker, the same way git finds `.git/`. Shareable reference files
(e.g. mixedbasis.txt) live inside the marker so every subdirectory of the project can find them.
"""
import functools, shutil
from pathlib import Path

from .console  import console
from .defaults import Defaults
from .prompts  import AskBool, AskChoice

# Known shareable files. -init (and the auto-prompt) offer to move these from the CWD into a new marker.
PROJECT_FILES = ["mixedbasis.txt", "orcablocks.txt", "spinstates.txt"]

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

    if offerMove:
        for fileName in PROJECT_FILES:
            localFile = Path(fileName)
            if localFile.is_file() and AskBool(f"Move ./{fileName} into {markerDir} to share it project-wide?", "y"):
                shutil.move(localFile, markerDir / fileName)
                console.print(f"[operation]Moved {fileName} -> {markerDir / fileName}[/operation]")
    return target


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
