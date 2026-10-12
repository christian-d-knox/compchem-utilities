"""Builder: one template for every job action, showing only what the action uses (TUI_DESIGN.md mock-up 4.2, D21)."""
from pathlib import Path

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Checkbox, Input, OptionList, Static

from ..actions  import Action, CubeOption
from ..analysis import GoodVibesArguments, SpcPartners
from ..catalog  import Catalog
from ..defaults import Defaults
from ..intent   import IntentDraft
from .common    import (NAME, NAV_BINDINGS, TEXT, FrameRule, KeyHint, NavFooter, Notice, OptionRow, Setting,
                        SettingsList)
from .home      import ACTION_LABELS, TitleLine
from .inspect   import MethodIndices, PreviewMolecule, PreviewRowFor, ProgramName, RowText, Styled

RESOURCE_KEYS = [("CPU", "CPU"), ("memoryRatio", "Memory Ratio"), ("wallTime", "WallTime"), ("partition", "Partition")]
USES_METHODS = (Action.SINGLE_POINT, Action.BENCHMARK)
USES_GENERATION = (Action.SINGLE_POINT, Action.BENCHMARK, Action.RERUN)
SHOWN_NAMES = 6                  # the Files section lists this many names, then "… +N More"
_FILE = Binding.Group("File", compact=True)


def NameList(paths: list[Path]) -> str:
    more = len(paths) - SHOWN_NAMES
    return ", ".join(path.name for path in paths[:SHOWN_NAMES]) + (f", … +{more} More" if more > 0 else "")


# ─── GoodVibes settings (mock-up 4.2, GoodVibes) ──────────────────────

def GoodVibesSettings() -> list[Setting]:
    """Defaults match the CLI wizard's, and Single Point is on: GoodVibes runs once both the opt and the SP are done.
    This screen's text is in Title Case (Christian). A choice's notes read as one note across its rows, the later
    ones indented as its hanging lines."""
    return [
        Setting("temperature", "Temperature", "Default 298.15 K", "298.15", "K"),
        Setting("concentration", "Concentration", "Off: Gas Phase (1 atm)", "1.0", "mol/l", on=False),
        Setting("grimme", "Entropy (qh-S)", "qh-G(T) Is Always Present (Required)", fixed="Grimme", on=True, choice="entropy"),
        Setting("truhlar", "", "  See G(T) for Uncorrected", fixed="Truhlar", on=False, choice="entropy"),
        Setting("enthalpy", "Enthalpy (qh-H)", "Off: RRHO Enthalpy", fixed="Head-Gordon", on=True),
        Setting("cutoff", "  └ qh Cutoff", "Softer Modes Are Corrected", "100", "cm⁻¹", under="enthalpy"),
        Setting("scale", "Vib Scale Factor", "Off: GV Auto-Detect from Method", "1.0", on=True),
        Setting("singlePoint", "Single Point", "", Defaults.singlePointExtra.lstrip("_"), on=True, accepts=NAME),
        Setting("check", "Check", "Check for Level of Theory Consistency", on=False),
        Setting("extra", "Extra Keys", "Passed Directly to GoodVibes", "", on=False, accepts=TEXT),
    ]



class FilesPane(Static):
    """The Files section: a click (or esc) goes back to Home to change the selection."""
    def on_click(self) -> None:
        self.screen.action_back()


class MethodList(OptionList):
    # Space chooses, like every other list; enter submits, as from every Builder field. Bound here (not only on the
    # screen) so the footer shows them: the list's hidden enter and the form's hidden ←/→ scrolling would shadow them.
    # ←/→ change the file Benchmark previews across its methods
    BINDINGS = [
        Binding("space", "select", "Choose", key_display="␣"),
        Binding("left", "screen.preview_file(-1)", "File", group=_FILE),
        Binding("right", "screen.preview_file(1)", "File", group=_FILE),
        Binding("enter", "screen.submit", "Submit"),
    ]


class BuilderScreen(Screen):
    # Start on the first real input: the method list, or the options for actions without one
    AUTO_FOCUS = "#methods, #options, #settings .-current"
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
        self.methodIndex = 0          # -ovr: Benchmark starts here, Single Point uses only this one
        self.previewIndex = 0         # the highlighted method row
        self.previewFile = 0          # Benchmark: the file previewed across its methods
        self.molecules = {}
        self.rows = {}                # (method index, file name) -> PreviewRow
        self.ready = False            # the draft is valid and at least one job would run
        self.valueErrors = []         # GoodVibes: typed values that aren't numbers
        self.selectionErrors = []     # GoodVibes: problems fixed by changing the selection (esc Change)

    def compose(self) -> ComposeResult:
        # One framed form (P2): Files on the top edge, a titled rule per section, Submit on the bottom edge
        yield Static(TitleLine(ACTION_LABELS[self.action]), id="title")
        yield FrameRule("┌┐", Text.assemble(f"Files ({len(self.files)}) · ", KeyHint(self.app, "esc", "Change")))
        with VerticalScroll(id="form"):
            yield FilesPane(NameList(self.files), id="files-summary", classes="side")
            if self.action == Action.GOODVIBES:
                yield FrameRule("├┤", Text.assemble("Settings · ", KeyHint(self.app, "␣", "On/Off or Select"), " · ",
                                                    KeyHint(self.app, "←→", "Choose"), " · Type a Value"))
                yield SettingsList(GoodVibesSettings(), id="settings", classes="side")
                yield FrameRule("├┤", "Command")
                yield Static(id="command", classes="side")
            else:
                yield from self.JobSections()
        yield FrameRule("└┘", id="submit")
        yield NavFooter()

    def JobSections(self) -> ComposeResult:
        """The sections of a SLURM job action: Methods, Options, Resources, Route Preview (each only if the action uses it)."""
        if self.action in USES_METHODS:
            yield FrameRule("├┤", "Methods (benchmarkMethods)")
            yield MethodList(*self.MethodPrompts(), id="methods", classes="side")
        # One tab stop per row: ←/→ move between the checkboxes (OptionRow). There is no Stalk option: every submitted
        # job is tracked, and the Results pop-up offers the Job Stalker. Run as Written has no options left
        if self.action == Action.CUBE:
            yield FrameRule("├┤", "Options")
            with Horizontal(id="options-pane", classes="side"):
                with OptionRow(id="options"):
                    for option in CubeOption:
                        yield Checkbox(option.value, id=f"cube-{option.value}", compact=True)
                yield Input(placeholder="MO Range, e.g. 10-15", id="range", disabled=True)
        elif self.action in USES_GENERATION:
            yield FrameRule("├┤", "Options")
            with Horizontal(id="options-pane", classes="side"):
                with OptionRow(id="options"):
                    yield Checkbox("Checkpoint", id="checkpoint", compact=True)
                    yield Checkbox("NBO", id="nbo", compact=True)
        if self.action != Action.RUN:
            yield FrameRule("├┤", "Resources")
            yield Static(self.ResourceLine(), id="resources", classes="side")
        if self.action in USES_GENERATION:
            yield FrameRule("├┤", id="preview-rule")
            yield Static(id="preview", classes="side")

    def on_mount(self) -> None:
        if self.action in USES_GENERATION:
            self.molecules = {path.name: PreviewMolecule(path) for path in self.files}
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

    def MethodPrompts(self) -> list[Text]:
        prompts = []
        for index, entry in enumerate(Defaults.benchmarkMethods):
            chosen = index == self.methodIndex
            skipped = index < self.methodIndex if self.action == Action.BENCHMARK else not chosen
            line = Text(f"({'•' if chosen else ' '}) {index}  {entry}   {ProgramName(Catalog.templates[index].method)}")
            if skipped:
                line.stylize("dim")
            prompts.append(line)
        return prompts

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

    def Row(self, index: int, name: str):
        if (index, name) not in self.rows:
            self.rows[index, name] = PreviewRowFor(self.action, self.molecules[name], index)
        return self.rows[index, name]

    def JobCount(self) -> tuple[int, int]:
        """(jobs that would be submitted, jobs that would be skipped)."""
        if self.action == Action.GOODVIBES:
            return len(self.Structures()[0]), 0
        if self.action == Action.CUBE:
            return len(self.files) * len(self.CubeOptions()), 0
        if self.action == Action.RUN:
            return len(self.files), 0
        rows = [self.Row(index, path.name) for index in MethodIndices(self.action, self.methodIndex) for path in self.files]
        skipped = sum(1 for row in rows if row.problem)
        return len(rows) - skipped, skipped

    def PreviewText(self) -> Text:
        # Benchmark: one file, a row per method it will run (▸ the highlighted method). Otherwise: a row per file
        if self.action == Action.BENCHMARK:
            name = self.files[self.previewFile].name
            rows = [(f"{'▸' if index == self.previewIndex else ' '} {index:<3}", self.Row(index, name))
                    for index in MethodIndices(self.action, self.methodIndex)]
        else:
            index = self.previewIndex if self.action in USES_METHODS else 0
            width = max(len(path.name) for path in self.files) + 2
            rows = [(f"{row.spin:<5}{path.name.ljust(width)}", row)
                    for path in self.files for row in [self.Row(index, path.name)]]
        return Text("\n").join(Text(prefix) + RowText(row) for prefix, row in rows)

    def PreviewLabel(self) -> str:
        if self.action == Action.BENCHMARK:
            name = self.files[self.previewFile].name
            count = f" · File {self.previewFile + 1} of {len(self.files)}" if len(self.files) > 1 else ""
            return f"Route Preview: {name} ({self.Row(self.methodIndex, name).spin}){count}"
        return f"Route Preview: Method {self.previewIndex}" if self.action in USES_METHODS else "Route Preview (From Each File)"

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
        draft.checkConsistency = settings.Row("check").on
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
        settings.Row("singlePoint").SetMeaning(f"{len(structures) - len(missing)} of {len(structures)} Structures Have One")
        inputs, _, missing = self.Structures()
        command = " ".join(["goodvibes", *GoodVibesArguments(draft)]) + " " + NameList(inputs).replace(", ", " ")
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
        draft = IntentDraft(action=self.action, files=list(self.files), indexOverride=self.methodIndex)
        if self.action == Action.GOODVIBES:
            return self.GoodVibesDraft(draft)
        if self.action == Action.CUBE:
            draft.cubeOptions = self.CubeOptions()
            draft.orbitalRange = self.query_one("#range", Input).value.strip() or None
            return draft
        if self.action in USES_GENERATION:
            draft.checkpoint = self.query_one("#checkpoint", Checkbox).value
            draft.nbo7 = self.query_one("#nbo", Checkbox).value
        return draft

    def Refresh(self) -> None:
        """Re-validate live (D16): Submit stays disabled while the draft has errors or no job would run."""
        if self.action in USES_GENERATION:
            self.query_one("#preview-rule", FrameRule).label = self.PreviewLabel()
            self.query_one("#preview", Static).update(self.PreviewText())
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

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        self.previewIndex = event.option_index
        self.Refresh()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.methodIndex = event.option_index
        methods = self.query_one("#methods", OptionList)
        for index, prompt in enumerate(self.MethodPrompts()):
            methods.replace_option_prompt_at_index(index, prompt)
        self.Refresh()

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
        # ⏎ Submit shows dimmed in the footer while nothing can be submitted; ←→ File only for a multi-file Benchmark
        if action == "preview_file":
            return self.action == Action.BENCHMARK and len(self.files) > 1
        # One ⏎ hint: Run for GoodVibes, Submit otherwise
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

    def action_preview_file(self, step: int) -> None:
        self.previewFile = (self.previewFile + step) % len(self.files)
        self.Refresh()

    def action_help(self) -> None:
        if self.action == Action.GOODVIBES:
            Notice(self.app, "Help", "\n".join([
                "↑/↓ Move Between Settings", "space Turn a Setting On/Off or Select a Method",
                "←/→ Switch the Entropy Method", "Type to Change a Value (Turns Its Setting On)",
                "enter Run GoodVibes", "esc Back to the File List", "ctrl+q Quit"]))
            return
        Notice(self.app, "Help", "\n".join([
            "tab / shift+tab Move Between Sections", "↑/↓ Move in the Method List", "←/→ Move Between Options",
            "space Toggle an Option or Choose the Method", "←/→ Change the Previewed File (Benchmark)",
            "enter Submit", "esc Back to the File List", "ctrl+q Quit"]))
