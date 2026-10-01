import copy, shutil
from dataclasses import dataclass, field
from typing import Any
from pathlib import Path
import regex
import tomllib as tom

from .console import console

_warningBox = """\
# +-----------------------------------------------------------------------------+
# |  POWER USER SECTION — NonVariant job script blocks                          |
# |  These strings are written verbatim into generated SLURM job files.         |
# |  Incorrect edits WILL break job submission. Only modify if you know exactly |
# |  what you are doing and have verified the output manually.                  |
# +-----------------------------------------------------------------------------+"""


def tomlValue(value: Any, forceMultiline: bool = False) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Path):
        value = str(value)
    if isinstance(value, str):
        escaped = (
            value
            .replace("\\", "\\\\")
            .replace('"',  '\\"')
            .replace("\n", "\\n")
            .replace("\r", "\\r")
            .replace("\t", "\\t")
        )
        return f'"{escaped}"'
    if isinstance(value, list):
        if not value:
            return "[]"
        formattedItems = [tomlValue(v) for v in value]
        inlineFmt = f"[{', '.join(formattedItems)}]"
        # Use multiline array format if forced or inline would be too wide
        if not forceMultiline and len(inlineFmt) <= 88:
            return inlineFmt
        innerFmt = ",\n    ".join(formattedItems)
        return f"[\n    {innerFmt},\n]"
    return str(value)


def loadToml(configDir: Path, filename: str) -> dict:
    filePath = configDir / Path(filename)
    try:
        with open(filePath, "rb") as file:
            return tom.load(file)
    except tom.TOMLDecodeError as error:
        console.print(f"[error]\\[config] Failed to parse {filename}: {error}\n"
            "         All hardcoded defaults will be used for this section.[/error]")
        return {}


def writeToml(configDir: Path, filename: str, content: str) -> bool:
    filePath = configDir / Path(filename)
    try:
        with open(filePath, "w", encoding="utf-8") as file:
            file.write(content)
        console.print(f"[operation]\\[config] Wrote config file: {filePath}[/operation]")
        return True
    except OSError as error:
        console.print(f"[error]\\[config] Could not write {filePath}: {error}\n"
            "         Hardcoded defaults will be used for this section.[/error]")
        return False


# _PatchFile: remove the key (a project override going back to following the global value)
DELETE = object()


def _TomlForm(value: Any) -> Any:
    """A value as it reads back from TOML (Paths are written as strings)."""
    return str(value) if isinstance(value, Path) else value


def _KeySpan(lines: list[str], key: str) -> tuple[int, int] | None:
    """The lines holding `key = value` in a flat TOML file: from the key line until the value parses (multiline arrays)."""
    pattern = regex.compile(rf'^(?:{regex.escape(key)}|"{regex.escape(key)}")\s*=')
    start = next((index for index, line in enumerate(lines) if pattern.match(line)), None)
    if start is None:
        return None
    for end in range(start + 1, len(lines) + 1):
        try:
            tom.loads("\n".join(lines[start:end]))
            return start, end
        except tom.TOMLDecodeError:
            continue
    return None


class Defaults:
    binDirectory = Path("~/bin").expanduser()
    projectMarker = ".computils"
    # Ordinary job defaults
    CPU = 12
    # GB per core. Floats: Stampede3 reserves whole nodes, so its ratio is 200/80 = 2.5
    memoryRatio = 2.0
    highMemoryRatio = 6.0
    memoryBuffer = 2
    wallTime = "24"
    cluster = ""
    partition = ""
    # Filemask and Extension related
    singlePointExtra = "_SP"
    reRunExtra = "_re"
    scratchFolderExtra = "-scratch"
    coordExtension = ".xyz"
    gaussianExtension = ".gjf"
    orcaExtension = ".inp"
    cubeExtension = ".cube"
    queueExtension = ".cmd"
    outputExtension = ".out"
    # What runs where
    methodNames = ["B3LYP","M062X","M06","M06L","B2PLYP","wB97XD","DLPNO-CCSD(T)","BLYP"]
    targetProgram = ["G16","G16","G16","G16","G16","G16","O","G16"]
    # Benchmark suite route cards (one complete route card per entry)
    benchmarkMethods = ["M062X 6-311+G(d,p)"]
    # Open-shell handling
    openShellReference = "U"
    ossSpinThreshold = 0.1
    # Optional job keylist data
    nboKeylist = "$NBO STERIC PLOT"
    mixedBasisVariants = ["Gen", "GenECP", "gen", "genecp"]
    # Cube Keylists
    potCube = "Pot"
    denCube = "Den"
    valenceCube = "Val"
    spinCube = "Spin"
    # Job stalking related
    stalkDuration = 120
    stalkFrequency = 3
    # Job submission related. Edit this across clusters
    gaussianNonVariant = ["\nmodule purge\nmodule load gaussian\n\n",
                          "export GAUSS_SCRDIR=$SLURM_SCRATCH\nulimit -s unlimited\nexport LC_COLLATE=C\n"]
    # The ORCA job's environment only: runJob writes the scratch copy, the copy-back and the run itself
    orcaNonVariant = ["\n# Load the module\nmodule purge\n","module load orca/6.1.0\n\n"]
    # Result files copied from scratch into <job name><scratchFolderExtra>/ however the job ends (the .out comes back
    # through SLURM -o)
    orcaResultSuffixes = [".gbw", ".property.txt", ".hess", ".xyz", "_trj.xyz", ".engrad"]
    # Formatting related
    coreLineVariants = ["%nproc","%nprocshared","%pal"]
    ramLineVariants = ["%mem","%maxcore"]
    terminationVariants = ["normal termination","terminated normally","error termination"]
    submissionList = []
    hpcType = ""
    isNotifications = False
    botToken = ""
    chatID = ""
    broadcastGroupChatID = ""
    broadcastThreshold = 30
    needsFirstTimeSetup = False
    colorMode = ""
    bareCommandOpensTUI = True

    # What files contain what keys
    _FILE_GROUPS: dict[str, list[str]] = {
        "paths.toml": [
            "binDirectory", "projectMarker",
        ],
        "slurm.toml": [
            "CPU", "memoryRatio", "highMemoryRatio", "memoryBuffer",
            "wallTime", "cluster", "partition", "hpcType",
            "stalkDuration", "stalkFrequency", "submissionList",
        ],
        "programs.toml": [
            "methodNames", "targetProgram",
            "benchmarkMethods",
            "openShellReference", "ossSpinThreshold",
            "nboKeylist", "mixedBasisVariants",
            "potCube", "denCube", "valenceCube", "spinCube",
            "coreLineVariants", "ramLineVariants", "terminationVariants",
            "gaussianNonVariant", "orcaNonVariant", "orcaResultSuffixes",
        ],
        "extensions.toml": [
            "singlePointExtra", "reRunExtra", "scratchFolderExtra",
            "coordExtension", "gaussianExtension", "orcaExtension",
            "cubeExtension", "queueExtension", "outputExtension",
        ],
        "notifications.toml": [
            "isNotifications",
            "botToken",
            "chatID",
            "broadcastGroupChatID",
            "broadcastThreshold",
        ],
        "qol.toml": [
            "colorMode", "bareCommandOpensTUI",
        ]
    }

    # Header comment block written at the top of each generated TOML file.
    _HEADERS: dict[str, str] = {
        "paths.toml": "# paths.toml -- Filesystem path defaults\n# Auto-generated by CompUtils. Edit to change your bin "
            "directory.",
        "slurm.toml": "# slurm.toml -- SLURM-related defaults\n# Auto-generated by CompUtils. Edit at your own risk."
            "\n# cluster, partition, and hpcType MUST be set correctly before use.",
        "programs.toml": "# programs.toml -- Job-related defaults\n# Auto-generated by CompUtils.",
        "extensions.toml": "# extensions.toml -- Filemask defaults\n# Auto-generated by CompUtils.",
        "notifications.toml": (
            "# notifications.toml -- Telegram notification settings\n"
            "# Auto-generated by CompUtils.\n"
            "#\n"
            "# *** This file contains secrets (botToken). Do NOT edit under ANY circumstances. ***\n"
        ),
        "qol.toml": (
            "# qol.toml -- Quality-of-Life related defaults\n# Auto-generated by CompUtils.\n"
        )
    }

    # Per-key documentation written as a comment above each key in the generated TOML files.
    # Keys without an entry here get no comment.
    _COMMENTS: dict[str, str] = {
        "binDirectory": "Directory containing CompUtils executables and config files.",
        "projectMarker": "Name of the directory that marks a project root; CompUtils searches upward from the CWD for it."
            "\n# Renaming this orphans existing markers until they are renamed to match.",
        "CPU": "Number of CPU cores to request per job.",
        "memoryRatio": "Memory-to-CPU ratio for standard jobs (GB per core).",
        "highMemoryRatio": "Memory-to-CPU ratio for high-memory jobs, e.g. DLPNO (GB per core).",
        "memoryBuffer": "Additional memory headroom added on top of the computed request (GB).",
        "wallTime": "Default wall time (hours).",
        "cluster": "Default cluster for job submission. REQUIRED — program will error at startup if unset.",
        "partition": "Default partition for job submission. REQUIRED — program will error at startup if unset.",
        "hpcType": "HPC identity: a built-in cluster (Bridges2, Stampede3) or one from your lab profile. REQUIRED.",
        "stalkDuration": "How long (minutes) before job stalking times out without looping.",
        "stalkFrequency": "How often (minutes) to ping the queue while stalking.",
        "submissionList": "SLURM header lines for job submission. Set automatically by hpcType.",
        "methodNames": "Ordered list of known method names. Index must match targetProgram.",
        "targetProgram": "Program that runs methodNames[i]. Must be the same length as methodNames.",
        "benchmarkMethods": "Benchmark suite: each entry is a COMPLETE route card (method + basis + keywords).\n# Entry 0 is also the default for -sp. Add one entry per line you want benchmarked."
            "\n# ORCA only: {tag} tokens (e.g. {cpcm} {tddft}) pull matching %blocks from the project's orcablocks.txt."
            "\n# Tags are removed from the route card before it is written to the input file."
            "\n# Spin groups: [selectors: keywords {tags}] apply only to matching molecules, on top of the base route."
            "\n# Selectors: css, oss, open (oss or multiplicity > 1), doublet..septet, m2..m7. e.g. [oss: guess=mix stable=opt]",
        "openShellReference": "Reference for open-shell species: \"U\" (default) or \"RO\". OSS (broken-symmetry singlets) always use U.",
        "ossSpinThreshold": "<S**2> above which a multiplicity-1 output is classified as an open-shell singlet (OSS).",
        "nboKeylist": "NBO keylist string appended to relevant Gaussian16 jobs.",
        "mixedBasisVariants": "Basis set keylist indicating a mixed/custom basis is in use.",
        "potCube": "Cube file label for electrostatic potential.",
        "denCube": "Cube file label for electron density.",
        "valenceCube": "Cube file label for valence density.",
        "spinCube": "Cube file label for spin density.",
        "coreLineVariants": "Input file keylist identifying the processor count line, by program.",
        "ramLineVariants": "Input file keylist identifying the memory line, by program.",
        "terminationVariants": "Output file strings indicating normal or error job termination.",
        "gaussianNonVariant": "Gaussian16 SLURM script boilerplate written verbatim into job files.",
        "orcaNonVariant": "ORCA 6.X SLURM script environment (modules), written verbatim into job files before the run.",
        "orcaResultSuffixes": "ORCA result files (<job name><suffix>) copied back from scratch, however the job ends.",
        "singlePointExtra": "Filename suffix appended to single-point calculation jobs.",
        "reRunExtra": "Filename suffix appended to re-run jobs.",
        "scratchFolderExtra": "Suffix of the folder (<job name><suffix>/) an ORCA job's result files are copied into.",
        "coordExtension": "Coordinate file extension.",
        "gaussianExtension": "Gaussian16 input file extension.",
        "orcaExtension": "ORCA 6.X input file extension.",
        "cubeExtension": "Cube file extension.",
        "queueExtension": "Job queue/submission script extension.",
        "outputExtension": "Program output file extension.",
        "isNotifications": "Master switch: set to true to enable Telegram notifications.",
        "botToken": "Telegram bot API token (from @BotFather). Treat as a secret.",
        "chatID": "Your personal Telegram chat ID (auto-detected during setup).",
        "broadcastGroupChatID": "Telegram group chat ID for broadcast queue alerts.",
        "broadcastThreshold": "Minimum jobs in a single submission to trigger a broadcast alert.",
        "colorMode": "Determined the level of color accuracy used in terminal output.",
        "bareCommandOpensTUI": "Set to true to open the TUI when `cu` is run with no arguments. `cu -tui` always opens it.",
    }

    # Expected Python type for each config key, used by _CoerceValue to validate values loaded from user-editable TOML.
    # Derived below the class from each key's hardcoded default, so it can't drift from them
    _TYPES: dict[str, type] = {}

    # Keys listed here get the _warningBox comment block inserted immediately above them in the generated TOML,
    # alerting users not to edit carelessly.
    _WARNINGS: dict[str, str] = {
        "gaussianNonVariant": _warningBox,
        "orcaNonVariant": _warningBox,
    }

    # Keys whose list values should always render as multiline arrays for readability.
    _FORCE_MULTILINE: set[str] = {"benchmarkMethods"}

    # Keys a project's <marker>/project.toml may override. Everything else is machine- or program-wide
    _PROJECT_KEYS: list[str] = [
        "CPU", "memoryRatio", "highMemoryRatio", "memoryBuffer", "wallTime", "cluster", "partition",
        "benchmarkMethods", "openShellReference", "ossSpinThreshold", "nboKeylist",
    ]
    # Global values displaced by project overrides, and the project values that replaced them. Used so project values
    # never leak into the global TOML files when a wizard re-saves a section
    _globalValues: dict[str, Any] = {}
    _projectValues: dict[str, Any] = {}
    # Every key's hardcoded default (set below the class), for DefaultValue
    _HARDCODED: dict[str, Any] = {}
    # Global comments that don't hold for a project file
    _PROJECT_COMMENTS: dict[str, str] = {
        "cluster": "Cluster for jobs in this project.", "partition": "Partition for jobs in this project.",
    }
    # Files the last Load() had to generate (Main runs their setup before the TUI opens)
    generatedFiles: list[str] = []
    # The installed lab profile's clusters (profile.py), read on every Load
    _profileClusters: list["Cluster"] = []


    @classmethod
    def Load(cls) -> None:
        cls.generatedFiles = []
        for filename in cls._FILE_GROUPS:
            configDir = cls.binDirectory
            filePath = configDir / Path(filename)

            if not filePath.exists():
                console.print(f"[warning]\\[config] {filename} not found — generating from hardcoded defaults.[/warning]")
                cls.generatedFiles.append(filename)
                cls._SaveSection(filename)
                continue

            data = loadToml(configDir, filename)
            if data:
                missingKeys = cls._ApplySection(data, filename)
                if missingKeys:
                    cls._AppendMissing(filename, missingKeys)
                cls._GuardBrokenLayouts(filename)

        from .profile import InstalledProfile
        profile = InstalledProfile()
        cls._profileClusters = profile.clusters if profile else []
        cls._Validate()


    @classmethod
    def _SaveSection(cls, filename: str) -> None:
        # binDirectory is a str once loaded from paths.toml
        configDir = Path(cls.binDirectory)
        # An existing file is patched in place (only the values that changed), so hand-written comments survive
        if (configDir / filename).is_file():
            data = loadToml(configDir, filename)
            if data:
                changed = {key: cls._PersistedValue(key) for key in cls._FILE_GROUPS[filename]
                           if data.get(key, DELETE) != _TomlForm(cls._PersistedValue(key))}
                if not changed or cls._PatchFile(configDir / filename, changed):
                    return
        writeToml(configDir, filename, cls._BuildContent(filename))


    @classmethod
    def _SaveKeys(cls, keys) -> None:
        """_SaveSection for every file holding one of these keys (a cluster's or a profile's settings span files)."""
        for filename, fileKeys in cls._FILE_GROUPS.items():
            if any(key in fileKeys for key in keys):
                cls._SaveSection(filename)


    @classmethod
    def _PatchFile(cls, filePath: Path, updates: dict[str, Any], comments: dict[str, str] | None = None) -> bool:
        """Write values into an existing flat TOML file in place: only each key's own `key = value` lines change, so
        comments survive. A missing key is appended with its comment; DELETE removes a key and the comments directly
        above it. Nothing is written unless the result parses and reads back as the values given."""
        comments = cls._COMMENTS if comments is None else comments
        try:
            lines = filePath.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError) as error:
            console.print(f"[error]\\[config] Could not read {filePath}: {error}[/error]")
            return False
        for key, value in updates.items():
            span = _KeySpan(lines, key)
            if span is None:
                if value is not DELETE:
                    lines += [""] + cls._KeyLines(key, value, comments.get(key, ""))
                continue
            start, end = span
            if value is not DELETE:
                lines[start:end] = cls._KeyLines(key, value, "")[-1:]
                continue
            while start > 0 and lines[start - 1].lstrip().startswith("#"):
                start -= 1
            if end < len(lines) and not lines[end].strip():
                end += 1
            del lines[start:end]
        text = "\n".join(lines) + "\n"
        try:
            data, problem = tom.loads(text), ""
        except tom.TOMLDecodeError as error:
            data, problem = None, str(error)
        if data is not None:
            wrong = [key for key, value in updates.items()
                     if (key in data if value is DELETE else data.get(key, DELETE) != _TomlForm(value))]
            problem = f"{', '.join(wrong)} did not read back as written" if wrong else ""
        if problem:
            console.print(f"[error]\\[config] Could not update {filePath} in place ({problem}); left unchanged.[/error]")
            return False
        return writeToml(filePath.parent, filePath.name, text)


    @classmethod
    def _KeyLines(cls, key: str, value: Any, commentText: str) -> list[str]:
        """One key as written to a TOML file: its power-user warning block and comment (if any), then key = value."""
        lines = [cls._WARNINGS[key], ""] if key in cls._WARNINGS else []
        if commentText:
            lines.append(f"# {commentText}")
        lines.append(f"{key} = {tomlValue(value, forceMultiline=(key in cls._FORCE_MULTILINE))}")
        return lines


    @classmethod
    def _BuildContent(cls, filename: str, valueOf=None) -> str:
        """A freshly generated file: header, then each key with its comment. valueOf(key) picks the values written."""
        valueOf = valueOf or cls._PersistedValue
        lines = [cls._HEADERS[filename], ""]
        for key in cls._FILE_GROUPS[filename]:
            lines += cls._KeyLines(key, valueOf(key), cls._COMMENTS.get(key, "")) + [""]
        return "\n".join(lines)


    @classmethod
    def _ApplySection(cls, data: dict, filename: str) -> list[str]:
        missing = []
        for key in cls._FILE_GROUPS[filename]:
            if key in data:
                value = cls._Checked(key, data[key], filename, f"Falling back to hardcoded default: {getattr(cls, key)!r}")
                if value is not None:
                    setattr(cls, key, value)
            else:
                missing.append(key)
                console.print(f"[warning]\\[config] Key '{key}' not found in {filename}. "
                       f"Falling back to hardcoded default: {getattr(cls, key)!r}[/warning]")
        return missing

    @classmethod
    def _Checked(cls, key: str, rawValue, source, fallback: str):
        """_CoerceValue, with a warning (ending in what is used instead) when the value has the wrong type."""
        value = cls._CoerceValue(key, rawValue)
        if value is None:
            console.print(f"[warning]\\[config] Key '{key}' in {source} has invalid type (expected "
                          f"{cls._TYPES[key].__name__}, got {type(rawValue).__name__}). {fallback}[/warning]")
        return value

    @classmethod
    def _CoerceValue(cls, key: str, value):
        expected = cls._TYPES.get(key)
        if expected is None:
            return value

        # TOML bool is a Python bool (subclass of int), check it before int
        if expected is bool:
            if isinstance(value, bool):
                return value
            return None

        # Reject bool where int/float expected (bool is subclass of int in Python)
        if isinstance(value, bool) and expected in (int, float):
            return None

        if isinstance(value, expected):
            return value

        # float → int coercion (e.g. user writes 12.0 instead of 12)
        if expected is int and isinstance(value, float):
            if value == int(value):
                return int(value)
            console.print(f"[warning]\\[config] Key '{key}' should be a whole number, got {value}. "
                   f"Rounding to {int(value)}.[/warning]")
            return int(value)

        # int → float coercion (unlikely but harmless)
        if expected is float and isinstance(value, int):
            return float(value)

        # str coercion for Path-bound keys (binDirectory loaded as str from TOML)
        if expected is str and isinstance(value, (int, float)):
            return str(value)

        return None


    @classmethod
    def _GuardBrokenLayouts(cls, filename: str) -> None:
        """A value still in a layout that can no longer work (_BROKEN_LAYOUTS) is replaced by the default in memory only.
        The file is never rewritten here (keys must not flip back and forth): `cu -refresh` updates it."""
        for key, marker in _BROKEN_LAYOUTS.items():
            if key in cls._FILE_GROUPS[filename] and _InBrokenLayout(key, getattr(cls, key)):
                setattr(cls, key, copy.deepcopy(cls._HARDCODED[key]))
                console.print(f"[warning]\\[config] {key} in {filename} is in a layout that no longer works. Using the "
                              f"default this run; run `cu -refresh` to update the file.[/warning]")


    @classmethod
    def RefreshFiles(cls) -> None:
        """`cu -refresh`: rewrite every global file in the current layout (header, comments, key order) with the loaded
        values. Stale keys are dropped, invalid values and old defaults (_OLD_DEFAULTS) become the current default, a key
        found in the wrong file moves to its own, and an unparseable file is backed up to .bak first. The report names
        keys only, since values can be secrets."""
        configDir = Path(cls.binDirectory)
        fileOf = {key: filename for filename, keys in cls._FILE_GROUPS.items() for key in keys}
        notes = {filename: {} for filename in cls._FILE_GROUPS}
        def Note(filename: str, label: str, key: str) -> None:
            notes[filename].setdefault(label, []).append(key)

        raw = {}
        for filename in cls._FILE_GROUPS:
            path = configDir / filename
            try:
                raw[filename] = tom.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
            except (tom.TOMLDecodeError, OSError, UnicodeDecodeError):
                raw[filename] = {}
                if BackUpFile(path):
                    Note(filename, "unreadable, backed up to", f"{filename}.bak")

        for filename, data in raw.items():
            for key, value in data.items():
                if key not in fileOf:
                    Note(filename, "dropped", key)
                    continue
                checked = cls._CoerceValue(key, value)
                home = fileOf[key]
                if home != filename:
                    Note(filename, "moved out", key)
                    # Load never read it here and appended the default to its own file: the value here is the user's
                    ownValue = raw[home].get(key, DELETE)
                    if checked is not None and (ownValue is DELETE or ownValue == _TomlForm(cls.DefaultValue(key))):
                        cls._SetGlobal(key, checked)
                        Note(home, "moved in", key)
                elif checked is None:
                    Note(filename, "reset to default", key)
                elif key in _BROKEN_LAYOUTS and _InBrokenLayout(key, checked) and checked not in _OLD_DEFAULTS.get(key, []):
                    Note(filename, "reset (old layout no longer works)", key)

        # Old hardcoded defaults become the current one: only ever here, so a value never flips back and forth on load
        for key, oldDefaults in _OLD_DEFAULTS.items():
            if cls.GlobalValue(key) in oldDefaults:
                cls._SetGlobal(key, cls.DefaultValue(key))
                Note(fileOf[key], "updated to the new default", key)

        for filename in cls._FILE_GROUPS:
            path = configDir / filename
            content = cls._BuildContent(filename)
            current = path.read_text(encoding="utf-8") if path.is_file() else None
            if current is not None and current.replace("\r\n", "\n") == content:
                console.print(f"[info]\\[config] {filename} is already current.[/info]")
                continue
            if writeToml(configDir, filename, content):
                details = "; ".join(f"{label}: {', '.join(keys)}" for label, keys in notes[filename].items())
                console.print(f"[good]\\[config] Refreshed {filename}{f' ({details})' if details else ''}.[/good]")


    @classmethod
    def _SetGlobal(cls, key: str, value: Any) -> None:
        """Set a key's global value, behind any project override of it (what _PersistedValue writes)."""
        if key in cls._globalValues:
            cls._globalValues[key] = value
        else:
            setattr(cls, key, value)


    @classmethod
    def _AppendMissing(cls, filename: str, missingKeys: list[str]) -> None:
        if cls._PatchFile(Path(cls.binDirectory) / filename, {key: cls._PersistedValue(key) for key in missingKeys}):
            console.print(f"[warning]\\[config] Appended {len(missingKeys)} missing key(s) to {filename}.[/warning]")


    @classmethod
    def _Validate(cls) -> None:
        cls._ValidateReference()
        # Main() runs the setup wizard. Recomputed on every Load, since the config can be reloaded mid-run
        cls.needsFirstTimeSetup = len(cls.hpcType) == 0


    @classmethod
    def _ValidateReference(cls) -> None:
        if cls.openShellReference.upper() not in ("U", "RO"):
            console.print(f"[warning]\\[config] openShellReference must be \"U\" or \"RO\", got {cls.openShellReference!r}. "
                          "Falling back to \"U\".[/warning]")
            cls.openShellReference = "U"
        cls.openShellReference = cls.openShellReference.upper()


    @classmethod
    def DefaultValue(cls, key: str) -> Any:
        """What resetting a key writes: the active cluster's setting for it (e.g. Stampede3's full-node memoryRatio),
        else its hardcoded default."""
        cluster = next((option for option in cls.Clusters() if option.hpcType == cls.hpcType), None)
        if cluster is not None and key in cluster.settings:
            return cluster.settings[key]
        return copy.deepcopy(cls._HARDCODED[key])


    @classmethod
    def Clusters(cls) -> list["Cluster"]:
        """The clusters setup offers: the lab profile's first (replacing a built-in of the same name), then the built-ins."""
        names = {cluster.hpcType for cluster in cls._profileClusters}
        return cls._profileClusters + [cluster for cluster in BUILTIN_CLUSTERS if cluster.hpcType not in names]


    @classmethod
    def ProjectComments(cls) -> dict[str, str]:
        return {**cls._COMMENTS, **cls._PROJECT_COMMENTS}


    @classmethod
    def GlobalValue(cls, key: str) -> Any:
        """The value from the global TOML files, even while a project override is active."""
        return cls._globalValues.get(key, getattr(cls, key))


    @classmethod
    def _PersistedValue(cls, key: str) -> Any:
        # A still-active project override is written as the global value it displaced. A value changed after the
        # overlay (e.g. by a setup wizard) is a deliberate global edit and is written as-is
        value = getattr(cls, key)
        if key in cls._projectValues and value == cls._projectValues[key]:
            return cls._globalValues[key]
        return value


    @classmethod
    def ApplyProjectOverrides(cls, data: dict, source: Path) -> list[str]:
        """Overlay a project.toml onto the loaded globals. Returns the keys whose value differs from the global one."""
        applied = []
        for key, rawValue in data.items():
            if key not in cls._PROJECT_KEYS:
                console.print(f"[warning]\\[config] Key '{key}' in {source} is not project-overridable. Ignored.[/warning]")
                continue
            value = cls._Checked(key, rawValue, source, f"Keeping the global value: {cls.GlobalValue(key)!r}")
            if value is None:
                continue
            cls._globalValues.setdefault(key, getattr(cls, key))
            setattr(cls, key, value)
            applied.append(key)
        cls._ValidateReference()
        # Record the post-validation values so the leak guard compares like with like (e.g. "ro" -> "RO")
        for key in applied:
            cls._projectValues[key] = getattr(cls, key)
        return [key for key in applied if cls._projectValues[key] != cls._globalValues[key]]


    @classmethod
    def ResetProjectOverrides(cls) -> None:
        """Undo ApplyProjectOverrides, restoring the global values (e.g. before switching to another project)."""
        for key, value in cls._globalValues.items():
            setattr(cls, key, value)
        cls._globalValues.clear()
        cls._projectValues.clear()


    @classmethod
    def BuildProjectContent(cls, values: dict[str, Any] | None = None) -> str:
        """project.toml holding values, by default a full snapshot of the current GLOBAL value of every
        project-overridable key (what `cu -init` writes)."""
        snapshot = values is None
        if snapshot:
            values = {key: cls.GlobalValue(key) for key in cls._PROJECT_KEYS}
        lines = ["# project.toml -- Project-level overrides of the global CompUtils config",
                 "# Generated by `cu -init` as a snapshot of the global config at that time." if snapshot
                 else "# Written by CompUtils (config editor or `cu -refresh`).",
                 "# Every key here overrides the global value for jobs run anywhere inside this project.",
                 "# Delete a key to follow the global value again. Only the keys below can be overridden." if snapshot
                 else f"# Delete a key to follow the global value again. Overridable: {', '.join(cls._PROJECT_KEYS)}.", ""]
        comments = cls.ProjectComments()
        for key in cls._PROJECT_KEYS:
            if key in values:
                lines += cls._KeyLines(key, values[key], comments.get(key, "")) + [""]
        return "\n".join(lines)


# binDirectory is a Path here but a str in TOML
Defaults._TYPES = {key: str if isinstance(getattr(Defaults, key), Path) else type(getattr(Defaults, key))
                   for keys in Defaults._FILE_GROUPS.values() for key in keys}
Defaults._HARDCODED = {key: copy.deepcopy(getattr(Defaults, key)) for keys in Defaults._FILE_GROUPS.values() for key in keys}

# Past hardcoded defaults, by key. `cu -refresh` (and only it) moves a value still equal to one of them to the current
# default. Lab-private values (e.g. the old broadcastGroupChatID) never go here: they must not be in the public source
_OLD_DEFAULTS: dict[str, list[Any]] = {
    # The whole ORCA 6.0.1 job plumbing, until runJob took it over (ORCA 6.1)
    "orcaNonVariant": [["\n# Load the module\nmodule purge\n","module load orca/6.0.1\n\n",
                        "# Copy files to SLURM_SCRATCH\n","for i in ${files[@]}; do\n",
                        "    cp $SLURM_SUBMIT_DIR/$i $SLURM_SCRATCH/$i\ndone\n\n","# cd to the SCRATCH space\n",
                        "cd $SLURM_SCRATCH\n\n","# run the job, $(which orca) is necessary\n",
                        "# finally, copy back gbw and prop files\n","cp $SLURM_SCRATCH/*.{gbw,prop} $SLURM_SUBMIT_DIR\n\n"]],
    "bareCommandOpensTUI": [False],
}

# Values in a layout that can no longer work, by a marker only that layout contains. Replaced in memory on every load
# (with a warning), and in the file by `cu -refresh`
_BROKEN_LAYOUTS: dict[str, str] = {"orcaNonVariant": "${files[@]}"}

def _InBrokenLayout(key: str, value) -> bool:
    return isinstance(value, list) and any(_BROKEN_LAYOUTS[key] in str(line) for line in value)


def BackUpFile(path: Path) -> bool:
    """Move a file aside to <name>.bak before it is regenerated. False (with an error) if that failed."""
    try:
        shutil.move(path, path.with_name(path.name + ".bak"))
        return True
    except OSError as error:
        console.print(f"[error]Could not back up {path}: {error}. Leaving it as-is.[/error]")
        return False


@dataclass
class Cluster:
    """A cluster setup can choose: its SLURM header, and the Defaults firstTimeSetup() sets for it."""
    hpcType: str
    submissionList: list[str]
    settings: dict[str, Any] = field(default_factory=dict)


# The public clusters. A lab's own (e.g. with its private partition) come from its lab profile (profile.py)
BUILTIN_CLUSTERS = (
    # JobName Nodes Partition NTasks Time
    Cluster("Bridges2", ["#!/bin/csh", "#SBATCH -J", "#SBATCH -N 1", "#SBATCH -p", "#SBATCH --ntasks-per-node=",
                         "#SBATCH -t"],
            {"partition": "RM-shared", "memoryRatio": 2.0, "memoryBuffer": 0, "highMemoryRatio": 2.0}),
    # JobName OutputName Error Nodes Partition Time. Stampede3 reserves whole nodes: 200 GB over the icx node's 80 cores
    Cluster("Stampede3", ["#!/usr/bin/env bash", "#SBATCH -J", "#SBATCH -o", "#SBATCH -e error.%j", "#SBATCH -N 1 -n 1",
                          "#SBATCH -p", "#SBATCH -t"],
            {"partition": "icx", "CPU": 80, "memoryRatio": 200/80, "memoryBuffer": 0, "highMemoryRatio": 200/80}),
)
