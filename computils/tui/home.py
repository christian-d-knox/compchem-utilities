"""Home: pick an action, move between folders, and select files in the CWD (TUI_DESIGN.md mock-up 4.1)."""
from fnmatch import fnmatch
from pathlib import Path

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Collapsible, DirectoryTree, Footer, Input, Label, ListItem, ListView, SelectionList, Static
from textual.widgets.selection_list import Selection

from ..actions  import Action
from ..intent   import FormCheckIntent
from ..project  import ChangeDirectory, FindProjectRoot
from .inspect   import BATCH_ACTIONS, ActionExtensions, FileDetails, FileStatus, Styled

ACTION_LABELS = {
    Action.RUN: "Run as Written", Action.SINGLE_POINT: "Single Point", Action.BENCHMARK: "Benchmark",
    Action.RERUN: "Re-run", Action.CUBE: "Cube Files", Action.FORM_CHECK: "FormChk",
}
# Listed so the layout matches the design; their screens come after v1
LATER_SCREENS = ["Queue Monitor", "GoodVibes", "Config", "Project Files"]
STATUS_STYLES = {"normal": "good", "error": "error", "unknown": "warning"}

HELP = {
    "actions": "↑/↓ choose the action · enter go to the file list · 1 collapse",
    "folders": "↑/↓ move · enter open folder (becomes the working directory) · backspace up one level · 2 collapse",
    "glob":    "Type a pattern to select matching files · enter or esc returns to the file list",
    "files":   "space toggle · a all · n none · / glob · enter continue to the builder",
}


def ShortPath(path: Path) -> str:
    """The CWD relative to the project root (or home), so the title line stays readable."""
    root = FindProjectRoot()
    for base, prefix in ((root, root.name if root else ""), (Path.home(), "~")):
        if base is not None and path.is_relative_to(base):
            relative = path.relative_to(base)
            return prefix if str(relative) == "." else f"{prefix}/{relative.as_posix()}"
    return str(path)


def TitleLine(label: str, *extra) -> Text:
    line = Text.assemble((f" {label}", "bold"), f"   {ShortPath(Path.cwd())}")
    for part in extra:
        line.append("   ·   ")
        line.append_text(part)
    line.no_wrap, line.overflow = True, "ellipsis"
    return line


class FolderTree(DirectoryTree):
    """Folders only; the file list on the right shows the files."""
    # Plain glyphs: emoji folder icons render unreliably over SSH
    ICON_NODE, ICON_NODE_EXPANDED = "▸ ", "▾ "
    def filter_paths(self, paths):
        return [path for path in paths if path.is_dir() and not path.name.startswith(".")]


class FileList(SelectionList):
    # enter continues to the builder instead of toggling (space still toggles)
    BINDINGS = [Binding("enter", "screen.continue", "Continue")]


class HomeScreen(Screen):
    BINDINGS = [
        Binding("1", "toggle_panel('actions')", "Actions"),
        Binding("2", "toggle_panel('folders')", "Folders"),
        Binding("/", "focus_glob", "Glob"),
        Binding("a", "select_all", "All"),
        Binding("n", "select_none", "None"),
        Binding("backspace", "folder_up", "Up"),
        Binding("escape", "leave_glob", show=False),
        Binding("question_mark", "help", "Help"),
        Binding("q", "app.exit", "Quit"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.action = Action.SINGLE_POINT
        self._detailsTimer = None

    def compose(self) -> ComposeResult:
        yield Static(id="title")
        with Horizontal(id="body"):
            with Vertical(id="left"):
                with Collapsible(title="Actions", collapsed=False, id="actions-panel"):
                    items = [ListItem(Label(label), id=f"action-{action.value}") for action, label in ACTION_LABELS.items()]
                    items.append(ListItem(Label("──────────────"), disabled=True))
                    items += [ListItem(Label(f"{name} (later)"), disabled=True, classes="later") for name in LATER_SCREENS]
                    yield ListView(*items, initial_index=1, id="actions")
                with Collapsible(title="Folders", collapsed=False, id="folders-panel"):
                    yield FolderTree(FindProjectRoot() or Path.cwd(), id="folders")
            with Vertical(id="right"):
                with Vertical(id="files-pane"):
                    yield Input(placeholder="Glob, e.g. *_failed*  (/ to focus)", id="glob")
                    yield FileList(id="files")
                yield Static(id="details")
        yield Footer()

    def on_mount(self) -> None:
        self.RefreshFiles()
        self.query_one("#files").focus()

    # ─── State ────────────────────────────────────────────────────────

    def RefreshHeader(self) -> None:
        root = FindProjectRoot()
        project = f"project: {root.name}" if root else "no project"
        self.query_one("#title", Static).update(TitleLine("CompUtils", Styled(project, "info")))
        self.query_one("#files-pane").border_title = f"{Path.cwd().name} · {' '.join(ActionExtensions(self.action))}"
        self.query_one("#actions-panel", Collapsible).title = f"Actions: {ACTION_LABELS[self.action]}"
        self.query_one("#folders-panel", Collapsible).title = f"Folders: {Path.cwd().name}"

    def RefreshFiles(self, keep: set[str] | None = None) -> None:
        """Re-list the CWD for the current action. Selections that no longer match the filter are dropped."""
        keep = keep or set()
        extensions = ActionExtensions(self.action)
        paths = sorted(path for path in Path.cwd().iterdir() if path.is_file() and path.suffix in extensions)
        width = max((len(path.name) for path in paths), default=0) + 3
        fileList = self.query_one("#files", FileList)
        fileList.clear_options()
        for path in paths:
            status = FileStatus(path)
            prompt = Text.assemble(path.name.ljust(width), Styled(status, STATUS_STYLES.get(status, "")))
            fileList.add_option(Selection(prompt, path.name, path.name in keep))
        self.RefreshHeader()
        self.ShowDetails(Path(paths[0].name) if paths else None)

    def SelectedFiles(self) -> list[Path]:
        return [Path(name) for name in self.query_one("#files", FileList).selected]

    def ShowDetails(self, path: Path | None) -> None:
        details = self.query_one("#details", Static)
        details.border_title = f"Details: {path.name}" if path else "Details"
        if path is None:
            details.update("")
            return
        fields = FileDetails(path)
        status = fields["Status"]
        statusStyle = "error" if "error" in status.lower() else "good" if "normal" in status.lower() else ""
        details.update(Text.assemble(
            "Status: ", Styled(status, statusStyle), "\n",
            f"Charge: {fields['Charge']}   Mult: {fields['Mult']}   Spin: {fields['Spin']}\n",
            f"Method: {fields['Method']} ({fields['Program']})   CPU: {fields['CPU']}   Mem: {fields['Mem']}\n",
            f"Keys:   {fields['Keys']}"))

    # ─── Events ───────────────────────────────────────────────────────

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if event.item is None or event.item.id is None:
            return
        previous = {name for name in self.query_one("#files", FileList).selected}
        self.action = Action(event.item.id.removeprefix("action-"))
        self.RefreshFiles(previous)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        self.query_one("#files").focus()

    def on_selection_list_selection_highlighted(self, event: SelectionList.SelectionHighlighted) -> None:
        # Debounced, so holding an arrow key doesn't scan every file it passes
        if self._detailsTimer is not None:
            self._detailsTimer.stop()
        path = Path(event.selection.value)
        self._detailsTimer = self.set_timer(0.15, lambda: self.ShowDetails(path))

    def on_directory_tree_directory_selected(self, event: DirectoryTree.DirectorySelected) -> None:
        self.MoveTo(event.path)

    def on_input_changed(self, event: Input.Changed) -> None:
        pattern = event.value.strip()
        if not pattern:
            return
        fileList = self.query_one("#files", FileList)
        fileList.deselect_all()
        for index in range(fileList.option_count):
            name = fileList.get_option_at_index(index).value
            if fnmatch(name, pattern):
                fileList.select(name)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.query_one("#files").focus()

    def MoveTo(self, target: Path) -> None:
        ChangeDirectory(target)
        # The selection belongs to the old directory (D9); the glob is cleared with it
        self.query_one("#glob", Input).value = ""
        tree = self.query_one("#folders", FolderTree)
        if tree.path not in (Path.cwd(), *Path.cwd().parents):
            tree.path = Path.cwd()
        self.RefreshFiles()

    # ─── Actions ──────────────────────────────────────────────────────

    def action_toggle_panel(self, name: str) -> None:
        panel = self.query_one(f"#{name}-panel", Collapsible)
        panel.collapsed = not panel.collapsed
        # With both collapsed, the two summaries share one strip and the file pane gets the full width (D19)
        bothCollapsed = all(self.query_one(f"#{n}-panel", Collapsible).collapsed for n in ("actions", "folders"))
        self.query_one("#body").set_class(bothCollapsed, "stacked")

    def action_focus_glob(self) -> None:
        self.query_one("#glob").focus()

    def action_leave_glob(self) -> None:
        self.query_one("#files").focus()

    def action_select_all(self) -> None:
        self.query_one("#files", FileList).select_all()

    def action_select_none(self) -> None:
        self.query_one("#files", FileList).deselect_all()

    def action_folder_up(self) -> None:
        self.MoveTo(Path.cwd().parent)

    def action_help(self) -> None:
        focused = self.focused.id if self.focused else None
        self.notify(HELP.get(focused, "tab moves between panes · 1/2 collapse panels · q quit"), title="Help")

    def action_continue(self) -> None:
        files = self.SelectedFiles()
        if self.action not in BATCH_ACTIONS:
            return
        if not files:
            self.notify("Select at least one file first (space, a, or a glob).", severity="warning")
            return
        # FormChk has no options, so it skips the builder (D22)
        if self.action == Action.FORM_CHECK:
            self.app.exit(FormCheckIntent(files=files))
            return
        from .builder import BuilderScreen
        self.app.push_screen(BuilderScreen(self.action, files))
