"""Home: pick an action, move between folders, and select files in the CWD (TUI_DESIGN.md mock-up 4.1)."""
from fnmatch import fnmatch
from pathlib import Path

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import DirectoryTree, Input, Label, ListItem, ListView, SelectionList, Static
from textual.widgets.selection_list import Selection
from textual.worker import get_current_worker

from ..actions  import Action, FILE_ACTIONS
from ..intent   import FormCheckIntent
from ..project  import ChangeDirectory, FindProjectRoot
from .common    import NAV_BINDINGS, NavFooter, Notice
from .inspect   import ActionExtensions, FileDetails, FileStatus, Styled

ACTION_LABELS = {
    Action.RUN: "Run as Written", Action.SINGLE_POINT: "Single Point", Action.BENCHMARK: "Benchmark",
    Action.RERUN: "Re-run", Action.CUBE: "Cube Files", Action.FORM_CHECK: "FormChk",
}
# Screens below the divider, opened with ␣, ⏎ or a click: item id -> (label, the Config section it opens).
# The later ones are listed so the layout matches the design
SCREEN_ITEMS = {"screen-config": ("Config", "SLURM"), "screen-files": ("Project Files", "Project Files")}
LATER_SCREENS = ["Queue Monitor", "GoodVibes"]
_COLLAPSE = Binding.Group("Collapse")
_ALL_NONE = Binding.Group("All/None")
_FOLD = Binding.Group("Fold", compact=True)
_TO_FILES = Binding.Group("Files", compact=True)
# Every pane: ␣ acts on the highlighted item, ⏎ continues to the builder (the glob box, a text box, returns to the list)
CONTINUE = Binding("enter", "screen.continue", "Continue")
STATUS_STYLES = {"normal": "good", "error": "error", "unknown": "warning"}

HELP = {
    "actions": "↑/↓ Choose the action · space Choose it and go to the folders · enter Continue (to the file list until files are selected) · space or enter on Config or Project Files: open the config editor there · 1 Collapse",
    "folders": "↑/↓ Move · space Open the folder (it becomes the working directory) · ←/→ Fold · enter Continue · 2 Collapse",
    "glob":    "Type a pattern to select matching files · enter or esc Return to the file list",
    "files":   "space Select · a All · n None · / Glob · enter Continue to the builder",
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


class ActionList(ListView):
    # ␣ chooses the highlighted action and moves on to the Folders tree (on_list_view_selected), like a click
    BINDINGS = [Binding("space", "select_cursor", "Choose", key_display="␣"), CONTINUE]


class GlobInput(Input, can_focus=False):
    """A text box: enter (Input's submit) and esc both return to the file list. Distinct actions, or the footer shows one.
    Not a tab stop, so Tab cycles only the panes; / (or a click) focuses it, and it stops being focusable on leaving."""
    BINDINGS = [Binding("enter", "submit", "Files", group=_TO_FILES),
                Binding("escape", "screen.leave_glob", "Files", group=_TO_FILES)]

    def Enter(self) -> None:
        self.can_focus = True
        self.focus()

    def on_click(self) -> None:
        self.Enter()

    def on_blur(self) -> None:
        self.can_focus = False


class FolderTree(DirectoryTree):
    """Folders only; the file list on the right shows the files."""
    BINDINGS = [
        Binding("space", "select_cursor", "Open", key_display="␣"),
        Binding("left", "fold(False)", "Fold", group=_FOLD),
        Binding("right", "fold(True)", "Fold", group=_FOLD),
        CONTINUE,
    ]

    # Plain glyphs: emoji folder icons render unreliably over SSH
    ICON_NODE, ICON_NODE_EXPANDED = "▸ ", "▾ "
    def filter_paths(self, paths):
        return [path for path in paths if path.is_dir() and not path.name.startswith(".")]

    async def watch_path(self) -> None:
        # The root is (re)loaded here, on mount and when MoveTo re-roots the tree; revealing any earlier gets reset
        await super().watch_path()
        await self.Reveal(Path.cwd().resolve())

    async def Reveal(self, target: Path) -> None:
        """Expand the tree down to target and put the cursor on it, so the user starts out seeing where they are."""
        if not target.is_relative_to(self.path):
            return
        node = self.root
        for part in (*target.relative_to(self.path).parts, None):
            # reload_node loads the node's children and expands it; awaiting it waits for the load
            await self.reload_node(node)
            if part is None:
                break
            child = next((child for child in node.children if child.data and child.data.path.name == part), None)
            # A hidden folder isn't in the tree: stop at the nearest one shown
            if child is None:
                break
            node = child
        # Node line numbers are only recomputed on the next refresh after the expansions; before that they're -1
        self.call_after_refresh(self.move_cursor, node, animate=False)

    def action_fold(self, expand: bool) -> None:
        # → expands the highlighted folder, ← collapses it (or moves to its parent if it's already collapsed)
        node = self.cursor_node
        if node is None:
            return
        if expand:
            node.expand()
        elif node.is_expanded:
            node.collapse()
        else:
            self.action_cursor_parent()


class FileList(SelectionList):
    # enter continues to the builder instead of toggling (space still toggles). These keys only mean something here,
    # so they're bound here and the footer shows them only while the list is focused (keeps it within 84 columns)
    BINDINGS = [
        Binding("space", "select", "Select", key_display="␣"),
        CONTINUE,
        Binding("a", "screen.select_all", "All", group=_ALL_NONE),
        Binding("n", "screen.select_none", "None", group=_ALL_NONE),
    ]


class HomeScreen(Screen):
    BINDINGS = [
        # Grouped as "1 2 Collapse" so the footer fits in 84 columns
        Binding("1", "toggle_panel('actions')", "Actions", group=_COLLAPSE),
        Binding("2", "toggle_panel('folders')", "Folders", group=_COLLAPSE),
        # Not in the footer (no room at 84 columns): the glob box's placeholder says "/ to focus"
        Binding("/", "focus_glob", "Glob", show=False),
        Binding("question_mark", "help", "Help"),
        *NAV_BINDINGS,
    ]

    def __init__(self) -> None:
        super().__init__()
        self.action = Action.SINGLE_POINT
        self._detailsTimer = None
        self._detailsPath: Path | None = None
        self._nameWidth = 0

    def compose(self) -> ComposeResult:
        yield Static(id="title")
        with Horizontal(id="body"):
            with Vertical(id="left"):
                # Panes like Files and Details: the title is on the border, so it is never a tab stop or a click target.
                # 1 / 2 collapse a pane to its title and a "(collapsed)" line
                with Vertical(id="actions-panel", classes="pane"):
                    items = [ListItem(Label(label), id=f"action-{action.value}") for action, label in ACTION_LABELS.items()]
                    items.append(ListItem(Label("──────────────"), disabled=True))
                    items += [ListItem(Label(label), id=itemId) for itemId, (label, _) in SCREEN_ITEMS.items()]
                    items += [ListItem(Label(f"{name} (later)"), disabled=True, classes="later") for name in LATER_SCREENS]
                    yield ActionList(*items, initial_index=1, id="actions")
                    yield Static("(collapsed)", classes="collapsed-note")
                with Vertical(id="folders-panel", classes="pane"):
                    yield FolderTree(FindProjectRoot() or Path.cwd(), id="folders")
                    yield Static("(collapsed)", classes="collapsed-note")
            with Vertical(id="right"):
                with Vertical(id="files-pane", classes="pane"):
                    yield GlobInput(placeholder="Glob, e.g. *_failed*  (/ to focus)", id="glob")
                    yield FileList(id="files")
                yield Static(id="details", classes="pane")
        yield NavFooter()

    def on_mount(self) -> None:
        self.RefreshFiles()
        self.query_one("#files").focus()
    # ─── State ────────────────────────────────────────────────────────

    def RefreshHeader(self) -> None:
        root = FindProjectRoot()
        project = f"Project: {root.name}" if root else "No project"
        self.query_one("#title", Static).update(TitleLine("CompUtils", Styled(project, "info")))
        self.query_one("#files-pane").border_title = f"{Path.cwd().name} · {' '.join(ActionExtensions(self.action))}"
        # A screen item (Config, Project Files) highlighted names itself, and dims the file pane it doesn't use (4.1)
        screenItem = self.ScreenItemHighlighted()
        label = SCREEN_ITEMS[screenItem][0] if screenItem else ACTION_LABELS[self.action]
        self.query_one("#actions-panel").border_title = f"Actions: {label}"
        for pane in ("#files-pane", "#details"):
            self.query_one(pane).set_class(screenItem is not None, "-dimmed")
        self.query_one("#folders-panel").border_title = f"Folders: {Path.cwd().name}"

    def RefreshFiles(self, keep: set[str] | None = None) -> None:
        """Re-list the CWD for the current action. Selections that no longer match the filter are dropped.

        Names appear at once; statuses and details are read by workers, so a folder of large outputs never blocks input.
        """
        keep = keep or set()
        extensions = ActionExtensions(self.action)
        paths = sorted(path for path in Path.cwd().iterdir() if path.is_file() and path.suffix in extensions)
        self._nameWidth = max((len(path.name) for path in paths), default=0) + 3
        fileList = self.query_one("#files", FileList)
        fileList.clear_options()
        fileList.add_options([Selection(self.FilePrompt(path.name, "…"), path.name, path.name in keep) for path in paths])
        self.RefreshHeader()
        self.LoadStatuses(paths)
        self.ScheduleDetails(paths[0] if paths else None, 0)

    def ScreenItemHighlighted(self) -> str | None:
        item = self.query_one("#actions", ActionList).highlighted_child
        return item.id if item is not None and item.id in SCREEN_ITEMS else None

    def FilePrompt(self, name: str, status: str) -> Text:
        return Text.assemble(name.ljust(self._nameWidth), Styled(status, STATUS_STYLES.get(status, "dim")))

    # Paths are absolute: the CWD can change while a worker is still running
    @work(thread=True, exclusive=True, group="status")
    def LoadStatuses(self, paths: list[Path]) -> None:
        worker = get_current_worker()
        for index, path in enumerate(paths):
            if worker.is_cancelled:
                return
            try:
                status = FileStatus(path)
            except (OSError, ValueError):
                # Deleted or unreadable since the folder was listed
                status = "unknown"
            self.app.call_from_thread(self.SetStatus, index, path.name, status)

    def SetStatus(self, index: int, name: str, status: str) -> None:
        fileList = self.query_one("#files", FileList)
        # The list may have been rebuilt (new folder or action) since the worker read this file
        if index < fileList.option_count and fileList.get_option_at_index(index).value == name:
            fileList.replace_option_prompt_at_index(index, self.FilePrompt(name, status))

    def ScheduleDetails(self, path: Path | None, delay: float = 0.15) -> None:
        # Debounced, so holding an arrow key doesn't scan every file it passes
        if self._detailsTimer is not None:
            self._detailsTimer.stop()
        self._detailsPath = path
        if delay:
            self._detailsTimer = self.set_timer(delay, lambda: self.LoadDetails(path))
        else:
            self._detailsTimer = None
            self.LoadDetails(path)

    @work(thread=True, exclusive=True, group="details")
    def LoadDetails(self, path: Path | None) -> None:
        try:
            fields = FileDetails(path) if path else None
        except (OSError, ValueError, IndexError):
            fields = None
        if not get_current_worker().is_cancelled:
            self.app.call_from_thread(self.ShowDetails, path, fields)

    def ShowDetails(self, path: Path | None, fields: dict[str, str] | None) -> None:
        # A newer highlight has been scheduled since this file was read
        if path != self._detailsPath:
            return
        details = self.query_one("#details", Static)
        details.border_title = f"Details: {path.name}" if path else "Details"
        if path is None or fields is None:
            details.update("")
            return
        status = fields["Status"]
        statusStyle = "error" if "error" in status.lower() else "good" if "normal" in status.lower() else ""
        details.update(Text.assemble(
            "Status: ", Styled(status, statusStyle), "\n",
            f"Charge: {fields['Charge']}   Mult: {fields['Mult']}   Spin: {fields['Spin']}\n",
            f"Method: {fields['Method']} ({fields['Program']})   CPU: {fields['CPU']}   Mem: {fields['Mem']}\n",
            f"Keys:   {fields['Keys']}"))

    # ─── Events ───────────────────────────────────────────────────────

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        # Screens below the divider don't change the action (or the file filter), only the header
        if event.item is None or event.item.id is None or not event.item.id.startswith("action-"):
            self.RefreshHeader()
            return
        action = Action(event.item.id.removeprefix("action-"))
        # The mount-time highlight (and re-highlighting the same action) would only re-scan the same files
        if action == self.action:
            self.RefreshHeader()
            return
        previous = set(self.query_one("#files", FileList).selected)
        self.action = action
        self.RefreshFiles(previous)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.item.id in SCREEN_ITEMS:
            self.OpenConfig(SCREEN_ITEMS[event.item.id][1])
            return
        # Next stop after the action is the folder; with Folders collapsed, the file list
        self.query_one("#files" if self.Collapsed("folders") else "#folders").focus()

    def on_selection_list_selection_highlighted(self, event: SelectionList.SelectionHighlighted) -> None:
        self.ScheduleDetails(Path.cwd() / event.selection.value)

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
        self.action_leave_glob()

    def MoveTo(self, target: Path) -> None:
        ChangeDirectory(target)
        # The selection belongs to the old directory (D9); the glob is cleared with it
        self.query_one("#glob", Input).value = ""
        tree = self.query_one("#folders", FolderTree)
        if tree.path not in (Path.cwd(), *Path.cwd().parents):
            tree.path = Path.cwd()
        self.RefreshFiles()

    # ─── Actions ──────────────────────────────────────────────────────

    def Collapsed(self, name: str) -> bool:
        return self.query_one(f"#{name}-panel").has_class("-collapsed")

    def action_toggle_panel(self, name: str) -> None:
        panel = self.query_one(f"#{name}-panel")
        # A collapsed pane's list or tree is hidden, so it can't keep the focus: hand it to the file list
        if not self.Collapsed(name) and self.focused is not None and panel in self.focused.ancestors:
            self.query_one("#files").focus()
        panel.toggle_class("-collapsed")
        # With both collapsed, the two summaries share one strip and the file pane gets the full width (D19)
        bothCollapsed = all(self.Collapsed(n) for n in ("actions", "folders"))
        self.query_one("#body").set_class(bothCollapsed, "stacked")

    def action_focus_glob(self) -> None:
        self.query_one("#glob", GlobInput).Enter()

    def action_leave_glob(self) -> None:
        self.query_one("#files").focus()

    def action_select_all(self) -> None:
        self.query_one("#files", FileList).select_all()

    def action_select_none(self) -> None:
        self.query_one("#files", FileList).deselect_all()

    def action_help(self) -> None:
        focused = self.focused.id if self.focused else None
        Notice(self.app, "Help", HELP.get(focused, "tab / shift+tab Move between panes · ↑/↓ Move · 1/2 Collapse panels · "
                                                    "ctrl+q Quit").replace(" · ", "\n"))

    def OpenConfig(self, section: str = "SLURM") -> None:
        from .config import ConfigScreen
        keep = set(self.query_one("#files", FileList).selected)

        def Closed(saved: bool) -> None:
            # Saved values (extensions, project overrides) change what Home lists and shows
            if saved:
                self.RefreshFiles(keep)
        self.app.push_screen(ConfigScreen(section), Closed)

    def action_continue(self) -> None:
        screenItem = self.ScreenItemHighlighted()
        if self.query_one("#actions", ActionList).has_focus and screenItem:
            self.OpenConfig(SCREEN_ITEMS[screenItem][1])
            return
        files = [Path(name) for name in self.query_one("#files", FileList).selected]
        if self.action not in FILE_ACTIONS:
            return
        if not files:
            # From another pane, continuing without a selection just moves on to the file list
            fileList = self.query_one("#files", FileList)
            if fileList.has_focus:
                Notice(self.app, "No files selected", "Select at least one file first (space, a, or a glob).", "warning")
            fileList.focus()
            return
        # FormChk has no options, so it skips the builder (D22)
        if self.action == Action.FORM_CHECK:
            self.app.exit(FormCheckIntent(files=files))
            return
        from .builder import BuilderScreen
        self.app.push_screen(BuilderScreen(self.action, files))
