"""The TUI application: Home -> Builder, then exit with the Intent for Main() to dispatch (D23)."""
from textual.app import App
from textual.containers import Center, Middle
from textual.screen import ModalScreen
from textual.widgets import Static

from .home import HomeScreen

MIN_WIDTH, MIN_HEIGHT = 84, 24


class TooSmallScreen(ModalScreen):
    """Shown instead of squeezing the layout (D26); dismissed automatically once the terminal is big enough."""
    def compose(self):
        with Middle():
            with Center():
                yield Static(id="too-small")

    def on_mount(self) -> None:
        self.Update(self.app.size)

    def Update(self, size) -> None:
        self.query_one("#too-small", Static).update(
            f"Terminal too small: {size.width}×{size.height}\nCompUtils needs at least {MIN_WIDTH}×{MIN_HEIGHT}.")


class CompUtilsApp(App):
    TITLE = "CompUtils"
    ENABLE_COMMAND_PALETTE = False
    # Layout only: every color comes from the CLI's Rich theme via inspect.Styled (D28)
    CSS = """
    #title { height: 1; background: $panel; }
    #body { height: 1fr; }
    #left { width: 26; }
    #left Collapsible { padding: 0; }
    #actions { height: auto; max-height: 14; }
    #folders { height: auto; max-height: 12; }
    #right { width: 1fr; }
    #files-pane { height: 1fr; border: round $secondary; }
    #files { height: 1fr; border: none; }
    #glob { border: none; height: 1; }
    #details { height: 6; border: round $secondary; padding: 0 1; }
    #body.stacked { layout: vertical; }
    #body.stacked #left { width: 100%; height: auto; layout: horizontal; }
    #body.stacked #left Collapsible { width: 1fr; }
    .later { color: $text-muted; }

    #form { padding: 0 1; }
    #form .row { height: auto; }
    #form .heading { margin-top: 1; text-style: bold; }
    #form Checkbox { margin-right: 2; }
    #file-line { width: 1fr; }
    #methods { height: auto; max-height: 10; }
    #range { width: 24; height: 1; border: none; }
    #preview { border: round $secondary; padding: 0 1; margin-top: 1; height: auto; }
    #errors { height: auto; margin-top: 1; }
    #submit-row { height: auto; align-horizontal: right; }

    #too-small { text-align: center; padding: 1 2; border: round $warning; width: auto; }
    """

    def on_mount(self) -> None:
        self.push_screen(HomeScreen())
        self.CheckSize(self.size)

    def on_resize(self, event) -> None:
        # The event carries the new size; self.size has not caught up yet when this runs
        self.CheckSize(event.size)

    def CheckSize(self, size) -> None:
        tooSmall = size.width < MIN_WIDTH or size.height < MIN_HEIGHT
        showing = isinstance(self.screen, TooSmallScreen)
        if tooSmall and not showing:
            self.push_screen(TooSmallScreen())
        elif tooSmall and showing:
            self.screen.Update(size)
        elif showing:
            self.pop_screen()
