"""Pieces every TUI screen shares, so navigation looks and works the same everywhere (TUI_DESIGN.md P2)."""
import io, threading
from contextlib import contextmanager

from rich.text import Text
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widgets import Checkbox, DataTable, Footer, Static
from textual.widgets._footer import FooterKey

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
    label and right are text or Text; setting either redraws the rule."""
    label = reactive("")
    right = reactive("")

    def __init__(self, corners: str, label: str = "", **kwargs) -> None:
        super().__init__(**kwargs)
        self.corners, self.label = corners, label

    def render(self) -> Text:
        line = self.app.theme_variables["secondary"]
        label = Text.assemble(" ", self.label, " ") if self.label else Text()
        right = Text.assemble(" ", self.right, " ") if self.right else Text()
        fill = "─" * max(0, self.size.width - 4 - label.cell_len - right.cell_len)
        return Text.assemble((self.corners[0] + "─", line), label, (fill, line), right, ("─" + self.corners[1], line))

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
