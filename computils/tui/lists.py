"""Pop-up editors framed like Popup (TUI_DESIGN.md 4.4): lists edited as lists, and project files edited as text."""
from rich.text import Text
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import DataTable, Input, Static, TextArea

from .common  import FrameRule, KeyHint, Popup
from .inspect import Styled

MISSING = "—"          # an empty cell, e.g. a method with no program yet


def Cut(text: str, width: int) -> str:
    return text if len(text) <= width else text[:max(0, width - 1)] + "…"


def FitColumns(table: DataTable, titles: list[str], firstWidth: int | None = None) -> int:
    """Rebuild table's columns at fixed widths: the first fits firstWidth (if given), the rest share the remaining width.
    A DataTable's columns otherwise grow with their widest value (and never shrink), pushing it past its pane.
    Returns the shared width."""
    # One cell of padding either side of each column, and room for the vertical scrollbar
    available = table.content_region.width - len(titles) * 2 * table.cell_padding - 2
    shared = max(8, (available - (firstWidth or 0)) // (len(titles) - (firstWidth is not None)))
    table.clear(columns=True)
    for position, title in enumerate(titles):
        table.add_column(title, width=firstWidth if position == 0 and firstWidth is not None else shared)
    return shared


def ProblemText(problems: list[str]) -> Text:
    # ✗ blocks, ⚠ warns (ListEditor's check)
    return Text("\n").join(Styled(problem, "error" if problem.startswith("✗") else "warning") for problem in problems)


class ListTable(DataTable):
    BINDINGS = [
        Binding("space", "screen.edit"), Binding("a", "screen.add"), Binding("x", "screen.remove"),
        Binding("left_square_bracket", "screen.move(-1)"), Binding("right_square_bracket", "screen.move(1)"),
        Binding("enter", "screen.done"),
    ]


class EntryInput(Input):
    # A text box: ⏎ accepts, esc cancels
    BINDINGS = [Binding("enter", "submit"), Binding("escape", "screen.cancel_entry")]


class ListEditor(ModalScreen):
    """A list edited as a list: one row per entry, a column per field. columns are (name, choices or None): ␣ cycles a
    choices column and opens the Entry box for a text column. check(rows) lists problems (✗ blocks Done, ⚠ only warns);
    preview(row) fills a pane for the highlighted row, following the Entry box as it is typed. guide explains the entry
    format, in its own box shown while typing. Dismisses with the rows (lists of str), or None if cancelled."""
    DEFAULT_CSS = """
    ListEditor { align: center middle; }
    ListEditor > Vertical { height: auto; }
    ListEditor ListTable { height: auto; max-height: 9; }
    /* While an entry is typed, the table gives its rows to the Entry box, the guide and the preview */
    ListEditor.-editing ListTable { max-height: 4; }
    ListEditor #entry { height: 1; border: none; }
    ListEditor #guide { color: $text-muted; }
    """
    BINDINGS = [Binding("escape", "cancel")]
    MAX_WIDTH = 96

    def __init__(self, title: str, columns: list[tuple[str, list[str] | None]], rows: list[list[str]], start: int = 0,
                 check=None, preview=None, previewTitle: str = "", guide=None, guideTitle: str = "") -> None:
        super().__init__()
        self.title_, self.columns, self.check, self.preview, self.previewTitle = title, columns, check, preview, previewTitle
        self.guide, self.guideTitle = guide, guideTitle
        self.rows = [list(row) for row in rows]
        self.original = [list(row) for row in rows]
        self.start = start
        self.editing: tuple[int, int] | None = None
        self.adding = False           # the entry being edited was just added (cancelling removes it)
        self.width = 0

    def compose(self):
        with Vertical():
            yield FrameRule("┌┐", self.title_)
            yield ListTable(id="list", classes="side", cursor_type="cell", zebra_stripes=False)
            yield FrameRule("├┤", id="entry-rule")
            yield EntryInput(id="entry", classes="side")
            if self.guide is not None:
                yield FrameRule("├┤", self.guideTitle, id="guide-rule")
                yield Static(self.guide, id="guide", classes="side")
            if self.preview:
                yield FrameRule("├┤", self.previewTitle)
                yield Static(id="preview", classes="side")
            yield FrameRule("├┤")
            yield Static(id="status", classes="side")
            yield FrameRule("└┘", id="list-keys")

    def on_mount(self) -> None:
        self.query_one(Vertical).styles.width = min(self.app.size.width - 4, self.MAX_WIDTH)
        keys = [("␣", "Edit"), ("a", "Add"), ("x", "Remove"), ("[ ]", "Move"), ("⏎", "Done"), ("esc", "Cancel")]
        self.query_one("#list-keys", FrameRule).right = Text("  ").join(KeyHint(self.app, *key) for key in keys)
        self.ShowEntry(False)
        self.call_after_refresh(self.Redraw, self.start)

    # ─── Content ──────────────────────────────────────────────────────

    def Problems(self) -> list[str]:
        return self.check(self.rows) if self.check else []

    def Redraw(self, row: int | None = None) -> None:
        table = self.query_one("#list", ListTable)
        row = table.cursor_row if row is None else row
        column = table.cursor_column
        self.width = FitColumns(table, [name for name, _ in self.columns])
        for entry in self.rows:
            table.add_row(*(Text(Cut(value, self.width)) if value else Text(MISSING, "dim") for value in entry))
        if self.rows:
            table.move_cursor(row=max(0, min(row, len(self.rows) - 1)), column=column, animate=False)
        self.RefreshPanes()

    def RefreshPanes(self) -> None:
        table = self.query_one("#list", ListTable)
        if self.preview:
            current = self.rows[table.cursor_row] if self.rows and table.cursor_row < len(self.rows) else None
            if self.editing is not None:
                # The entry as it is being typed
                row, column = self.editing
                current = list(self.rows[row])
                current[column] = self.query_one("#entry", EntryInput).value
            self.query_one("#preview", Static).update(self.preview(current) if current else "")
        problems = self.Problems()
        self.query_one("#status", Static).update(
            ProblemText(problems) if problems else Styled(f"{len(self.rows)} entr{'y' if len(self.rows) == 1 else 'ies'}", "dim"))

    def ShowEntry(self, shown: bool) -> None:
        for widget in ("#entry-rule", "#entry", "#guide-rule", "#guide"):
            for found in self.query(widget):
                found.display = shown
        self.set_class(shown, "-editing")

    # ─── Events ───────────────────────────────────────────────────────

    def on_data_table_cell_highlighted(self, event: DataTable.CellHighlighted) -> None:
        self.RefreshPanes()

    def on_input_changed(self, event: Input.Changed) -> None:
        if self.editing is not None:
            self.RefreshPanes()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if self.editing is None:
            return
        row, column = self.editing
        self.rows[row][column] = event.value.strip()
        self.CloseEntry()

    # ─── Actions ──────────────────────────────────────────────────────

    def action_edit(self) -> None:
        if not self.rows:
            return
        table = self.query_one("#list", ListTable)
        row, column = table.cursor_row, table.cursor_column
        choices = self.columns[column][1]
        if choices:
            value = self.rows[row][column]
            self.rows[row][column] = choices[(choices.index(value) + 1) % len(choices)] if value in choices else choices[0]
            self.Redraw()
            return
        self.editing = (row, column)
        self.query_one("#entry-rule", FrameRule).label = f"Entry {row + 1} · {self.columns[column][0]}"
        entry = self.query_one("#entry", EntryInput)
        entry.value = self.rows[row][column]
        self.ShowEntry(True)
        entry.focus()
        self.RefreshPanes()

    def CloseEntry(self) -> None:
        self.editing, self.adding = None, False
        self.ShowEntry(False)
        self.query_one("#list").focus()
        self.Redraw()

    def action_cancel_entry(self) -> None:
        # Cancelling a just-added entry takes it back out
        if self.adding and self.editing is not None:
            del self.rows[self.editing[0]]
        self.CloseEntry()

    def action_add(self) -> None:
        table = self.query_one("#list", ListTable)
        row = table.cursor_row + 1 if self.rows else 0
        # Every cell starts empty (a choices column shows — until one is picked, so nothing is chosen silently); the
        # first text column is opened for typing
        self.rows.insert(row, ["" for _ in self.columns])
        self.Redraw(row)
        textColumn = next((position for position, (_, choices) in enumerate(self.columns) if not choices), None)
        if textColumn is not None:
            table.move_cursor(row=row, column=textColumn, animate=False)
            self.adding = True
            self.action_edit()

    def action_remove(self) -> None:
        if self.rows:
            del self.rows[self.query_one("#list", ListTable).cursor_row]
            self.Redraw()

    def action_move(self, step: int) -> None:
        row = self.query_one("#list", ListTable).cursor_row
        target = row + step
        if self.rows and 0 <= target < len(self.rows):
            self.rows[row], self.rows[target] = self.rows[target], self.rows[row]
            self.Redraw(target)

    def action_done(self) -> None:
        # ✗ problems block (they are already shown in the status strip)
        if any(problem.startswith("✗") for problem in self.Problems()):
            self.app.bell()
            return
        self.dismiss(self.rows)

    def action_cancel(self) -> None:
        if self.rows == self.original:
            self.dismiss(None)
            return
        self.app.push_screen(Popup("Discard changes?", "Your changes to this list will be lost.",
                                   [("d", "Discard", "discard")], "warning", cancel="Keep editing"),
                             lambda result: self.dismiss(None) if result == "discard" else None)


class TextEditor(ModalScreen):
    """A project file edited as text, with a summary(text) -> Text pane re-parsed as you type and, below it, an example
    of the format. esc is Done (⏎ types a newline here, and nothing is written until the config editor saves); ^r
    reverts. Dismisses with the text."""
    DEFAULT_CSS = """
    TextEditor { align: center middle; }
    /* Sized by CSS, so the frame follows the terminal when it is resized */
    TextEditor > Vertical { width: 100%; max-width: 110; height: 100%; margin: 1 2; }
    /* Not .side: the app's .side { height: auto } outranks this widget CSS, and would size the body to its text
       instead of the frame, pushing the right column (and its Example) past the bottom edge */
    TextEditor #editor-body { height: 1fr; border-left: solid $secondary; border-right: solid $secondary; padding: 0 1; }
    /* :focus too, or TextArea's own focus border (more specific) comes back */
    TextEditor TextArea, TextEditor TextArea:focus { width: 1fr; border: none; padding: 0; }
    TextEditor #editor-side { width: 34; border-left: solid $secondary; }
    TextEditor #summary, TextEditor #example { border-top: solid $secondary; border-title-align: left; padding: 0 1; }
    TextEditor #summary { height: 1fr; }
    /* The example takes at most half the column, and scrolls within it rather than pushing past the frame */
    TextEditor #example { height: auto; max-height: 50%; color: $text-muted; }
    """
    BINDINGS = [Binding("escape", "done"), Binding("ctrl+r", "revert")]

    def __init__(self, title: str, text: str, summary, example: str = "") -> None:
        super().__init__()
        self.title_, self.original, self.summary, self.example = title, text, summary, example

    def compose(self):
        with Vertical():
            yield FrameRule("┌┐", self.title_)
            with Horizontal(id="editor-body"):
                yield TextArea(self.original, id="text", soft_wrap=False)
                with Vertical(id="editor-side"):
                    yield Static(id="summary")
                    if self.example:
                        with VerticalScroll(id="example"):
                            yield Static(self.example)
            yield FrameRule("└┘", id="text-keys")

    def on_mount(self) -> None:
        self.query_one("#summary").border_title = "Check"
        for example in self.query("#example"):
            example.border_title = "Example"
        self.query_one("#text-keys", FrameRule).right = Text("  ").join(
            [KeyHint(self.app, "esc", "Done"), KeyHint(self.app, "^r", "Revert")])
        self.query_one("#summary", Static).update(self.summary(self.original))
        self.query_one("#text", TextArea).focus()

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        self.query_one("#summary", Static).update(self.summary(event.text_area.text))

    def action_done(self) -> None:
        self.dismiss(self.query_one("#text", TextArea).text)

    def action_revert(self) -> None:
        self.query_one("#text", TextArea).text = self.original
