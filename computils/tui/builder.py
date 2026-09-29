"""Builder: one template for every job action, showing only what the action uses (TUI_DESIGN.md mock-up 4.2, D21)."""
from pathlib import Path

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Checkbox, Footer, Input, Label, OptionList, Static

from ..actions  import Action, CubeOption
from ..catalog  import Catalog
from ..defaults import Defaults
from ..intent   import IntentDraft
from .home      import ACTION_LABELS, TitleLine
from .inspect   import MethodIndices, PreviewMolecule, PreviewRowFor, ProgramName, Styled

SPAN_STYLES = {"method": "operation", "added": "good", "base": ""}   # D17 / D28
RESOURCE_KEYS = [("CPU", "CPU"), ("memoryRatio", "Memory Ratio"), ("wallTime", "WallTime"), ("partition", "Partition")]
USES_METHODS = (Action.SINGLE_POINT, Action.BENCHMARK)
USES_GENERATION = (Action.SINGLE_POINT, Action.BENCHMARK, Action.RERUN)


class BuilderScreen(Screen):
    BINDINGS = [
        Binding("escape", "back", "Back"),
        Binding("ctrl+s", "submit", "Submit"),
        Binding("question_mark", "help", "Help"),
    ]

    def __init__(self, action: Action, files: list[Path]) -> None:
        super().__init__()
        self.action, self.files = action, files
        self.methodIndex = 0          # -ovr: Benchmark starts here, Single Point uses only this one
        self.previewIndex = 0         # the highlighted method row
        self.molecules = {}
        self.rows = {}                # (method index, file name) -> PreviewRow

    def compose(self) -> ComposeResult:
        yield Static(TitleLine(ACTION_LABELS[self.action]), id="title")
        with VerticalScroll(id="form"):
            with Horizontal(classes="row"):
                names = ", ".join(path.name for path in self.files)
                yield Label(f"Files ({len(self.files)}): {names}", id="file-line")
                yield Button("Change…", id="back", compact=True)
            if self.action in USES_METHODS:
                hint = "enter chooses where the benchmark starts" if self.action == Action.BENCHMARK else "enter chooses the method"
                yield Label(f"Methods (benchmarkMethods) · {hint}", classes="heading")
                yield OptionList(*self.MethodPrompts(), id="methods")
            yield Label("Options:", classes="heading")
            with Horizontal(classes="row"):
                if self.action == Action.CUBE:
                    for option in CubeOption:
                        yield Checkbox(option.value, id=f"cube-{option.value}", compact=True)
                    yield Input(placeholder="MO range, e.g. 10-15", id="range", disabled=True)
                else:
                    if self.action in USES_GENERATION:
                        yield Checkbox("Checkpoint", id="checkpoint", compact=True)
                        yield Checkbox("NBO", id="nbo", compact=True)
                    yield Checkbox("Stalk", id="stalk", compact=True)
                    yield Checkbox("Loop", id="loop", disabled=True, compact=True)
            if self.action != Action.RUN:
                yield Label("Resources:", classes="heading")
                yield Static(self.ResourceLine(), id="resources")
            if self.action in USES_GENERATION:
                yield Static(id="preview")
            yield Static(id="errors")
            with Horizontal(id="submit-row"):
                yield Button("Submit", id="submit", variant="primary")
        yield Footer()

    def on_mount(self) -> None:
        if self.action in USES_GENERATION:
            self.molecules = {path.name: PreviewMolecule(path) for path in self.files}
        self.Refresh()

    # ─── Content ──────────────────────────────────────────────────────

    def MethodPrompts(self) -> list[Text]:
        prompts = []
        for index, entry in enumerate(Defaults.benchmarkMethods):
            chosen = index == self.methodIndex
            skipped = index < self.methodIndex if self.action == Action.BENCHMARK else not chosen
            line = Text(f"({'•' if chosen else ' '}) {index}  {entry}   {ProgramName(Catalog.methodLine[index])}")
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
            shown = f"{value}:00:00" if key == "wallTime" else (value if value != "" else "—")
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
        if self.action == Action.CUBE:
            return len(self.files) * len(self.CubeOptions()), 0
        if self.action == Action.RUN:
            return len(self.files), 0
        rows = [self.Row(index, path.name) for index in MethodIndices(self.action, self.methodIndex) for path in self.files]
        skipped = sum(1 for row in rows if row.problem)
        return len(rows) - skipped, skipped

    def PreviewText(self) -> Text:
        index = self.previewIndex if self.action in USES_METHODS else 0
        width = max(len(path.name) for path in self.files) + 2
        lines = []
        for path in self.files:
            row = self.Row(index, path.name)
            line = Text.assemble(f"{row.spin:<5}", path.name.ljust(width))
            for text, origin in row.spans:
                line.append_text(Styled(text, SPAN_STYLES[origin]))
            if row.tags:
                line.append_text(Styled(f"  ← orcablocks {' '.join('{' + tag + '}' for tag in row.tags)}", "good"))
            if row.problem:
                line.append_text(Styled(f"  ⚠ {row.problem}", "warning"))
            lines.append(line)
        return Text("\n").join(lines)

    def CubeOptions(self) -> list[CubeOption]:
        return [option for option in CubeOption if self.query_one(f"#cube-{option.value}", Checkbox).value]

    def Draft(self) -> IntentDraft:
        draft = IntentDraft(action=self.action, files=list(self.files), indexOverride=self.methodIndex)
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
            preview = self.query_one("#preview", Static)
            preview.border_title = (f"Route Preview: method {self.previewIndex}" if self.action in USES_METHODS
                                    else "Route Preview (from each file)")
            preview.update(self.PreviewText())
        count, skipped = self.JobCount()
        errors = self.Draft().Validate()
        if count == 0 and not errors:
            errors = ["No job would be submitted."]
        skippedNote = f" ({skipped} skipped)" if skipped else ""
        self.query_one("#errors", Static).update(Styled("\n".join(errors), "error") if errors else "")
        submit = self.query_one("#submit", Button)
        submit.label = f"Submit {count} Job{'s' if count != 1 else ''}{skippedNote}"
        submit.disabled = bool(errors)

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

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "back":
            self.action_back()
        elif event.button.id == "submit":
            self.action_submit()

    # ─── Actions ──────────────────────────────────────────────────────

    def action_back(self) -> None:
        # Home keeps its selection, since it was never unmounted
        self.app.pop_screen()

    def action_submit(self) -> None:
        if self.query_one("#submit", Button).disabled:
            return
        self.app.exit(self.Draft().Finalize())

    def action_help(self) -> None:
        self.notify("tab moves between fields · space toggles a checkbox · enter picks the highlighted method · "
                    "ctrl+s submits · esc goes back to the file list", title="Help")
