"""
Intent classes describe what a user wants CompUtils to do.

The flow is:
    CLI argparse / TUI screens
        -> populate an IntentDraft
        -> validate it
        -> .Finalize() to a concrete Intent subclass
        -> dispatch.Dispatch(intent) executes it

This module declares the types. The Step-4 refactor wires them up.
"""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .actions import Action, CubeOption


# ─── Base ─────────────────────────────────────────────────────────────

@dataclass
class Intent:
    """Abstract base for all dispatchable intents. Never instantiated directly."""
    pass


# ─── SLURM-submitting intents ──────────────────────────────────────────

@dataclass
class JobIntent(Intent):
    """Base for any intent that submits one or more SLURM jobs.

    The modifier fields (stalk, stalkLoop, checkpoint, nbo7, indexOverride)
    correspond to today's -st, the stalk-loop sub-prompt, -ch, -nbo, -ovr.
    """
    files:         list[Path]
    stalk:         bool = False
    stalkLoop:     bool = False
    checkpoint:    bool = False
    nbo7:          bool = False
    indexOverride: int  = 0


@dataclass
class RunIntent(JobIntent):
    """`cu -r <pattern>` — submit existing input files as-written."""
    pass


@dataclass
class SinglePointIntent(JobIntent):
    """`cu -sp <pattern>` — generate single-point input, then submit."""
    pass


@dataclass
class BenchmarkIntent(JobIntent):
    """`cu -b <pattern>` — generate the full benchmark suite, then submit each."""
    pass


@dataclass
class ReRunIntent(JobIntent):
    """`cu -re <pattern>` — regenerate failed-job input, then submit.

    skipIndex answers the sub-prompt: "Is your input 'opt freq FUNCTIONAL' (2)
    or 'FUNCTIONAL other keys' (0)?"
    """
    skipIndex: int = 0


@dataclass
class CubeIntent(JobIntent):
    """`cu -cu <pattern>` — generate cube files via cubegen.

    orbitalRange is required iff CubeOption.RANGE is in cubeOptions.
    """
    cubeOptions:  list[CubeOption] = field(default_factory=list)
    orbitalRange: Optional[str]    = None


# ─── Non-SLURM intents ─────────────────────────────────────────────────

@dataclass
class FormCheckIntent(Intent):
    """`cu -form <pattern>` — run formchk directly (no SLURM submission)."""
    files: list[Path]


@dataclass
class ExcelIntent(Intent):
    """`cu -ex <file>` — convert a Goodvibes_output.dat to xlsx."""
    inputFile: Path


@dataclass
class GoodVibesIntent(Intent):
    """`cu -gv` — run goodvibes against *.out in CWD, then convert to xlsx.

    All fields here correspond to today's goodVibesInteractive() prompts.
    """
    quasiharmonic:      bool            = False
    freqCutoff:         Optional[float] = None
    tempCorrection:     Optional[float] = None   # K
    concCorrection:     Optional[float] = None   # mol/L
    vibeScale:          Optional[float] = None
    singlePointPattern: Optional[str]   = None   # e.g. "SP"
    extraKeys:          Optional[str]   = None   # raw passthrough


@dataclass
class FirstTimeSetupIntent(Intent):
    """`cu -first` — re-run the HPC + notification setup wizard."""
    pass


@dataclass
class UpdateIntent(Intent):
    """`cu --update` — re-install CompUtils from GitHub."""
    branch: str = "main"


# ─── Mutable draft (TUI assembles this progressively) ──────────────────

@dataclass
class IntentDraft:
    """
    Accumulator used by the TUI as the user answers prompts.
    Convert to a typed Intent via Finalize() after Validate() returns no errors.

    The CLI also uses this — argparse populates the draft, validation runs,
    then Finalize() produces the concrete Intent. Same flow as the TUI;
    just a different way of filling the fields.
    """
    action: Optional[Action] = None

    # File-bearing actions (RUN, SP, BENCH, CUBE, RERUN, FORM_CHECK)
    files: list[Path] = field(default_factory=list)

    # SLURM modifiers (only meaningful when action ∈ JobIntent subclasses)
    stalk:         bool = False
    stalkLoop:     bool = False
    checkpoint:    bool = False
    nbo7:          bool = False
    indexOverride: int  = 0

    # RERUN
    skipIndex: int = 0

    # CUBE
    cubeOptions:  list[CubeOption] = field(default_factory=list)
    orbitalRange: Optional[str]    = None

    # EXCEL
    excelInputFile: Optional[Path] = None

    # GOODVIBES
    quasiharmonic:      bool            = False
    freqCutoff:         Optional[float] = None
    tempCorrection:     Optional[float] = None
    concCorrection:     Optional[float] = None
    vibeScale:          Optional[float] = None
    singlePointPattern: Optional[str]   = None
    extraKeys:          Optional[str]   = None

    # UPDATE
    updateBranch: str = "main"

    def Validate(self) -> list[str]:
        """Return human-readable errors. Empty list means valid."""
        errors: list[str] = []
        if self.action is None:
            return ["No action selected."]

        fileActions = {
            Action.RUN, Action.SINGLE_POINT, Action.BENCHMARK,
            Action.CUBE, Action.RERUN, Action.FORM_CHECK,
        }
        if self.action in fileActions and not self.files:
            errors.append("No matching files for the given pattern.")

        if self.action == Action.CUBE:
            if not self.cubeOptions:
                errors.append("Select at least one cube option.")
            if CubeOption.RANGE in self.cubeOptions and not self.orbitalRange:
                errors.append("Orbital range required when 'Range' is selected.")

        if self.action == Action.EXCEL and self.excelInputFile is None:
            errors.append("Input file required for Excel conversion.")

        return errors

    def Finalize(self) -> Intent:
        """Caller MUST have validated first.

        Returns a concrete Intent subclass corresponding to self.action,
        with only the fields relevant to that action populated.
        """
        jobKwargs = dict(
            files=self.files, stalk=self.stalk, stalkLoop=self.stalkLoop,
            checkpoint=self.checkpoint, nbo7=self.nbo7,
            indexOverride=self.indexOverride,
        )

        match self.action:
            case Action.RUN:
                return RunIntent(**jobKwargs)
            case Action.SINGLE_POINT:
                return SinglePointIntent(**jobKwargs)
            case Action.BENCHMARK:
                return BenchmarkIntent(**jobKwargs)
            case Action.RERUN:
                return ReRunIntent(**jobKwargs, skipIndex=self.skipIndex)
            case Action.CUBE:
                return CubeIntent(
                    **jobKwargs,
                    cubeOptions=self.cubeOptions,
                    orbitalRange=self.orbitalRange,
                )
            case Action.FORM_CHECK:
                return FormCheckIntent(files=self.files)
            case Action.EXCEL:
                return ExcelIntent(inputFile=self.excelInputFile)
            case Action.GOODVIBES:
                return GoodVibesIntent(
                    quasiharmonic=self.quasiharmonic,
                    freqCutoff=self.freqCutoff,
                    tempCorrection=self.tempCorrection,
                    concCorrection=self.concCorrection,
                    vibeScale=self.vibeScale,
                    singlePointPattern=self.singlePointPattern,
                    extraKeys=self.extraKeys,
                )
            case Action.FIRST_TIME_SETUP:
                return FirstTimeSetupIntent()
            case Action.UPDATE:
                return UpdateIntent(branch=self.updateBranch)
            case _:
                raise ValueError(f"Unknown action: {self.action}")

# ─── Smoke tests (run with: python -m computils.intent) ────────────────

if __name__ == "__main__":
    from pathlib import Path

    print("Smoke tests for intent.py")
    print("=" * 50)

    # Test 1: Valid run intent
    draft = IntentDraft()
    draft.action = Action.RUN
    draft.files = [Path("test.gjf")]
    draft.stalk = True
    errors = draft.Validate()
    assert errors == [], f"Test 1 failed: {errors}"
    intent = draft.Finalize()
    assert isinstance(intent, RunIntent), f"Test 1 type: {type(intent)}"
    assert intent.stalk is True
    print("Test 1 (valid RUN intent): PASS")

    # Test 2: Run intent with no files fails validation
    draft = IntentDraft()
    draft.action = Action.RUN
    draft.files = []
    errors = draft.Validate()
    assert errors, "Test 2 should have produced errors"
    assert any("No matching files" in e for e in errors), \
        f"Test 2 wrong error: {errors}"
    print("Test 2 (RUN with no files): PASS")

    # Test 3: Cube intent without RANGE option doesn't need orbitalRange
    draft = IntentDraft()
    draft.action = Action.CUBE
    draft.files = [Path("test.chk")]
    draft.cubeOptions = [CubeOption.POTENTIAL, CubeOption.DENSITY]
    errors = draft.Validate()
    assert errors == [], f"Test 3 errors: {errors}"
    intent = draft.Finalize()
    assert isinstance(intent, CubeIntent)
    assert CubeOption.RANGE not in intent.cubeOptions
    print("Test 3 (CUBE without RANGE): PASS")

    # Test 4: Cube intent WITH RANGE requires orbitalRange
    draft = IntentDraft()
    draft.action = Action.CUBE
    draft.files = [Path("test.chk")]
    draft.cubeOptions = [CubeOption.RANGE]
    errors = draft.Validate()
    assert errors, "Test 4 should have produced errors"
    assert any("Orbital range" in e for e in errors), \
        f"Test 4 wrong error: {errors}"
    print("Test 4 (CUBE with RANGE, no range): PASS")

    # Test 5: ReRun intent preserves skipIndex
    draft = IntentDraft()
    draft.action = Action.RERUN
    draft.files = [Path("failed.out")]
    draft.skipIndex = 2
    errors = draft.Validate()
    assert errors == [], f"Test 5 errors: {errors}"
    intent = draft.Finalize()
    assert isinstance(intent, ReRunIntent)
    assert intent.skipIndex == 2
    print("Test 5 (RERUN with skipIndex): PASS")

    # Test 6: GoodVibes intent has no files (acts on CWD)
    draft = IntentDraft()
    draft.action = Action.GOODVIBES
    draft.quasiharmonic = True
    draft.freqCutoff = 100.0
    errors = draft.Validate()
    assert errors == [], f"Test 6 errors: {errors}"
    intent = draft.Finalize()
    assert isinstance(intent, GoodVibesIntent)
    assert intent.quasiharmonic is True
    assert intent.freqCutoff == 100.0
    print("Test 6 (GOODVIBES with options): PASS")

    # Test 7: Action-specific fields aren't carried into wrong intents
    draft = IntentDraft()
    draft.action = Action.RUN
    draft.files = [Path("test.gjf")]
    draft.skipIndex = 99   # this is for RERUN, should be ignored by Finalize
    intent = draft.Finalize()
    assert isinstance(intent, RunIntent)
    assert not hasattr(intent, "skipIndex"), \
        "RunIntent should not have skipIndex"
    print("Test 7 (irrelevant fields not propagated): PASS")

    print("=" * 50)
    print("All smoke tests passed.")