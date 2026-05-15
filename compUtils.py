#!/usr/bin/env python3
# Welcome to Computational Chemistry Utilities!
# Now bigger, harder, faster, and stronger than ever before!
# This package has been hand-crafted lovingly through untold pain and suffering
# Last major commit to the project was 2026-05-13 (previously 2025-10-27)
# Last minor commit to the project was 2025-12-29

import os, argparse, glob, subprocess, regex, time, json#, sys    # Only necessary for occasional troubleshooting
from termcolor import cprint
import pandas#, numpy   # Will implement eventually (probably)
from contextlib import closing
from mmap import mmap, ACCESS_READ
import tomllib as tom
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

### Begin TOML file stuff (assisted via Claude)
_warningBox = """\
# +-----------------------------------------------------------------------------+
# |  POWER USER SECTION — NonVariant job script blocks                          |
# |  These strings are written verbatim into generated SLURM job files.         |
# |  Incorrect edits WILL break job submission. Only modify if you know exactly |
# |  what you are doing and have verified the output manually.                  |
# +-----------------------------------------------------------------------------+"""

def tomlValue(value: Any) -> str:
    """
    Serialize a single Python value to its TOML literal string.

    Handles the types actually used in Defaults: str, int, bool, and list.
    Strings are escaped for newlines, tabs, backslashes, and quotes so that
    the NonVariant shell-script fragments round-trip correctly through TOML.
    Lists are written inline if short, or multiline if long.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
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
        # Use multiline array format if inline would be too wide
        if len(inlineFmt) <= 88:
            return inlineFmt
        innerFmt = ",\n    ".join(formattedItems)
        return f"[\n    {innerFmt},\n]"
    return str(value)

def loadToml(configDir: Path, filename: str) -> dict:
    """
    Open and parse a TOML file from configDir. Returns the parsed dict.

    Returns an empty dict (with a warning) if the file is malformed,
    so that the rest of the load process can continue using hardcoded
    defaults rather than crashing the program at startup.
    """
    filePath = configDir / filename
    try:
        with open(filePath, "rb") as f:
            return tom.load(f)
    except tom.TOMLDecodeError as e:
        cprint(f"[config] Failed to parse {filename}: {e}\n"
            "         All hardcoded defaults will be used for this section.", "light_red")
        return {}

def writeToml(configDir: Path, filename: str, content: str) -> None:
    """
    Write a TOML content string to a file in configDir.

    Called either when generating a file for the first time or when
    firstTimeSetup() persists interactive HPC configuration. Warns
    (rather than crashing) on filesystem errors.
    """
    filePath = configDir / filename
    try:
        with open(filePath, "w", encoding="utf-8") as f:
            f.write(content)
        cprint(f"[config] Wrote config file: {filePath}", "light_cyan")
    except OSError as e:
        cprint(f"[config] Could not write {filePath}: {e}\n"
            "         Hardcoded defaults will be used for this section.", "light_red")

def warnMissing(key: str, filename: str, fallback: Any) -> None:
    """
    Issue a UserWarning when a key expected in a TOML file is absent.

    This is the "warn and fall back" behavior: the program continues
    using the hardcoded default value, but the user is told what was
    missing and what value was substituted.
    """
    cprint(f"[config] Key '{key}' not found in {filename}. "
        f"Falling back to hardcoded default: {fallback!r}", "light_red")

# Set your defaults HERE
class Defaults:
    # New!! No longer need to specify your bin directory
    binDirectory = os.path.expanduser("~/bin")
    # Ordinary job defaults
    CPU = 12
    memoryRatio = 2
    highMemoryRatio = 6
    memoryBuffer = 2
    wallTime = "24"
    cluster = ""
    partition = ""
    # Filemask and Extension related
    singlePointExtra = "_SP"
    reRunExtra = "_re"
    coordExtension = ".xyz"
    gaussianExtension = ".gjf"
    orcaExtension = ".inp"
    qChemExtension = ".in"
    cubeExtension = ".cube"
    queueExtension = ".cmd"
    outputExtension = ".out"
    # Single point calculation related
    method = "M062X"
    methodLine = "M062X 6-311+G(d,p)"
    # What runs where. Edit carefully (recommended to use programs.txt instead)
    methodNames = ["B3LYP","M062X","M06","M06L","B2PLYP","wB97XD","DLPNO-CCSD(T)","BLYP"]
    targetProgram = ["G16","G16","G16","G16","G16","G16","O","G16"]
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
    orcaNonVariant = ["\n# Load the module\nmodule purge\n","module load orca/6.0.1\n\n",
                      "# Copy files to SLURM_SCRATCH\n","for i in ${files[@]}; do\n",
                      "    cp $SLURM_SUBMIT_DIR/$i $SLURM_SCRATCH/$i\ndone\n\n","# cd to the SCRATCH space\n",
                      "cd $SLURM_SCRATCH\n\n","# run the job, $(which orca) is necessary\n",
                      "# finally, copy back gbw and prop files\n","cp $SLURM_SCRATCH/*.{gbw,prop} $SLURM_SUBMIT_DIR\n\n"]
    qChemNonVariant = []
    # Formatting related
    coreLineVariants = ["%nproc","%nprocshared","%pal"]
    ramLineVariants = ["%mem","%maxcore"]
    terminationVariants = ["normal termination","terminated normally","error termination"]
    submissionList = []
    hpcType = ""
    isNotifications = False
    botToken = ""
    chatID = ""
    broadcastGroupChatID = "-1003992367027"
    broadcastThreshold = 30

    # ── TOML config file mappings ──────────────────────────────────────────────
    #
    # _FILE_GROUPS maps each TOML filename to the ordered list of attribute
    # names it owns. This is the single source of truth for which attributes
    # belong in which file — Load(), _SaveSection(), and _ApplySection() all
    # derive their behavior from this dict.
    #
    # "paths.toml" is listed first intentionally: binDirectory is loaded before
    # any other file, so that if the user has changed their bin location, all
    # subsequent reads happen from the correct directory.
    #
    # To add a new config key:
    #   1. Add the attribute with its hardcoded default above.
    #   2. Add its name to the correct list here.
    #   3. Add a comment string to _COMMENTS.
    #   That's all — Load(), _SaveSection(), and _ApplySection() handle the rest.
    #
    # To add a new config file:
    #   1. Add a new entry here with the filename and its attribute list.
    #   2. Add a header string to _HEADERS.
    #   3. Add comments to _COMMENTS for the new keys.
    _FILE_GROUPS: dict[str, list[str]] = {
        "paths.toml": [
            "binDirectory",
        ],
        "slurm.toml": [
            "CPU", "memoryRatio", "highMemoryRatio", "memoryBuffer",
            "wallTime", "cluster", "partition", "hpcType",
            "stalkDuration", "stalkFrequency", "submissionList",
        ],
        "programs.toml": [
            "method", "methodLine", "methodNames", "targetProgram",
            "nboKeylist", "mixedBasisVariants",
            "potCube", "denCube", "valenceCube", "spinCube",
            "coreLineVariants", "ramLineVariants", "terminationVariants",
            "gaussianNonVariant", "orcaNonVariant", "qChemNonVariant",
        ],
        "extensions.toml": [
            "singlePointExtra", "reRunExtra",
            "coordExtension", "gaussianExtension", "orcaExtension",
            "qChemExtension", "cubeExtension", "queueExtension", "outputExtension",
        ],
        "notifications.toml": [
            "isNotifications",
            "botToken",
            "chatID",
            "broadcastGroupChatID",
            "broadcastThreshold",
        ],
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
    }

    # Per-key documentation written as a comment above each key in the
    # generated TOML files. Keys without an entry here get no comment.
    _COMMENTS: dict[str, str] = {
        "binDirectory": "Directory containing CompUtils executables and config files.",
        "CPU": "Number of CPU cores to request per job.",
        "memoryRatio": "Memory-to-CPU ratio for standard jobs (GB per core).",
        "highMemoryRatio": "Memory-to-CPU ratio for high-memory jobs, e.g. DLPNO (GB per core).",
        "memoryBuffer": "Additional memory headroom added on top of the computed request (GB).",
        "wallTime": "Default wall time (hours).",
        "cluster": "Default cluster for job submission. REQUIRED — program will error at startup if unset.",
        "partition": "Default partition for job submission. REQUIRED — program will error at startup if unset.",
        "hpcType": "HPC identity (H2P, Stampede3, Bridges2). REQUIRED.",
        "stalkDuration": "How long (minutes) before job stalking times out without looping.",
        "stalkFrequency": "How often (minutes) to ping the queue while stalking.",
        "submissionList": "SLURM header lines for job submission. Set automatically by hpcType.",
        "method": "Default DFT method for single-point calculations.",
        "methodLine": "Full method/basis string written into input files.",
        "methodNames": "Ordered list of known method names. Index must match targetProgram.",
        "targetProgram": "Program that runs methodNames[i]. Must be the same length as methodNames.",
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
        "orcaNonVariant": "ORCA 6.X SLURM script boilerplate written verbatim into job files.",
        "qChemNonVariant": "Q-Chem SLURM script boilerplate written verbatim into job files.",
        "singlePointExtra": "Filename suffix appended to single-point calculation jobs.",
        "reRunExtra": "Filename suffix appended to re-run jobs.",
        "coordExtension": "Coordinate file extension.",
        "gaussianExtension": "Gaussian16 input file extension.",
        "orcaExtension": "ORCA 6.X input file extension.",
        "qChemExtension": "Q-Chem input file extension.",
        "cubeExtension": "Cube file extension.",
        "queueExtension": "Job queue/submission script extension.",
        "outputExtension": "Program output file extension.",
        "isNotifications": "Master switch: set to true to enable Telegram notifications.",
        "botToken": "Telegram bot API token (from @BotFather). Treat as a secret.",
        "chatID": "Your personal Telegram chat ID (auto-detected during setup).",
        "broadcastGroupChatID": "Telegram group chat ID for broadcast queue alerts.",
        "broadcastThreshold": "Minimum jobs in a single submission to trigger a broadcast alert.",
    }

    # Keys listed here get the _warningBox comment block inserted immediately
    # above them in the generated TOML, alerting users not to edit carelessly.
    _WARNINGS: dict[str, str] = {
        "gaussianNonVariant": _warningBox,
        "orcaNonVariant": _warningBox,
        "qChemNonVariant": _warningBox,
    }

    # ── Class methods ──────────────────────────────────────────────────────────

    @classmethod
    def Load(cls) -> None:
        """
        Load all TOML config files and apply their values to this class.

        1. Use the current cls.binDirectory (hardcoded default on first run)
             as the config directory. paths.toml is loaded first, so if the
             user has changed binDirectory, all subsequent files are read from
             the correct location.

        2. For each file in _FILE_GROUPS:
               - If the file is absent: generate it from current defaults and
                 write it to disk. Skip loading (values are already correct).
               - If the file exists: parse it with tomllib and call
                 _ApplySection(), which walks the key list and applies each
                 value via setattr(). Missing keys trigger a warning and the
                 hardcoded default is kept.

        3. Call _Validate() to check required fields. Add your
             cluster / partition / hpcType checks there.
        """
        for filename in cls._FILE_GROUPS:
            # Re-evaluate configDir on each iteration: if paths.toml just
            # updated binDirectory, subsequent files use the new location.
            configDir = Path(cls.binDirectory)
            filePath = configDir / filename

            if not filePath.exists():
                cprint(
                    f"[config] {filename} not found — generating from hardcoded defaults.",
                    "light_yellow"
                )
                cls._SaveSection(filename)
                # File was just written with current defaults; nothing to load.
                continue

            data = loadToml(configDir, filename)
            if data:
                missingKeys = cls._ApplySection(data, filename)
                if missingKeys:
                    cls._AppendMissing(filename, missingKeys)

        cls._Validate()

    @classmethod
    def _SaveSection(cls, filename: str) -> None:
        """
        Build and write a single TOML config file from current class attributes.

        Called automatically by Load() when a file is missing (first run), and
        also called by firstTimeSetup() after it sets the HPC-specific defaults,
        so that the user's selection is persisted to slurm.toml.

        Uses _BuildContent() to serialize the relevant attributes, then hands
        the string to _WriteToml() for the actual file write.
        """
        configDir = Path(cls.binDirectory)
        content = cls._BuildContent(filename)
        writeToml(configDir, filename, content)

    @classmethod
    def _BuildContent(cls, filename: str) -> str:
        """
        Serialize one config section to a TOML-formatted string.

        Iterates the attribute names listed in _FILE_GROUPS[filename] in order.
        For each key:
          - If it appears in _WARNINGS, the warning box is inserted first.
          - If it appears in _COMMENTS, a '#'-prefixed comment line is added.
          - The key = value line is written using _TomlValue() for correct
            TOML syntax (escaping shell-script strings, formatting lists, etc.)
        """
        header = cls._HEADERS[filename]
        keys = cls._FILE_GROUPS[filename]
        lines = [header, ""]

        for key in keys:
            value = getattr(cls, key)
            # Insert power-user warning block before flagged keys
            if key in cls._WARNINGS:
                lines.append(cls._WARNINGS[key])
                lines.append("")
            # Insert per-key documentation comment
            commentText = cls._COMMENTS.get(key, "")
            if commentText:
                lines.append(f"# {commentText}")
            lines.append(f"{key} = {tomlValue(value)}")
            lines.append("")

        return "\n".join(lines)

    @classmethod
    def _ApplySection(cls, data: dict, filename: str) -> list[str]:
        """
        Apply a parsed TOML dict to this class's attributes.

        Iterates _FILE_GROUPS[filename] and for each key:
          - If the key is present in data: setattr() updates the class attribute
            in place. Because these are class-level attributes (not instance
            attributes), the change is immediately visible everywhere in the
            program that reads Defaults.something.
          - If the key is absent: _WarnMissing() issues a UserWarning and the
            hardcoded default is left untouched.
        """
        missing = []
        for key in cls._FILE_GROUPS[filename]:
            if key in data:
                setattr(cls, key, data[key])
            else:
                # Collect for append rather than just warning
                missing.append(key)
                cprint(f"[config] Key '{key}' not found in {filename}. "
                       f"Falling back to hardcoded default: {getattr(cls, key)!r}", "light_yellow")
        return missing

    @classmethod
    def _AppendMissing(cls, filename: str, missingKeys: list[str]) -> None:
        # Opens the existing file in append mode and writes only the keys
        # that were absent, each with its documentation comment. The rest
        # of the file is untouched, so user customization is preserved.
        configDir = Path(cls.binDirectory)
        filePath = configDir / filename
        try:
            with open(filePath, "a", encoding="utf-8") as f:
                for key in missingKeys:
                    if key in cls._WARNINGS:
                        f.write(f"\n{cls._WARNINGS[key]}\n")
                    commentText = cls._COMMENTS.get(key, "")
                    if commentText:
                        f.write(f"\n# {commentText}\n")
                    f.write(f"{key} = {tomlValue(getattr(cls, key))}\n")
            cprint(f"[config] Appended {len(missingKeys)} missing key(s) to {filename}.", "light_yellow")
        except OSError as e:
            cprint(f"[config] Could not append missing keys to {filePath}: {e}", "light_red")

    @classmethod
    def _Validate(cls) -> None:
        if len(cls.hpcType) == 0:
            firstTimeSetup()
        pass

class Stampede3Submission:
    # JobName OutputName Error Nodes Partition Time
    submissionList = ["#!/usr/bin/env bash","#SBATCH -J ","#SBATCH -o ","#SBATCH -e error.%j","#SBATCH -N 1 -n 1",
                      "#SBATCH -p ","#SBATCH -t "]
    hpcType = "Stampede3"

class H2PSubmission:
    # JobName OutputName Nodes CPUs Mem Time Cluster Partition
    submissionList = ["#!/bin/bash -l","#SBATCH -J ","#SBATCH -o ","#SBATCH -N 1",
                      "#SBATCH --ntasks-per-node=","#SBATCH --mem=","#SBATCH -t ","#SBATCH -M ","#SBATCH -p "]
    hpcType = "H2P"

class Bridges2Submission:
    # JobName Nodes Partition NTasks Time
    submissionList = ["#!/bin/csh","#SBATCH -J ","#SBATCH -N 1","#SBATCH -p ",
                      "#SBATCH --ntasks-per-node=","#SBATCH -t "]
    hpcType = "Bridges2"

# Defines global variables for use in various functions
fileNames, fullPaths, totalJobList, totalOutputs, methodLine, fullMethodLine = [], [], [], [], [], []
isStalking, isLooping, isCheck, isNBO, isCustomTarget, canBench = False, False, False, False, True, True
indexOverride = 0
fileExtension = ""
# Paired together for program identification and input processing
methodList, targetProgram, booleanStrings = [], [], ["y","n"]
stalkingSet = set()

# NEW!! Attempting to keep track of everything related to a job in one central location
# This allows for file name, extension, charge, multiplicity, and coordinate list to be edited and stored on a per-complex basis
class Molecule:
    def __init__(self, fullPath, baseName, charge, multiplicity, coordinateList, extensionType, rootName):
        self.fullPath = fullPath
        self.baseName = baseName
        self.charge = charge
        self.multiplicity = multiplicity
        self.coordinateList = coordinateList
        self.extensionType = extensionType
        self.rootName = rootName

# Globally checks for benchmarking and programs data, limiting functionality and alerting user
if os.path.isfile(os.path.join(Defaults.binDirectory, "benchmarking.txt")):
    with open(os.path.join(Defaults.binDirectory, "benchmarking.txt"), "r") as methodFile:
        for line in methodFile:
            fullMethodLine.append(line)
            methodLine.append(line.strip().split()[0])
else:
    canBench = False
    fullMethodLine = Defaults.methodLine
    methodLine = Defaults.method
    cprint("Notice: Could not find benchmarking.txt in ~/bin/.", "light_red")
    cprint("Benchmarking functionality is unavailable without requisite file. Please create your own or download "
           "the template from GitHub.", "light_red")

if os.path.isfile(os.path.join(Defaults.binDirectory, "programs.txt")):
    with open(os.path.join(Defaults.binDirectory, "programs.txt"), 'r') as programFile:
        for targetLine in programFile:
            currentSubs = targetLine.strip().split(" ")
            methodList.append(currentSubs[0])
            targetProgram.append(currentSubs[1])
else:
    isCustomTarget = False
    methodList = Defaults.methodNames
    targetProgram = Defaults.targetProgram
    cprint("Notice: Could not find programs.txt in ~/bin/.", "light_red")
    cprint("Defaulting to hardcoded method targets.", "light_red")

# Load all TOML config files from ~/bin/ and apply them to the Defaults class.
#
# On first run: each missing TOML file is generated from the hardcoded defaults
# above and written to disk. The user should then edit them to match their HPC
# environment. If hpcType / cluster / partition are empty after loading,
# _Validate() will detect that and can call firstTimeSetup() (defined just above).
#
# On subsequent runs: each file is parsed and its values are applied to Defaults
# via setattr(), so the rest of the program sees the user's configured values.
# Any key absent from a file triggers a warning and the hardcoded default is kept.
#
# This call replaces the old hpc.type file check. hpc.type is no longer used.
Defaults.Load()


def _SendTelegram(botToken: str, chatID: str, message: str) -> bool:
    """Send a message via the Telegram Bot API.

    Uses only urllib (stdlib) — no pip dependencies. Supports HTML
    formatting (<b>bold</b>, <i>italic</i>, <code>monospace</code>)
    via parse_mode="HTML".

    Returns True on success, False on failure. Never raises — prints
    a warning on error so the caller doesn't need its own try/except.
    """
    url = f"https://api.telegram.org/bot{botToken}/sendMessage"
    payload = json.dumps({
        "chat_id": chatID,
        "text": message,
        "parse_mode": "HTML"
    }).encode("utf-8")

    request = Request(
        url, data=payload,
        headers={"Content-Type": "application/json"}
    )
    try:
        with urlopen(request, timeout=10) as response:
            return response.status == 200
    except (URLError, HTTPError, TimeoutError) as e:
        cprint(f"  [Notification] Telegram send failed: {e}", "light_red")
        return False


def _DetectTelegramChatID(botToken: str, timeoutSeconds: int = 60) -> str | None:
    """Auto-detect the user's Telegram chat ID.

    How it works:
      1. Clears stale updates — messages the bot received before this
         function was called. Without this, the bot might pick up an old
         message and return the wrong chat ID.
      2. Polls the getUpdates endpoint in a loop. Telegram supports
         long-polling: the server holds the connection open and responds
         instantly when a new message arrives, so this doesn't spam the API.
      3. Returns the chat ID from the first new message it sees.

    Returns None if no message is received within timeoutSeconds.
    """
    baseURL = f"https://api.telegram.org/bot{botToken}"

    # ── Step 1: Flush stale updates ────────────────────────────────
    # Request the most recent update (offset=-1), then acknowledge it
    # by requesting offset = update_id + 1. This tells Telegram to
    # discard everything up to that point.
    try:
        clearURL = f"{baseURL}/getUpdates?offset=-1"
        with urlopen(clearURL, timeout=10) as response:
            data = json.loads(response.read())
            if data.get("result"):
                lastUpdateID = data["result"][-1]["update_id"]
                ackURL = (
                    f"{baseURL}/getUpdates"
                    f"?offset={lastUpdateID + 1}"
                )
                urlopen(ackURL, timeout=10)
    except Exception:
        pass  # Best-effort; detection may still work without flushing

    # ── Step 2: Poll for the user's new message ───────────────────
    print("\n  Waiting for your message (60 seconds)...")
    startTime = time.time()

    while time.time() - startTime < timeoutSeconds:
        try:
            # Long-poll with 5-second timeout: Telegram holds the
            # connection open and responds immediately when a message
            # arrives, so we're not hammering the API.
            pollURL = f"{baseURL}/getUpdates?timeout=5"
            with urlopen(pollURL, timeout=15) as response:
                data = json.loads(response.read())
                for update in data.get("result", []):
                    chatID = (
                        update
                        .get("message", {})
                        .get("chat", {})
                        .get("id")
                    )
                    if chatID:
                        return str(chatID)
        except Exception:
            time.sleep(2)

    return None


def NotifyPersonal(message: str) -> None:
    """Send a personal DM to the current user via Telegram.

    Reads Defaults.botToken and Defaults.chatID directly — no separate
    config file to load. Exits silently if notifications are disabled
    or credentials are missing.

    Example usage in stalk loop or job completion handler:
        NotifyPersonal(f"Job {jobName} finished successfully.")
        NotifyPersonal(f"Job {jobName} FAILED — check the log.")
    """
    if not Defaults.isNotifications:
        return
    if Defaults.botToken and Defaults.chatID:
        _SendTelegram(Defaults.botToken, Defaults.chatID, message)


def NotifyBroadcast(message: str) -> None:
    """Post a broadcast message to the shared Telegram group.

    Used for queue-wide alerts (e.g., someone submitted 100 jobs).
    Reads Defaults.botToken and Defaults.broadcastGroupChatID.
    Exits silently if notifications are disabled or the group ID
    isn't configured.
    """
    if not Defaults.isNotifications:
        return
    if Defaults.botToken and Defaults.broadcastGroupChatID:
        _SendTelegram(
            Defaults.botToken,
            Defaults.broadcastGroupChatID,
            f"\U0001F4E2 Queue Alert\n{message}"
        )


def CheckAndBroadcast(jobCount: int) -> None:
    """Broadcast a queue alert if jobCount >= Defaults.broadcastThreshold.

    Call this from RunJob (or equivalent) after submitting jobs:

        CheckAndBroadcast(len(submittedJobs))

    Does nothing if notifications are disabled, credentials are missing,
    or the count is below the threshold. Fails silently.
    """
    if not Defaults.isNotifications:
        return
    if jobCount >= Defaults.broadcastThreshold:
        NotifyBroadcast(
            f"{jobCount} jobs were just submitted to the queue. "
            f"Expect slower turnaround for a while."
        )


# ── Setup Wizard ───────────────────────────────────────────────────────────
def NotificationSetup() -> None:
    """Interactive setup wizard for Telegram notifications.

    Walks the user through:
      1. Opting in or out (sets Defaults.isNotifications)
      2. Entering the shared bot token (from lab admin)
      3. Auto-detecting their personal chat ID
      4. Sending a test notification to confirm it works
      5. Persisting everything to notifications.toml via _SaveSection

    Uses only input() and print(), so it works in any terminal
    (SSH sessions, SLURM interactive shells, etc.).

    Call from firstTimeSetup(). The wizard handles the yes/no prompt
    internally, so it won't force notifications on users who say no —
    it just leaves isNotifications = False and saves the file.
    """
    print("\n" + "=" * 55)
    print("  Notification Setup (Telegram)")
    print("=" * 55)

    print("\n  CompUtils can send you Telegram notifications when")
    print("  your jobs finish, and alert the group when someone submits a large batch to the queue.")

    # ── Opt in / out ───────────────────────────────────────────────
    while True:
        choice = input(
            "\n  Enable notifications? [Y/n]: "
        ).strip().lower()
        if choice in ("", "y", "n"):
            break
        print("  Please enter Y or N.")

    if choice == "n":
        # isNotifications stays False (the hardcoded default).
        # Save the file so Load() doesn't re-prompt on next run.
        Defaults._SaveSection("notifications.toml")
        print("\n  Notifications disabled.")
        print("  Re-run setup or edit notifications.toml to enable later.")
        print("=" * 55)
        return

    Defaults.isNotifications = True

    # ── Bot token ──────────────────────────────────────────────────
    print("\n  You'll need the bot token from your lab admin.")
    print("  (Enter 'q' to quit and come back later.)")

    botToken = input("\n  Bot token: ").strip()

    if botToken.lower() == "q":
        # Save with isNotifications = True but empty token so the user
        # doesn't get re-prompted by Load(), just needs to fill in the
        # token manually or re-run setup.
        Defaults._SaveSection("notifications.toml")
        print("\n  Setup paused. Run again when you have the token.")
        print("=" * 55)
        return

    # Basic format check: Telegram tokens always contain a colon
    # separating the bot ID from the secret portion.
    if ":" not in botToken:
        cprint(
            "\n  Warning: that doesn't look like a valid bot token",
            "light_yellow"
        )
        print("  (expected format: 123456789:ABCdef...).")

        while True:
            proceed = input(
                "  Continue anyway? [y/N]: "
            ).strip().lower()
            if proceed in ("", "n"):
                Defaults._SaveSection("notifications.toml")
                print("  Setup cancelled.")
                print("=" * 55)
                return
            if proceed == "y":
                break
            print("  Please enter Y or N.")

    Defaults.botToken = botToken

    # ── Auto-detect chat ID ────────────────────────────────────────
    print("\n  Now open Telegram and send any message to the bot.")

    chatID = _DetectTelegramChatID(botToken, timeoutSeconds=60)

    if chatID:
        Defaults.chatID = chatID
        cprint(f"\n  \u2713 Chat ID detected: {chatID}", "light_green")

        # ── Test message ───────────────────────────────────────────
        testChoice = input(
            "\n  Send a test message? [Y/n]: "
        ).strip().lower()
        if testChoice != "n":
            success = _SendTelegram(
                botToken, chatID,
                "\u2713 Welcome to remote queue notifications with CompUtils!"
            )
            if success:
                cprint(
                    "  \u2713 Test sent! Check Telegram.", "light_green"
                )
            else:
                cprint(
                    "  \u2717 Test failed. Double-check the bot token.",
                    "light_red"
                )
    else:
        cprint("\n  \u2717 Detection timed out.", "light_red")
        print("  This usually means the bot token is wrong, or")
        print("  Telegram couldn't be reached from this machine.")

        manualID = input(
            "\n  Enter your chat ID manually"
            " (or press Enter to skip): "
        ).strip()
        if manualID:
            Defaults.chatID = manualID
        else:
            print("  Personal notifications won't work, but")
            print("  broadcast alerts will still go to the group.")
            print("  Re-run setup or edit notifications.toml later.")

    # ── Persist to notifications.toml ──────────────────────────────
    # This is the same pattern as firstTimeSetup() calling
    # Defaults._SaveSection("slurm.toml") — set the attributes,
    # then save the whole section to disk.
    Defaults._SaveSection("notifications.toml")

    cprint(
        f"\n  \u2713 Saved to {Defaults.binDirectory}/notifications.toml",
        "light_green"
    )
    print("\n  Reminders:")
    print("    \u2022 notifications.toml contains your bot token —")
    print("      DO NOT EDIT THIS FOR ANY REASON.")
    print("    \u2022 Join the lab's broadcast group for queue alerts!")
    print("\n" + "=" * 55)

def firstTimeSetup():
    systemType = str(input("Enter the name of the HPC cluster you are using (H2P, Expanse, Bridges2, Stampede3) :"))
    match systemType:
        case "H2P":
            Defaults.hpcType, Defaults.partition, Defaults.cluster = "H2P", "pliu", "smp"
            Defaults.submissionList = H2PSubmission.submissionList
        case "Bridges2":
            Defaults.hpcType, Defaults.partition = "Bridges2", "RM-shared"
            Defaults.submissionList = Bridges2Submission.submissionList
            Defaults.memoryRatio, Defaults.memoryBuffer, Defaults.highMemoryRatio = 2, 0, 2
        case "Expanse":
            cprint("CompUtils is not supported on the Expanse architecture due to being outdated and messy. Have a good day.", "light_red")
        case "Stampede3":
            Defaults.hpcType, Defaults.partition = "Stampede3", "icx"
            Defaults.CPU, Defaults.memoryRatio, Defaults.memoryBuffer, Defaults.highMemoryRatio = 80, 200/80, 0, 200/80
            Defaults.submissionList = Stampede3Submission.submissionList
        case _:
            cprint("Unknown HPC architecture input. Aborting.", "light_red")
            return
    # Persist the just-configured SLURM defaults to slurm.toml so that
    # Load() reads the correct values on all future runs.
    Defaults._SaveSection("slurm.toml")
    NotificationSetup()

# Defines all the terminal flags the program can accept
def commandLineParser():
    global isStalking, isCheck, isNBO, indexOverride, isLooping

    parser = argparse.ArgumentParser(description="The main command line argument parser for flag handling")

    #The various flags for defining the features of this utility
    parser.add_argument('-r', '--run', type=str, help="Indicates the 'run' subroutine for the given filelist.")
    parser.add_argument('-sp', '--singlePoint', type=str, help="Indicates the 'single point' subroutine for"
                                                               " the listed file(s)")
    parser.add_argument('-b', '--bench', type=str, help="Indicates the 'benchmark' subroutine, for creating"
                                                        " a single point benchmark")
    parser.add_argument('-ch', '--checkpoint', action='store_true', help="Enables checkpoint functionality "
                                                                         "for the job creation subroutines.")
    parser.add_argument('-cu','--cube', type=str, help="Indicates the gimmeCubes functionality on a given "
                                                       "Gaussian16 checkpoint file.")
    parser.add_argument('-st','--stalk', action='store_true', help="Activates job stalking.")
    parser.add_argument('-ex', '--excel', type=str, help="Indicates the goodVibesToExcel functionality on a"
                                                         " given GoodVibes output file.")
    parser.add_argument('-ovr', '--override', type=int, help="Indicates an integer override for indexing, used"
                                                             " for accessing single point methods that are not the first"
                                                             " line in a controlled manner. Keep in mind index counting "
                                                             "starts from 0, not from 1.")
    parser.add_argument('-gv', '--goodvibes', action='store_true', help="Activates the CompUtils interactive interface for GoodVibes."
                                                                        " This acts upon all files in the CWD.")
    parser.add_argument('-nbo','--nbo7',action='store_true',help="Enables NBO7 keylist addition for job creation subroutines.")
    parser.add_argument('-re','--rerun',type=str,help="Reruns a failed Gaussian16 job using the failed output"
                                                      " to generate the new input file.")
    parser.add_argument('-form','--formcheck',type=str,help="Activates the Gaussian16 formchk utility without"
                                                            " full passthrough into gimmeCubes.")
    parser.add_argument('-first','--first',action='store_true',help="Activates first-time set-up again.")

    # Figures out what the hell you told it to do
    args = parser.parse_args()

    # Flags that set bools come first
    if args.stalk:
        isStalking = True
        willLoop = str(input("Enable stalk looping (i.e. re-initialize until all jobs terminate)?  (y/n): )"))
        if willLoop == booleanStrings[0]:
            isLooping = True
    if args.checkpoint:
        isCheck = True
    if args.nbo7:
        isNBO = True
    if args.override:
        indexOverride = args.override
        cprint("Registered " + str(indexOverride) + " as the index override.", "light_cyan")
    if args.first:
        firstTimeSetup()

    if args.run:
        # Compiles the entire list of files to run (built-in 'runall' capabilities)
        jobList = glob.glob(args.run)
        # Builds the molecule object per complex in input
        for job in jobList:
            baseName, extension = grabPaths(job)
            newMolecule = Molecule(job, baseName, 0, 0, 0, extension, baseName)
            runJob(newMolecule)

    if args.singlePoint:
        jobList = glob.glob(args.singlePoint)
        for job in jobList:
            baseName, extension = grabPaths(job)
            charge, multiplicity = gaussianChargeFinder(job)
            coordList = getCoords(job,baseName + Defaults.coordExtension)
            newMolecule = Molecule(job, baseName, charge, multiplicity, coordList, extension, baseName)
            genSinglePoint(newMolecule)

    if args.bench:
        if canBench:
            jobList = glob.glob(args.bench)
            for job in jobList:
                baseName, extension = grabPaths(job)
                charge, multiplicity = gaussianChargeFinder(job)
                coordList = getCoords(job,baseName + Defaults.coordExtension)
                newMolecule = Molecule(job, baseName, charge, multiplicity, coordList, extension, baseName)
                genBench(newMolecule)
        else:
            cprint("Notice: Benchmarking is unavailable without requisite file. Please create your own or download "
                "the template from GitHub.", "light_red")

    if args.cube:
        # Needs to run interactively in order to be useful
        cubeList = str(input("Enter the list of options you want for cube files generated, separated by spaces (e.g. Pot"
            " Den Val Spin or Range): "))
        jobList = glob.glob(args.cube)
        # This splits the entered keylist into separate keys, passed into gimmeCubes as an array which can be iterated through
        cubeOptions = cubeList.split(" ")
        os.system("module purge")
        os.system("module load gaussian")
        for job in jobList:
            baseName, extension = grabPaths(job)
            newMolecule = Molecule(job, baseName, 0, 0, 0, extension, baseName)
            if extension == ".chk":
                formCheck(newMolecule)
            gimmeCubes(newMolecule, cubeOptions)

    if args.formcheck:
        jobList = glob.glob(args.formcheck)
        os.system("module purge")
        os.system("module load gaussian")
        for job in jobList:
            baseName, extension = grabPaths(job)
            newMolecule = Molecule(job, baseName, 0, 0, 0, extension, baseName)
            formCheck(newMolecule)

    if args.goodvibes:
        cprint("Interactive GoodVibes interface activated. Please select your keylist from the common ones.", "light_cyan")
        totalKeyList = goodVibesInteractive()
        os.system("goodvibes " + totalKeyList + " *.out")
        cprint("GoodVibes has terminated. Handing output over to the excel exporter.", "light_cyan")
        goodVibesProcessor("Goodvibes_output.dat")
        cprint("Enjoy your Excel-formatted GoodVibes output!", "light_green")

    if args.rerun:
        jobList = glob.glob(args.rerun)
        keylistOrder = str(input("Is your input structured as 'opt freq FUNCTIONAL' (y) or 'FUNCTIONAL other keys' (n)? :"))
        if keylistOrder == booleanStrings[0]:
            skipIndex = 2
        else:
            skipIndex = 0
        for job in jobList:
            baseName, extension = grabPaths(job)
            charge, multiplicity = gaussianChargeFinder(job)
            coordList = getCoords(job,baseName + "_failed" + Defaults.coordExtension)
            newMolecule = Molecule(job, baseName, charge, multiplicity, coordList, extension, baseName)
            genReRun(newMolecule,skipIndex)

# Finally handle filename creation in one place to stop the infinite copypasta
def fileCreation(baseName, extensionType, extra):
    if not len(extra) == 0:
        fullFile = baseName + extra + extensionType
    else:
        fullFile = baseName + extensionType
    return fullFile

# Formats checkpoints automatically
def formCheck(molecule):
    os.system("formchk " + molecule.fullPath)
    molecule.extensionType = ".fchk"
    molecule.fullPath = molecule.rootName + molecule.extensionType

# A new fully pythonic solution to coordinate scraping, agnostic of the PERL bullshit on H2P
def getCoords(fileName, outputFileName):
    coordinateList = []
    atSymbol = {
        1: 'H', 2: 'He', 3: 'Li', 4: 'Be', 5: 'B', 6: 'C', 7: 'N', 8: 'O', 9: 'F', 10: 'Ne',
        11: 'Na', 12: 'Mg', 13: 'Al', 14: 'Si', 15: 'P', 16: 'S', 17: 'Cl', 18: 'Ar', 19: 'K',
        20: 'Ca', 21: 'Sc', 22: 'Ti', 23: 'V', 24: 'Cr', 25: 'Mn', 26: 'Fe', 27: 'Co', 28: 'Ni',
        29: 'Cu', 30: 'Zn', 31: 'Ga', 32: 'Ge', 33: 'As', 34: 'Se', 35: 'Br', 36: 'Kr',

        42: 'Mo', 44: 'Ru', 45: 'Rh', 46: 'Pd', 47: 'Ag', 48: 'Cd', 50: 'Sn', 51: 'Sb',
        53: 'I', 54: 'Xe', 77: 'Ir', 78: 'Pt', 79: 'Au', 80: 'Hg', 81: 'Tl', 82: 'Pb',
        83: 'Bi'
    }

    # Initialize local empty lists
    at, X, Y, Z = [], [], [], []

    with open(fileName, 'r+') as inFile, open(outputFileName, 'w') as outputFile:
        # Maps the file into memory for reading byte-wise, without any read buffer. AFAIK this is the most memory efficient
        # way to be able to read files of any size
        with closing(mmap(inFile.fileno(), 0, access=ACCESS_READ)) as data:
            tableHeader = "                         Standard orientation:                         "
            tableBytes = tableHeader.encode()
            finalTableHeader = regex.search(tableBytes, data, regex.REVERSE)
            # Finds where the header ends, sets that as the pointer, and reads ahead two bytes to skip over the newline character
            pointer = finalTableHeader.ends()
            data.seek(pointer[0])
            data.read(2)
            for index in range(4):
                data.readline()
            line = data.readline().decode().strip()
            while len(line.split()) > 2:
                # Extracts the Atomic Number, and X Y Z coordinates into their respective lists
                at.append(str(line.split()[1]))
                X.append(str(line.split()[3]))
                Y.append(str(line.split()[4]))
                Z.append(str(line.split()[5]))
                line = data.readline().decode().strip()

        outputFile.write(str(len(at))+"\nPointless Comment Line\n")
        for k in range(len(at)):
            # Ensures the list elements are integers for dictionary pairing
            at[k] = int(at[k])
            # Translates from Atomic Number to Atomic Symbol and builds the entire line to be written with proper formatting
            coordLine = str(atSymbol[at[k]]) + "   " + str(X[k]) + "   " + str(Y[k]) + "   " + str(Z[k]) + "\n"
            coordLine = coordLine.replace(' ', ' ')
            outputFile.write(coordLine)
            coordinateList.append(coordLine)
    return coordinateList

# Handles extensions so I don't have to copypasta this
def extensionGetter(method):
    global fileExtension

    programTarget = ""
    for x in range(len(methodList)):
        if method == methodList[x]:
            programTarget = targetProgram[x]
    match programTarget:
        case "G16":
            fileExtension = Defaults.gaussianExtension
        case "O":
            fileExtension = Defaults.orcaExtension
        case "Q":
            fileExtension = Defaults.qChemExtension
        case _:
            cprint("Notice: One or more of your intended methods is not specified in programs file nor hardcoded. "
                   "Defaulting to Gaussian16.","light_red")
            fileExtension = Defaults.gaussianExtension
    return fileExtension

# Gaussian16 Charge Finder in its own method
def gaussianChargeFinder(geometryFile):
    chargeLine = "Charge"
    chargeLineBytes = chargeLine.encode()
    with open(geometryFile, 'r') as geomFile:
        with closing(mmap(geomFile.fileno(), 0, access=ACCESS_READ)) as data:
            chargeLineLocation = regex.search(chargeLineBytes, data)
            pointer = chargeLineLocation.starts()
            data.seek(pointer[0])
            targetLine = data.readline().decode()
            chargeSub = targetLine.strip().split()
            # This chunk handles the special case where a stupid non-breaking space is used for neutral charges?
            if chargeSub[2] == '':
                del chargeSub[2]
            charge = chargeSub[2]
            multiplicity = chargeSub[5]
    return charge, multiplicity

# This subroutine returns file name and extension for ease-of-use
def grabPaths(fileName):
    if os.path.exists(fileName):
        baseName, extension = os.path.basename(fileName).split(".")
        extension = "." + extension
        return baseName, extension
    else:
        cprint("Could not locate: " + fileName, "light_red")
        return None, None

def genBench(molecule):
    # First, make the original Single Point
    genSinglePoint(molecule)
    global indexOverride
    # Since methodFile is defined globally, no need to iterate a line to catch-up after genSinglePoint
    startTime = time.time()
    if indexOverride != 0:
        indexShift = indexOverride + 1
    else:
        indexShift = 1
    for index in range(indexShift, len(methodLine)):
        molecule.extensionType = extensionGetter(methodLine[index])
        isSMD = regex.search("smd", fullMethodLine[index], regex.IGNORECASE)
        if isSMD:
            filemaskExtra = (f"-{index}-" + methodLine[index].replace("(", "").replace(")", "")
                + "SMD" + Defaults.singlePointExtra)
        else:
            filemaskExtra = (f"-{index}-" + methodLine[index].replace("(","").replace(")","")
                + Defaults.singlePointExtra)
        inputFile = fileCreation(molecule.rootName, molecule.extensionType, filemaskExtra)
        molecule.fullPath = inputFile
        molecule.baseName = (molecule.rootName + filemaskExtra)
        genFile(molecule,index)
        runJob(molecule)
    endTime = time.time()
    totalTime = round(endTime - startTime,2)
    cprint("Total time for non-SP benchmark generation is: " + str(totalTime) + " seconds.", "light_cyan")

def genSinglePoint(molecule):
    startTime = time.time()

    # Update molecule properties
    molecule.extensionType = extensionGetter(methodLine[0])
    inputFile = fileCreation(molecule.baseName, molecule.extensionType, Defaults.singlePointExtra)
    molecule.fullPath = inputFile
    molecule.baseName = molecule.baseName + Defaults.singlePointExtra
    if indexOverride != 0:
        index = indexOverride
    else:
        index = 0

    # Calls the separate file generation method, feeds directly into runJob
    genFile(molecule, index)
    runJob(molecule)
    endTime = time.time()
    totalTime = round(endTime - startTime,2)
    cprint("Total single point time is " + str(totalTime) + " seconds.", "light_cyan")

# Separate method for input file generation to improve code efficiency. No longer returns anything as path to input is
# previously stored in molecule
def genFile(molecule, index):
    global isCheck, isNBO
    inputFile = molecule.fullPath
    mixedBasis = False
    match molecule.extensionType:
        case Defaults.gaussianExtension:
            # No longer accesses the XYZ file due to Molecule coordinateList property
            with open(inputFile, 'w') as jobInput:
                # Sets the job's CPU and RAM
                jobCPU = str(Defaults.CPU)
                jobMem = str(Defaults.CPU * Defaults.memoryRatio)
                # Writes the standard Gaussian16 formatted opening
                jobInput.write("%nprocshared=" + jobCPU + "\n%mem=" + jobMem + "GB")
                if isCheck:
                    jobInput.write("\n%chk=" + molecule.baseName + ".chk")
                # If the methodLine from benchmarking.txt is garbage, the calculation will fail. Not my fault.
                jobInput.write("\n# " + fullMethodLine[index].replace("\n","") + "\n\nUseless Comment line\n\n")
                jobInput.write(molecule.charge + " " + molecule.multiplicity + "\n")
                # New mixed basis checking
                for keyWord in fullMethodLine[index].split():
                    if keyWord in Defaults.mixedBasisVariants:
                        mixedBasis = True
                        break
                # Accessing the stored coordinate list is significantly faster in run-time than prior crappy implementation
                for line in molecule.coordinateList:
                    jobInput.write(line)
                # Adds in mixed basis info from local file
                if os.path.isfile("mixedbasis.txt") and mixedBasis:
                    jobInput.write("\n")
                    with open("mixedbasis.txt") as mixedBasisFile:
                        for line in mixedBasisFile:
                            jobInput.write(line)
                elif mixedBasis and not os.path.isfile("mixedbasis.txt"):
                    cprint("Mixed basis information not found. Aborting.", "light_red")
                    return
                jobInput.write("\n")
                # New NBO7 section
                if isNBO:
                    jobInput.write(Defaults.nboKeylist + " FILE=" + molecule.baseName + " ARCHIVE $END")
                else:
                    jobInput.write("\n")

        case Defaults.orcaExtension:
            # Opens the job file
            with open(inputFile, 'w') as jobInput:
                # Sets the job's CPU and RAM
                jobCPU = str(Defaults.CPU)
                if methodLine == "DLPNO-CCSD(T)":
                    jobMem = str(Defaults.highMemoryRatio * 1000)
                else:
                    jobMem = str(Defaults.memoryRatio * 1000)
                # Writes the standard ORCA formatted opening
                jobInput.write("%pal nprocs " + jobCPU + "\nend" + "\n%maxcore " + jobMem)
                # If the methodLine from benchmarking.txt is garbage, the calculation will fail. Not my fault.
                jobInput.write("\n! " + fullMethodLine[index].replace("\n","") + "\n\n")
                # ORCA is smart enough to read from an XYZ directly
                jobInput.write(f"* xyz {molecule.charge} {molecule.multiplicity} \n")
                for line in molecule.coordinateList:
                    jobInput.write(line)
                jobInput.write("\n*")

        case Defaults.qChemExtension:
            pass

# Reorganized! Now handles SLURM commands independently because of HPC cluster agnosticism
def slurmHandler(molecule,queueName,outputName,firstFiveLines):
    coresLine, ramLine, cpus, jobRam = "", "", 0, 0
    match molecule.extensionType:
        case Defaults.gaussianExtension:
            for line in firstFiveLines:
                if regex.search(Defaults.coreLineVariants[0], line) or regex.search(Defaults.coreLineVariants[1], line):
                    coresLine = line
                if regex.search(Defaults.ramLineVariants[0], line):
                    ramLine = line
            if len(coresLine) == 0:
                cpus = Defaults.CPU
                cprint("Couldn't find CPU count in input file. Submitting instead according to Defaults.", "light_red")
            else:
                cpus = coresLine.strip().split("=")[1]
            if len(ramLine) == 0:
                ram = cpus * Defaults.memoryRatio
                jobRam = ram + Defaults.memoryBuffer
                cprint("Couldn't find RAM count in input file. Submitting instead according to Defaults.", "light_red")
            else:
                ram = int(ramLine.strip().split("=")[1].replace("GB", ""))
                jobRam = ram + Defaults.memoryBuffer

        case Defaults.orcaExtension:
            for line in firstFiveLines:
                if regex.search(Defaults.coreLineVariants[2], line):
                    coresLine = line
                if regex.search(Defaults.ramLineVariants[1], line):
                    ramLine = line
            if len(coresLine) == 0:
                cpus = Defaults.CPU
                cprint("Couldn't find CPU count in input file. Submitting instead according to Defaults.",
                       "light_red")
            else:
                cpus = coresLine.strip().split()[2]
            if len(ramLine) == 0:
                ram = cpus * Defaults.memoryRatio
                jobRam = ram + Defaults.memoryBuffer
                cprint("Couldn't find RAM count in input file. Submitting instead according to Defaults.",
                       "light_red")
            else:
                ram = int(ramLine.strip().split()[1]) / 1000
                jobRam = int(int(cpus) * ram + Defaults.memoryBuffer)

        case Defaults.qChemExtension:
            for line in firstFiveLines:
                if regex.search(Defaults.coreLineVariants[0], line) or regex.search(Defaults.coreLineVariants[1], line):
                    coresLine = line
                if regex.search(Defaults.ramLineVariants[0], line):
                    ramLine = line
            if len(coresLine) == 0:
                cpus = Defaults.CPU
                cprint("Couldn't find CPU count in input file. Submitting instead according to Defaults.", "light_red")
            else:
                cpus = coresLine.strip().split("=")[1]
            if len(ramLine) == 0:
                ram = cpus * Defaults.memoryRatio
                jobRam = ram + Defaults.memoryBuffer
                cprint("Couldn't find RAM count in input file. Submitting instead according to Defaults.", "light_red")
            else:
                ram = int(ramLine.strip().split("=")[1].replace("GB", ""))
                jobRam = ram + Defaults.memoryBuffer

        case _:
            cpus = Defaults.CPU
            jobRam = cpus * Defaults.memoryRatio + Defaults.memoryBuffer

    with open(queueName, 'w') as outputFile:
        for line in Defaults.submissionList:
            if regex.search("-J", line):
                outputFile.write(line + str(molecule.baseName) + "\n")
            elif regex.search("-o", line):
                outputFile.write(line + outputName + "\n")
            elif regex.search("--ntasks", line):
                outputFile.write(line + str(cpus) + "\n")
            elif regex.search("--mem", line):
                outputFile.write(line + str(jobRam) + "GB\n")
            elif regex.search("-t", line):
                outputFile.write(line + Defaults.wallTime + ":00:00\n")
            elif regex.search("-p", line):
                outputFile.write(line + Defaults.partition + "\n")
            elif regex.search("-M", line):
                outputFile.write(line + Defaults.cluster + "\n")
            else:
                outputFile.write(line + "\n")

# This routine is for job submission to the cluster
def runJob(molecule):
    global isStalking, stalkingSet

    # Sets up all the basic filenames for the rest of submission
    outputName = molecule.baseName + Defaults.outputExtension
    queueName = molecule.baseName + Defaults.queueExtension

    # A potential minor speed uplift would be the closing(mmap()) implementation used basically everywhere else, since
    # I've learned just how fast it is. Probably not necessary, though
    with open(molecule.fullPath, 'r+') as inputFile:
        firstFiveLines = []
        # Reads the first line of the file
        currentLine = inputFile.readline().strip()
        firstFiveLines.append(currentLine)

        # Craps out if the first line doesn't exist, or is entirely blank
        if not currentLine:
            cprint(f"Job file " + molecule.baseName + " is empty. Terminating submission attempt.", "light_red")
        if len(currentLine) == 0:
            cprint(f"First line of job file " + molecule.baseName + " is blank. Terminating submission attempt.", "light_red")

        for index in range(0,4):
            firstFiveLines.append(inputFile.readline().strip())

        slurmHandler(molecule, queueName, outputName, firstFiveLines)

        match molecule.extensionType:
            case Defaults.gaussianExtension:
                with open(queueName, 'a') as outputFile:
                    for nonVariantLine in Defaults.gaussianNonVariant:
                        outputFile.write(nonVariantLine)
                    outputFile.write("\ng16 < " + molecule.fullPath + "\n\n")

                os.system("sbatch " + queueName)
                #os.remove(queueName)
                cprint(f"Submitted job " + molecule.baseName + " to Gaussian16", "light_green")
                if isStalking:
                    molecule.fullPath = molecule.baseName + Defaults.outputExtension
                    stalkingSet.add((molecule.baseName,molecule.fullPath))

            case Defaults.orcaExtension:
                with open(queueName, 'a') as outputFile:
                    # Now runs in ORCA 6.0.1 instead of 4.2.0
                    for index in range(0,3):
                        outputFile.write(Defaults.orcaNonVariant[index])
                    outputFile.write("files=(" + str(molecule.baseName + Defaults.orcaExtension) + ")\n")
                    for index in range(3,8):
                        outputFile.write(Defaults.orcaNonVariant[index])
                    outputFile.write("$(which orca) " + str(molecule.baseName + Defaults.orcaExtension) + "\n\n")
                    for index in range(8,10):
                        outputFile.write(Defaults.orcaNonVariant[index])

                os.system("sbatch " + queueName)
                #os.remove(queueName)
                cprint(f"Submitted job " + molecule.baseName + " to ORCA 6.0.1", "light_green")
                if isStalking:
                    molecule.fullPath = molecule.baseName + Defaults.outputExtension
                    stalkingSet.add((molecule.baseName,molecule.fullPath))

            # This will need updated to the modern architecture at some point
            case Defaults.qChemExtension:
                with open(queueName, 'a') as outputFile:
                    outputFile.write("module purge\nmodule load qchem/6.3.0-pliu\n\n\n")
                    # This crap was in the original and I have no clue if it's still necessary
                    # output.write("export QCSCRATCH=$LOCAL\n")
                    # output.write("export QC=/ihome/pliu/xiq23/qchem/qchem_for_peng_20151014\n")
                    # output.write("export PATH=$PATH:$QC/bin\n")
                    # output.write("export QCAUX=/ihome/pliu/xiq23/qchem/qcaux4\n")
                    outputFile.write("# Change to working directory\n")
                    outputFile.write("cp $SLURM_SUBMIT_DIR/" + molecule.fullPath + " $SLURM_SCRATCH\n")
                    outputFile.write("cd $SLURM_SCRATCH\n\n")
                    # No clue what this line is and if it's needed, it's not in the Q-Chem documentation
                    outputFile.write("df -h\n")

                    # Re-wrote the script in order to make this line *actually* match the Q-Chem documentation
                    # Re-wrote to allow nthreads to match ncpu since each thread only runs on 1 CPU (see Q-Chem manual)
                    # Do NOT specify outfile, it LITERALLY breaks shit for some reason
                    #outputFile.write("qchem -slurm -nt " + str(cpus) + " " + molecule.fullPath + " " + "\n")
                    outputFile.write("du -h\n\n")

                os.system("sbatch " + queueName)
                #os.remove(queueName)
                cprint(f"Submitted job " + molecule.baseName + " to Q-Chem 6.3", "light_green")
                if isStalking:
                    molecule.fullPath = molecule.baseName + Defaults.outputExtension
                    stalkingSet.add((molecule.baseName,molecule.fullPath))

# Better, interactive implementation of my own gimmeCubesv3
def gimmeCubes(molecule, cubeKeyList):
    keyWord, queueName, outputName = "", "", ""
    for cubeKey in cubeKeyList:
        match cubeKey:
            case Defaults.spinCube:
                outputName = fileCreation(molecule.baseName, Defaults.cubeExtension, cubeKey)
                queueName = fileCreation(molecule.baseName, Defaults.queueExtension, cubeKey)
                keyWord = "Spin=SCF"
            case Defaults.denCube:
                outputName = fileCreation(molecule.baseName, Defaults.cubeExtension, cubeKey)
                queueName = fileCreation(molecule.baseName, Defaults.queueExtension, cubeKey)
                keyWord = "Density=SCF"
            case Defaults.potCube:
                outputName = fileCreation(molecule.baseName, Defaults.cubeExtension, cubeKey)
                queueName = fileCreation(molecule.baseName, Defaults.queueExtension, cubeKey)
                keyWord = "Potential=SCF"
            case Defaults.valenceCube:
                outputName = fileCreation(molecule.baseName, Defaults.cubeExtension, cubeKey)
                queueName = fileCreation(molecule.baseName, Defaults.queueExtension, cubeKey)
                keyWord = "MO=Valence"
            case "Range":
                orbitalRange = str(input("Enter the range of MOs you want printed (e.g. 10-15) : "))
                outputName = fileCreation(molecule.baseName, Defaults.cubeExtension, cubeKey + orbitalRange)
                queueName = fileCreation(molecule.baseName, Defaults.queueExtension, cubeKey + orbitalRange)
                keyWord = "MO=" + orbitalRange
            case _:
                cprint("Error: Unknown keyword found in keylist for " + molecule.baseName + " : " + cubeKey, "light_red")

        slurmHandler(molecule,queueName,outputName,[])

        with open(queueName,"a") as queueFile:
            for nonVariantLine in Defaults.gaussianNonVariant:
                queueFile.write(nonVariantLine)

            # Writes the specifics for running the Density Cube
            queueFile.write("cubegen 1 " + keyWord + " " + molecule.fullPath + " " + outputName + " 0""\n\n")

        os.system("sbatch " + queueName)
        #os.remove(queueName)
        cprint(f"Submitted cube job " + molecule.baseName + " " + cubeKey + " to the cluster.", "light_green")

# Because jobs don't always work the first time
def genReRun(molecule,skipIndex):
    with open(molecule.fullPath,"r") as inputFile:
        with closing(mmap(inputFile.fileno(),0,access=ACCESS_READ)) as data:
            preTable = "Will use up to"
            preBytes = preTable.encode()
            originalMethod = regex.search(preBytes,data)
            pointer = originalMethod.ends()
            data.seek(pointer[0])
            data.read(2)
            for index in range(0,3):
                data.readline()
            originalMethod = data.readline().decode()

    fullMethodLine[0] = originalMethod.replace("#","").strip()
    methodLine[0] = originalMethod.replace("#","").strip().split()[skipIndex]
    molecule.extensionType = extensionGetter(methodLine[0])
    inputFile = fileCreation(molecule.baseName, molecule.extensionType, Defaults.reRunExtra)
    molecule.fullPath = inputFile
    molecule.baseName = molecule.baseName + Defaults.reRunExtra

    # Calls the separate file generation method, feeds directly into runJob
    genFile(molecule, 0)
    runJob(molecule)

# Finally implemented in a way I can be proud of.
def jobStalking(jobSet, duration, frequency):
    startTime = time.time()
    # Prints queue in format of JOBNAME STATUS NODE/REASON START_TIME CURRENT_DURATION courtesy of my own improved
    # obsessiveQueuev2
    command = ["squeue -h --me --format='%25j %10T %18R %S %20M'"]
    finishedJobs = []
    # Repeats every frequency over duration
    pingTime = time.time()
    while (pingTime - startTime) < duration * 60:
        stalkStatus = set()
        # Tells the function that jobs you submitted are the ones to track
        for job in jobSet:
            stalkStatus.add(job[0])
        # Executes the qeueue command for further processing
        stalker = subprocess.run(command, shell=True, capture_output=True)
        result = stalker.stdout.splitlines()
        for index in range(len(result)):
            line = result[index].decode("utf-8")
            # noinspection PyTypeChecker
            result[index] = line
            # Adds job basename to stalkStatus for comparison
            stalkStatus.add(result[index].split()[0])

        # The heart of the magic, iterates over the output of the queue command using JOBNAME, STATUS, and CURRENT_DURATION
        for index in range(len(result)):
            match result[index].split()[1]:
                case "PENDING":
                    cprint("Job " + str(result[index].split()[0]) + " is currently pending. Expected start time is "
                       + str(result[index].split()[3]), "light_yellow")
                    stalkStatus.remove(result[index].split()[0])
                case "RUNNING":
                    for job in jobSet:
                        convergeCriteria = "Unknown"
                        if job[0] == result[index].split()[0]:
                            if os.path.isfile(job[1]) and os.path.getsize(job[1]) > 0:
                                with open(job[1],'r+') as file:
                                    with closing(mmap(file.fileno(),0,access=ACCESS_READ)) as data:
                                        hasStability = "Stability analysis"
                                        hasStabBytes = hasStability.encode()
                                        isStable = "The wavefunction is already stable."
                                        isStabBytes = isStable.encode()
                                        containsStability = regex.search(hasStabBytes, data)
                                        if containsStability is not None:
                                            hasStabilized = regex.search(isStabBytes, data, regex.REVERSE)
                                            if hasStabilized is not None:
                                                stabilityInsert = "Wavefunction has stabilized."
                                            else:
                                                stabilityInsert = "Wavefunction has not stabilized."
                                        else:
                                            stabilityInsert = ""
                                        # Checks for convergence section header, defaults to Unknown or Not Found
                                        tableHeader = "         Item               Value     Threshold  Converged?"
                                        tableBytes = tableHeader.encode()
                                        finalTableHeader = regex.search(tableBytes, data, regex.REVERSE)
                                        if finalTableHeader is not None:
                                            if len(finalTableHeader.group().decode()) != 0:
                                                convergeCriteria = 0
                                                pointer = finalTableHeader.ends()
                                                data.seek(pointer[0])
                                                data.read(2)
                                                convergeMet = []
                                                for outdex in range(0,4):
                                                    convergeLine = data.readline().decode()
                                                    convergeMet.append(convergeLine.split()[4])
                                                    convergeCriteria = convergeMet.count("YES")
                                            cprint("Job " + str(result[index].split()[0]) + " is currently running, and "
                                                "has converged on " + str(convergeCriteria) + " out of 4 criteria.\n    "
                                                   + stabilityInsert + " Current duration is " + str(result[index].split()[4]),
                                                   "light_magenta")
                                            stalkStatus.remove(result[index].split()[0])
                                        else:
                                            cprint("Job " + str(result[index].split()[0]) + " is currently running. Convergence "
                                                "criterion header not found.\n    " + stabilityInsert + " Current duration is "
                                                   + str(result[index].split()[4]),"light_magenta")
                                            stalkStatus.remove(result[index].split()[0])
                            break

        jobCopy = jobSet.copy()

        # Finds how the job terminated and stores the data
        for job in jobCopy:
            # If the output is created during the execution of the subroutine, it won't be detected in the prior
            # block and it will be size 0
            if job[0] in stalkStatus and os.path.isfile(job[1]) and os.path.getsize(job[1]) > 0:
                with open(job[1], "r+") as file:
                    with closing(mmap(file.fileno(), 0, access=ACCESS_READ)) as data:
                        for termination in Defaults.terminationVariants:
                            termBytes = termination.encode()
                            termLine = regex.search(termBytes, data, regex.IGNORECASE)
                            if termLine is not None:
                                finishedJobs.append((job[0], termination))
                                NotifyPersonal(f"Job {job} has finished!")
                                break
                jobSet.remove(job)
            elif job[0] in stalkStatus and os.path.isfile(job[1]) and os.path.getsize(job[1]) == 0:
                cprint("Job " + job[0] + " started running during stalk subroutine execution.", "light_magenta")

        # Reports job termination data
        for job in finishedJobs:
            if job[1] == Defaults.terminationVariants[0] or job[1] == Defaults.terminationVariants[1]:
                cprint("Job " + str(job[0]) + " has encountered " + Defaults.terminationVariants[0],"light_green")
            if job[1] == Defaults.terminationVariants[2]:
                cprint("Job " + str(job[0]) + " has encountered " + Defaults.terminationVariants[2], "light_red")

        # If all jobs for stalking are done, finish execution and release the terminal
        if len(jobSet) == 0:
            cprint("All jobs tagged for stalking have finished.","light_cyan")
            break


        lastPing = time.strftime("%a %I:%M:%S",time.localtime(pingTime))
        cprint("Waiting " + str(frequency*60) + " seconds to ping the queue again. Last ping at " + lastPing
               + " local time.","light_blue")
        time.sleep(frequency * 60)
        pingTime = time.time()

        # Timeout warning
        if (pingTime - startTime) > duration * 60:
            if isLooping:
                startTime = pingTime
            else:
                cprint("Job stalking terminated by timeout. Your jobs are still running.","light_red")
                cprint("Consider editing the default stalk duration and frequency if your jobs regularly timeout.","light_red")

# Because everyone hates remembering manuals. Walks through the most common use-cases with catch-all final custom keylist
def goodVibesInteractive():
    keyList = ["-v 1.0"]
    isQuasiHarmonic = str(input("Utilize quasiharmonic S and H correction (Grimme)? (y/n)"))
    if isQuasiHarmonic == booleanStrings[0]:
        keyList.append("-q")
    isFreqCut = str(input("Utilize a frequency cutoff? (y/n)"))
    if isFreqCut == booleanStrings[0]:
        freqCutoff = float(input("Enter the frequency cutoff (wavenumbers): "))
        keyList.append("-f " + str(freqCutoff))
    isTempCorrection = str(input("Utilize a temperature correction? (y/n)"))
    if isTempCorrection == booleanStrings[0]:
        tempCorrection = float(input("Enter temperature (K): "))
        keyList.append("-t " + str(tempCorrection))
    isConcCorrection = str(input("Utilize a concentration correction? (y/n)"))
    if isConcCorrection == booleanStrings[0]:
        concCorrection = float(input("Enter concentration (mol/l): "))
        keyList.append("-c " + str(concCorrection))
    isVibeScale = str(input("Utilize a non-default (i.e. not 1.0) vibrational scale factor? (y/n)"))
    if isVibeScale == booleanStrings[0]:
        vibeScale = float(input("Enter vibrational scale factor: "))
        keyList.append("-v " + str(vibeScale))
    isSinglePoint = str(input("Run program with single point corrections? (y/n)"))
    if isSinglePoint == booleanStrings[0]:
        isNonDefault = str(input("Is your filemask pattern different than the CompUtils default (_SP)? (y/n)"))
        if isNonDefault == booleanStrings[0]:
            singlePoint = str(input("Enter your filemask pattern without the underscore:"))
            keyList.append("--spc " + str(singlePoint))
        else:
            keyList.append("--spc SP")
    isNonCommonKeys = str(input("Do you want to run with additional, less common keys? (y/n)"))
    if isNonCommonKeys == booleanStrings[0]:
        nonCommonKeys = str(input("Enter all of your non-common keys exactly as GoodVibes must receive them, separated by spaces."))
        keyList.append(nonCommonKeys)
    finalKeyList = ""
    for key in range(len(keyList)):
        finalKeyList = finalKeyList + " " + keyList[key]
    return finalKeyList

# An improved version of goodVibesToExcelv3 that now properly formats the numbers in Excel as numbers
def goodVibesProcessor(inputFile):
    outputData = []
    header = "Structure"
    headerBytes = header.encode()
    with open(inputFile, 'r') as inFile:
        with closing(mmap(inFile.fileno(), 0, access=ACCESS_READ)) as data:
            headerLocation = regex.search(headerBytes, data, regex.IGNORECASE)
            pointer = headerLocation.starts()
            data.seek(pointer[0])
            line = data.readline()
            tempSubs = line.decode().strip().split()
            outputData.append(tempSubs)
            data.readline()
            line = data.readline().decode().strip()
            while '*' not in line:
                subLines = line.split()
                subLines.pop(0)
                outputData.append(subLines)
                line = data.readline().decode().strip()
    dataFrame = pandas.DataFrame(outputData)
    # Sets the headers to the table header from GoodVibes
    dataFrame.columns = dataFrame.iloc[0]
    # Removes the header from the rest of the data
    dataFrame = dataFrame[1:]
    # This whole block is just to get the numbers to number properly
    writer = pandas.ExcelWriter("GoodVibes.xlsx", engine='xlsxwriter', engine_kwargs={'options': {'strings_to_numbers': True}})
    dataFrame.to_excel(writer, index=False)
    workBook = writer.book
    workSheet = writer.sheets['Sheet1']
    formatNumber = workBook.add_format({'num_format': '#,##0.000000'})
    workSheet.set_column('B:J', 12, formatNumber)
    writer.close()

commandLineParser()
#print(sys.orig_argv)

if isStalking:
    jobStalking(stalkingSet, Defaults.stalkDuration, Defaults.stalkFrequency)