"""Pieces every TUI screen shares, so navigation looks and works the same everywhere (TUI_DESIGN.md P2)."""
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Checkbox, Footer
from textual.widgets._footer import FooterKey

# Every screen puts these in its BINDINGS and yields NavFooter, so the footer always shows how to move around
_PANE = Binding.Group("Pane", compact=False)
NAV_BINDINGS = [
    Binding("tab", "app.focus_next", "Next Pane", group=_PANE),
    Binding("shift+tab", "app.focus_previous", "Prev Pane", group=_PANE),
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


_OPTION = Binding.Group("Option", compact=True)


class OptionRow(Horizontal, can_focus=True, can_focus_children=False):
    """A row of checkboxes that is one tab stop: ←/→ move between them and space toggles, like Textual's RadioSet.
    Other widgets (e.g. the parentheses around Loop) can sit in the row; only the checkboxes are stops."""
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
        Binding("space", "toggle", "Toggle"),
    ]

    def __init__(self, *children, **kwargs) -> None:
        super().__init__(*children, **kwargs)
        self.highlighted = 0

    @property
    def boxes(self) -> list[Checkbox]:
        return list(self.query_children(Checkbox))

    def on_mount(self) -> None:
        for box in self.boxes:
            box.can_focus = False
        self.Highlight(0)

    def Highlight(self, index: int) -> None:
        boxes = self.boxes
        if not boxes:
            return
        self.highlighted = max(0, min(index, len(boxes) - 1))
        for position, box in enumerate(boxes):
            box.set_class(position == self.highlighted, "-selected")

    def action_move(self, step: int) -> None:
        boxes = self.boxes
        # Disabled checkboxes (Loop while Stalk is off) are skipped; stay put at either end
        position = self.highlighted + step
        while 0 <= position < len(boxes):
            if not boxes[position].disabled:
                self.Highlight(position)
                return
            position += step

    def action_toggle(self) -> None:
        boxes = self.boxes
        if boxes and not boxes[self.highlighted].disabled:
            boxes[self.highlighted].toggle()

    def on_click(self, event) -> None:
        # The checkbox toggles itself on click; the row takes focus and the highlight follows the click
        self.focus()
        if isinstance(event.widget, Checkbox) and event.widget in self.boxes:
            self.Highlight(self.boxes.index(event.widget))

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        # If the highlighted box just got disabled (unticking Stalk by mouse while on Loop), step back to an enabled one
        self.call_after_refresh(self._KeepEnabled)

    def _KeepEnabled(self) -> None:
        boxes = self.boxes
        if boxes and boxes[self.highlighted].disabled:
            self.action_move(-1)
