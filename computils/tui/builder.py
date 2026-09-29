"""Builder: one template for every job action, showing only what the action uses (TUI_DESIGN.md mock-up 4.2, D21)."""
from pathlib import Path

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Checkbox, Input, Label, OptionList, Static

from ..actions  import Action, CubeOption
from ..catalog  import Catalog
from ..defaults import Defaults
from ..intent   import IntentDraft
from .common    import NAV_BINDINGS, FrameRule, KeyHint, NavFooter, OptionRow
from .home      import ACTION_LABELS, TitleLine
from .inspect   import MethodIndices, PreviewMolecule, PreviewRowFor, ProgramName, Styled

SPAN_STYLES = {"method": "operation", "added": "good", "base": ""}   # D17 / D28
RESOURCE_KEYS = [("CPU", "CPU"), ("memoryRatio", "Memory Ratio"), ("wallTime", "WallTime"), ("partition", "Partition")]
USES_METHODS = (Action.SINGLE_POINT, Action.BENCHMARK)
USES_GENERATION = (Action.SINGLE_POINT, Action.BENCHMARK, Action.RERUN)
SHOWN_NAMES = 6                  # the Files section lists this many names, then "… +N more"


class FilesPane(Static):
    """The Files section: a click (or esc) goes back to Home to change the selection."""
    def on_click(self) -> None:
        self.screen.action_back()


class MethodList(OptionList):
    # Space chooses, like every other list; enter submits, as it does from every Builder field
    BINDINGS = [Binding("space", "select", "Choose", key_display="␣"), Binding("enter", "screen.submit", show=False)]


class BuilderScreen(Screen):
    # Start on the first real input: the method list, or the options for actions without one
    AUTO_FOCUS = "#methods, #options"
    BINDINGS = [
        Binding("escape", "back", "Back"),
        Binding("enter", "submit", "Submit"),
        Binding("question_mark", "help", "Help"),
        *NAV_BINDINGS,
    ]

    def __init__(self, action: Action, files: list[Path]) -> None:
        super().__init__()
        self.action, self.files = action, files
        self.methodIndex = 0          # -ovr: Benchmark starts here, Single Point uses only this one
        self.previewIndex = 0         # the highlighted method row
        self.molecules = {}
        self.rows = {}                # (method index, file name) -> PreviewRow
        self.ready = False            # the draft is valid and at least one job would run

    def compose(self) -> ComposeResult:
        # One framed form (P2): Files on the top edge, a titled rule per section, Submit on the bottom edge
        yield Static(TitleLine(ACTION_LABELS[self.action]), id="title")
        yield FrameRule("┌┐", Text.assemble(f"Files ({len(self.files)}) · ", KeyHint(self.app, "esc", "Change")))
        with VerticalScroll(id="form"):
            names = ", ".join(path.name for path in self.files[:SHOWN_NAMES])
            more = len(self.files) - SHOWN_NAMES
            yield FilesPane(names + (f", … +{more} more" if more > 0 else ""), id="files-summary", classes="side")
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
        yield FrameRule("└┘", id="submit")
        yield NavFooter()

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
            self.query_one("#preview-rule", FrameRule).label = (f"Route Preview: method {self.previewIndex}"
                                                                if self.action in USES_METHODS else "Route Preview (from each file)")
            self.query_one("#preview", Static).update(self.PreviewText())
        count, skipped = self.JobCount()
        errors = self.Draft().Validate()
        if count == 0 and not errors:
            errors = ["No job would be submitted."]
        skippedNote = f" ({skipped} skipped)" if skipped else ""
        self.ready = not errors
        # The bottom edge shows what enter would submit, or the first thing stopping it
        more = f" (+{len(errors) - 1} more)" if len(errors) > 1 else ""
        self.query_one("#submit", FrameRule).right = (Styled(errors[0] + more, "error") if errors
            else Text.assemble(Styled(f"{count} job{'s' if count != 1 else ''} ready{skippedNote}", "good"), " · ",
                               KeyHint(self.app, "⏎", "Submit")))
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

    def on_input_submitted(self, event: Input.Submitted) -> None:
        # The MO range box takes enter itself; it submits like every other field
        self.action_submit()

    # ─── Actions ──────────────────────────────────────────────────────

    def action_back(self) -> None:
        # Home keeps its selection, since it was never unmounted
        self.app.pop_screen()

    def check_action(self, action: str, parameters) -> bool | None:
        # ⏎ Submit shows dimmed in the footer while nothing can be submitted
        return None if action == "submit" and not self.ready else True

    def action_submit(self) -> None:
        if not self.ready:
            return
        self.app.exit(self.Draft().Finalize())

    def action_help(self) -> None:
        self.notify("tab / shift+tab move between fields · ↑/↓ move in the method list · ←/→ move between options · "
                    "space toggles an option or chooses the highlighted method · enter submits · "
                    "esc goes back to the file list · ctrl+q quits", title="Help")
