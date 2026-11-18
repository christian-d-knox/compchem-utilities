"""CompUtils' conda environments: checking, refreshing, rebuilding and installing them (`cu -env`, the TUI's Update
Environment). The one place for it.

A variant is one compUtils*.yml at the repository's root (compUtils.yml: Standard, compUtilsDebug.yml: Debug, …), read
from the installed branch on GitHub (a local install: its checkout). Checking is instant and offline: the environment's
packages are read from its conda-meta folder and compared with the yml's specs.

The work is never done here. An environment can't be replaced by a process running from it, so cu hands it to
conda-installer.py (the one environment builder, from the same commit as this code), run detached on conda's own
Python in its background mode: it builds `<name>-next` alongside, installs the CompUtils already installed into it,
checks it with `python -m computils --self-test`, waits until nothing on this machine runs from the old environment,
then swaps the new one in (the old one is renamed aside first, so a failed swap is rolled back). Its progress is a
state file in <binDirectory>/environments/, which cu reads; a finished job is reported once (Reports).

Update CompUtils (code) and this stay separate actions; CodeUpdateProblem keeps them safe together."""
import importlib.metadata
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import regex

from .defaults import Defaults
from . import update

STANDARD = "compUtils"
YML_PREFIX, YML_SUFFIX = "compUtils", ".yml"
INSTALLER = "conda-installer.py"
STATE_FOLDER = "environments"
# The installer's phases (conda-installer.py writes them); the last two end a job
DONE, FAILED = "Done", "Failed"
ENDED = (DONE, FAILED)
WAITING = "Waiting for CompUtils to Close"
MODES = ("refresh", "rebuild", "install")
# What each mode did, for the reports
DONE_VERBS = {"refresh": "refreshed", "rebuild": "rebuilt", "install": "installed"}
DOING = {"refresh": "Refreshing", "rebuild": "Rebuilding", "install": "Installing"}
THIS_PLATFORM = "windows" if sys.platform == "win32" else "linux"


# ─── Variants (the yml files) ─────────────────────────────────────────

@dataclass
class Variant:
    file:     str                       # compUtilsDebug.yml
    name:     str                       # the conda environment's name (the yml's name:)
    channels: list[str]
    specs:    list[str]
    platform: str = ""                  # '' any; 'linux' / 'windows' from a '# @platform' line
    text:     str = ""                  # the yml as read (written for the installer)

    @property
    def label(self) -> str:
        """Standard for compUtils.yml, else the file's own suffix (compUtilsDebug.yml: Debug)."""
        stem = self.file[:-len(YML_SUFFIX)]
        return stem[len(YML_PREFIX):] or "Standard"

    def Fits(self) -> bool:
        return not self.platform or self.platform == THIS_PLATFORM


def ReadYml(text: str, file: str) -> Variant | str:
    """A variant from an env file's text, or why it can't be read. The twin of conda-installer.py's ReadYml (which
    can't be imported: it is a standalone Python 3.6 script): flat lists only, nodefaults left out."""
    name, channels, specs, platform, section = "", [], [], "", None
    for raw in text.splitlines():
        directive = regex.match(r"\s*#\s*@platform\s+(\w+)", raw)
        if directive:
            platform = directive.group(1).lower()
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if not line[0].isspace():
            key, _, value = line.partition(":")
            section = key.strip()
            if section == "name":
                name = value.strip()
            continue
        item = line.strip()
        if not item.startswith("- "):
            continue
        item = item[2:].strip()
        if item.endswith(":"):
            return f"{file} has a nested entry ('{item}')"
        if section == "channels" and item != "nodefaults":
            channels.append(item)
        elif section == "dependencies":
            specs.append(item)
    if not name or not channels or not specs:
        return f"{file} has no name, channels or dependencies"
    return Variant(file, name, channels, specs, platform, text)


def IsVariantFile(name: str) -> bool:
    return name.startswith(YML_PREFIX) and name.endswith(YML_SUFFIX)


@dataclass
class Variants:
    """The variants a source offers (this platform's), where they came from ('branch dev', 'your checkout'), and
    the files that couldn't be read."""
    found:    list[Variant]
    where:    str
    problems: list[str] = field(default_factory=list)


def ReadVariants(source: update.Source, ref: str | None = None) -> Variants | str:
    """Every compUtils*.yml for this platform: from the checkout for a local install, else from GitHub at ref (default:
    the installed branch's latest). A str says why none could be read."""
    texts, problems = {}, []
    if source.localPath:
        folder = Path(source.localPath)
        if not folder.is_dir():
            return f"the local install's folder ({folder}) isn't there"
        for path in sorted(folder.glob(f"{YML_PREFIX}*{YML_SUFFIX}")):
            texts[path.name] = path.read_text(encoding="utf-8")
        where = "your checkout"
    else:
        ref = ref or source.branch
        files = update.RepoFiles(ref)
        if isinstance(files, str):
            return files
        for file in sorted(filter(IsVariantFile, files)):
            read, text = update.RawFile(ref, file)
            if read:
                texts[file] = text
            else:
                problems.append(f"{file}: {text}")
        where = f"branch {source.branch}" if ref == source.branch else f"commit {ref[:7]}"
    found = []
    for file, text in texts.items():
        variant = ReadYml(text, file)
        if isinstance(variant, str):
            problems.append(variant)
        elif variant.Fits():
            found.append(variant)
    # Standard first, then by label
    found.sort(key=lambda variant: (variant.name != STANDARD, variant.label))
    return Variants(found, where, problems)


# ─── Conda and the environments on this machine ───────────────────────

def RunningPrefix() -> Path | None:
    """The conda environment this CompUtils runs from (None: not a conda environment)."""
    prefix = Path(sys.prefix)
    return prefix if (prefix / "conda-meta").is_dir() else None


def CondaExe() -> Path | None:
    """The conda executable: the one that activated this environment, else the one this environment sits under,
    else conda on PATH."""
    found = os.environ.get("CONDA_EXE")
    if found and Path(found).exists():
        return Path(found)
    running = RunningPrefix()
    if running is not None and running.parent.name == "envs":
        for candidate in ("bin/conda", "Scripts/conda.exe", "condabin/conda.bat"):
            if (running.parent.parent / candidate).exists():
                return running.parent.parent / candidate
    found = shutil.which("conda")
    return Path(found) if found else None


def CondaBase(conda: Path) -> Path:
    """The conda installation an executable belongs to (bin/conda, Scripts/conda.exe, condabin/conda.bat)."""
    return conda.parent.parent


def BasePython(conda: Path | None) -> Path | None:
    """conda's own Python (never this environment's): it runs the installer, which may replace this environment."""
    found = os.environ.get("CONDA_PYTHON_EXE")
    if found and Path(found).exists():
        return Path(found)
    if conda is None:
        return None
    base = CondaBase(conda)
    for candidate in ("bin/python", "python.exe", "bin/python3"):
        if (base / candidate).exists():
            return base / candidate
    return None


def EnvsFolders() -> list[Path]:
    """Where named environments live: beside the running one, under conda's own envs folder, then ~/.conda/envs."""
    folders = []
    running = RunningPrefix()
    if running is not None and running.parent.name == "envs":
        folders.append(running.parent)
    conda = CondaExe()
    if conda is not None:
        folders.append(CondaBase(conda) / "envs")
    folders.append(Path.home() / ".conda" / "envs")
    unique = []
    for folder in folders:
        if folder not in unique:
            unique.append(folder)
    return unique


def EnvPrefix(name: str) -> Path | None:
    """The installed environment with this name (None: not installed)."""
    running = RunningPrefix()
    if running is not None and running.name == name:
        return running
    return next((folder / name for folder in EnvsFolders() if (folder / name / "conda-meta").is_dir()), None)


def NewPrefix(name: str) -> Path | None:
    """Where a new environment with this name goes: the first envs folder that exists (None: none does)."""
    return next((folder / name for folder in EnvsFolders() if folder.is_dir()), None)


def InstalledPackages(prefix: Path) -> dict[str, str]:
    """name -> version, from conda-meta's '<name>-<version>-<build>.json' file names (no conda call)."""
    packages = {}
    for record in (prefix / "conda-meta").glob("*.json"):
        parts = record.stem.rsplit("-", 2)
        if len(parts) == 3:
            packages[parts[0].lower()] = parts[1]
    return packages


# ─── Matching specs (a subset of conda's MatchSpec) ───────────────────

def _VersionKey(version: str) -> list[tuple]:
    """Comparable components: numbers as numbers, letters below them (1.0rc1 < 1.0), 'post' above."""
    key = []
    for part in regex.findall(r"\d+|[a-z]+", version.lower()):
        if part.isdigit():
            key.append((1, int(part), ""))
        else:
            key.append((2 if part == "post" else 0, 0, part))
    return key


def _Compare(left: str, right: str) -> int:
    a, b = _VersionKey(left), _VersionKey(right)
    length = max(len(a), len(b))
    a += [(1, 0, "")] * (length - len(a))
    b += [(1, 0, "")] * (length - len(b))
    return (a > b) - (a < b)


def _Prefixed(version: str, prefix: str) -> bool:
    """conda's fuzzy '=3.2' (and '3.2.*'): the version's leading components are the prefix's."""
    wanted = _VersionKey(prefix.rstrip(".*"))
    return _VersionKey(version)[:len(wanted)] == wanted


def _Satisfies(version: str, constraint: str) -> bool | None:
    """Whether version meets one constraint ('>=13.7', '=3.2', '3.2.*'); None when it can't be read."""
    match = regex.fullmatch(r"(==|>=|<=|!=|~=|>|<|=)?\s*([0-9A-Za-z._*+!]+)", constraint.strip())
    if match is None:
        return None
    operator, wanted = match.group(1) or "", match.group(2)
    if operator in ("", "=") or (operator == "==" and wanted.endswith("*")):
        return _Prefixed(version, wanted)
    if "*" in wanted:
        return None
    order = _Compare(version, wanted)
    return {"==": order == 0, "!=": order != 0, ">=": order >= 0, "<=": order <= 0, ">": order > 0, "<": order < 0,
            "~=": order >= 0 and _Prefixed(version, wanted.rsplit(".", 1)[0])}[operator]


def SpecName(spec: str) -> str:
    return regex.match(r"\s*([A-Za-z0-9_.\-]+)", spec).group(1).lower()


def Meets(version: str, spec: str) -> bool:
    """Whether an installed version meets a spec. A spec this can't read counts as met: a refresh settles it."""
    constraint = spec.strip()[len(SpecName(spec)):].strip()
    # 'name=1.2=build': the build string is left to conda
    if constraint.startswith("=") and not constraint.startswith("==") and constraint.count("=") > 1:
        constraint = constraint[:constraint.index("=", 1)]
    if not constraint:
        return True
    for alternative in constraint.split("|"):
        results = [_Satisfies(version, part) for part in alternative.split(",")]
        if all(result is not False for result in results):
            return True
    return False


# ─── Status ───────────────────────────────────────────────────────────

@dataclass
class Status:
    variant: Variant
    prefix:  Path | None                                   # None: not installed
    running: bool = False                                  # CompUtils runs from it
    missing: list[str] = field(default_factory=list)       # specs with no package
    behind:  list[tuple[str, str]] = field(default_factory=list)   # (spec, installed version)
    python:  tuple[str, str] | None = None                 # (python spec, installed) when Python itself changed
    job:     dict | None = None                            # its environment job, while one runs

    @property
    def current(self) -> bool:
        return self.prefix is not None and not self.missing and not self.behind and self.python is None

    @property
    def needsRebuild(self) -> bool:
        return self.python is not None

    def Verdict(self) -> str:
        """Title Case, for a list row."""
        if self.job is not None:
            return f"{DOING[self.job['mode']]}: {self.job.get('phase', '')}"
        if self.prefix is None:
            return "Not Installed"
        if self.needsRebuild:
            return f"Rebuild Needed (Python {self.python[1]})"
        count = len(self.missing) + len(self.behind)
        return f"{count} Package{'s' if count != 1 else ''} Behind" if count else "Current"


def CheckStatus(variant: Variant) -> Status:
    prefix = EnvPrefix(variant.name)
    running = RunningPrefix()
    status = Status(variant, prefix, running is not None and prefix == running, job=RunningJob(variant.name))
    if prefix is None:
        return status
    installed = InstalledPackages(prefix)
    for spec in variant.specs:
        name = SpecName(spec)
        version = installed.get(name)
        if version is None and status.running:
            # pip-installed in the running environment (conda-meta lists conda's packages only)
            try:
                version = importlib.metadata.version(name)
            except importlib.metadata.PackageNotFoundError:
                pass
        if version is None:
            status.missing.append(spec)
        elif not Meets(version, spec):
            if name == "python":
                status.python = (spec, version)
            else:
                status.behind.append((spec, version))
    return status


def RunningName() -> str | None:
    running = RunningPrefix()
    return running.name if running is not None else None


def StatusLines(status: Status, where: str) -> list[str]:
    """One variant's status: which environment, then what its yml asks for that it lacks, or its job."""
    variant = status.variant
    lines = [f"{'In Use' if status.running else 'Environment'}: {variant.label} ({variant.name})"]
    if status.job is not None:
        started = time.strftime("%H:%M", time.localtime(status.job.get("started", time.time())))
        phase = status.job.get("phase", "")
        lines.append(f"{DOING[status.job['mode']]} in the background since {started}: {phase}.")
        if phase == WAITING:
            lines.append("It swaps in once CompUtils closes.")
        return lines
    if status.prefix is None:
        return lines + ["Not installed."]
    if status.current:
        return lines + [f"Up to date with {where}'s {variant.file}."]
    lines.append(f"{where[0].upper()}{where[1:]} asks for:")
    lines += [f"  + {spec} (Not Installed)" for spec in status.missing]
    lines += [f"  ↑ {spec} (Installed {version})" for spec, version in status.behind]
    if status.python is not None:
        lines.append(f"  ↑ {status.python[0]} (Installed {status.python[1]}): needs a rebuild")
    return lines


def StatusRecord(status: Status, where: str) -> dict:
    """One variant's status for a report (`cu -env --json`)."""
    variant = status.variant
    record = {"label": variant.label, "name": variant.name, "file": variant.file, "from": where,
              "inUse": status.running, "installed": status.prefix is not None, "verdict": status.Verdict()}
    if status.prefix is not None:
        record["prefix"] = str(status.prefix)
    if status.missing:
        record["missing"] = status.missing
    if status.behind:
        record["behind"] = [{"spec": spec, "installed": version} for spec, version in status.behind]
    if status.python is not None:
        record["python"] = {"spec": status.python[0], "installed": status.python[1]}
    if status.job is not None:
        record["job"] = {key: status.job.get(key) for key in ("mode", "phase", "started", "host")}
        record["log"] = str(LogPath(variant.name))
    return record


def ChangeSummary(status: Status) -> str:
    """What a job will change, for its report: 'rdkit added, textual updated'."""
    parts = [f"{SpecName(spec)} added" for spec in status.missing]
    parts += [f"{SpecName(spec)} updated" for spec, _ in status.behind]
    if status.python is not None:
        parts.append(f"Python {status.python[1]} → {status.python[0][len('python'):].lstrip('=<>! ')}")
    return ", ".join(parts)


# ─── Jobs (state files written by the installer's background mode) ───

def StateFolder() -> Path:
    return Path(Defaults.binDirectory).expanduser() / STATE_FOLDER


def StatePath(name: str) -> Path:
    return StateFolder() / f"{name}.json"


def LockPath(name: str) -> Path:
    return StateFolder() / f"{name}.lock"


def LogPath(name: str) -> Path:
    return StateFolder() / f"{name}.log"


def ReadState(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _WriteState(path: Path, state: dict) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(state, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _Alive(pid: int) -> bool:
    """Whether a process on this machine is still running (os.kill(pid, 0) would end it on Windows)."""
    if os.name == "nt":
        import ctypes
        kernel = ctypes.windll.kernel32
        handle = kernel.OpenProcess(0x1000, False, pid)        # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        kernel.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel.CloseHandle(handle)
        return code.value == 259                                # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _Live(state: dict | None) -> bool:
    """Whether a job is still running. One on another machine is taken at its word (its process can't be seen)."""
    if state is None or state.get("phase") in ENDED:
        return False
    if state.get("host") == socket.gethostname() and state.get("pid") and not _Alive(state["pid"]):
        return False
    return True


def RunningJob(name: str) -> dict | None:
    state = ReadState(StatePath(name))
    return state if _Live(state) else None


def _CloseDeadJob(name: str, state: dict) -> None:
    """A job whose process died without saying so (killed at logout, the machine restarted) is marked failed, so it
    is reported and a new one can start."""
    state.update(failedDuring=state.get("phase", ""), phase=FAILED,
                 error="its process stopped without finishing (killed, or the machine restarted)")
    _WriteState(StatePath(name), state)


def _InstallerText(source: update.Source) -> tuple[bool, str]:
    """conda-installer.py from the same commit as this code (a local install: its checkout's), so the two always
    agree on the background mode's arguments."""
    if source.localPath:
        path = Path(source.localPath) / INSTALLER
        if not path.is_file():
            return False, f"the local install has no {INSTALLER}"
        return True, path.read_text(encoding="utf-8")
    return update.RawFile(source.commit or source.branch, INSTALLER)


def _PackageArguments(source: update.Source) -> list[str]:
    """What the new environment gets as CompUtils: always the code installed now, so an environment job never changes
    the code (Update CompUtils does that)."""
    if not source.pipRecord:
        # A checkout pip doesn't know: nothing to install, the self-test runs on the checkout
        return ["--pythonpath", source.localPath]
    if source.localPath:
        return ["--package", source.localPath] + (["--editable"] if source.editable else [])
    return ["--package", update.PackageUrl(source.commit or source.branch)]


def Start(status: Status, mode: str, source: update.Source) -> tuple[bool, str]:
    """Starts a job on a variant in the background: (True, what started) or (False, why it can't)."""
    variant = status.variant
    if mode not in MODES:
        return False, f"Unknown environment job '{mode}'."
    if mode == "install" and status.prefix is not None:
        return False, f"{variant.name} is already installed."
    if mode != "install" and status.prefix is None:
        return False, f"{variant.name} isn't installed (install it instead)."
    if mode == "refresh" and status.needsRebuild:
        return False, f"{variant.name} needs a rebuild: its Python ({status.python[1]}) isn't what {variant.file} asks for."
    conda = CondaExe()
    python = BasePython(conda)
    if conda is None or python is None:
        return False, "Couldn't find conda (or conda's own Python) to run the job with."
    prefix = status.prefix or NewPrefix(variant.name)
    if prefix is None:
        return False, "Couldn't find conda's folder for environments."
    folder = StateFolder()
    folder.mkdir(parents=True, exist_ok=True)

    # One job per environment: the lock makes starting atomic (the installer removes it when it ends)
    lock = LockPath(variant.name)
    for _ in range(2):
        try:
            os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            break
        except FileExistsError:
            state = ReadState(StatePath(variant.name))
            if _Live(state):
                return False, (f"A {state['mode']} of {variant.name} is already running ({state.get('phase', '')}, "
                               f"on {state.get('host', 'another machine')}).")
            if state is not None and state.get("phase") not in ENDED:
                _CloseDeadJob(variant.name, state)
            lock.unlink(missing_ok=True)
    else:
        return False, f"Couldn't take {lock}."

    def Abandon(reason: str) -> tuple[bool, str]:
        lock.unlink(missing_ok=True)
        return False, reason

    read, installerText = _InstallerText(source)
    if not read:
        return Abandon(f"Couldn't read {INSTALLER} ({installerText}).")
    installer = folder / INSTALLER
    installer.write_text(installerText, encoding="utf-8")
    yml = folder / variant.file
    yml.write_text(variant.text, encoding="utf-8")
    log = LogPath(variant.name)
    state = {"name": variant.name, "label": variant.label, "mode": mode, "prefix": str(prefix),
             "host": socket.gethostname(), "pid": None, "started": time.time(), "phase": "Starting",
             "summary": ChangeSummary(status) if mode == "refresh" else "", "log": str(log), "reported": False}
    _WriteState(StatePath(variant.name), state)

    command = [str(python), str(installer), "--background", str(StatePath(variant.name)), "--mode", mode,
               "--yml", str(yml), "--prefix", str(prefix), "--conda-dir", str(CondaBase(conda)),
               *_PackageArguments(source)]
    # The job's own environment: no PYTHONPATH (it would make the self-test pass on code the new environment lacks)
    environment = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    detached = ({"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
                if os.name == "nt" else {"start_new_session": True})
    try:
        with open(log, "w", encoding="utf-8") as output:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                                       cwd=folder, env=environment, **detached)
    except OSError as error:
        state.update(phase=FAILED, failedDuring="Starting", error=str(error), reported=True)
        _WriteState(StatePath(variant.name), state)
        return Abandon(f"Couldn't start the job ({error}).")
    # The installer writes its own pid too; this one covers the moment before it does
    current = ReadState(StatePath(variant.name)) or state
    if not current.get("pid"):
        current["pid"] = process.pid
        _WriteState(StatePath(variant.name), current)
    return True, (f"{DOING[mode]} {variant.name} in the background. It needs nothing from you"
                  f"{'; it swaps in once CompUtils closes' if mode != 'install' else ''}. Log: {log}")


def AnyJobRunning() -> dict | None:
    """Any environment job that is running (for Home's item label)."""
    folder = StateFolder()
    if not folder.is_dir():
        return None
    for path in folder.glob("*.json"):
        state = ReadState(path)
        if _Live(state):
            return state
    return None


def Reports() -> list[tuple[str, str]]:
    """(severity, message) for each job that ended and hasn't been reported, each marked reported (shown once)."""
    folder = StateFolder()
    if not folder.is_dir():
        return []
    reports = []
    for path in sorted(folder.glob("*.json")):
        state = ReadState(path)
        if state is None or state.get("reported"):
            continue
        if state.get("phase") not in ENDED:
            if _Live(state):
                continue
            _CloseDeadJob(path.stem, state)
            state = ReadState(path)
        name, mode = state.get("name", path.stem), state.get("mode", "refresh")
        named = f"the {state['label']} environment ({name})" if state.get("label") else name
        if state["phase"] == DONE:
            summary = state.get("summary", "")
            message = f"{named} was {DONE_VERBS.get(mode, 'updated')}{': ' + summary if summary else ''}."
            reports.append(("good", message[0].upper() + message[1:]))
        else:
            kept = "nothing was installed" if mode == "install" else f"{name} is unchanged"
            reports.append(("error", f"The {mode} of {named} failed while "
                                     f"{(state.get('failedDuring') or 'starting').lower()} "
                                     f"({state.get('error') or 'no reason given'}); {kept}. Log: {state.get('log', '')}"))
        state["reported"] = True
        _WriteState(path, state)
    return reports


def LogTail(name: str, lines: int = 15) -> str:
    try:
        return "\n".join(LogPath(name).read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
    except OSError:
        return ""


# ─── Keeping Update CompUtils safe ────────────────────────────────────

def CodeUpdateProblem(source: update.Source, ref: str) -> list[str]:
    """Why installing CompUtils at ref (a branch or commit) into the running environment isn't safe yet, as lines (empty:
    it is): an environment job for it is running (its swap would bring the old code back), or ref's yml for this
    environment asks for packages it lacks. Nothing to check (not a conda environment, no matching yml, GitHub
    unreachable) isn't a problem: the update itself says what went wrong."""
    name = RunningName()
    if name is None:
        return []
    job = RunningJob(name)
    if job is not None:
        return [f"A {job['mode']} of {name} is running in the background ({job.get('phase', '')}).",
                "Update CompUtils once it's done: it reinstalls the code you have now."]
    variants = ReadVariants(source, ref)
    if isinstance(variants, str):
        return []
    variant = next((variant for variant in variants.found if variant.name == name), None)
    if variant is None:
        return []
    status = CheckStatus(variant)
    if status.current:
        return []
    # The lines after "In Use: …" and "<where> asks for:"
    return ["The new version needs an updated environment:", *StatusLines(status, variants.where)[2:],
            "Update the environment first (it runs in the background)."]


# ─── Neva-Touch Scheduler hooks ───────────────────────────────────────

def StablePython() -> str:
    """This CompUtils' interpreter as a job script should name it: <env>/bin/python rather than a versioned name
    (python3.11), so a queued job's hook still runs after a rebuild changes Python."""
    plain = Path(sys.prefix) / "bin" / "python"
    if Path(sys.executable).is_relative_to(sys.prefix) and plain.exists():
        return str(plain)
    return sys.executable


def SwapGuard(prefix: Path | None = None) -> Path:
    """The file the installer writes while it swaps an environment in (a hook waits while it exists)."""
    return Path(str(prefix or sys.prefix) + ".swapping")


# ─── Self-test (python -m computils --self-test) ──────────────────────

def SelfTest() -> int:
    """Imports every CompUtils module and checks that GoodVibes runs: what proves an environment works (cu --help
    imports almost nothing). Answered before any config is read, so nothing is written. Returns the exit code."""
    import importlib, pkgutil
    import computils
    failures = []
    names = sorted(module.name for module in pkgutil.walk_packages(computils.__path__, "computils."))
    for name in names:
        if name == "computils.__main__":
            continue
        try:
            importlib.import_module(name)
        except Exception as error:
            failures.append(f"{name}: {error!r}")
    if shutil.which("goodvibes") is None:
        failures.append("goodvibes: not on PATH")
    for failure in failures:
        print(f"✗ {failure}")
    print("✗ Self-test failed." if failures else f"✓ Self-test passed ({len(names)} modules, goodvibes).")
    return 1 if failures else 0
