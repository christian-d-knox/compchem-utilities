import os
from typing import Any, ClassVar
from pathlib import Path
import tomllib as tom

from .console import console

_warningBox = """\
# +-----------------------------------------------------------------------------+
# |  POWER USER SECTION — NonVariant job script blocks                          |
# |  These strings are written verbatim into generated SLURM job files.         |
# |  Incorrect edits WILL break job submission. Only modify if you know exactly |
# |  what you are doing and have verified the output manually.                  |
# +-----------------------------------------------------------------------------+"""


def tomlValue(value: Any) -> str:
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
    filePath = configDir / filename
    try:
        with open(filePath, "rb") as file:
            return tom.load(file)
    except tom.TOMLDecodeError as error:
        console.print(f"[config] Failed to parse {filename}: {error}\n"
            "         All hardcoded defaults will be used for this section.") #light_red error
        return {}


def writeToml(configDir: Path, filename: str, content: str) -> None:
    filePath = configDir / filename
    try:
        with open(filePath, "w", encoding="utf-8") as file:
            file.write(content)
        console.print(f"[config] Wrote config file: {filePath}") #light_cyan operation
    except OSError as error:
        console.print(f"[config] Could not write {filePath}: {error}\n"
            "         Hardcoded defaults will be used for this section.") #light_red error


def warnMissing(key: str, filename: str, fallback: Any) -> None:
    console.print(f"[config] Key '{key}' not found in {filename}. "
        f"Falling back to hardcoded default: {fallback!r}", "light_red") #light_red error


class Defaults:
    binDirectory = Path("~/bin").expanduser()
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
    needsFirstTimeSetup = False

    # What files contain what keys
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

    # Per-key documentation written as a comment above each key in the generated TOML files.
    # Keys without an entry here get no comment.
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

    # Keys listed here get the _warningBox comment block inserted immediately above them in the generated TOML,
    # alerting users not to edit carelessly.
    _WARNINGS: dict[str, str] = {
        "gaussianNonVariant": _warningBox,
        "orcaNonVariant": _warningBox,
        "qChemNonVariant": _warningBox,
    }


    @classmethod
    def Load(cls) -> None:
        for filename in cls._FILE_GROUPS:
            configDir = Path(cls.binDirectory)
            filePath = configDir / filename

            if not filePath.exists():
                console.print(f"[config] {filename} not found — generating from hardcoded defaults.") #light_yellow warning
                cls._SaveSection(filename)
                continue

            data = loadToml(configDir, filename)
            if data:
                missingKeys = cls._ApplySection(data, filename)
                if missingKeys:
                    cls._AppendMissing(filename, missingKeys)

        cls._Validate()


    @classmethod
    def _SaveSection(cls, filename: str) -> None:
        configDir = Path(cls.binDirectory)
        content = cls._BuildContent(filename)
        writeToml(configDir, filename, content)


    @classmethod
    def _BuildContent(cls, filename: str) -> str:
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
        missing = []
        for key in cls._FILE_GROUPS[filename]:
            if key in data:
                setattr(cls, key, data[key])
            else:
                missing.append(key)
                console.print(f"[config] Key '{key}' not found in {filename}. "
                       f"Falling back to hardcoded default: {getattr(cls, key)!r}") #light_yellow warning
        return missing


    @classmethod
    def _AppendMissing(cls, filename: str, missingKeys: list[str]) -> None:
        configDir = Path(cls.binDirectory)
        filePath = configDir / filename
        try:
            with open(filePath, "a", encoding="utf-8") as file:
                for key in missingKeys:
                    if key in cls._WARNINGS:
                        file.write(f"\n{cls._WARNINGS[key]}\n")
                    commentText = cls._COMMENTS.get(key, "")
                    if commentText:
                        file.write(f"\n# {commentText}\n")
                    file.write(f"{key} = {tomlValue(getattr(cls, key))}\n")
            console.print(f"[config] Appended {len(missingKeys)} missing key(s) to {filename}.") #light_yellow warning
        except OSError as error:
            console.print(f"[config] Could not append missing keys to {filePath}: {error}") #light_red error


    @classmethod
    def _Validate(cls) -> None:
        if len(cls.hpcType) == 0:
            #firstTimeSetup()
            cls.needsFirstTimeSetup = True
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