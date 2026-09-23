"""
Centralizes data and runtime state previously held in module-level globals.

Loaded explicitly from __main__.Main() at startup, after Defaults.Load().
In Step 4, the CLI-state attributes (isStalking, isCheck, isNBO,
indexOverride, isLooping, fileExtension) move into Intent fields; only
the loaded-data attributes (methodLine, methodList, etc.) stay here.
"""
from .defaults import Defaults


class Catalog:
    # ── Data derived from Defaults at startup ────────────────────────────────
    fullMethodLine = []
    methodLine = []
    methodList = []
    targetProgram = []

    # ── Capability flags (set during Load) ──────────────────────────────────
    canBench = True

    @classmethod
    def Load(cls) -> None:
        """Populate Catalog from Defaults (loaded from TOML config)."""
        # Method → program mapping
        cls.methodList    = list(Defaults.methodNames)
        cls.targetProgram = list(Defaults.targetProgram)

        # Benchmark method lines
        cls.fullMethodLine = list(Defaults.benchmarkMethods)
        cls.methodLine     = [line.strip().split()[0] for line in cls.fullMethodLine]
        cls.canBench       = len(cls.fullMethodLine) > 1