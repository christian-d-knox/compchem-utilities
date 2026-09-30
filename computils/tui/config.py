"""Config editor: global values and project overrides, one TOML file per section, patched in place, plus the project
files (TUI_DESIGN.md 4.4). Lists are edited in a ListEditor pop-up, project files in a TextEditor pop-up."""
import tomllib as tom
from itertools import zip_longest
from pathlib import Path

import regex
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import DataTable, Input, Label, ListItem, ListView, Static

from ..actions  import SpinState
from ..catalog  import MethodKey, ParseRouteTemplate
from ..defaults import DELETE, Defaults, tomlValue, writeToml
from ..fileops  import (PROGRAMS, RESERVED_ORCA_BLOCKS, ExtractFromText, ExtractMixedBasis, ExtractOrcaBlocks,
                        MixedBasis, extensionGetter)
from ..molecule import Molecule
from ..project  import (PROJECT_CONFIG, PROJECT_FILES, FindProjectRoot, ProjectFilePath, ReloadConfig,
                        ResolveProjectFile, SaveProjectConfig)
from ..spin     import ParseSpinOverrides
from .common    import NAV_BINDINGS, FrameRule, KeyHint, NavFooter, Notice, Popup
from .home      import TitleLine
from .inspect   import PreviewRow, RenderedRow, RowText, Styled
from .lists     import Cut, FitColumns, ListEditor, ProblemText, TextEditor

# (label, file). The divider and Project Files sit below the TOML sections, as in Home's Actions list
FILES_SECTION = "project files"
SECTIONS = [("SLURM", "slurm.toml"), ("Programs", "programs.toml"), ("Benchmark Suite", None),
            ("Extensions", "extensions.toml"), ("Notifications", "notifications.toml"),
            ("Quality of Life", "qol.toml"), ("Paths", "paths.toml"), ("──────────────", "divider"),
            ("Project Files", FILES_SECTION)]
# Set by first-time setup only (the TUI opens only once it has run), or power-user edits made by hand in the file
HIDDEN = {"hpcType", "submissionList", "gaussianNonVariant", "orcaNonVariant"}
READ_ONLY = {"binDirectory", "projectMarker", "botToken", "chatID", "broadcastGroupChatID", "broadcastThreshold"}
MASKED = {"botToken", "chatID", "broadcastGroupChatID"}
# Keys with a fixed set of values: ␣ cycles through them
CHOICES = {"openShellReference": ["U", "RO"], "colorMode": ["lowColor", "hexCode"]}
# methodNames and targetProgram are edited together, as one method -> program list
METHOD_MAP = ("methodNames", "targetProgram")
TYPE_NAMES = {int: "a whole number", float: "a number", str: "text", bool: "true or false", list: "a list"}
FILE_NOTES = {
    "mixedbasis.txt": "Gen/GenECP basis sets and ECPs for mixed-basis Gaussian jobs.",
    "orcablocks.txt": "ORCA %blocks, pulled into jobs by {tag} tokens in benchmarkMethods.",
    "spinstates.txt": "Per-molecule CSS/OSS overrides for singlets (glob  css|oss).",
}
# The Renders-as preview's stand-in molecules: (label, multiplicity, spin state)
STAND_INS = [("CSS", 1, SpinState.CSS), ("OSS", 1, SpinState.OSS), ("Doublet", 2, SpinState.OPEN),
             ("Triplet", 3, SpinState.OPEN)]
GLOBAL, PROJECT = 1, 2          # table columns (0 is the key)
FILES = 3                       # pending project-file text: self.pending[FILES, file name]


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
    if value is None:
        return None, f"Expected {TYPE_NAMES[expected]}."
    if key in CHOICES and value not in CHOICES[key]:
        return None, f"Expected one of {', '.join(CHOICES[key])}."
    return value, ""


def EmptyEntries(rows: list[list[str]]) -> list[str]:
    return [f"✗ Entry {position} is empty" for position, row in enumerate(rows, start=1) if not row[0].strip()]


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

    def __init__(self, section: str = "SLURM") -> None:
        super().__init__()
        self.root = FindProjectRoot()
        self.section = next(index for index, (label, _) in enumerate(SECTIONS) if label == section)
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
                yield SectionList(*[ListItem(Label(label), disabled=kind == "divider") for label, kind in SECTIONS],
                                  initial_index=self.section, id="sections")
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
        self.ShowSection(self.section)

    def on_resize(self, event) -> None:
        # Column widths follow the table's width
        self.call_after_refresh(self.Redraw)

    # ─── State ────────────────────────────────────────────────────────

    def SavedValue(self, column: int, key: str):
        if column == FILES:
            path = self.FilePath(key)
            return path.read_text(encoding="utf-8") if path is not None and path.is_file() else ""
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

    def FilesSection(self) -> bool:
        return SECTIONS[self.section][1] == FILES_SECTION

    def Current(self) -> tuple[int, str] | None:
        table = self.query_one("#config-table", ConfigTable)
        if not self.keys or table.cursor_row >= len(self.keys):
            return None
        return (FILES if self.FilesSection() else max(table.cursor_column, GLOBAL)), self.keys[table.cursor_row]

    def SectionFile(self) -> str | None:
        return SECTIONS[self.section][1]

    def Unsaved(self) -> int:
        return len(self.pending) + len(self.regenerate)

    # ─── Project files ────────────────────────────────────────────────

    def FilePath(self, name: str) -> Path | None:
        """The copy jobs would use (a CWD copy overrides the project's), else where a new one goes: the project marker."""
        found = ResolveProjectFile(name, False)
        if found is not None:
            return Path(found)
        return ProjectFilePath(self.root, name) if self.root is not None else None

    def PendingTexts(self) -> dict[str, str]:
        return {name: text for (column, name), text in self.pending.items() if column == FILES}

    def UsedTags(self) -> list[str]:
        """Every {tag} the (unsaved) benchmark suites use, global and project."""
        tags = []
        for column in (GLOBAL, PROJECT):
            entries = self.Value(column, "benchmarkMethods")
            for entry in ([] if entries is DELETE else entries):
                template = ParseRouteTemplate(entry)
                for tag in template.tags + [tag for _, _, groupTags in template.groups for tag in groupTags]:
                    if tag not in tags:
                        tags.append(tag)
        return tags

    def FileSummary(self, name: str, text: str) -> tuple[str, Text]:
        """(a short summary for the table, the full summary pane) of a project file's text."""
        lines = []
        if name == "orcablocks.txt":
            blocks = ExtractFromText(text, ExtractOrcaBlocks, empty={})
            used = self.UsedTags()
            lines.append(Text(f"Tags: {', '.join(blocks) or 'none'}"))
            if used:
                lines.append(Text("benchmarkMethods uses:"))
                lines += [Styled(f"  {tag} ✓", "good") if tag in blocks else Styled(f"  {tag} ✗ missing", "error") for tag in used]
            reserved = sorted({name.lower() for name in regex.findall(r"(?m)^%(\w+)", text)} & RESERVED_ORCA_BLOCKS)
            lines += [Styled(f"⚠ %{block} is written by CompUtils; ignored", "warning") for block in reserved]
            short = f"{len(blocks)} tag{'s' if len(blocks) != 1 else ''}"
        elif name == "mixedbasis.txt":
            basis = ExtractFromText(text, ExtractMixedBasis, empty=MixedBasis([], []))
            elements = [element for entry in basis.basis for element in entry.elements]
            ecp = [element for entry in basis.ecp for element in entry.elements]
            byCenter = sum(entry.byCenter for entry in basis.basis + basis.ecp)
            lines.append(Text(f"Basis: {' '.join(elements) or 'none'}"))
            lines.append(Text(f"ECP: {' '.join(ecp) or 'none'}"))
            if byCenter:
                lines.append(Styled(f"{byCenter} group(s) by center number (written as-is)", "info"))
            short = f"{len(elements)} elements · {len(ecp)} ECP"
        else:
            overrides, problems = ParseSpinOverrides(text)
            lines += [Text(f"{glob} → {state.name}") for glob, state in overrides] or [Text("No overrides")]
            lines += [Styled(f"✗ line {number}: {line}", "error") for number, line in problems]
            short = f"{len(overrides)} override{'s' if len(overrides) != 1 else ''}"
            if problems:
                short += f" · {len(problems)} bad"
        return short, Text("\n").join(lines)

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

    def ShowSection(self, index: int) -> None:
        self.section = index
        label, filename = SECTIONS[index]
        table = self.query_one("#config-table", ConfigTable)
        row, column = table.cursor_row, max(table.cursor_column, GLOBAL)
        if filename is None:
            # Benchmark Suite: one row per entry; ␣ opens the list in the Benchmark Suite editor
            lists = [self.Value(GLOBAL, "benchmarkMethods"), self.Value(PROJECT, "benchmarkMethods")]
            projectList = None if lists[1] is DELETE or self.root is None else lists[1]
            self.keys = ["benchmarkMethods"] * max(len(lists[0]), len(projectList or []))
            self.valueWidth = FitColumns(table, ["#", "Global", "Project"], len(str(len(self.keys))))
            for entry in range(len(self.keys)):
                cells = []
                for listColumn, entries in ((GLOBAL, lists[0]), (PROJECT, projectList)):
                    if entries is None:
                        cells.append(Text("—" if entry == 0 else "", "dim"))
                        continue
                    text = Cut(entries[entry], self.valueWidth - 2) if entry < len(entries) else ""
                    cells.append(Text(f"[{text}]" if text and (listColumn, "benchmarkMethods") in self.pending else text))
                table.add_row(str(entry), *cells)
        elif filename == FILES_SECTION:
            self.keys = list(PROJECT_FILES)
            self.valueWidth = FitColumns(table, ["File", "Location", "Summary"], max(map(len, self.keys)))
            for name in self.keys:
                path = self.FilePath(name)
                location = ("CWD" if Path(name).is_file() else "project" if path is not None and path.is_file()
                            else "missing")
                short, _ = self.FileSummary(name, self.Value(FILES, name))
                edited = "[edited] " if (FILES, name) in self.pending else ""
                table.add_row(name, Text(location, "dim" if location == "missing" else ""),
                              Text(Cut(edited + short, self.valueWidth)))
        else:
            self.keys = [key for key in Defaults._FILE_GROUPS[filename] if key not in HIDDEN]
            self.valueWidth = FitColumns(table, ["Key", "Global", "Project"], max(map(len, self.keys)))
            for key in self.keys:
                table.add_row(key, self.Cell(GLOBAL, key), self.Cell(PROJECT, key))
        table.move_cursor(row=min(row, len(self.keys) - 1), column=column, animate=False)
        where = (f"{label} · {filename}" if filename not in (None, FILES_SECTION)
                 else f"{label} · benchmarkMethods" if filename is None
                 else f"{label} · {self.root.name}/{Defaults.projectMarker}" if self.root else f"{label} · no project")
        self.query_one("#config-top", FrameRule).label = where
        self.query_one("#config-top", FrameRule).right = f"project: {self.root.name}" if self.root else ""
        self.RefreshDetails()

    def RefreshDetails(self, problem: str = "") -> None:
        current = self.Current()
        details = self.query_one("#config-details", Static)
        width = details.content_region.width
        if current is None:
            details.update("")
        elif current[0] == FILES:
            name = current[1]
            path = self.FilePath(name)
            lines = [Text(Cut(FILE_NOTES[name], width)),
                     Text(Cut(f"Path: {path if path is not None else 'none (not inside a project)'}", width)),
                     Styled("␣ edits the file · r discards unsaved edits", "info")]
            details.update(Text("\n").join(lines))
            self.query_one("#config-rule", FrameRule).label = name
        else:
            column, key = current
            comments = Defaults.ProjectComments() if column == PROJECT else Defaults._COMMENTS
            default = "" if key in MASKED else f" · default {Shown(Defaults.DefaultValue(key))}"
            isList = Defaults._TYPES[key] is list
            note = ("Read-only here." if key in READ_ONLY else "␣ opens the Benchmark Suite editor." if key == "benchmarkMethods"
                    else "␣ opens the Method → Program editor." if key in METHOD_MAP
                    else "␣ opens the list editor." if isList else "")
            # The whole value (the table cuts it to its column), then what kind of value it is
            value = self.Value(column, key) if column == GLOBAL or key in Defaults._PROJECT_KEYS else DELETE
            lines = [Text(Cut(comments.get(key, "").split("\n# ")[0], width)),
                     Text(Cut(f"Value: {Shown(value, key in MASKED)}", width)),
                     Text.assemble(Cut(f"{TYPE_NAMES[Defaults._TYPES[key]].capitalize()}"
                                       f"{'' if isList else default}. ", width), Styled(note, "info"))]
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

    # ─── Pop-up editors ───────────────────────────────────────────────

    def EditList(self, column: int, key: str) -> None:
        """A list value in a ListEditor: the Method → Program pair, the Benchmark Suite, or any other list key."""
        table = self.query_one("#config-table", ConfigTable)
        if key in METHOD_MAP:
            # Program codes are shown by name (G16, ORCA) and stored as codes
            names, codes = self.Value(GLOBAL, "methodNames"), self.Value(GLOBAL, "targetProgram")
            nameOf = {code: name for code, (name, _) in PROGRAMS.items()}
            codeOf = {name: code for code, name in nameOf.items()}
            rows = [[method, nameOf.get(code, code)] for method, code in zip_longest(names, codes, fillvalue="")]

            def Check(rows: list[list[str]]) -> list[str]:
                problems, seen = [], set()
                for position, (method, program) in enumerate(rows, start=1):
                    if not method.strip():
                        problems.append(f"✗ Entry {position} has no method")
                    elif not program:
                        problems.append(f"✗ {method} has no program")
                    if method and MethodKey(method) in seen:
                        problems.append(f"⚠ {method} is listed twice (the first wins)")
                    seen.add(MethodKey(method))
                return problems

            def Done(rows) -> None:
                if rows is not None:
                    self.SetPending(GLOBAL, "methodNames", [method.strip() for method, _ in rows])
                    self.SetPending(GLOBAL, "targetProgram", [codeOf.get(program, program) for _, program in rows])
                    self.Redraw()
            self.app.push_screen(ListEditor("Methods → Programs · programs.toml",
                                            [("Method", None), ("Program", list(codeOf))], rows,
                                            check=Check), Done)
            return
        values = self.Value(column, key)
        if values is DELETE:
            # An empty project cell starts its override from the global list
            values = self.Value(GLOBAL, key)
        scope = "Project" if column == PROJECT else "Global"

        def DoneList(rows) -> None:
            if rows is not None:
                self.SetPending(column, key, [row[0].strip() for row in rows])
                self.Redraw()
        if key == "benchmarkMethods":
            self.app.push_screen(ListEditor(f"Benchmark Suite · {scope}", [("Route card", None)], [[entry] for entry in values],
                                            start=table.cursor_row, previewTitle="Renders as",
                                            check=lambda rows: EmptyEntries(rows) or ([] if rows else ["✗ The suite needs at least one entry"]),
                                            preview=lambda row: self.RendersAs(row[0])), DoneList)
        else:
            self.app.push_screen(ListEditor(f"{key} · {scope}", [(key, None)], [[entry] for entry in values],
                                            check=EmptyEntries), DoneList)

    def RendersAs(self, entry: str) -> Text:
        """A route card rendered for stand-in molecules, with what would stop it: the program comes from the unsaved
        method map, and project-file problems are checked against unsaved project-file text."""
        if not entry.strip():
            return Text("")
        template = ParseRouteTemplate(entry)
        programOf = dict(zip(self.Value(GLOBAL, "methodNames"), self.Value(GLOBAL, "targetProgram")))
        extension = extensionGetter(template.method, programOf)
        lines, problems = [], []
        for label, multiplicity, spin in STAND_INS:
            molecule = Molecule(Path("preview"), "preview", 0, multiplicity, 0, extension, "preview", spin)
            row = RenderedRow(PreviewRow(label, spin.name), template, molecule, set(), self.PendingTexts())
            if row.problem and row.problem not in problems:
                problems.append(row.problem)
            row.problem = ""
            lines.append(Text(f"{label:<9}") + RowText(row))
        if template.method not in programOf:
            problems.insert(0, f"{template.method or 'The first token'} is not in methodNames: it would run as Gaussian16")
        tags = template.tags + [tag for _, _, groupTags in template.groups for tag in groupTags]
        if tags and extension != Defaults.orcaExtension:
            problems.append(f"{{tags}} only apply to ORCA jobs; ignored for {template.method}")
        return Text("\n").join(lines + ([ProblemText([f"⚠ {problem}" for problem in problems])] if problems else []))

    def EditFile(self, name: str) -> None:
        path = self.FilePath(name)
        if path is None:
            Notice(self.app, "No project", f"Not inside a project and no ./{name}: `cu -init` creates a project.", "warning")
            return

        def Done(text) -> None:
            if text is not None:
                self.SetPending(FILES, name, text)
                self.Redraw()
        where = path.parent.name if path.parent != Path(".") else "CWD"
        self.app.push_screen(TextEditor(f"{name} · {where}", self.Value(FILES, name),
                                        lambda text: self.FileSummary(name, text)[1]), Done)

    # ─── Events ───────────────────────────────────────────────────────

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        index = self.query_one("#sections", SectionList).index
        if index is not None and SECTIONS[index][1] != "divider" and index != self.section:
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
        if column == FILES:
            self.EditFile(key)
            return
        if not self.Editable(column, key):
            if column == PROJECT and key in Defaults._PROJECT_KEYS and self.root is None:
                Notice(self.app, "No project", "Not inside a project: `cu -init` creates one.", "warning")
            return
        if Defaults._TYPES[key] is list:
            self.EditList(column, key)
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
            edit.value = str(value)
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
        # Global: back to the default (the cluster's setting, else the hardcoded one). Project: follow global.
        # A project file: drop its unsaved edits
        current = self.Current()
        if current is None:
            return
        column, key = current
        if column == FILES:
            self.pending.pop(current, None)
        elif self.Editable(column, key):
            # The method map resets as a pair, so the two lists stay the same length
            for resetKey in (METHOD_MAP if key in METHOD_MAP else (key,)):
                self.SetPending(column, resetKey, Defaults.DefaultValue(resetKey) if column == GLOBAL else DELETE)
        self.Redraw()

    def action_reset_file(self) -> None:
        current = self.Current()
        filename = self.SectionFile()
        if current is None or filename in (None, FILES_SECTION):
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
        """Why methodNames and targetProgram don't pair up, or "". The Catalog zips them, dropping the extras.
        Edits made in the Method → Program editor always pair up; this catches a file edited by hand."""
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
        # Project files are written exactly as edited
        for name, text in self.PendingTexts().items():
            path = self.FilePath(name)
            (written if writeToml(path.parent, path.name, text) else failed).append(name)
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
        if column == FILES:
            return key
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
            "↑/↓ move between keys", "←/→ Global or Project",
            "space edit: toggles true/false, cycles choices, opens lists and project files in a pop-up editor",
            "r reset the value to its default (Project: follow global; a project file: drop its edits)",
            "R reset the whole file", "enter save and return home", "esc back", "ctrl+q quit"]))
