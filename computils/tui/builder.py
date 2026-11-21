"""Builder: GoodVibes and Cube Files, one framed form showing only what the action uses (TUI_DESIGN.md mock-up 4.2,
D21). Single Point, Benchmark, Re-Run and Run as Written have their job screens (jobscreens.py, the Optimization
builder's template)."""
from pathlib import Path

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Checkbox, Input, Static

from ..actions  import Action, CubeOption
from ..analysis import (ALLOW_ALL, CHECK_CATEGORIES, DEFAULT_CONCENTRATION, DEFAULT_CUTOFF, DEFAULT_SCALE,
                         DEFAULT_TEMPERATURE, CheckArguments, GoodVibesArguments, SelectionSuffix, SpcPartners)
from ..defaults import Defaults
from ..intent   import IntentDraft
from .common    import (NAME, NAV_BINDINGS, TEXT, Column, FrameRule, KeyHint, NavFooter, Notice, OptionRow, Setting,
                        SettingsList)
from .home      import ACTION_LABELS, TitleLine
from .inspect   import Styled

RESOURCE_KEYS = [("CPU", "CPU"), ("memoryRatio", "Memory Ratio"), ("wallTime", "WallTime"), ("partition", "Partition")]
SHOWN_NAMES = 6                  # the Files section lists this many names, then "… +N More"


def NameList(paths: list[Path]) -> str:
    more = len(paths) - SHOWN_NAMES
    return ", ".join(path.name for path in paths[:SHOWN_NAMES]) + (f", … +{more} More" if more > 0 else "")


# ─── GoodVibes settings (mock-up 4.2, GoodVibes) ──────────────────────

# What each Allow row lets differ (analysis.CHECK_CATEGORIES)
ALLOW_MEANINGS = {"theory": "Levels of Theory May Differ", "solvation": "Solvation Models May Differ",
                  "dispersion": "Dispersion May Differ", "geometry": "Opt/SP Geometries May Differ"}

def GoodVibesSettings(suffix: str) -> list[Setting]:
    """Defaults match the CLI wizard's, and Single Point is on (suffix: detected, preset, or the default): GoodVibes
    runs once both the opt and the SP are done.
    This screen's text is in Title Case (Christian). A choice's notes read as one note across its rows, the later
    ones indented as its hanging lines."""
    return [
        Setting("temperature", "Temperature", f"Default {DEFAULT_TEMPERATURE:g} K", f"{DEFAULT_TEMPERATURE:g}", "K"),
        Setting("concentration", "Concentration", "Off: Gas Phase (1 atm)", f"{DEFAULT_CONCENTRATION:.1f}", "mol/l",
                on=False),
        Setting("grimme", "Entropy (qh-S)", "qh-G(T) Is Always Present (Required)", fixed="Grimme", on=True, choice="entropy"),
        Setting("truhlar", "", "  See G(T) for Uncorrected", fixed="Truhlar", on=False, choice="entropy"),
        Setting("enthalpy", "Enthalpy (qh-H)", "Off: RRHO Enthalpy", fixed="Head-Gordon", on=True),
        Setting("cutoff", "  └ qh Cutoff", "Softer Modes Are Corrected", f"{DEFAULT_CUTOFF:g}", "cm⁻¹", under="enthalpy"),
        Setting("scale", "Vib Scale Factor", "Off: GV Auto-Detect from Method", f"{DEFAULT_SCALE:.1f}", on=True),
        Setting("singlePoint", "Single Point", "", suffix, on=True, accepts=NAME),
        # The consistency check (P39): a first pass, no corrections; off waives everything. Each row under it
        # waives one category of its stops where the difference is intended (a benchmark set never shares a method).
        # Labels without "Allow": the shared label column is 19 cells
        Setting("check", "Consistency Check", "First Pass, No Corrections · Ticked Below: Allowed", on=True),
        *(Setting(f"allow-{category}", f"  └ {label}", ALLOW_MEANINGS[category], on=False, under="check")
          for category, (label, _) in CHECK_CATEGORIES.items()),
        Setting("extra", "Extra Keys", "Passed Directly to GoodVibes", "", on=False, accepts=TEXT),
    ]



class FilesPane(Static):
    """The Files section: a click (or esc) goes back to Home to change the selection."""
    def on_click(self) -> None:
        self.screen.action_back()


class BuilderScreen(Screen):
    # Start on the first real input: Cube Files' options, GoodVibes' settings
    AUTO_FOCUS = "#options, #settings .-current"
    BINDINGS = [
        Binding("escape", "back", "Back"),
        Binding("enter", "submit", "Submit"),
        # GoodVibes runs rather than submitting to SLURM; check_action shows one of the two
        Binding("enter", "run", "Run"),
        Binding("question_mark", "help", "Help"),
        *NAV_BINDINGS,
    ]

    def __init__(self, action: Action, files: list[Path], preset: dict[str, str] | None = None,
                 returns: bool = False) -> None:
        """preset: GoodVibes setting values to start from (turned on), e.g. {"singlePoint": "SP"}. returns: another
        screen opened this one and waits for it (dismissed with whether the run succeeded) instead of going Home."""
        super().__init__()
        self.action, self.files, self.preset, self.returns = action, files, preset or {}, returns
        self.ready = False            # the draft is valid and at least one job would run
        self.valueErrors = []         # GoodVibes: typed values that aren't numbers
        self.selectionErrors = []     # GoodVibes: problems fixed by changing the selection (esc Change)
        # GoodVibes: the single point suffix the selection's structures pair with in this folder (as the SI Scraper
        # detects it; None: nothing pairs). A selected SP file counts for its structure
        self.detected = None
        if action == Action.GOODVIBES:
            self.detected = SelectionSuffix(files)

    def compose(self) -> ComposeResult:
        # One framed form (P2): Files on the top edge, a titled rule per section, Submit on the bottom edge
        yield Static(TitleLine(ACTION_LABELS[self.action]), id="title")
        yield FrameRule("┌┐", Text.assemble(f"Files ({len(self.files)}) · ", KeyHint(self.app, "esc", "Change")))
        with Column(id="form"):
            yield FilesPane(NameList(self.files), id="files-summary", classes="side")
            if self.action == Action.GOODVIBES:
                yield FrameRule("├┤", Text.assemble("Settings · ", KeyHint(self.app, "␣", "On/Off or Select"), " · ",
                                                    KeyHint(self.app, "←→", "Choose"), " · Type a Value"))
                yield SettingsList(GoodVibesSettings(self.detected or Defaults.singlePointExtra.lstrip("_")), id="settings",
                                   classes="side")
                yield FrameRule("├┤", "Command")
                yield Static(id="command", classes="side")
            else:
                yield from self.JobSections()
        yield FrameRule("└┘", id="submit")
        yield NavFooter()

    def JobSections(self) -> ComposeResult:
        """Cube Files' sections: Options, Resources."""
        # One tab stop per row: ←/→ move between the checkboxes (OptionRow). There is no Stalk option: every submitted
        # job is tracked, and the Results pop-up offers the Job Stalker
        if self.action == Action.CUBE:
            yield FrameRule("├┤", "Options")
            with Horizontal(id="options-pane", classes="side"):
                with OptionRow(id="options"):
                    for option in CubeOption:
                        yield Checkbox(option.value, id=f"cube-{option.value}", compact=True)
                yield Input(placeholder="MO Range, e.g. 10-15", id="range", disabled=True)
        yield FrameRule("├┤", "Resources")
        yield Static(self.ResourceLine(), id="resources", classes="side")

    def on_mount(self) -> None:
        if self.preset:
            settings = self.query_one("#settings", SettingsList)
            for key, value in self.preset.items():
                row = settings.Row(key)
                row.SetValue(value)
                if row.on is not None:
                    row.on = True
            settings.ShowAll()
        self.Refresh()
        # Auto-focus scrolls the form when it's taller than the screen; always open at the top (the Files section)
        form = self.query_one("#form", VerticalScroll)
        self.call_after_refresh(form.scroll_home, animate=False)

    def on_resize(self, event) -> None:
        # The form hugs its sections but must scroll rather than push the frame's bottom edge off screen.
        # CSS can't express "auto, but at most the space left": title + two frame rules + footer = 4 rows
        self.query_one("#form").styles.max_height = max(1, event.size.height - 4)

    # ─── Content ──────────────────────────────────────────────────────

    def ResourceLine(self) -> Text:
        # • marks values that project.toml overrides (D15)
        parts, anyOverride = [], False
        for key, label in RESOURCE_KEYS:
            overridden = key in Defaults._projectValues and getattr(Defaults, key) != Defaults.GlobalValue(key)
            anyOverride |= overridden
            value = getattr(Defaults, key)
            # slurmHandler writes wallTime as hours:00:00
            shown = (f"{value}:00:00" if key == "wallTime" else f"{value:g}" if isinstance(value, float)
                     else value if value != "" else "—")
            parts += [f"{label} {shown}", ("•" if overridden else " ", "bold"), "   "]
        if anyOverride:
            parts.append(Styled("• Set by project.toml", "info"))
        return Text.assemble(*parts)

    def JobCount(self) -> tuple[int, int]:
        """(jobs that would be submitted, jobs that would be skipped)."""
        if self.action == Action.GOODVIBES:
            return len(self.Structures()[0]), 0
        return len(self.files) * len(self.CubeOptions()), 0

    def Structures(self) -> tuple[list[Path], list[Path], list[Path]]:
        """GoodVibes: (structures, their single point files, structures without one), as the run will see them."""
        return SpcPartners(self.files, self.Draft().singlePointPattern)

    def GoodVibesDraft(self, draft: IntentDraft) -> IntentDraft:
        settings = self.query_one("#settings", SettingsList)
        self.valueErrors = []

        def Number(key: str) -> float | None:
            row = settings.Row(key)
            if not settings.Applies(row):
                return None
            try:
                return float(row.value)
            except ValueError:
                self.valueErrors.append(f"Enter a Number for {row.setting.label.strip(' └')}")
                return None
        draft.tempCorrection = Number("temperature")
        draft.concCorrection = Number("concentration")
        draft.freqCutoff = Number("cutoff")
        draft.vibeScale = Number("scale")
        draft.truhlarEntropy = settings.Row("truhlar").on
        draft.headGordonEnthalpy = settings.Row("enthalpy").on
        draft.allowDifferences = ([category for category in CHECK_CATEGORIES if settings.Row(f"allow-{category}").on]
                                  if settings.Row("check").on else [ALLOW_ALL])
        singlePoint = settings.Row("singlePoint")
        draft.singlePointPattern = singlePoint.value.strip().lstrip("_") or None if singlePoint.on else None
        if singlePoint.on and draft.singlePointPattern is None:
            self.valueErrors.append("Single Point Needs a Suffix, e.g. SP")
        draft.extraKeys = settings.Row("extra").value.strip() or None
        return draft

    def GoodVibesRefresh(self) -> list[str]:
        """Update the Single Point count and the command; return what stops the run (a missing SP file among them)."""
        draft = self.Draft()
        settings = self.query_one("#settings", SettingsList)
        # The count follows the typed suffix even while Single Point is off, so turning it on holds no surprise
        suffix = settings.Row("singlePoint").value.strip().lstrip("_")
        structures, _, missing = SpcPartners(self.files, suffix)
        detected = "Detected · " if suffix == self.detected else ""
        settings.Row("singlePoint").SetMeaning(f"{detected}{len(structures) - len(missing)} of {len(structures)} "
                                               "Structures Have One")
        inputs, _, missing = self.Structures()
        names = NameList(inputs).replace(", ", " ")
        command = " ".join(["goodvibes", *GoodVibesArguments(draft)]) + " " + names
        # The check pass runs first, unless Consistency Check is off
        if ALLOW_ALL not in draft.allowDifferences:
            command = " ".join(["goodvibes", *CheckArguments(draft)]) + f" {names}   (Check Pass)\n{command}"
        self.query_one("#command", Static).update(command)
        self.selectionErrors = []
        if missing:
            self.selectionErrors.append(f"No {draft.singlePointPattern} File: {', '.join(path.name for path in missing)}")
        if not inputs:
            self.selectionErrors.append("No Structures: Only Single Point Files Are Selected")
        return self.valueErrors + self.selectionErrors

    def CubeOptions(self) -> list[CubeOption]:
        return [option for option in CubeOption if self.query_one(f"#cube-{option.value}", Checkbox).value]

    def Draft(self) -> IntentDraft:
        draft = IntentDraft(action=self.action, files=list(self.files))
        if self.action == Action.GOODVIBES:
            return self.GoodVibesDraft(draft)
        draft.cubeOptions = self.CubeOptions()
        draft.orbitalRange = self.query_one("#range", Input).value.strip() or None
        return draft

    def Refresh(self) -> None:
        """Re-validate live (D16): Submit stays disabled while the draft has errors or no job would run."""
        count, skipped = self.JobCount()
        errors = self.Draft().Validate()
        if self.action == Action.GOODVIBES:
            errors += self.GoodVibesRefresh()
        if count == 0 and not errors:
            errors = ["No Job Would Be Submitted"]
        skippedNote = f" ({skipped} Skipped)" if skipped else ""
        self.ready = not errors
        # The bottom edge shows what enter would submit (or run), or the first thing stopping it
        unit, key = ("Structure", "Run") if self.action == Action.GOODVIBES else ("Job", "Submit")
        more = f" (+{len(errors) - 1} more)" if len(errors) > 1 else ""
        # A missing SP file is fixed by changing the selection
        change = [" · ", KeyHint(self.app, "esc", "Change")] if errors and errors[0] in self.selectionErrors else []
        # A status, so no full stop: Validate's errors are also CLI sentences
        self.query_one("#submit", FrameRule).right = (Text.assemble(Styled(errors[0].rstrip(".") + more, "error"), *change) if errors
            else Text.assemble(Styled(f"{count} {unit}{'s' if count != 1 else ''} Ready{skippedNote}", "good"), " · ",
                               KeyHint(self.app, "⏎", key)))
        self.refresh_bindings()

    # ─── Events ───────────────────────────────────────────────────────

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        # Range needs its orbital range
        if event.checkbox.id == f"cube-{CubeOption.RANGE.value}":
            self.query_one("#range", Input).disabled = not event.value
        self.Refresh()

    def on_input_changed(self, event: Input.Changed) -> None:
        self.Refresh()

    def on_settings_list_changed(self, event: SettingsList.Changed) -> None:
        self.Refresh()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        # The MO range box takes enter itself; it submits like every other field
        self.action_submit()

    # ─── Actions ──────────────────────────────────────────────────────

    def action_back(self) -> None:
        # Home keeps its selection, since it was never unmounted; a screen waiting on this one hears nothing ran
        self.dismiss(False)

    def check_action(self, action: str, parameters) -> bool | None:
        # ⏎ Submit shows dimmed in the footer while nothing can be submitted. One ⏎ hint: Run for GoodVibes, Submit otherwise
        if action in ("submit", "run") and (action == "run") != (self.action == Action.GOODVIBES):
            return False
        return None if action in ("submit", "run") and not self.ready else True

    def action_submit(self) -> None:
        if not self.ready:
            return
        # Both run inside the app: GoodVibes (its output in the Results pop-up), and job actions
        if self.action == Action.GOODVIBES:
            self.app.RunGoodVibes(self.Draft().Finalize(), self)
            return
        self.app.Submit(self.Draft().Finalize(), self.JobCount()[0])

    def action_run(self) -> None:
        self.action_submit()

    def action_help(self) -> None:
        if self.action == Action.GOODVIBES:
            Notice(self.app, "Help", "\n".join([
                "↑/↓ Move Between Settings", "space Turn a Setting On/Off or Select a Method",
                "←/→ Switch the Entropy Method", "Type to Change a Value (Turns Its Setting On)",
                "enter Run GoodVibes", "esc Back to the File List", "ctrl+q Quit"]))
            return
        Notice(self.app, "Help", "\n".join([
            "tab / shift+tab Move Between Sections", "←/→ Move Between Options", "space Toggle an Option",
            "Range: Type the Orbitals, e.g. 10-15", "enter Submit", "esc Back to the File List", "ctrl+q Quit"]))
