"""
Centralizes data and runtime state previously held in module-level globals.

Loaded explicitly from __main__.Main() at startup, after Defaults.Load().
In Step 4, the CLI-state attributes (isStalking, isCheck, isNBO,
indexOverride, isLooping, fileExtension) move into Intent fields; only
the loaded-data attributes (methodLine, methodList, etc.) stay here.
"""
from pathlib import Path
from .console  import console
from .defaults import Defaults


class Catalog:
    # ── Data loaded from text files at startup ──────────────────────────────
    fullMethodLine = []
    methodLine = []
    methodList = []
    targetProgram = []

    # ── Capability flags (set during Load) ──────────────────────────────────
    canBench = True
    isCustomTarget = True

    @classmethod
    def Load(cls) -> None:
        """Read benchmarking.txt and programs.txt from Defaults.binDirectory."""
        # benchmarking.txt
        benchPath = Defaults.binDirectory / Path("benchmarking.txt")
        if benchPath.is_file():
            with open(benchPath, "r") as methodFile:
                for line in methodFile:
                    cls.fullMethodLine.append(line)
                    cls.methodLine.append(line.strip().split()[0])
        else:
            cls.canBench = False
            # See note in walkthrough section 1.4 about the bug fix here.
            cls.fullMethodLine = [Defaults.methodLine]
            cls.methodLine     = [Defaults.method]
            console.print(
                f"[error]Notice: Could not find benchmarking.txt in {Defaults.binDirectory}.[/error]"
            )
            console.print(
                "[error]Benchmarking functionality is unavailable without "
                "requisite file. Please create your own or download the "
                "template from GitHub.[/error]"
            )

        # programs.txt
        progPath = Defaults.binDirectory / Path("programs.txt")
        if progPath.is_file():
            with open(progPath, "r") as programFile:
                for targetLine in programFile:
                    parts = targetLine.strip().split(" ")
                    cls.methodList.append(parts[0])
                    cls.targetProgram.append(parts[1])
        else:
            cls.isCustomTarget = False
            cls.methodList     = list(Defaults.methodNames)
            cls.targetProgram  = list(Defaults.targetProgram)
            console.print(
                f"[error]Notice: Could not find programs.txt in {Defaults.binDirectory}.[/error]"
            )
            console.print(
                "[error]Defaulting to hardcoded method targets.[/error]"
            )