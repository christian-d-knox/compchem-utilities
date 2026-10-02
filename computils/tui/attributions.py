"""Attributions: credit for everything CompUtils relies on, as one scrolling page (TUI_DESIGN.md 4.8). The text is
attributions.AttributionText(), the same page `cu -attributions` prints."""
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Static

from ..attributions import AttributionText
from .common import NAV_BINDINGS, NavFooter
from .home import TitleLine


class AttributionsScreen(Screen):
    AUTO_FOCUS = "#attributions"
    BINDINGS = [
        Binding("escape", "home", "Home"),
        *NAV_BINDINGS,
    ]

    def compose(self) -> ComposeResult:
        yield Static(TitleLine("Attributions"), id="title")
        with VerticalScroll(id="attributions", classes="pane"):
            yield Static(AttributionText())
        yield NavFooter()

    def on_mount(self) -> None:
        self.query_one("#attributions").border_title = "License and Credits"

    def action_home(self) -> None:
        self.app.pop_screen()
