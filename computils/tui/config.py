"""Config editor: global values and project overrides, one TOML file per section, patched in place; jobRelatedKeylists'
sections (Job Keylists ›, a sub-menu: one section at a time, per level); and the project files (TUI_DESIGN.md 4.4,
4.12). Lists are edited in a ListEditor pop-up, keylist sections and project files in a TextEditor pop-up."""
import textwrap
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
from ..catalog  import MethodKey, ParseRouteTemplate, UnknownSelectors
from ..defaults import DELETE, Defaults, tomlValue, writeToml
from ..fileops  import PROGRAMS, RESERVED_ORCA_BLOCKS, TAG_DIRECTIVE, ExtractFromText, StageProblem, extensionGetter
from ..jobs     import RenderStages
from ..keylists import (BENCHMARK, FILE_PREAMBLE, KEYLISTS_FILE, MIXED_BASIS, OPTIMIZATION, ORCA_BLOCKS,
                        SECTIONS as KEYLIST_SECTIONS, FileProblems, Keylist, KeylistSource, LevelSections, Levels,
                        RemoveSection, ReplaceSection, RouteAt, SectionBody, WriteKeylists)
from ..molecule import Molecule
from ..project  import (PROJECT_CONFIG, PROJECT_FILES, FindProjectRoot, ProjectFilePath, ReloadConfig,
                        SaveProjectConfig)
from ..spin     import ParseSpinOverrides
from .common    import NAV_BINDINGS, EditableTable, FrameRule, KeyHint, NavFooter, Notice, Popup, SwapItems
from .home      import TitleLine
from .inspect   import PreviewRow, ProgramName, RenderedRow, RowText, Styled
from .lists     import Cut, FitColumns, ListEditor, ProblemText, TextEditor

# (label, kind): a TOML file, the divider, Job Keylists › (a sub-menu of jobRelatedKeylists' sections), or the project
# files. The divider and Project Files sit below the TOML sections, as in Home's Actions list
FILES_SECTION = "project files"
KEYLISTS_MENU = "keylists"          # Job Keylists ›: swaps the list for KEYLIST_ENTRIES (as Home's Input Builders ›)
KEYLISTS_BACK = "keylists-back"     # its ‹ Config item (esc too)
KEYLIST = "keylist:"                # a section's kind: KEYLIST + its name
SECTIONS = [("SLURM", "slurm.toml"), ("Programs", "programs.toml"), ("Job Keylists ›", KEYLISTS_MENU),
            ("Extensions", "extensions.toml"), ("Notifications", "notifications.toml"),
            ("Quality of Life", "qol.toml"), ("Paths", "paths.toml"), ("──────────────", "divider"),
            ("Project Files", FILES_SECTION)]
# One entry per section, so a new section is one keylists.SECTIONS entry
KEYLIST_ENTRIES = [("‹ Config", KEYLISTS_BACK)] + [(name, KEYLIST + name) for name in KEYLIST_SECTIONS]
# The project files edited here as whole files (jobRelatedKeylists is edited by section)
FILE_KEYS = [name for name in PROJECT_FILES if name != KEYLISTS_FILE]
# Set by first-time setup only (the TUI opens only once it has run), or power-user edits made by hand in the file
HIDDEN = {"hpcType", "submissionList", "gaussianNonVariant", "orcaNonVariant"}
READ_ONLY = {"binDirectory", "projectMarker", "botToken", "chatID", "broadcastGroupChatID", "broadcastThreshold"}
MASKED = {"botToken", "chatID", "broadcastGroupChatID"}
# Keys with a fixed set of values: ␣ cycles through them
CHOICES = {"openShellReference": ["U", "RO"], "colorMode": ["lowColor", "hexCode"]}
# methodNames and targetProgram are edited together, as one method -> program list
METHOD_MAP = ("methodNames", "targetProgram")
TYPE_NAMES = {int: "A Whole Number", float: "A Number", str: "Text", bool: "True or False", list: "A List"}
FILE_NOTES = {
    OPTIMIZATION: "Named optimization routes (G16 and ORCA), listed by the Optimization builder.",
    BENCHMARK: "The benchmark suite, one route per entry: entry 0 is Single Point's default.",
    MIXED_BASIS: "Gen/GenECP basis sets and ECPs for mixed-basis Gaussian jobs.",
    ORCA_BLOCKS: "ORCA %blocks, pulled into ORCA jobs by {tag} tokens in the method routes.",
    "spinstates.txt": "Per-molecule CSS/OSS overrides for singlets (glob  css|oss).",
}
# How route cards work, in the method sections' Example panel: (syntax, meaning) rows, then a closing line
ROUTE_GUIDE_ROWS = [
    ("> route", "A later stage of the route above it, in the same job"),
    ("[css: keywords]", "Closed-shell singlets only"),
    ("[oss: guess=mix]", "Open-shell (broken-symmetry) singlets only"),
    ("[open: stable=opt]", "Every open shell multiplicity"),
    ("[triplet: keywords]", "One multiplicity by name: doublet, triplet … septet"),
    ("[oss, triplet: …]", "Several selectors share one group"),
    ("{cpcm}", "Adds the cpcm block from ORCA Blocks (ORCA only)"),
]
ROUTE_GUIDE_CLOSING = "Groups apply in the order written. U / RO (UKS, UHF…) is added for you."
# The Example panel is 32 columns wide
EXAMPLE_WIDTH = 32


def RouteGuide() -> str:
    """ROUTE_GUIDE_ROWS for the 32-column Example panel: each syntax, its meaning indented below it."""
    lines = []
    for syntax, meaning in ROUTE_GUIDE_ROWS:
        lines += [syntax] + textwrap.wrap(meaning, EXAMPLE_WIDTH, initial_indent="  ", subsequent_indent="  ")
    return "\n".join(lines + textwrap.wrap(ROUTE_GUIDE_CLOSING, EXAMPLE_WIDTH))


# The lower half of each editor: the format, by example (lines fit its 32-column panel)
FILE_EXAMPLES = {
    OPTIMIZATION: """\
# @intermediate
M06/SDD opt freq

# @min-oss
M06/SDD opt [oss: guess=mix]

# @ts-hard
M06/SDD opt=modredundant
> M06/SDD stable=opt
> M06/SDD opt=(ts,calcfc) freq

# @ts-orca
r2SCAN-3c OptTS Freq {tshess}

One route per line, named by the
# @NAME line above it (else its
method).

""" + RouteGuide(),
    BENCHMARK: """\
M062X 6-311+G(d,p)
B3LYP/GenECP [oss: guess=mix]
M06/SDD stable=opt
> M06/SDD
DLPNO-CCSD(T) def2-TZVP {cpcm}

One route per line, in order:
entry 0 is Single Point's
default, and Benchmark runs
every entry from the chosen one.

""" + RouteGuide(),
    ORCA_BLOCKS: """\
%cpcm
    smd true
    SMDsolvent "water"
end

# @toluene
%cpcm
    smd true
    SMDsolvent "toluene"
end

%tddft
    nroots 10
end
Tag: its %name, or the # @NAME
(or # @tag NAME) line above it.
Use them in routes:
{cpcm} {toluene} {tddft}""",
    MIXED_BASIS: """\
C H N O 0
6-31G(d)
****
Fe Cu 0
SDD
****

Fe Cu 0
SDD

Basis groups end with ****.
The first blank line starts the
ECP section. Each job keeps only
the elements it contains.""",
    "spinstates.txt": """\
Fe2S2_*     oss
Cu2O2_bis   oss
Ni_sqpl_*   css   # a comment

<name or glob>  css|oss
Matched against the file name;
the first match wins. Singlets
only: higher spins come from
the multiplicity.""",
}
# The Renders-as preview's stand-in molecules: (label, multiplicity, spin state)
STAND_INS = [("CSS", 1, SpinState.CSS), ("OSS", 1, SpinState.OSS), ("Doublet", 2, SpinState.OPEN),
             ("Triplet", 3, SpinState.OPEN)]
GLOBAL, PROJECT = 1, 2          # table columns (0 is the key)
FILES = 3                       # pending project-file text: self.pending[FILES, file name]
SECTION_TEXT = 4                # pending keylist section text: self.pending[SECTION_TEXT, (level label, section)]
OVERVIEW = 5                    # Job Keylists › itself: one row per section (␣ enters it)


def TemplateTags(template) -> list[str]:
    """The {tags} a route card uses, unconditional and in its spin groups."""
    return template.tags + [tag for _, _, groupTags in template.groups for tag in groupTags]


def ShortPath(path: Path) -> str:
    """A path for a table cell: the home folder as ~."""
    try:
        return "~/" + path.relative_to(Path.home()).as_posix()
    except ValueError:
        return path.as_posix()


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


class ConfigTable(EditableTable):
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
        # The section list's entries: SECTIONS, or KEYLIST_ENTRIES while in Job Keylists ›
        self.entries = list(SECTIONS)
        self.section = next(index for index, (label, _) in enumerate(self.entries) if label == section)
        # jobRelatedKeylists' levels, nearest first (the project can't change while the editor is open)
        self.levels = Levels()
        self.keys: list[str] = []                   # the table's rows
        self.pending: dict[tuple[int, str], object] = {}   # (column, key) -> unsaved value (DELETE: follow global)
        self.regenerate: set[str] = set()           # files to regenerate whole on save (R)
        self.editing: tuple[int, str] | None = None
        self.saved = False                          # anything written (Home re-lists its files)
        self.valueWidth = 14

    def compose(self) -> ComposeResult:
        project = f"Project: {self.root.name}" if self.root else "No Project"
        yield Static(TitleLine("Config", Styled(project, "info")), id="title")
        with Horizontal(id="body"):
            with Vertical(id="left", classes="pane") as left:
                left.border_title = "Sections"
                yield SectionList(*self.EntryItems(), initial_index=self.section, id="sections")
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

    def SavedValue(self, column: int, key):
        if column == SECTION_TEXT:
            return self.SavedSection(*key)
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

    def SetPending(self, column: int, key, value) -> None:
        saved = self.SavedValue(column, key)
        # Removing a section a level doesn't have changes nothing
        if value == saved or (value is DELETE and saved is None):
            self.pending.pop((column, key), None)
        else:
            self.pending[column, key] = value

    def Editable(self, column: int, key: str) -> bool:
        if key in READ_ONLY:
            return False
        return column == GLOBAL or (key in Defaults._PROJECT_KEYS and self.root is not None)

    def Kind(self) -> str:
        return self.entries[self.section][1]

    def FilesSection(self) -> bool:
        return self.Kind() == FILES_SECTION

    def Current(self) -> tuple[int, object] | None:
        """(column, key) under the cursor: a TOML key's Global or Project cell, a project file (FILES), a keylist
        section at one level (SECTION_TEXT, (level label, section)), or a section in Job Keylists' overview."""
        table = self.query_one("#config-table", ConfigTable)
        if not self.keys or table.cursor_row >= len(self.keys):
            return None
        kind, key = self.Kind(), self.keys[table.cursor_row]
        if kind in (KEYLISTS_MENU, KEYLISTS_BACK):
            return OVERVIEW, key
        if kind.startswith(KEYLIST):
            return SECTION_TEXT, (key, kind[len(KEYLIST):])
        return (FILES if self.FilesSection() else max(table.cursor_column, GLOBAL)), key

    def SectionFile(self) -> str | None:
        return self.Kind()

    def EntryItems(self) -> list[ListItem]:
        return [ListItem(Label(label), disabled=kind == "divider") for label, kind in self.entries]

    def Unsaved(self) -> int:
        return len(self.pending) + len(self.regenerate)

    # ─── Project files ────────────────────────────────────────────────

    def FilePath(self, name: str) -> Path | None:
        """The project's copy (config is Global / Project only, never the CWD), outside a project None."""
        return ProjectFilePath(self.root, name) if self.root is not None else None

    def FileTexts(self) -> dict[str, str]:
        """The project files' unsaved text, by name."""
        return {name: text for (column, name), text in self.pending.items() if column == FILES}

    def FileSummary(self, name: str, text: str) -> tuple[str, Text]:
        """(a short summary for the table, the full summary pane) of a project file's text (spinstates.txt)."""
        overrides, problems = ParseSpinOverrides(text)
        lines = [Text(f"{glob} → {state.name}") for glob, state in overrides] or [Text("No Overrides")]
        lines += [Styled(f"✗ Line {number}: {line}", "error") for number, line in problems]
        short = f"{len(overrides)} Override{'s' if len(overrides) != 1 else ''}"
        if problems:
            short += f" · {len(problems)} Bad"
        return short, Text("\n").join(lines)

    # ─── Job Keylists (jobRelatedKeylists, one section at a time) ─────

    def Level(self, label: str):
        return next(level for level in self.levels if level.label == label)

    def SavedSection(self, label: str, name: str) -> str | None:
        """A level's section as saved: its jobRelatedKeylists section, else the old file or TOML key standing in for it
        (keylists.LevelSections); None when the level doesn't have the section."""
        level = self.Level(label)
        if level.path.is_file():
            body = SectionBody(level.path.read_text(encoding="utf-8"), name)
            if body is not None:
                return body
        found = LevelSections(level).get(name)
        if found is None:
            return None
        if found[1].suffix == ".toml":
            return "\n".join(found[0])
        return found[1].read_text(encoding="utf-8").strip("\r\n")

    def InFile(self, label: str, name: str) -> bool:
        """Whether the level's own jobRelatedKeylists has the section (saved or pending), not an old file standing in."""
        pending = self.pending.get((SECTION_TEXT, (label, name)))
        if pending is not None:
            return pending is not DELETE
        path = self.Level(label).path
        return path.is_file() and SectionBody(path.read_text(encoding="utf-8"), name) is not None

    def PendingTexts(self) -> dict[Path, str]:
        """{level path: its jobRelatedKeylists text with the unsaved section edits applied}, for the levels with any.
        It stands in for the files everywhere the editor checks (keylists' texts)."""
        texts = {}
        for (column, key), value in self.pending.items():
            if column != SECTION_TEXT:
                continue
            label, name = key
            path = self.Level(label).path
            text = texts.get(path) or (path.read_text(encoding="utf-8") if path.is_file() else FILE_PREAMBLE + "\n")
            texts[path] = RemoveSection(text, name) if value is DELETE else ReplaceSection(text, name, value)
        return texts

    def InForce(self, name: str) -> str:
        """The level whose section applies, with the unsaved edits ('' when no level has it)."""
        source = KeylistSource(name, self.PendingTexts())
        return source[0].label if source is not None else ""

    def Location(self, label: str, name: str) -> str:
        """Where a level's section is (or would be) written: its jobRelatedKeylists, or the old file standing in."""
        level = self.Level(label)
        if not self.InFile(label, name):
            found = LevelSections(level).get(name)
            if found is not None:
                return f"{found[1].name} (Old)"
            if not level.path.is_file():
                return "—"
        return ShortPath(level.path.parent)

    def UsedTags(self) -> dict[str, list[str]]:
        """Every {tag} the method sections in force use (with the unsaved edits), by section."""
        texts = self.PendingTexts()
        routes = {OPTIMIZATION: list(Keylist(OPTIMIZATION, texts).values()), BENCHMARK: list(Keylist(BENCHMARK, texts))}
        used = {}
        for source, entries in routes.items():
            tags = used.setdefault(source, [])
            for entry in entries:
                tags += [tag for stage in ParseRouteTemplate(entry).stages for tag in TemplateTags(stage)
                         if tag not in tags]
        return used

    def SectionSummary(self, name: str, body: str | None) -> tuple[str, Text]:
        """(a short summary for the tables, the editor's Check pane) of a section's text at one level."""
        if body is None or body is DELETE:
            return "—", Text("Not at this level: a project without it follows the global one.", "dim")
        section = KEYLIST_SECTIONS[name]
        value = ExtractFromText(body, section.extractor, empty=section.empty())
        lines, problems = [], []
        if name in (OPTIMIZATION, BENCHMARK):
            # Each route's program from the unsaved method map; its {tags} against the ORCA Blocks in force
            programOf = dict(zip(self.Value(GLOBAL, "methodNames"), self.Value(GLOBAL, "targetProgram")))
            blocks = Keylist(ORCA_BLOCKS, self.PendingTexts())
            routes = list(value.items()) if name == OPTIMIZATION else list(enumerate(value))
            counted = (f"{len(routes)} Method{'s' if len(routes) != 1 else ''}" if name == OPTIMIZATION
                       else f"{len(routes)} {'Entry' if len(routes) == 1 else 'Entries'}")
            lines.append(Text(counted if routes else "No Routes"))
            for label, route in routes:
                template = ParseRouteTemplate(route)
                program = ProgramName(template.method, programOf)
                stages = len(template.stages)
                lines.append(Text(f"  {label} → {program or 'G16'}" + (f" ({template.method})" if name == BENCHMARK else "")
                                  + (f" · {stages} Stages" if stages > 1 else "")))
                stageProblem = StageProblem(template, programOf)
                if stageProblem:
                    problems.append(f"✗ {label}: {stageProblem}")
                problems += [f"✗ {label}: Unknown Selector {selector}" for selector in UnknownSelectors(route)]
                if not program:
                    problems.append(f"⚠ {label}: {template.method} Not in methodNames (Runs as G16)")
                elif program == "ORCA":
                    problems += [f"✗ {label}: ORCA Blocks Has No {{{block}}}" for stage in template.stages
                                 for block in TemplateTags(stage) if block not in blocks]
            # The chaining is said, not left implicit (the strip shows what it adds to each stage)
            if any(len(ParseRouteTemplate(route).stages) > 1 for _, route in routes):
                lines.append(Styled("Stages share one checkpoint; later stages read the geometry and orbitals from "
                                    "it (Geom=AllCheck, Guess=Read). Renders As shows each.", "info"))
            if name == OPTIMIZATION:
                # A name declared twice is numbered, not dropped (two unnamed routes of one method are expected)
                declared = [found.lower() for found in regex.findall(rf"(?m)^\s*{TAG_DIRECTIVE}", body)]
                problems += [f"⚠ @{found} Names {declared.count(found)} Routes (Second Is {found}-2)"
                             for found in dict.fromkeys(declared) if declared.count(found) > 1]
            elif not routes:
                problems.append("⚠ No Routes: the Global (or Default) Suite Applies")
            short = counted
        elif name == MIXED_BASIS:
            elements = [element for entry in value.basis for element in entry.elements]
            ecp = [element for entry in value.ecp for element in entry.elements]
            byCenter = sum(entry.byCenter for entry in value.basis + value.ecp)
            lines.append(Text(f"Basis: {' '.join(elements) or 'None'}"))
            lines.append(Text(f"ECP: {' '.join(ecp) or 'None'}"))
            if byCenter:
                lines.append(Styled(f"{byCenter} Group(s) by Center Number (Written As-Is)", "info"))
            short = f"{len(elements)} Element{'s' if len(elements) != 1 else ''} · {len(ecp)} ECP"
        else:
            lines.append(Text(f"Tags: {', '.join(value) or 'None'}"))
            for source, used in self.UsedTags().items():
                if used:
                    lines.append(Text(f"{source} Use:"))
                    lines += [Styled(f"  {tag} ✓", "good") if tag in value else Styled(f"  {tag} ✗ Missing", "error")
                              for tag in used]
            reserved = sorted({found.lower() for found in regex.findall(r"(?m)^%(\w+)", body)} & RESERVED_ORCA_BLOCKS)
            problems += [f"⚠ %{block} is written by CompUtils; ignored" for block in reserved]
            short = f"{len(value)} Tag{'s' if len(value) != 1 else ''}"
        lines += [Styled(problem, "error" if problem.startswith("✗") else "warning") for problem in problems]
        if problems:
            short += f" · {len(problems)} Problem{'s' if len(problems) != 1 else ''}"
        return short, Text("\n").join(lines)

    def RoutePreview(self, name: str, text: str, row: int) -> tuple[str, Text] | None:
        """The editor's Renders As strip: the stage on the cursor's line, rendered for the stand-ins (with what the
        chaining adds to a later stage)."""
        located = RouteAt(name, text, row)
        if located is None:
            return None
        label, stage, stages, route = located
        return (f"Renders As: {label}" + (f", Stage {stage} of {stages}" if stages > 1 else ""),
                self.RendersAs(route, stage if stages > 1 else None))

    def EditSection(self, label: str, name: str) -> None:
        """A level's section in the TextEditor. A level without it starts from the section in force (closing it
        unchanged adds nothing)."""
        body = self.Value(SECTION_TEXT, (label, name))
        missing = body is None or body is DELETE
        start = body
        if missing:
            inForce = self.InForce(name)
            start = self.Value(SECTION_TEXT, (inForce, name)) if inForce else ""
            start = "" if start in (None, DELETE) else start

        def Done(text) -> None:
            if text is None or (missing and text == start):
                return
            self.SetPending(SECTION_TEXT, (label, name), text.strip("\r\n"))
            self.Redraw()
        preview = ((lambda text, row: self.RoutePreview(name, text, row)) if name in (OPTIMIZATION, BENCHMARK)
                   else None)
        self.app.push_screen(TextEditor(f"{name} · {label}", start,
                                        lambda text: self.SectionSummary(name, text)[1], FILE_EXAMPLES[name],
                                        preview), Done)

    async def ShowKeylists(self, entered: bool, name: str | None = None) -> None:
        """Swap the section list for Job Keylists' sections (name highlighted, else the first), or back to SECTIONS
        with Job Keylists highlighted."""
        if entered:
            self.entries = list(KEYLIST_ENTRIES)
            index = next((position for position, (label, _) in enumerate(self.entries) if label == name), 1)
        else:
            self.entries = list(SECTIONS)
            index = next(position for position, (_, kind) in enumerate(self.entries) if kind == KEYLISTS_MENU)
        await SwapItems(self.query_one("#sections", SectionList), self.EntryItems(), index)
        self.ShowSection(index)

    # ─── Content ──────────────────────────────────────────────────────

    def Cell(self, column: int, key: str) -> Text:
        if column == PROJECT and key not in Defaults._PROJECT_KEYS:
            return Text("(Global Only)", "dim")
        if column == PROJECT and self.root is None:
            return Text("No Project", "dim")
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
        label, kind = self.entries[index]
        table = self.query_one("#config-table", ConfigTable)
        row, column = table.cursor_row, max(table.cursor_column, GLOBAL)
        if kind in (KEYLISTS_MENU, KEYLISTS_BACK):
            # Job Keylists itself: one row per section, from the level in force
            self.keys = list(KEYLIST_SECTIONS)
            self.valueWidth = FitColumns(table, ["Section", "From", "Summary"], max(map(len, self.keys)))
            for name in self.keys:
                inForce = self.InForce(name)
                body = self.Value(SECTION_TEXT, (inForce, name)) if inForce else None
                short = self.SectionSummary(name, body)[0] if inForce else ("Default" if name == BENCHMARK else "None")
                table.add_row(name, inForce or "—", Text(Cut(short, self.valueWidth)))
        elif kind.startswith(KEYLIST):
            # One section: a row per level, • on the one in force
            name = kind[len(KEYLIST):]
            self.keys = [level.label for level in self.levels]
            self.valueWidth = FitColumns(table, ["Level", "Location", "Summary"], 9)
            inForce = self.InForce(name)
            for level in self.levels:
                body = self.Value(SECTION_TEXT, (level.label, name))
                edited = "[Edited] " if (SECTION_TEXT, (level.label, name)) in self.pending else ""
                table.add_row(Text(level.label + (" •" if level.label == inForce else "")),
                              Text(Cut(self.Location(level.label, name), self.valueWidth),
                                   "dim" if body is None or body is DELETE else ""),
                              Text(Cut(edited + self.SectionSummary(name, body)[0], self.valueWidth)))
        elif kind == FILES_SECTION:
            self.keys = list(FILE_KEYS)
            self.valueWidth = FitColumns(table, ["File", "Location", "Summary"], max(map(len, self.keys)))
            for name in self.keys:
                path = self.FilePath(name)
                location = "Project" if path is not None and path.is_file() else "Missing"
                short, _ = self.FileSummary(name, self.Value(FILES, name))
                edited = "[Edited] " if (FILES, name) in self.pending else ""
                table.add_row(name, Text(location, "dim" if location == "Missing" else ""),
                              Text(Cut(edited + short, self.valueWidth)))
        else:
            self.keys = [key for key in Defaults._FILE_GROUPS[kind] if key not in HIDDEN]
            self.valueWidth = FitColumns(table, ["Key", "Global", "Project"], max(map(len, self.keys)))
            for key in self.keys:
                table.add_row(key, self.Cell(GLOBAL, key), self.Cell(PROJECT, key))
        table.move_cursor(row=min(row, len(self.keys) - 1), column=column, animate=False)
        if kind in (KEYLISTS_MENU, KEYLISTS_BACK):
            where = f"Job Keylists · {KEYLISTS_FILE}"
        elif kind.startswith(KEYLIST):
            where = f"Job Keylists · {label}"
        elif kind == FILES_SECTION:
            where = f"{label} · {self.root.name}/{Defaults.projectMarker}" if self.root else f"{label} · No Project"
        else:
            where = f"{label} · {kind}"
        self.query_one("#config-top", FrameRule).label = where
        self.query_one("#config-top", FrameRule).right = f"Project: {self.root.name}" if self.root else ""
        self.RefreshDetails()

    def RefreshDetails(self, problem: str = "") -> None:
        current = self.Current()
        details = self.query_one("#config-details", Static)
        width = details.content_region.width
        if current is None:
            details.update("")
        elif current[0] == OVERVIEW:
            name = current[1]
            inForce = self.InForce(name)
            lines = [Text(Cut(FILE_NOTES[name], width)),
                     Text(Cut(f"In Force: {inForce or 'No Level Has It'}" +
                              (f" ({ShortPath(self.Level(inForce).path)})" if inForce else ""), width)),
                     Styled("␣ Open the Section (Each Level's Copy)", "info")]
            details.update(Text("\n").join(lines))
            self.query_one("#config-rule", FrameRule).label = name
        elif current[0] == SECTION_TEXT:
            label, name = current[1]
            level = self.Level(label)
            text = self.PendingTexts().get(level.path) or (level.path.read_text(encoding="utf-8")
                                                           if level.path.is_file() else "")
            remove = "r Drop Unsaved Edits" if label == "Global" else "r Remove (Follow Global)"
            lines = [Text(Cut(FILE_NOTES[name], width)),
                     Text(Cut(f"Path: {ShortPath(level.path)}", width)),
                     *[Styled(Cut(f"⚠ {problem}", width), "warning") for problem in FileProblems(text)],
                     Styled(f"␣ Edit · {remove}", "info")]
            details.update(Text("\n").join(lines))
            self.query_one("#config-rule", FrameRule).label = f"{name} · {label}"
        elif current[0] == FILES:
            name = current[1]
            path = self.FilePath(name)
            lines = [Text(Cut(FILE_NOTES[name], width)),
                     Text(Cut(f"Path: {path if path is not None else 'None (Not Inside a Project)'}", width)),
                     Styled("␣ Edit the File · r Discard Its Unsaved Edits", "info")]
            details.update(Text("\n").join(lines))
            self.query_one("#config-rule", FrameRule).label = name
        else:
            column, key = current
            comments = Defaults.ProjectComments() if column == PROJECT else Defaults._COMMENTS
            default = "" if key in MASKED else f" · Default {Shown(Defaults.DefaultValue(key))}"
            isList = Defaults._TYPES[key] is list
            note = ("Read-only here." if key in READ_ONLY else "␣ opens the Method → Program editor." if key in METHOD_MAP
                    else "␣ opens the list editor." if isList else "")
            # The whole value (the table cuts it to its column), then what kind of value it is
            value = self.Value(column, key) if column == GLOBAL or key in Defaults._PROJECT_KEYS else DELETE
            lines = [Text(Cut(comments.get(key, "").split("\n# ")[0], width)),
                     Text(Cut(f"Value: {Shown(value, key in MASKED)}", width)),
                     Text.assemble(Cut(f"{TYPE_NAMES[Defaults._TYPES[key]]}{'' if isList else default}", width),
                                   Styled(f" · {note}" if note else "", "info"))]
            if problem:
                lines.append(Styled(problem, "error"))
            details.update(Text("\n").join(lines))
            scope = "Project Override" if column == PROJECT else "Global"
            self.query_one("#config-rule", FrameRule).label = f"{key} · {scope}"
        count = self.Unsaved()
        self.query_one("#config-save", FrameRule).right = (
            Text.assemble(Styled(f"{count} Unsaved", "warning"), " · ", KeyHint(self.app, "⏎", "Save")) if count
            else Styled("No Changes", "dim"))
        self.refresh_bindings()

    def Redraw(self) -> None:
        self.ShowSection(self.section)

    # ─── Pop-up editors ───────────────────────────────────────────────

    def EditList(self, column: int, key: str) -> None:
        """A list value in a ListEditor: the Method → Program pair, or any other list key."""
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
        self.app.push_screen(ListEditor(f"{key} · {scope}", [(key, None)], [[entry] for entry in values],
                                        check=EmptyEntries), DoneList)

    def RendersAs(self, entry: str, stage: int | None = None) -> Text:
        """A route card rendered for stand-in molecules (only stage N of one with stages), with what would stop it: the
        program comes from the unsaved method map, and Mixed Basis / ORCA Blocks problems are checked against the
        sections in force, unsaved edits included. A later stage says what its chaining adds."""
        if not entry.strip():
            return Text("")
        template = ParseRouteTemplate(entry)
        programOf = dict(zip(self.Value(GLOBAL, "methodNames"), self.Value(GLOBAL, "targetProgram")))
        extension = extensionGetter(template.method, programOf)
        lines, problems, notes = [], [], []
        for label, multiplicity, spin in STAND_INS:
            molecule = Molecule(Path("preview"), "preview", 0, multiplicity, 0, extension, "preview", spin)
            row = RenderedRow(PreviewRow(label, spin.name), template, molecule, set(), self.PendingTexts(), stage,
                              programOf)
            if stage is not None and not notes:
                notes = RenderStages(template, molecule)[stage - 1].notes
            if row.problem and row.problem not in problems:
                problems.append(row.problem)
            row.problem = ""
            lines.append(Text(f"{label:<9}") + RowText(row))
        problems[:0] = [f"Unknown selector '{selector}' (that group is ignored)" for selector in UnknownSelectors(entry)]
        if template.method not in programOf:
            problems.insert(0, f"{template.method or 'The first token'} is not in methodNames: it would run as Gaussian16")
        if any(TemplateTags(stage) for stage in template.stages) and extension != Defaults.orcaExtension:
            problems.append(f"{{tags}} only apply to ORCA jobs; ignored for {template.method}")
        lines += [Styled(f"+ {note}", "good") for note in notes]
        return Text("\n").join(lines + ([ProblemText([f"⚠ {problem}" for problem in problems])] if problems else []))

    def EditFile(self, name: str) -> None:
        path = self.FilePath(name)
        if path is None:
            Notice(self.app, "No Project", f"{name} lives in a project, and this isn't inside one: `cu -init` creates "
                   "a project.", "warning")
            return

        def Done(text) -> None:
            if text is not None:
                self.SetPending(FILES, name, text)
                self.Redraw()
        self.app.push_screen(TextEditor(f"{name} · Project", self.Value(FILES, name),
                                        lambda text: self.FileSummary(name, text)[1], FILE_EXAMPLES[name]), Done)

    # ─── Events ───────────────────────────────────────────────────────

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        index = self.query_one("#sections", SectionList).index
        if (index is not None and index < len(self.entries) and self.entries[index][1] != "divider"
                and index != self.section):
            self.ShowSection(index)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        # Job Keylists › and ‹ Config swap the list (as Home's sub-menus); any other section moves to its table
        index = self.query_one("#sections", SectionList).index
        kind = self.entries[index][1] if index is not None else ""
        if kind == KEYLISTS_MENU:
            self.call_later(self.ShowKeylists, True)
        elif kind == KEYLISTS_BACK:
            self.call_later(self.ShowKeylists, False)
        else:
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
        if column == OVERVIEW:
            self.call_later(self.ShowKeylists, True, key)
            return
        if column == SECTION_TEXT:
            self.EditSection(*key)
            return
        if not self.Editable(column, key):
            if column == PROJECT and key in Defaults._PROJECT_KEYS and self.root is None:
                Notice(self.app, "No Project", "Not inside a project: `cu -init` creates one.", "warning")
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
        if column == FILES or (column == SECTION_TEXT and current in self.pending):
            self.pending.pop(current, None)
        elif column == SECTION_TEXT:
            # A Project copy of a section can be removed, so the project follows the global one
            label, name = key
            if label == "Global" or not self.InFile(label, name):
                self.app.bell()
                return

            def Remove(result: str) -> None:
                if result == "remove":
                    self.SetPending(SECTION_TEXT, key, DELETE)
                    self.Redraw()
            self.app.push_screen(Popup(f"Remove {name} From {label}?",
                                       f"{label} will follow the next level for {name}.",
                                       [("r", "Remove", "remove")], "warning"), Remove)
            return
        elif self.Editable(column, key):
            # The method map resets as a pair, so the two lists stay the same length
            for resetKey in (METHOD_MAP if key in METHOD_MAP else (key,)):
                self.SetPending(column, resetKey, Defaults.DefaultValue(resetKey) if column == GLOBAL else DELETE)
        self.Redraw()

    def action_reset_file(self) -> None:
        current = self.Current()
        filename = self.SectionFile()
        if current is None or filename not in Defaults._FILE_GROUPS:
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
        self.app.push_screen(Popup("Reset File", message, [("y", "Reset", "reset")], "warning"), Apply)

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
            self.app.push_screen(Popup("Method List Mismatch", mismatch, [("t", "Trim & Save", "trim")], "warning",
                                       cancel="Keep Editing"), Chosen)
            return
        written, failed = self.WriteFiles()
        if failed:
            Notice(self.app, "Save Failed", f"Could not save {', '.join(failed)}; left unchanged.", "error")
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
        # Project files are written exactly as edited; jobRelatedKeylists keeps every part it isn't given
        for name, text in self.FileTexts().items():
            path = self.FilePath(name)
            (written if writeToml(path.parent, path.name, text) else failed).append(name)
        labelOf = {level.path: level.label for level in self.levels}
        for path, text in self.PendingTexts().items():
            (written if WriteKeylists(path, text) else failed).append(f"{KEYLISTS_FILE} ({labelOf[path]})")
        ReloadConfig()
        if failed:
            # What was written stays written; the rest is still shown as unsaved against the reloaded values
            self.pending = {cell: value for cell, value in self.pending.items()
                            if self.FileOf(*cell) in failed and value != self.SavedValue(*cell)}
            self.regenerate &= set(failed)
            return written, failed
        self.pending, self.regenerate = {}, set()
        return written, failed

    def FileOf(self, column: int, key) -> str:
        if column == SECTION_TEXT:
            return f"{KEYLISTS_FILE} ({key[0]})"
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
        self.app.push_screen(Popup("Unsaved Changes", f"{count} unsaved change{'s' if count != 1 else ''} in {', '.join(files)}.",
                                   [("s", "Save", "save"), ("d", "Discard", "discard")], "warning"), Chosen)

    def action_back(self) -> None:
        # In Job Keylists ›, esc swaps back first (as Home's sub-menus)
        if self.entries != SECTIONS:
            self.call_later(self.ShowKeylists, False)
            return
        self.ConfirmLeave(lambda: self.dismiss(self.saved))

    def action_help(self) -> None:
        Notice(self.app, "Help", "\n".join([
            "↑/↓ Move Between Keys", "←/→ Global or Project",
            "space Edit (Toggle, Cycle, or Open an Editor)", "Double-Click Edit a Value",
            "r Reset to Default (Project: Follow Global; a File: Drop Its Edits)",
            "R Reset the Whole File",
            "Job Keylists ›: One Section at a Time, a Row per Level (• the One in Force); space Edits a Level's Copy",
            "r on a Project Copy: Remove It, so the Global One Applies",
            "enter Save and Return Home", "esc Back (Out of Job Keylists First)", "ctrl+q Quit"]))
