"""
Intent classes describe what a user wants CompUtils to do.

The flow is:
    CLI argparse / TUI screens
        -> populate an IntentDraft
        -> validate it
        -> .Finalize() to a concrete Intent subclass
        -> dispatch.Dispatch(intent) executes it

This module declares the types.
"""
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Optional

from .actions import Action, CubeOption, FILE_ACTIONS

# CLI flags indicate a specific task. The parser populates an IntentDraft field-by-field as it reads each flag —
# including setting draft.action to one of the Action enum values. After all fields are set, Validate() checks that the
# draft has everything the chosen Action requires; if so, Finalize() constructs the appropriate Intent subclass with only
# the relevant fields populated. Dispatch(intent) then runs the per-action handler.
# Each Intent subclass is a @dataclass that declares its own fields plus inherits fields from its parent class. Each
# instantiation produces an independent object with its own field values — modifying one Intent doesn't affect any other.
# The field(default_factory=list) calls ensure mutable defaults are created fresh per instance rather than shared."""


# ─── Base ─────────────────────────────────────────────────────────────
# This is the master class, shaping all subclasses as an Intent
@dataclass
class Intent:
    """Abstract base for all dispatchable intents. Never instantiated directly."""
    pass


# ─── SLURM-submitting intents ──────────────────────────────────────────
# Subclass Master for all batch-file operations (SLURM, etc.)
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

# Subclass of JobIntent, specific for runJob() by Action()
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

    Method detection is now automatic via ExtractRouteLine + IdentifyMethod.
    """
    pass


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
    """`cu -gv [FILE ...]` — run goodvibes on the given outputs (default: every output in the CWD), then convert to xlsx.

    analysis.GoodVibesArguments turns the fields into goodvibes flags. truhlarEntropy and checkConsistency are set by the TUI only.
    """
    files:              list[Path]
    headGordonEnthalpy: bool            = False  # -q
    truhlarEntropy:     bool            = False  # --qs truhlar (Grimme otherwise)
    checkConsistency:   bool            = False  # --check
    freqCutoff:         Optional[float] = None
    tempCorrection:     Optional[float] = None   # K
    concCorrection:     Optional[float] = None   # mol/L
    vibeScale:          Optional[float] = 1.0    # None: GoodVibes picks one from the level of theory
    singlePointPattern: Optional[str]   = None   # e.g. "SP"
    extraKeys:          Optional[str]   = None   # raw passthrough


@dataclass
class FirstTimeSetupIntent(Intent):
    """`cu -first` — re-run the HPC + notification setup wizard."""
    pass


@dataclass
class UpdateIntent(Intent):
    """`cu --update` — re-install CompUtils from GitHub."""
    branch: Optional[str] = None


@dataclass
class InitProjectIntent(Intent):
    """`cu -init` — mark the CWD as a project root."""
    pass


@dataclass
class ProfileIntent(Intent):
    """`cu -profile [FILE]` — apply a lab profile (None: re-apply the last one)."""
    profileFile: Optional[Path] = None


# The Intent each Action finalizes to. Excel and Update aren't listed: their fields are named differently on the draft
_INTENT_TYPES = {
    Action.RUN: RunIntent, Action.SINGLE_POINT: SinglePointIntent, Action.BENCHMARK: BenchmarkIntent,
    Action.RERUN: ReRunIntent, Action.CUBE: CubeIntent, Action.FORM_CHECK: FormCheckIntent,
    Action.GOODVIBES: GoodVibesIntent, Action.FIRST_TIME_SETUP: FirstTimeSetupIntent,
    Action.INIT_PROJECT: InitProjectIntent, Action.PROFILE: ProfileIntent,
}


# ─── Mutable draft (TUI assembles this progressively) ──────────────────
# Draft containing ALL options. This is what a parser will add arguments to, before Validating and Finalizing
# to a specific Intent type as shown above
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

    # File-bearing actions (RUN, SP, BENCH, CUBE, RERUN, FORM_CHECK, GOODVIBES)
    files: list[Path] = field(default_factory=list)

    # SLURM modifiers (only meaningful when action ∈ JobIntent subclasses)
    stalk:         bool = False
    stalkLoop:     bool = False
    checkpoint:    bool = False
    nbo7:          bool = False
    indexOverride: int  = 0

    # CUBE
    cubeOptions:  list[CubeOption] = field(default_factory=list)
    orbitalRange: Optional[str]    = None

    # EXCEL
    excelInputFile: Optional[Path] = None

    # GOODVIBES
    headGordonEnthalpy: bool            = False
    truhlarEntropy:     bool            = False
    checkConsistency:   bool            = False
    freqCutoff:         Optional[float] = None
    tempCorrection:     Optional[float] = None
    concCorrection:     Optional[float] = None
    vibeScale:          Optional[float] = 1.0
    singlePointPattern: Optional[str]   = None
    extraKeys:          Optional[str]   = None

    # UPDATE
    updateBranch: Optional[str] = None  # None: the branch CompUtils was installed from

    # PROFILE
    profileFile: Optional[Path] = None  # None: re-apply the last one

    # This validates the fields for an Action() wants from its Intent()
    def Validate(self) -> list[str]:
        """Return human-readable errors. Empty list means valid."""
        errors: list[str] = []
        if self.action is None:
            return ["No action selected."]

        if self.action in FILE_ACTIONS and not self.files:
            errors.append("No matching files for the given pattern.")

        if self.action == Action.CUBE:
            if not self.cubeOptions:
                errors.append("Select at least one cube option.")
            if CubeOption.RANGE in self.cubeOptions and not self.orbitalRange:
                errors.append("Orbital range required when 'Range' is selected.")

        if self.action == Action.EXCEL and self.excelInputFile is None:
            errors.append("Input file required for Excel conversion.")

        if self.action == Action.PROFILE and self.profileFile is not None and not self.profileFile.is_file():
            errors.append(f"No lab profile file at {self.profileFile}.")

        return errors

    def Finalize(self) -> Intent:
        """Caller MUST have validated first.

        Returns a concrete Intent subclass corresponding to self.action,
        with only the fields relevant to that action populated: each one is
        copied from the draft field of the same name.
        """
        match self.action:
            case Action.EXCEL:
                return ExcelIntent(inputFile=self.excelInputFile)
            case Action.UPDATE:
                return UpdateIntent(branch=self.updateBranch)
        intentType = _INTENT_TYPES.get(self.action)
        if intentType is None:
            raise ValueError(f"Unknown action: {self.action}")
        return intentType(**{entry.name: getattr(self, entry.name) for entry in fields(intentType)})

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

    # Test 5: ReRun intent finalizes correctly
    draft = IntentDraft()
    draft.action = Action.RERUN
    draft.files = [Path("failed.out")]
    errors = draft.Validate()
    assert errors == [], f"Test 5 errors: {errors}"
    intent = draft.Finalize()
    assert isinstance(intent, ReRunIntent)
    print("Test 5 (RERUN intent): PASS")

    # Test 6: GoodVibes intent takes files, and needs at least one
    draft = IntentDraft()
    draft.action = Action.GOODVIBES
    draft.headGordonEnthalpy = True
    draft.freqCutoff = 100.0
    assert draft.Validate(), "Test 6 should need files"
    draft.files = [Path("ethane.out")]
    errors = draft.Validate()
    assert errors == [], f"Test 6 errors: {errors}"
    intent = draft.Finalize()
    assert isinstance(intent, GoodVibesIntent)
    assert intent.headGordonEnthalpy is True
    assert intent.freqCutoff == 100.0 and intent.files == [Path("ethane.out")]
    print("Test 6 (GOODVIBES with options): PASS")

    # Test 7: Init project intent needs no files
    draft = IntentDraft()
    draft.action = Action.INIT_PROJECT
    errors = draft.Validate()
    assert errors == [], f"Test 7 errors: {errors}"
    intent = draft.Finalize()
    assert isinstance(intent, InitProjectIntent)
    print("Test 7 (INIT_PROJECT intent): PASS")

    # Test 8: Profile intent re-applies without a file, and needs an existing file when given one
    draft = IntentDraft()
    draft.action = Action.PROFILE
    assert draft.Validate() == [], "Test 8 bare -profile should be valid"
    assert draft.Finalize() == ProfileIntent(profileFile=None)
    draft.profileFile = Path("no-such-profile.toml")
    assert any("No lab profile file" in e for e in draft.Validate()), "Test 8 should reject a missing file"
    print("Test 8 (PROFILE intent): PASS")

    print("=" * 50)
    print("All smoke tests passed.")