"""Config editor: global values and project overrides, one TOML file per section, patched in place (TUI_DESIGN.md 4.4)."""
import tomllib as tom
from pathlib import Path

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import DataTable, Input, Label, ListItem, ListView, Static

from ..defaults import DELETE, Defaults, tomlValue, writeToml
from ..project  import PROJECT_CONFIG, FindProjectRoot, ProjectFilePath, ReloadConfig, SaveProjectConfig
from .common    import NAV_BINDINGS, FrameRule, KeyHint, NavFooter, Notice, Popup
from .home      import TitleLine
from .inspect   import Styled

# (label, file); the Benchmark Suite lists benchmarkMethods read-only until its own editor exists
SECTIONS = [("SLURM", "slurm.toml"), ("Programs", "programs.toml"), ("Benchmark Suite", None),
            ("Extensions", "extensions.toml"), ("Notifications", "notifications.toml"),
            ("Quality of Life", "qol.toml"), ("Paths", "paths.toml")]
# Set by first-time setup only (the TUI opens only once it has run), or power-user edits made by hand in the file
HIDDEN = {"hpcType", "submissionList", "gaussianNonVariant", "orcaNonVariant"}
READ_ONLY = {"binDirectory", "projectMarker", "benchmarkMethods",
             "botToken", "chatID", "broadcastGroupChatID", "broadcastThreshold"}
MASKED = {"botToken", "chatID", "broadcastGroupChatID"}
# Keys with a fixed set of values: ␣ cycles through them
CHOICES = {"openShellReference": ["U", "RO"], "colorMode": ["lowColor", "hexCode"]}
TYPE_NAMES = {int: "a whole number", float: "a number", str: "text", bool: "true or false",
              list: 'a list, e.g. ["a", "b"]'}
GLOBAL, PROJECT = 1, 2          # table columns (0 is the key)


def Cut(text: str, width: int) -> str:
    return text if len(text) <= width else text[:max(0, width - 1)] + "…"


def Shown(value, masked: bool = False) -> str:
    if value is DELETE:
        return "—"
    if masked and value:
        return "••••"
    return tomlValue(value) if isinstance(value, (bool, list)) else str(value)


def ParseValue(key: str, text: str):
    """(value, "") for text typed into the edit box, or (None, why not). Checked like a value loaded from TOML."""
    expected = Defaults._TYPES[key]
    raw = text
    if expected is not str:
        try:
            raw = tom.loads(f"value = {text}")["value"]
        except tom.TOMLDecodeError:
            raw = None
    value = None if raw is None else Defaults._CoerceValue(key, raw)
    if value is None or (expected is list and not all(isinstance(item, str) for item in value)):
        return None, f"Expected {TYPE_NAMES[expected]}."
    if key in CHOICES and value not in CHOICES[key]:
        return None, f"Expected one of {', '.join(CHOICES[key])}."
    return value, ""


class SectionList(ListView):
    # ␣ opens the section's table; ⏎ saves, as from every pane
    BINDINGS = [Binding("space", "select_cursor", "Open", key_display="␣"), Binding("enter", "screen.save", "Save")]


class ConfigTable(DataTable):
    BINDINGS = [
        Binding("left", "cursor_left", "Column", group=Binding.Group("Column", compact=True)),
        Binding("right", "cursor_right", "Column", group=Binding.Group("Column", compact=True)),
        Binding("space", "screen.edit", "Edit", key_display="␣"),
        Binding("r", "screen.reset", "Reset"),
        Binding("R", "screen.reset_file", "Reset File", show=False),
        Binding("enter", "screen.save", "Save"),
    ]

    def action_cursor_left(self) -> None:
        # The key column is a label, never a stop
        if self.cursor_column > GLOBAL:
            super().action_cursor_left()


class EditInput(Input):
    # A text box: ⏎ accepts, esc cancels (distinct actions, or the footer shows only one)
    BINDINGS = [Binding("enter", "submit", "Accept"), Binding("escape", "screen.cancel_edit", "Cancel")]


class ConfigScreen(Screen):
    AUTO_FOCUS = "#config-table"
    BINDINGS = [
        Binding("escape", "back", "Back"),
        Binding("question_mark", "help", "Help"),
        *NAV_BINDINGS,
    ]

    def __init__(self) -> None:
        super().__init__()
        self.root = FindProjectRoot()
        self.section = 0
        self.keys: list[str] = []                   # the table's rows
        self.pending: dict[tuple[int, str], object] = {}   # (column, key) -> unsaved value (DELETE: follow global)
        self.regenerate: set[str] = set()           # files to regenerate whole on save (R)
        self.editing: tuple[int, str] | None = None
        self.saved = False                          # anything written (Home re-lists its files)
        self.valueWidth = 14

    def compose(self) -> ComposeResult:
        project = f"project: {self.root.name}" if self.root else "no project"
        yield Static(TitleLine("Config", Styled(project, "info")), id="title")
        with Horizontal(id="body"):
            with Vertical(id="left", classes="pane") as left:
                left.border_title = "Sections"
                items = [ListItem(Label(label)) for label, _ in SECTIONS]
                items += [ListItem(Label("──────────────"), disabled=True),
                          ListItem(Label("Project Files (later)"), disabled=True, classes="later")]
                yield SectionList(*items, id="sections")
            with Vertical(id="right"):
                yield FrameRule("┌┐", id="config-top")
                yield ConfigTable(id="config-table", classes="side", cursor_type="cell", zebra_stripes=False)
                yield FrameRule("├┤", id="config-rule")
                yield Static(id="config-details", classes="side")
                yield EditInput(id="config-edit", classes="side")
                yield FrameRule("└┘", id="config-save")
        yield NavFooter()

    def on_mount(self) -> None:
        self.query_one("#config-edit").display = False
        self.ShowSection(0)

    def on_resize(self, event) -> None:
        # Column widths follow the table's width
        self.call_after_refresh(self.Redraw)

    # ─── State ────────────────────────────────────────────────────────

    def SavedValue(self, column: int, key: str):
        if column == GLOBAL:
            return Defaults.GlobalValue(key)
        return Defaults._projectValues.get(key, DELETE)

    def Value(self, column: int, key: str):
        if (column, key) in self.pending:
            return self.pending[column, key]
        # A project file marked for regeneration becomes a snapshot of the global values (cu -init)
        if column == PROJECT and PROJECT_CONFIG in self.regenerate:
            return Defaults.GlobalValue(key)
        return self.SavedValue(column, key)

    def SetPending(self, column: int, key: str, value) -> None:
        if value == self.SavedValue(column, key):
            self.pending.pop((column, key), None)
        else:
            self.pending[column, key] = value

    def Editable(self, column: int, key: str) -> bool:
        if key in READ_ONLY:
            return False
        return column == GLOBAL or (key in Defaults._PROJECT_KEYS and self.root is not None)

    def Current(self) -> tuple[int, str] | None:
        table = self.query_one("#config-table", ConfigTable)
        if not self.keys or table.cursor_row >= len(self.keys):
            return None
        return max(table.cursor_column, GLOBAL), self.keys[table.cursor_row]

    def SectionFile(self) -> str | None:
        return SECTIONS[self.section][1]

    def Unsaved(self) -> int:
        return len(self.pending) + len(self.regenerate)

    # ─── Content ──────────────────────────────────────────────────────

    def Cell(self, column: int, key: str) -> Text:
        if column == PROJECT and key not in Defaults._PROJECT_KEYS:
            return Text("(global only)", "dim")
        if column == PROJECT and self.root is None:
            return Text("no project", "dim")
        value = self.Value(column, key)
        pending, marked = (column, key) in self.pending, column == PROJECT and value is not DELETE
        # Cut to the column, leaving room for the [unsaved] brackets and the • of a project value (the one in force)
        text = Cut(Shown(value, key in MASKED), self.valueWidth - 2 * pending - marked)
        cell = Text(f"[{text}]" if pending else text, "dim" if key in READ_ONLY else "")
        if marked:
            cell.append("•", "bold")
        return cell

    def SetColumns(self, keyTexts: list[str]) -> None:
        """Fixed widths: Key fits its longest entry, Global and Project share the rest. A DataTable's columns otherwise
        grow with their widest value (and never shrink), pushing the table past its pane."""
        table = self.query_one("#config-table", ConfigTable)
        keyWidth = max(map(len, keyTexts))
        # One cell of padding either side of each column, and room for the vertical scrollbar
        available = table.content_region.width - 3 * 2 * table.cell_padding - 2
        self.valueWidth = max(8, (available - keyWidth) // 2)
        table.clear(columns=True)
        table.add_column("Key", width=keyWidth)
        table.add_column("Global", width=self.valueWidth)
        table.add_column("Project", width=self.valueWidth)

    def ShowSection(self, index: int) -> None:
        self.section = index
        label, filename = SECTIONS[index]
        table = self.query_one("#config-table", ConfigTable)
        row, column = table.cursor_row, max(table.cursor_column, GLOBAL)
        if filename is None:
            # Benchmark Suite: one row per entry, read-only for now
            globalList, projectList = Defaults.GlobalValue("benchmarkMethods"), Defaults._projectValues.get("benchmarkMethods")
            self.keys = ["benchmarkMethods"] * max(len(globalList), len(projectList or []))
            self.SetColumns([str(len(self.keys))])
            for entry in range(len(self.keys)):
                cells = [globalList[entry] if entry < len(globalList) else "",
                         (projectList[entry] if entry < len(projectList) else "") if projectList else "—"]
                table.add_row(str(entry), *(Text(Cut(cell, self.valueWidth), "dim") for cell in cells))
        else:
            self.keys = [key for key in Defaults._FILE_GROUPS[filename] if key not in HIDDEN and key != "benchmarkMethods"]
            self.SetColumns(self.keys)
            for key in self.keys:
                table.add_row(key, self.Cell(GLOBAL, key), self.Cell(PROJECT, key))
        table.move_cursor(row=min(row, len(self.keys) - 1), column=column, animate=False)
        where = f"{label} · {filename}" if filename else f"{label} · benchmarkMethods"
        self.query_one("#config-top", FrameRule).label = where
        self.query_one("#config-top", FrameRule).right = f"project: {self.root.name}" if self.root else ""
        self.RefreshDetails()

    def RefreshDetails(self, problem: str = "") -> None:
        current = self.Current()
        details = self.query_one("#config-details", Static)
        if current is None:
            details.update("")
        else:
            column, key = current
            comments = Defaults.ProjectComments() if column == PROJECT else Defaults._COMMENTS
            width = details.content_region.width
            default = "" if key in MASKED else f" · default {Shown(Defaults.DefaultValue(key))}"
            note = ("Editing comes with the Benchmark Suite editor." if key == "benchmarkMethods"
                    else "Read-only here." if key in READ_ONLY else "")
            # The whole value (the table cuts it to its column), then what kind of value it is
            value = self.Value(column, key) if column == GLOBAL or key in Defaults._PROJECT_KEYS else DELETE
            lines = [Text(Cut(comments.get(key, "").split("\n# ")[0], width)),
                     Text(Cut(f"Value: {Shown(value, key in MASKED)}", width)),
                     Text.assemble(Cut(f"{TYPE_NAMES[Defaults._TYPES[key]].capitalize()}{default}. ", width), Styled(note, "info"))]
            if problem:
                lines.append(Styled(problem, "error"))
            details.update(Text("\n").join(lines))
            scope = "Project override" if column == PROJECT else "Global"
            self.query_one("#config-rule", FrameRule).label = f"{key} · {scope}"
        count = self.Unsaved()
        self.query_one("#config-save", FrameRule).right = (
            Text.assemble(Styled(f"{count} unsaved", "warning"), " · ", KeyHint(self.app, "⏎", "Save")) if count
            else Styled("no changes", "dim"))
        self.refresh_bindings()

    def Redraw(self) -> None:
        self.ShowSection(self.section)

    # ─── Events ───────────────────────────────────────────────────────

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        index = self.query_one("#sections", SectionList).index
        if index is not None and index < len(SECTIONS) and index != self.section:
            self.ShowSection(index)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        self.query_one("#config-table").focus()

    def on_data_table_cell_highlighted(self, event: DataTable.CellHighlighted) -> None:
        # A click on the key column lands on the Global value instead. The table's own cursor, not the event's: clearing
        # the table queues a highlight of (0, 0) that arrives after the cursor has been put back
        if event.data_table.cursor_column < GLOBAL:
            event.data_table.move_cursor(column=GLOBAL, animate=False)
            return
        self.RefreshDetails()

    def on_input_changed(self, event: Input.Changed) -> None:
        if self.editing:
            self.RefreshDetails(ParseValue(self.editing[1], event.value)[1])

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if not self.editing:
            return
        value, problem = ParseValue(self.editing[1], event.value)
        if problem:
            self.RefreshDetails(problem)
            return
        self.SetPending(*self.editing, value)
        self.action_cancel_edit()

    # ─── Actions ──────────────────────────────────────────────────────

    def action_edit(self) -> None:
        current = self.Current()
        if current is None:
            return
        column, key = current
        if not self.Editable(column, key):
            if column == PROJECT and key in Defaults._PROJECT_KEYS and self.root is None:
                Notice(self.app, "No project", "Not inside a project: `cu -init` creates one.", "warning")
            return
        value = self.Value(column, key)
        # An empty project cell starts its override from the global value
        if value is DELETE:
            value = self.Value(GLOBAL, key)
            if Defaults._TYPES[key] is bool or key in CHOICES:
                self.SetPending(column, key, value)
                self.Redraw()
                return
        if Defaults._TYPES[key] is bool:
            self.SetPending(column, key, not value)
        elif key in CHOICES:
            options = CHOICES[key]
            self.SetPending(column, key, options[(options.index(value) + 1) % len(options)] if value in options else options[0])
        else:
            self.editing = (column, key)
            edit = self.query_one("#config-edit", EditInput)
            edit.value = value if isinstance(value, str) else tomlValue(value)
            edit.display = True
            edit.focus()
            self.RefreshDetails()
            return
        self.Redraw()

    def action_cancel_edit(self) -> None:
        self.editing = None
        self.query_one("#config-edit").display = False
        self.query_one("#config-table").focus()
        self.Redraw()

    def action_reset(self) -> None:
        # Global: back to the default (the cluster's setting, else the hardcoded one). Project: follow global
        current = self.Current()
        if current is None or not self.Editable(*current):
            return
        column, key = current
        self.SetPending(column, key, Defaults.DefaultValue(key) if column == GLOBAL else DELETE)
        self.Redraw()

    def action_reset_file(self) -> None:
        current = self.Current()
        filename = self.SectionFile()
        if current is None or filename is None:
            return
        if current[0] == PROJECT:
            if self.root is None:
                return
            message = f"Regenerate {PROJECT_CONFIG} as a snapshot of the global config (like `cu -init`)?"
            target = PROJECT_CONFIG
        else:
            message = f"Reset every editable key in {filename} to its default and regenerate the file?"
            target = filename

        def Apply(result: str) -> None:
            if result != "reset":
                return
            self.regenerate.add(target)
            if target == PROJECT_CONFIG:
                self.pending = {cell: value for cell, value in self.pending.items() if cell[0] != PROJECT}
            else:
                for key in Defaults._FILE_GROUPS[filename]:
                    if self.Editable(GLOBAL, key) and key not in HIDDEN:
                        self.SetPending(GLOBAL, key, Defaults.DefaultValue(key))
            self.Redraw()
        self.app.push_screen(Popup("Reset file", message, [("y", "Reset", "reset")], "warning"), Apply)

    def MethodMismatch(self) -> str:
        """Why methodNames and targetProgram don't pair up, or "". The Catalog zips them, dropping the extras."""
        names, programs = self.Value(GLOBAL, "methodNames"), self.Value(GLOBAL, "targetProgram")
        if len(names) == len(programs):
            return ""
        extras, lacking = (names[len(programs):], "no program") if len(names) > len(programs) else (programs[len(names):], "no method")
        return (f"methodNames has {len(names)} entries, targetProgram {len(programs)}: "
                f"{', '.join(repr(extra) for extra in extras)} {'has' if len(extras) == 1 else 'have'} {lacking}.\n"
                "Jobs ignore entries without a partner. Trim them and save,\nor keep editing to add the missing ones.")

    def TrimMethods(self) -> None:
        names, programs = self.Value(GLOBAL, "methodNames"), self.Value(GLOBAL, "targetProgram")
        count = min(len(names), len(programs))
        self.SetPending(GLOBAL, "methodNames", names[:count])
        self.SetPending(GLOBAL, "targetProgram", programs[:count])

    def Save(self, then) -> None:
        """Write every unsaved change, reload the config, say what was saved, then run then(). A method-list mismatch
        asks first; a file that can't be written is reported and the editor stays open."""
        mismatch = self.MethodMismatch()
        if mismatch:
            def Chosen(result: str) -> None:
                if result == "trim":
                    self.TrimMethods()
                    self.Save(then)
            self.app.push_screen(Popup("Method list mismatch", mismatch, [("t", "Trim & Save", "trim")], "warning",
                                       cancel="Keep editing"), Chosen)
            return
        written, failed = self.WriteFiles()
        if failed:
            Notice(self.app, "Save failed", f"Could not save {', '.join(failed)}; left unchanged.", "error")
            self.Redraw()
            return
        self.saved = True
        # Trimming (or undoing edits by hand) can leave nothing that differs from the files
        Notice(self.app, "Saved", f"Saved {', '.join(written)}." if written else "Nothing to save: the files already match.",
               then=then)

    def WriteFiles(self) -> tuple[list[str], list[str]]:
        """Write the pending changes and reload the config. (files written, files that failed)."""
        binDirectory = Path(Defaults.binDirectory)
        written, failed = [], []
        for filename, fileKeys in Defaults._FILE_GROUPS.items():
            updates = {key: value for (column, key), value in self.pending.items() if column == GLOBAL and key in fileKeys}
            if filename in self.regenerate:
                done = writeToml(binDirectory, filename, Defaults._BuildContent(
                    filename, lambda key: updates.get(key, Defaults._PersistedValue(key))))
            elif updates:
                done = Defaults._PatchFile(binDirectory / filename, updates)
            else:
                continue
            (written if done else failed).append(filename)
        projectUpdates = {key: value for (column, key), value in self.pending.items() if column == PROJECT}
        if self.root is not None and (projectUpdates or PROJECT_CONFIG in self.regenerate):
            configPath = ProjectFilePath(self.root, PROJECT_CONFIG)
            done = True
            if PROJECT_CONFIG in self.regenerate:
                done = writeToml(configPath.parent, PROJECT_CONFIG, Defaults.BuildProjectContent())
            if done and projectUpdates:
                done = SaveProjectConfig(self.root, projectUpdates)
            (written if done else failed).append(PROJECT_CONFIG)
        ReloadConfig()
        if failed:
            # What was written stays written; the rest is still shown as unsaved against the reloaded values
            self.pending = {cell: value for cell, value in self.pending.items()
                            if self.FileOf(*cell) in failed and value != self.SavedValue(*cell)}
            self.regenerate &= set(failed)
            return written, failed
        self.pending, self.regenerate = {}, set()
        return written, failed

    def FileOf(self, column: int, key: str) -> str:
        if column == PROJECT:
            return PROJECT_CONFIG
        return next(filename for filename, keys in Defaults._FILE_GROUPS.items() if key in keys)

    def action_save(self) -> None:
        # Back to Home, which re-lists its files (extensions and the project config may have changed)
        if self.Unsaved():
            self.Save(lambda: self.dismiss(True))

    def check_action(self, action: str, parameters) -> bool | None:
        # ⏎ Save shows dimmed while there is nothing to save
        return None if action == "save" and not self.Unsaved() else True

    def ConfirmLeave(self, leave) -> None:
        """Run leave(), first asking what to do with unsaved changes (also used by the app's ^q)."""
        if not self.Unsaved():
            leave()
            return

        def Chosen(result: str) -> None:
            if result == "discard":
                leave()
            elif result == "save":
                self.Save(leave)
        files = sorted({self.FileOf(*cell) for cell in self.pending} | self.regenerate)
        count = self.Unsaved()
        self.app.push_screen(Popup("Unsaved changes", f"{count} unsaved change{'s' if count != 1 else ''} in {', '.join(files)}.",
                                   [("s", "Save", "save"), ("d", "Discard", "discard")], "warning"), Chosen)

    def action_back(self) -> None:
        self.ConfirmLeave(lambda: self.dismiss(self.saved))

    def action_help(self) -> None:
        Notice(self.app, "Help", "\n".join([
            "↑/↓ move between keys", "←/→ Global or Project", "space edit (toggles true/false, cycles choices)",
            "r reset the value to its default (Project: follow global)", "R reset the whole file",
            "enter save and return home", "esc back", "ctrl+q quit"]))
