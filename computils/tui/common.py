"""Pieces every TUI screen shares, so navigation looks and works the same everywhere (TUI_DESIGN.md P2)."""
import io, threading
from contextlib import contextmanager
from dataclasses import dataclass

import regex
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widgets import Checkbox, DataTable, Footer, Input, OptionList, Static
from textual.widgets._footer import FooterKey
from textual.widgets.option_list import Option

from ..console import console

# Every screen puts these at the end of its BINDINGS (so Quit is the footer's last hint) and yields NavFooter, so the footer always shows how to move around
_PANE = Binding.Group("Pane", compact=False)
NAV_BINDINGS = [
    Binding("tab", "app.focus_next", "Next Pane", group=_PANE),
    Binding("shift+tab", "app.focus_previous", "Prev Pane", key_display="⇧tab", group=_PANE),
    # Textual's own ctrl+q is a hidden priority binding, which would replace a non-priority one here in the footer
    Binding("ctrl+q", "app.quit", "Quit", key_display="^q", priority=True),
]


class NavFooter(Footer):
    """The footer, compact so every hint fits in 84 columns, and always starting with the arrow-key hint.
    Arrow keys belong to the focused widget (lists, tree, scrolling), whose own up/down bindings are hidden and
    would shadow a screen-level hint binding, so the hint is added here instead."""
    def __init__(self) -> None:
        super().__init__(compact=True)

    def compose(self):
        if self._bindings_ready:
            yield FooterKey("up", "↑↓", "Move", "", tooltip="Arrow keys move within the focused pane").data_bind(
                compact=Footer.compact)
        yield from super().compose()


class EditableTable(DataTable):
    """A table whose cells ␣ edits (the screen's edit action): a double-click edits the clicked cell the same way, so
    the mouse works here as it does on every list. The first click has already moved the cursor; the header row
    (row -1) is not a cell."""
    def on_click(self, event) -> None:
        if event.chain == 2 and event.style.meta.get("row", -1) >= 0:
            self.call_after_refresh(self.run_action, "screen.edit")


_OPTION = Binding.Group("Option", compact=True)


class OptionRow(Horizontal, can_focus=True, can_focus_children=False):
    """A row of checkboxes that is one tab stop: ←/→ move between them and space toggles, like Textual's RadioSet.
    Other widgets (e.g. a label) can sit in the row; only the checkboxes are stops."""
    DEFAULT_CSS = """
    OptionRow { height: auto; width: 1fr; }
    OptionRow:focus > Checkbox.-selected > .toggle--label {
        background: $block-cursor-background;
        color: $block-cursor-foreground;
        text-style: $block-cursor-text-style;
    }
    """
    BINDINGS = [
        Binding("left", "move(-1)", "Option", group=_OPTION),
        Binding("right", "move(1)", "Option", group=_OPTION),
        Binding("space", "toggle", "Toggle", key_display="␣"),
    ]

    highlighted = 0

    @property
    def boxes(self) -> list[Checkbox]:
        return list(self.query_children(Checkbox))

    def on_mount(self) -> None:
        for box in self.boxes:
            box.can_focus = False
        self.Highlight(0)

    def Highlight(self, index: int) -> None:
        boxes = self.boxes
        self.highlighted = max(0, min(index, len(boxes) - 1))
        for position, box in enumerate(boxes):
            box.set_class(position == self.highlighted, "-selected")

    def action_move(self, step: int) -> None:
        boxes = self.boxes
        # Disabled checkboxes are skipped; stay put at either end
        position = self.highlighted + step
        while 0 <= position < len(boxes):
            if not boxes[position].disabled:
                self.Highlight(position)
                return
            position += step

    def action_toggle(self) -> None:
        box = self.boxes[self.highlighted]
        if not box.disabled:
            box.toggle()

    def on_click(self, event) -> None:
        # The checkbox toggles itself on click; the row takes focus and the highlight follows the click
        self.focus()
        if isinstance(event.widget, Checkbox) and event.widget in self.boxes:
            self.Highlight(self.boxes.index(event.widget))

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        # If the highlighted box just got disabled (e.g. by a click elsewhere), step back to an enabled one
        self.call_after_refresh(self._KeepEnabled)

    def _KeepEnabled(self) -> None:
        if self.boxes[self.highlighted].disabled:
            self.action_move(-1)


def KeyHint(app, key: str, description: str) -> Text:
    """A key hint styled like the footer's (key, then description), for hints shown outside it."""
    colors = app.theme_variables
    return Text.assemble((key, f"bold {colors['footer-key-foreground']}"), " ",
                         (description, colors["footer-description-foreground"]))


class Popup(ModalScreen[str]):
    """Every message the TUI shows, framed like the Builder (P2; no toasts): the title on the top edge, the message
    inside, the keys on the bottom edge (`s Save  d Discard  esc Cancel`). choices are (key, label, result): a key
    dismisses with its result, esc with "cancel". Without choices it is a notice, dismissed by ⏎ or esc.
    severity (warning, error) colours only the title."""
    DEFAULT_CSS = """
    Popup { align: center middle; }
    Popup > Vertical { height: auto; }
    Popup #popup-body { height: auto; max-height: 20; }
    """
    MIN_WIDTH, MAX_WIDTH = 40, 72

    def __init__(self, title: str, message: str | Text, choices: list[tuple[str, str, str]] = (), severity: str = "",
                 cancel: str = "Cancel", ok: str = "OK") -> None:
        super().__init__()
        self.title_, self.message, self.choices, self.severity, self.cancel, self.ok = (title, message, choices, severity,
                                                                                        cancel, ok)

    def compose(self):
        with Vertical():
            yield FrameRule("┌┐", Text(self.title_, console.get_style(self.severity) if self.severity else ""))
            # Text (e.g. captured CLI output) is never read as markup; a long message scrolls
            body = Text.assemble("\n", self.message, "\n") if isinstance(self.message, Text) else f"\n{self.message}\n"
            with VerticalScroll(id="popup-body", classes="side"):
                yield Static(body)
            yield FrameRule("└┘", id="popup-keys")

    def KeyHints(self) -> list[tuple[str, str]]:
        return ([(key, label) for key, label, _ in self.choices] + [("esc", self.cancel)]) if self.choices else [("⏎", self.ok)]

    def on_mount(self) -> None:
        hints = Text("  ").join(KeyHint(self.app, key, label) for key, label in self.KeyHints())
        self.query_one("#popup-keys", FrameRule).right = hints
        # As wide as the message (or the key hints), within limits and the screen
        message = self.message.plain if isinstance(self.message, Text) else self.message
        widest = max([len(line) + 4 for line in message.split("\n")] + [hints.cell_len + 8, len(self.title_) + 8])
        self.query_one(Vertical).styles.width = min(max(widest, self.MIN_WIDTH), self.MAX_WIDTH, self.app.size.width - 4)

    def on_key(self, event) -> None:
        # Other keys (arrows, page keys) are left to the scrolling message
        if event.key == "escape" or (event.key == "enter" and not self.choices):
            event.stop()
            self.dismiss("cancel" if self.choices else "ok")
            return
        # A choice keyed ⏎ takes enter
        pressed = "⏎" if event.key == "enter" else event.character
        for key, _, result in self.choices:
            if pressed == key:
                event.stop()
                self.dismiss(result)
                return


class ChoiceList(Popup):
    """A Popup asking for one of several entries: the message, then the list. ␣ or ⏎ picks the highlighted entry
    (dismissed with its value), esc cancels ("cancel"). entries are (value, label)."""
    DEFAULT_CSS = """
    ChoiceList OptionList, ChoiceList OptionList:focus { height: auto; max-height: 12; border: none; padding: 0; }
    """

    def __init__(self, title: str, message: str, entries: list[tuple[str, str]], highlighted: str | None = None) -> None:
        super().__init__(title, message)
        self.entries, self.highlighted = entries, highlighted

    def compose(self):
        with Vertical():
            yield FrameRule("┌┐", Text(self.title_))
            with Vertical(classes="side"):
                yield Static(f"\n{self.message}\n")
                yield OptionList(*(Option(label, id=value) for value, label in self.entries))
            yield FrameRule("└┘", id="popup-keys")

    def KeyHints(self) -> list[tuple[str, str]]:
        return [("␣", "Choose"), ("esc", "Cancel")]

    def on_mount(self) -> None:
        super().on_mount()
        options = self.query_one(OptionList)
        values = [value for value, _ in self.entries]
        if self.highlighted in values:
            options.highlighted = values.index(self.highlighted)
        options.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self.dismiss(event.option.id)

    def on_key(self, event) -> None:
        if event.key == "escape":
            event.stop()
            self.dismiss("cancel")
        elif event.key == "space":
            event.stop()
            options = self.query_one(OptionList)
            if options.highlighted is not None:
                self.dismiss(options.get_option_at_index(options.highlighted).id)


class Busy(Popup):
    """A Popup without keys, shown while work runs in a worker; the code that pushed it dismisses it."""
    def KeyHints(self) -> list[tuple[str, str]]:
        return []

    def on_key(self, event) -> None:
        event.stop()


def Notice(app, title: str, message: str, severity: str = "", then=None) -> None:
    """A one-button Popup (⏎ OK); then() runs once it is dismissed."""
    app.push_screen(Popup(title, message, severity=severity), (lambda _: then()) if then else None)


class FrameRule(Static):
    """One edge of a framed form, drawn to its width: ┌─ label ───── right ─┐ (corners ┌┐, ├┤ or └┘).
    Textual's borders have no ├/┤ junctions, so a form's section dividers are drawn here (P2).
    label and right are text or Text; setting either redraws the rule. split: a form in two columns, the junction
    (┬ ┼ ┴) at that column; label2 is then the right column's label."""
    label = reactive("")
    label2 = reactive("")
    right = reactive("")

    def __init__(self, corners: str, label: str = "", split: int | None = None, label2: str = "", **kwargs) -> None:
        super().__init__(**kwargs)
        self.corners, self.label, self.split, self.label2 = corners, label, split, label2

    def render(self) -> Text:
        line = self.app.theme_variables["secondary"]
        label = Text.assemble(" ", self.label, " ") if self.label else Text()
        right = Text.assemble(" ", self.right, " ") if self.right else Text()
        if self.split is None:
            fill = "─" * max(0, self.size.width - 4 - label.cell_len - right.cell_len)
            return Text.assemble((self.corners[0] + "─", line), label, (fill, line), right, ("─" + self.corners[1], line))
        junction = {"┌┐": "┬", "├┤": "┼", "└┘": "┴"}[self.corners]
        label2 = Text.assemble(" ", self.label2, " ") if self.label2 else Text()
        leftFill = "─" * max(0, self.split - 2 - label.cell_len)
        rightFill = "─" * max(0, self.size.width - self.split - 4 - label2.cell_len - right.cell_len)
        return Text.assemble((self.corners[0] + "─", line), label, (leftFill + junction + "─", line), label2,
                             (rightFill, line), right, ("─" + self.corners[1], line))

# ─── Settings lists (GoodVibes Builder 4.2b, SI Scraper 4.10) ─────────

_CHOOSE = Binding.Group("Choose", compact=True)
# The characters a typed value takes. A box that never takes a space leaves ␣ to turn its row on/off
NUMBER, NAME, TEXT = r"[\d.eE+-]", r"\w", None


@dataclass
class Setting:
    """One row of a settings list: [X] Label  value unit  meaning."""
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


class ValueInput(Input):
    """A setting's typed value: one line, as wide as its text, so the unit follows it (298.15 K).
    A box takes only its setting's characters (a number, a name), so ␣ (on/off) and ? (help) still reach the list and
    the screen, and the footer keeps showing them. ⏎ runs, like every Builder field (Input's own hidden ⏎ would hide the hint)."""
    BINDINGS = [Binding("enter", "screen.run", "Run")]

    def __init__(self, row: "SettingRow", value: str, widest: int) -> None:
        super().__init__(value, compact=True)
        self.row, self.widest = row, widest
        self.can_focus = False
        self.Fit(value)

    def Fit(self, value: str) -> None:
        # One cell past the text, for the cursor; capped to the value column
        self.styles.width = min(len(value) + 1, self.widest)

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
        row = next(ancestor for ancestor in self.ancestors if isinstance(ancestor, SettingRow))
        row.parent.Choose(row)
        row.parent.action_toggle()


class SettingRow(Horizontal, can_focus=False):
    """Rows without a typed value take the focus themselves (the list keeps only its current row focusable).
    Stacked (a narrow column): the label on its own line, the value under it, then the meaning."""
    def __init__(self, setting: Setting, stacked: bool, widest: int, inputClass: type["ValueInput"]) -> None:
        # A choice's rows carry one shared note, which is never greyed with an unselected option
        classes = "setting -choice" if setting.choice else "setting"
        super().__init__(classes=classes + (" -stacked" if stacked else ""), id=f"setting-{setting.key}")
        self.setting, self.on, self.stacked, self.widest = setting, setting.on, stacked, widest
        self.inputClass = inputClass
        self.typing = False

    def compose(self) -> ComposeResult:
        if self.stacked:
            with Horizontal(classes="setting-head" + ("" if self.setting.label else " -empty")):
                yield SettingBox(classes="setting-box")
                yield Static(self.setting.label, classes="setting-label")
        else:
            yield SettingBox(classes="setting-box")
            yield Static(self.setting.label, classes="setting-label")
        with Horizontal(classes="setting-value"):
            if self.setting.value is not None:
                yield self.inputClass(self, self.setting.value, self.widest)
                yield Static(self.setting.unit, classes="setting-unit")
            else:
                yield Static(classes="setting-fixed")
        yield Static(self.setting.meaning, classes="setting-meaning" + ("" if self.setting.meaning else " -empty"))

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

    def SetValue(self, value: str) -> None:
        # Set from code (a preset, a detected value): like the initial value, it doesn't count as typing
        typing, self.typing = self.typing, False
        self.query_one(ValueInput).value = value
        self.call_after_refresh(setattr, self, "typing", typing)

    def Show(self) -> None:
        box = "   " if self.on is None or self.setting.choice else "[X]" if self.on else "[ ]"
        # Text, not str: a str "[X]" is read as markup. Stacked, a row without a checkbox starts at the edge
        self.query_one(".setting-box", Static).update(Text(box))
        self.query_one(".setting-box", Static).display = not (self.stacked and not box.strip())
        if self.setting.value is None:
            fixed = f"({'•' if self.on else ' '}) {self.setting.fixed}" if self.setting.choice else self.setting.fixed
            self.query_one(".setting-fixed", Static).update(fixed)
        # Whatever won't apply (off, an unselected choice, a sub-option of an off row) is greyed out
        self.set_class(not self.parent.Applies(self), "-off")

    def SetMeaning(self, text: str) -> None:
        meaning = self.query_one(".setting-meaning", Static)
        meaning.update(text)
        meaning.set_class(not text, "-empty")

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
    """A settings list: one tab stop, ↑↓ move between rows, ␣ turns the current one on/off (or selects a choice), ←→
    switch a choice, and a row with a value is typed into directly. stacked: for a narrow column (label above value);
    widest caps a typed value's box."""
    BINDINGS = [
        Binding("up", "move(-1)", "Move", show=False),
        Binding("down", "move(1)", "Move", show=False),
        Binding("space", "toggle", "On/Off", key_display="␣"),
        Binding("left", "choose(-1)", "Choose", group=_CHOOSE),
        Binding("right", "choose(1)", "Choose", group=_CHOOSE),
    ]

    class Changed(Message):
        """A row was turned on or off, or a choice changed (typed values post Input.Changed)."""

    def __init__(self, settings: list[Setting], stacked: bool = False, widest: int = 17,
                 inputClass: type["ValueInput"] | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.settings, self.index, self.stacked, self.widest = settings, 0, stacked, widest
        # A screen whose ⏎ isn't Run (the SI Scraper's Write) gives its own ValueInput subclass
        self.inputClass = inputClass or ValueInput

    def compose(self) -> ComposeResult:
        for setting in self.settings:
            yield SettingRow(setting, self.stacked, self.widest, self.inputClass)

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

    def Chosen(self, choice: str) -> str:
        """The key of a choice's selected row."""
        return next(row.setting.key for row in self.rows if row.setting.choice == choice and row.on)

    def Applies(self, row: SettingRow) -> bool:
        """Whether the row takes part: not off, and not under a row that is off."""
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


# ─── Re-orderable tables (List editor, SI Scraper species) ────────────

def MoveRow(rows: list, index: int, target: int) -> bool:
    """Move rows[index] to target (in place), the others shifting; False if either is out of range or they're equal."""
    if not (0 <= index < len(rows) and 0 <= target < len(rows)) or index == target:
        return False
    rows.insert(target, rows.pop(index))
    return True


class ReorderableTable(EditableTable):
    """A table whose rows [ ] move (the screen's move action) and the mouse drags: press on a row, move to where it
    goes (it follows the pointer), release. A press without movement stays a click. The screen's move(step) and
    drag_to(index, target) actions do the moving, so its own row list stays the one source of order."""
    _dragRow: int | None = None

    def _RowAt(self, event) -> int | None:
        row = event.style.meta.get("row") if event.style else None
        return row if isinstance(row, int) and row >= 0 else None

    def on_mouse_down(self, event) -> None:
        self._dragRow = self._RowAt(event)
        if self._dragRow is not None:
            self.capture_mouse()

    def on_mouse_move(self, event) -> None:
        if self._dragRow is None:
            return
        # While captured, the row under the pointer comes from its position (above or below the table included)
        offset = event.get_content_offset_capture(self)
        target = offset.y - (self.header_height if self.show_header else 0) + self.scroll_offset.y
        target = max(0, min(target, self.row_count - 1))
        if target != self._dragRow:
            self.screen.DragRow(self._dragRow, target)
            self._dragRow = target

    def on_mouse_up(self, event) -> None:
        if self._dragRow is not None:
            self._dragRow = None
            self.release_mouse()


class OutputCapture(io.TextIOBase):
    """Stands in for the console's file while the TUI runs. CLI messages written by a thread inside Capture() (an
    in-app submission) are kept, with their colours, for the TUI to show; every other write (extractors run by the
    preview workers) is dropped, as it would otherwise corrupt the screen."""
    def __init__(self) -> None:
        super().__init__()
        self._buffers: dict[int, list[str]] = {}

    def write(self, text: str) -> int:
        buffer = self._buffers.get(threading.get_ident())
        if buffer is not None:
            buffer.append(text)
        return len(text)

    def isatty(self) -> bool:
        # Rich keeps its colours for a terminal
        return True

    @contextmanager
    def Capture(self):
        """Collects this thread's console output; the yielded list holds Text once the block ends."""
        result: list[Text] = []
        self._buffers[threading.get_ident()] = buffer = []
        try:
            yield result
        finally:
            del self._buffers[threading.get_ident()]
            text = Text.from_ansi("".join(buffer))
            text.rstrip()
            result.append(text)


CAPTURE = OutputCapture()
