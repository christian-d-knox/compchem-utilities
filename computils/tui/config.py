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
from .common    import NAV_BINDINGS, ConfirmScreen, FrameRule, KeyHint, NavFooter
from .home      import TitleLine
from .inspect   import Styled

# (label, file); the Benchmark Suite lists benchmarkMethods read-only until its own editor exists
SECTIONS = [("SLURM", "slurm.toml"), ("Programs", "programs.toml"), ("Benchmark Suite", None),
            ("Extensions", "extensions.toml"), ("Notifications", "notifications.toml"),
            ("Quality of Life", "qol.toml"), ("Paths", "paths.toml")]
# Set by first-time setup only (the TUI opens only once it has run)
HIDDEN = {"hpcType", "submissionList"}
READ_ONLY = {"binDirectory", "projectMarker", "gaussianNonVariant", "orcaNonVariant", "benchmarkMethods",
             "botToken", "chatID", "broadcastGroupChatID", "broadcastThreshold"}
MASKED = {"botToken"}
# Keys with a fixed set of values: ␣ cycles through them
CHOICES = {"openShellReference": ["U", "RO"], "colorMode": ["lowColor", "hexCode"]}
TYPE_NAMES = {int: "a whole number", float: "a number", str: "text", bool: "true or false",
              list: 'a list, e.g. ["a", "b"]'}
GLOBAL, PROJECT = 1, 2          # table columns (0 is the key)
SHOWN_WIDTH = 30                # longer values are cut with …


def Shown(value) -> str:
    if value is DELETE:
        return "—"
    text = tomlValue(value) if isinstance(value, (bool, list)) else str(value)
    return text if len(text) <= SHOWN_WIDTH else text[:SHOWN_WIDTH - 1] + "…"


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
                table = ConfigTable(id="config-table", classes="side", cursor_type="cell", zebra_stripes=False)
                table.add_columns("Key", "Global", "Project")
                yield table
                yield FrameRule("├┤", id="config-rule")
                yield Static(id="config-details", classes="side")
                yield EditInput(id="config-edit", classes="side")
                yield FrameRule("└┘", id="config-save")
        yield NavFooter()

    def on_mount(self) -> None:
        self.query_one("#config-edit").display = False
        self.ShowSection(0)

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
        text = "••••" if key in MASKED and value else Shown(value)
        cell = Text(f"[{text}]" if (column, key) in self.pending else text, "dim" if key in READ_ONLY else "")
        # • marks a project value, which is the one in force
        if column == PROJECT and value is not DELETE:
            cell.append("•", "bold")
        return cell

    def ShowSection(self, index: int) -> None:
        self.section = index
        label, filename = SECTIONS[index]
        table = self.query_one("#config-table", ConfigTable)
        row, column = table.cursor_row, max(table.cursor_column, GLOBAL)
        table.clear()
        if filename is None:
            # Benchmark Suite: one row per entry, read-only for now
            globalList, projectList = Defaults.GlobalValue("benchmarkMethods"), Defaults._projectValues.get("benchmarkMethods")
            self.keys = ["benchmarkMethods"] * max(len(globalList), len(projectList or []))
            for entry in range(len(self.keys)):
                cells = [globalList[entry] if entry < len(globalList) else "",
                         (projectList[entry] if entry < len(projectList) else "") if projectList else "—"]
                table.add_row(str(entry), *(Text(cell, "dim") for cell in cells))
        else:
            self.keys = [key for key in Defaults._FILE_GROUPS[filename] if key not in HIDDEN and key != "benchmarkMethods"]
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
            default = "" if key in MASKED else f" · default {Shown(Defaults.DefaultValue(key))}"
            note = ("Editing comes with the Benchmark Suite editor." if key == "benchmarkMethods"
                    else "Read-only here." if key in READ_ONLY else "")
            lines = [Text(comments.get(key, "").split("\n# ")[0]),
                     Text.assemble(f"{TYPE_NAMES[Defaults._TYPES[key]].capitalize()}{default}. ", Styled(note, "info"))]
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
                self.notify("Not inside a project: `cu -init` creates one.", severity="warning")
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
        self.app.push_screen(ConfirmScreen(message, [("y", "Reset", "reset")]), Apply)

    def Problem(self) -> str:
        names, programs = self.Value(GLOBAL, "methodNames"), self.Value(GLOBAL, "targetProgram")
        if len(names) != len(programs):
            return f"methodNames has {len(names)} entries but targetProgram has {len(programs)}; they must match."
        return ""

    def Save(self) -> bool:
        """Write every unsaved change, then reload the config. False (screen stays open) if a file could not be written."""
        problem = self.Problem()
        if problem:
            self.notify(problem, severity="error")
            return False
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
            self.notify(f"Could not save {', '.join(failed)}; left unchanged.", severity="error")
            self.Redraw()
            return False
        self.pending, self.regenerate = {}, set()
        self.notify(f"Saved {', '.join(written)}." if written else "Nothing to save.")
        return True

    def FileOf(self, column: int, key: str) -> str:
        if column == PROJECT:
            return PROJECT_CONFIG
        return next(filename for filename, keys in Defaults._FILE_GROUPS.items() if key in keys)

    def action_save(self) -> None:
        if self.Unsaved() and self.Save():
            # Home re-lists its files, since extensions and the project config may have changed
            self.dismiss(True)

    def check_action(self, action: str, parameters) -> bool | None:
        # ⏎ Save shows dimmed while there is nothing to save
        return None if action == "save" and not self.Unsaved() else True

    def ConfirmLeave(self, leave) -> None:
        """Run leave(), first asking what to do with unsaved changes (also used by the app's ^q)."""
        if not self.Unsaved():
            leave()
            return

        def Chosen(result: str) -> None:
            if result == "discard" or (result == "save" and self.Save()):
                leave()
        self.app.push_screen(ConfirmScreen(f"{self.Unsaved()} unsaved change(s).",
                                           [("s", "Save", "save"), ("d", "Discard", "discard")]), Chosen)

    def action_back(self) -> None:
        saved = bool(self.Unsaved())
        self.ConfirmLeave(lambda: self.dismiss(saved and not self.Unsaved()))

    def action_help(self) -> None:
        self.notify("↑/↓ move between keys · ←/→ Global or Project · space edit (toggles true/false, cycles choices) · "
                    "r reset the value to its default (Project: follow global) · R reset the whole file · "
                    "enter save and return home · esc back · ctrl+q quit", title="Help")
