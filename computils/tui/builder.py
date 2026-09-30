"""Builder: one template for every job action, showing only what the action uses (TUI_DESIGN.md mock-up 4.2, D21)."""
from dataclasses import dataclass
from pathlib import Path

import regex
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import Screen
from textual.widgets import Checkbox, Input, Label, OptionList, Static

from ..actions  import Action, CubeOption
from ..analysis import GoodVibesArguments, SpcPartners
from ..catalog  import Catalog
from ..defaults import Defaults
from ..intent   import IntentDraft
from .common    import NAV_BINDINGS, FrameRule, KeyHint, NavFooter, Notice, OptionRow
from .home      import ACTION_LABELS, TitleLine
from .inspect   import MethodIndices, PreviewMolecule, PreviewRowFor, ProgramName, RowText, Styled

RESOURCE_KEYS = [("CPU", "CPU"), ("memoryRatio", "Memory Ratio"), ("wallTime", "WallTime"), ("partition", "Partition")]
USES_METHODS = (Action.SINGLE_POINT, Action.BENCHMARK)
USES_GENERATION = (Action.SINGLE_POINT, Action.BENCHMARK, Action.RERUN)
SHOWN_NAMES = 6                  # the Files section lists this many names, then "… +N more"
_FILE = Binding.Group("File", compact=True)
_CHOOSE = Binding.Group("Choose", compact=True)
# The characters a typed value takes. A box that never takes a space leaves ␣ to turn its row on/off
NUMBER, NAME, TEXT = r"[\d.eE+-]", r"\w", None


def NameList(paths: list[Path]) -> str:
    more = len(paths) - SHOWN_NAMES
    return ", ".join(path.name for path in paths[:SHOWN_NAMES]) + (f", … +{more} more" if more > 0 else "")


# ─── GoodVibes settings (mock-up 4.2, GoodVibes) ──────────────────────

@dataclass
class Setting:
    """One row of the GoodVibes settings list: [X] Label  value unit  meaning."""
    key: str
    label: str
    meaning: str = ""
    value: str | None = None    # a typed value; None: the row has no value box
    unit: str = ""
    fixed: str = ""             # a value that is shown, not typed (Enthalpy's Head-Gordon, a choice's name)
    on: bool | None = None      # the checkbox; None: always applies, so no checkbox
    accepts: str | None = NUMBER  # the characters it takes (a regex); TEXT: anything, a space included
    choice: str = ""            # rows of one choice share this name, shown as (•) / ( )
    under: str = ""             # the row this one is a sub-option of: it applies only while that row is on


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


class ValueInput(Input):
    """A setting's typed value: one line, as wide as its text, so the unit follows it (298.15 K).
    A box takes only its setting's characters (a number, a name), so ␣ (on/off) and ? (help) still reach the list and
    the screen, and the footer keeps showing them. ⏎ runs, like every Builder field (Input's own hidden ⏎ would hide the hint)."""
    BINDINGS = [Binding("enter", "screen.run", "Run")]

    def __init__(self, row: "SettingRow", value: str) -> None:
        super().__init__(value, compact=True)
        self.row = row
        self.can_focus = False
        self.Fit(value)

    def Fit(self, value: str) -> None:
        # One cell past the text, for the cursor; capped to the value column
        self.styles.width = min(len(value) + 1, 17)

    def check_consume_key(self, key: str, character: str | None) -> bool:
        if self.row.setting.accepts is None:
            return super().check_consume_key(key, character)
        return character is not None and regex.fullmatch(self.row.setting.accepts, character) is not None

    async def _on_key(self, event) -> None:
        # Other printable keys are left to the bindings (Input would type them)
        if event.is_printable and not self.check_consume_key(event.key, event.character):
            event.prevent_default()


class SettingBox(Static):
    def on_click(self) -> None:
        self.parent.parent.Choose(self.parent)
        self.parent.parent.action_toggle()


class SettingRow(Horizontal, can_focus=False):
    """Rows without a typed value take the focus themselves (the list keeps only its current row focusable)."""
    def __init__(self, setting: Setting) -> None:
        # A choice's rows carry one shared note, which is never greyed with an unselected option
        super().__init__(classes="setting -choice" if setting.choice else "setting", id=f"setting-{setting.key}")
        self.setting, self.on = setting, setting.on
        self.typing = False

    def compose(self) -> ComposeResult:
        yield SettingBox(classes="setting-box")
        yield Static(self.setting.label, classes="setting-label")
        with Horizontal(classes="setting-value"):
            if self.setting.value is not None:
                yield ValueInput(self, self.setting.value)
                yield Static(self.setting.unit, classes="setting-unit")
            else:
                yield Static(classes="setting-fixed")
        yield Static(self.setting.meaning, classes="setting-meaning")

    def on_mount(self) -> None:
        self.Show()
        # An Input posts Changed for its initial value too; only typing turns a row on
        self.call_after_refresh(setattr, self, "typing", True)

    @property
    def target(self):
        return self.query_one(ValueInput) if self.setting.value is not None else self

    @property
    def value(self) -> str:
        return self.query_one(ValueInput).value if self.setting.value is not None else ""

    def Show(self) -> None:
        box = "   " if self.on is None or self.setting.choice else "[X]" if self.on else "[ ]"
        # Text, not str: a str "[X]" is read as markup
        self.query_one(".setting-box", Static).update(Text(box))
        if self.setting.value is None:
            fixed = f"({'•' if self.on else ' '}) {self.setting.fixed}" if self.setting.choice else self.setting.fixed
            self.query_one(".setting-fixed", Static).update(fixed)
        # Whatever won't apply (off, an unselected choice, a sub-option of an off row) is greyed out
        self.set_class(not self.parent.Applies(self), "-off")

    def SetMeaning(self, text: str) -> None:
        self.query_one(".setting-meaning", Static).update(text)

    def on_input_changed(self, event: Input.Changed) -> None:
        event.input.Fit(event.value)
        # Typing a value turns its row on; free text (Extra keys) is on whenever it holds something
        if self.typing:
            if self.on is not None:
                self.on = bool(event.value.strip()) if self.setting.accepts is TEXT else True
            # ...and a sub-option's value turns on the row it belongs to
            if self.setting.under:
                self.parent.Row(self.setting.under).on = True
            self.parent.ShowAll()

    def on_click(self) -> None:
        self.parent.Choose(self)


class SettingsList(Vertical):
    """The GoodVibes settings: one tab stop, ↑↓ move between rows, ␣ turns the current one on/off (or selects a
    choice), ←→ switch a choice, and a row with a value is typed into directly."""
    BINDINGS = [
        Binding("up", "move(-1)", "Move", show=False),
        Binding("down", "move(1)", "Move", show=False),
        Binding("space", "toggle", "On/Off", key_display="␣"),
        Binding("left", "choose(-1)", "Choose", group=_CHOOSE),
        Binding("right", "choose(1)", "Choose", group=_CHOOSE),
    ]

    class Changed(Message):
        """A row was turned on or off, or a choice changed (typed values post Input.Changed)."""

    def __init__(self, settings: list[Setting], **kwargs) -> None:
        super().__init__(**kwargs)
        self.settings, self.index = settings, 0

    def compose(self) -> ComposeResult:
        for setting in self.settings:
            yield SettingRow(setting)

    def on_mount(self) -> None:
        self.Choose(self.rows[0])

    @property
    def rows(self) -> list[SettingRow]:
        return list(self.query_children(SettingRow))

    @property
    def current(self) -> SettingRow:
        return self.rows[self.index]

    def Row(self, key: str) -> SettingRow:
        return self.query_one(f"#setting-{key}", SettingRow)

    def Applies(self, row: SettingRow) -> bool:
        """Whether the row takes part in the run: not off, and not under a row that is off."""
        return row.on is not False and (not row.setting.under or self.Applies(self.Row(row.setting.under)))

    def ShowAll(self) -> None:
        # A row's change can grey out (or bring back) its sub-options
        for row in self.rows:
            row.Show()

    def Choose(self, row: SettingRow) -> None:
        """Make row current: the only focusable one, so Tab enters and leaves the list in one stop."""
        hadFocus = self.screen.focused is not None and self in self.screen.focused.ancestors_with_self
        self.index = self.rows.index(row)
        for other in self.rows:
            other.set_class(other is row, "-current")
            other.target.can_focus = other is row
        if hadFocus or self.screen.focused is None:
            row.target.focus()

    def check_action(self, action: str, parameters) -> bool | None:
        # Shown dimmed where they do nothing: ←→ off a choice, ␣ on a row that always applies (or Extra keys)
        if action == "choose":
            return True if self.current.setting.choice else None
        if action == "toggle":
            return True if self.current.on is not None and self.current.setting.accepts is not TEXT else None
        return True

    def action_move(self, step: int) -> None:
        self.Choose(self.rows[max(0, min(self.index + step, len(self.rows) - 1))])
        self.refresh_bindings()

    def action_toggle(self) -> None:
        row = self.current
        if row.setting.choice:
            self.Select(row)
        elif row.on is not None and row.setting.accepts is not TEXT:
            row.on = not row.on
            self.ShowAll()
            self.post_message(self.Changed())

    def action_choose(self, step: int) -> None:
        options = [row for row in self.rows if row.setting.choice == self.current.setting.choice]
        selected = next(index for index, row in enumerate(options) if row.on)
        self.Select(options[(selected + step) % len(options)])

    def Select(self, chosen: SettingRow) -> None:
        for row in self.rows:
            if row.setting.choice == chosen.setting.choice:
                row.on = row is chosen
        self.ShowAll()
        self.post_message(self.Changed())



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
        # GoodVibes runs in the terminal rather than submitting to SLURM; check_action shows one of the two
        Binding("enter", "run", "Run"),
        Binding("question_mark", "help", "Help"),
        *NAV_BINDINGS,
    ]

    def __init__(self, action: Action, files: list[Path]) -> None:
        super().__init__()
        self.action, self.files = action, files
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
        yield FrameRule("├┤", "Options")
        # One tab stop per row: ←/→ move between the checkboxes (OptionRow)
        with Horizontal(id="options-pane", classes="side"):
            if self.action == Action.CUBE:
                with OptionRow(id="options"):
                    for option in CubeOption:
                        yield Checkbox(option.value, id=f"cube-{option.value}", compact=True)
                yield Input(placeholder="MO range, e.g. 10-15", id="range", disabled=True)
            else:
                with OptionRow(id="options"):
                    if self.action in USES_GENERATION:
                        yield Checkbox("Checkpoint", id="checkpoint", compact=True)
                        yield Checkbox("NBO", id="nbo", compact=True)
                    yield Checkbox("Stalk", id="stalk", compact=True)
                    # Loop is a sub-flag of Stalk (mock-up 4.2): Stalk ( [ ] Loop )
                    yield Label("( ", classes="nest")
                    yield Checkbox("Loop", id="loop", disabled=True, compact=True)
                    yield Label(")", classes="nest")
        if self.action != Action.RUN:
            yield FrameRule("├┤", "Resources")
            yield Static(self.ResourceLine(), id="resources", classes="side")
        if self.action in USES_GENERATION:
            yield FrameRule("├┤", id="preview-rule")
            yield Static(id="preview", classes="side")

    def on_mount(self) -> None:
        if self.action in USES_GENERATION:
            self.molecules = {path.name: PreviewMolecule(path) for path in self.files}
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
            parts.append(Styled("• set by project.toml", "info"))
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
            count = f" · file {self.previewFile + 1} of {len(self.files)}" if len(self.files) > 1 else ""
            return f"Route Preview: {name} ({self.Row(self.methodIndex, name).spin}){count}"
        return f"Route Preview: method {self.previewIndex}" if self.action in USES_METHODS else "Route Preview (from each file)"

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
        draft.stalk = self.query_one("#stalk", Checkbox).value
        draft.stalkLoop = draft.stalk and self.query_one("#loop", Checkbox).value
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
            errors = ["No job would be submitted."]
        skippedNote = f" ({skipped} skipped)" if skipped else ""
        self.ready = not errors
        # The bottom edge shows what enter would submit (or run), or the first thing stopping it
        # GoodVibes' screen is in Title Case
        unit, ready, key = (("Structure", "Ready", "Run") if self.action == Action.GOODVIBES else ("job", "ready", "Submit"))
        more = f" (+{len(errors) - 1} more)" if len(errors) > 1 else ""
        # A missing SP file is fixed by changing the selection
        change = [" · ", KeyHint(self.app, "esc", "Change")] if errors and errors[0] in self.selectionErrors else []
        self.query_one("#submit", FrameRule).right = (Text.assemble(Styled(errors[0] + more, "error"), *change) if errors
            else Text.assemble(Styled(f"{count} {unit}{'s' if count != 1 else ''} {ready}{skippedNote}", "good"), " · ",
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
        # Loop only means something while stalking; Range needs its orbital range
        if event.checkbox.id == "stalk":
            self.query_one("#loop", Checkbox).disabled = not event.value
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
        # Home keeps its selection, since it was never unmounted
        self.app.pop_screen()

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
        self.app.exit(self.Draft().Finalize())

    def action_run(self) -> None:
        self.action_submit()

    def action_preview_file(self, step: int) -> None:
        self.previewFile = (self.previewFile + step) % len(self.files)
        self.Refresh()

    def action_help(self) -> None:
        if self.action == Action.GOODVIBES:
            Notice(self.app, "Help", "\n".join([
                "↑/↓ Move between settings", "space Turn the setting on or off, or select an entropy method",
                "←/→ Switch the entropy method", "Type to change a value (typing turns its setting on)",
                "enter Run GoodVibes", "esc Back to the file list", "ctrl+q Quit"]))
            return
        Notice(self.app, "Help", "\n".join([
            "tab / shift+tab Move between fields", "↑/↓ Move in the method list", "←/→ Move between options",
            "space Toggle an option, or choose the highlighted method", "←/→ Change the previewed file (Benchmark)",
            "enter Submit", "esc Back to the file list", "ctrl+q Quit"]))
