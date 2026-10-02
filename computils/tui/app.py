"""The TUI application: Home -> Builder -> submitted in the app (Results pop-up), then Home or the Job Stalker.
FormChk and GoodVibes still exit with their Intent for Main() to dispatch (D23)."""
from rich.markup import escape
from textual.app import App
from textual.containers import Center, Middle
from textual.screen import ModalScreen
from textual.widgets import Static

from ..console import console
from ..intent  import Intent
from ..        import jobs
from .common   import CAPTURE, Busy, Popup
from .home     import HomeScreen
from .stalker  import StalkerScreen, StalkingRoutine

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
            f"Terminal Too Small: {size.width}×{size.height}\nCompUtils needs at least {MIN_WIDTH}×{MIN_HEIGHT}.")


class CompUtilsApp(App):
    TITLE = "CompUtils"
    ENABLE_COMMAND_PALETTE = False
    # Layout only: every color comes from the CLI's Rich theme via inspect.Styled (D28)
    CSS = """
    #title { height: 1; background: $panel; }
    #body { height: 1fr; }
    /* Fits the longest pane title, "Actions: Run as Written" (a border title shows width - 6 characters) */
    #left { width: 29; }
    #actions, #actions-panel { height: auto; }
    #actions { border: none; }
    /* Folders takes whatever height Actions leaves (all of it when Actions is collapsed) */
    #folders-panel { height: 1fr; }
    #folders-panel.-collapsed { height: auto; }
    #folders { height: 1fr; min-height: 4; }
    /* A collapsed pane (1 / 2) shows only its border title and this note */
    .collapsed-note { display: none; color: $text-muted; }
    .pane.-collapsed > .collapsed-note { display: block; }
    .pane.-collapsed > #actions, .pane.-collapsed > #folders { display: none; }
    #right { width: 1fr; }
    /* Every titled box on every screen (P2), square-cornered (house style); the focused one takes the focus colour */
    .pane { border: solid $secondary; padding: 0 1; height: auto; }
    .pane:focus, .pane:focus-within { border: solid $border; }
    #files-pane { height: 1fr; padding: 0; }
    #files { height: 1fr; border: none; }
    #glob { border: none; height: 1; }
    #details { height: 6; }
    #body.stacked { layout: vertical; }
    #body.stacked #left { width: 100%; height: auto; layout: horizontal; }
    #body.stacked #left .pane { width: 1fr; }
    /* Home's file pane and Details while a screen item (Config) is highlighted in Actions */
    .-dimmed { text-opacity: 45%; }

    /* The Builder's framed form: FrameRules draw the edges and section dividers, .side draws the sides */
    FrameRule { height: 1; width: 1fr; }
    /* Hug the sections so the bottom edge closes the frame (BuilderScreen caps the height to scroll instead) */
    #form { height: auto; }
    .side { border: none; border-left: solid $secondary; border-right: solid $secondary; padding: 0 1; height: auto; }
    #form Checkbox { margin-right: 2; }
    #methods { height: auto; max-height: 12; }
    #range { width: 24; height: 1; border: none; }
    /* GoodVibes settings: [X] label, value + unit, meaning; the current row is highlighted while the list has focus */
    #settings { height: auto; }
    .setting { height: 1; }
    .setting-box { width: 4; }
    .setting-label { width: 19; }
    .setting-value { width: 18; height: 1; }
    .setting-value Input, .setting-value Input:focus { width: 2; height: 1; padding: 0; border: none; background: transparent; }
    .setting-unit { width: auto; margin-left: 0; }
    .setting-meaning { width: 1fr; color: $text-muted; }
    /* Rows that won't apply (off, an unselected choice, a sub-option of an off row) are greyed out whole */
    .setting.-off Static, .setting.-off Input { text-opacity: 45%; }
    .setting.-off.-choice .setting-meaning { text-opacity: 100%; }
    #settings:focus-within .setting.-current { background: $boost; }

    /* The config editor: the table takes the height the details strip leaves */
    #config-table { height: 1fr; }
    #config-details { height: 4; }
    #config-edit { height: 1; border: none; }

    /* Even single spaces between footer hints; compact mode otherwise runs group labels into the next key */
    NavFooter FooterKey.-grouped { margin: 0 1 0 0; }
    NavFooter FooterLabel { margin: 0 1 0 0; }
    NavFooter KeyGroup.-compact { padding-left: 0; }
    NavFooter KeyGroup.-compact FooterKey.-grouped { margin: 0; }

    /* The Job Stalker: the state panes on the left (each as tall as its jobs), Details on the right */
    #stalker-body { height: 1fr; }
    #stalker-left { width: 46; }
    .state-pane { padding: 0; }
    .state-pane DataTable { height: auto; max-height: 12; }
    #stalker-details { width: 1fr; height: 1fr; }

    /* Attributions: one page, scrolling within the screen */
    #attributions { height: 1fr; }

    #too-small { text-align: center; padding: 1 2; border: solid $warning; width: auto; }
    """

    def on_mount(self) -> None:
        self.stalking = StalkingRoutine(self)
        self.push_screen(HomeScreen())

    # ─── Submitting in the app ────────────────────────────────────────

    def Submit(self, intent: Intent, expected: int) -> None:
        """Dispatches a job Intent in a worker (behind a Busy pop-up), then shows what happened: the CLI's messages,
        captured with their colours."""
        self.push_screen(Busy("Submitting", f"Submitting {expected} job{'s' if expected != 1 else ''}..."))
        self.run_worker(lambda: self._Dispatch(intent, expected), thread=True, group="submit", exit_on_error=False)

    def _Dispatch(self, intent: Intent, expected: int) -> None:
        from ..dispatch import Dispatch
        with CAPTURE.Capture() as log:
            try:
                tracked = Dispatch(intent)
            except Exception as error:
                # A crash must still reach the screen, with what was submitted before it
                console.print(f"[error]Submission stopped: {escape(repr(error))}[/error]")
                tracked = []
        self.call_from_thread(self._Submitted, bool(tracked), jobs.submittedJobs, expected, log[0])

    def _Submitted(self, tracked: bool, submitted: int, expected: int, log) -> None:
        self.pop_screen()
        title = f"Submitted {submitted} of {expected} Job{'s' if expected != 1 else ''}"
        severity = "" if submitted == expected else "warning" if submitted else "error"
        # Every submitted job is tracked: offer the stalker; esc (or ⏎ without jobs) returns Home
        choices = [("⏎", "Job Stalker", "stalker")] if tracked else []
        self.push_screen(Popup(title, log, choices, severity, cancel="Home", ok="Home"), self._AfterResults)

    def _AfterResults(self, result: str) -> None:
        # Back to Home (the Builder is done), which re-lists its files: the submission wrote new ones
        while not isinstance(self.screen, HomeScreen):
            self.pop_screen()
        home = self.screen
        home.RefreshFiles(set(home.query_one("#files").selected))
        if result == "stalker":
            self.OpenStalker()

    def OpenStalker(self) -> None:
        """The Job Stalker; the first time, this starts the stalking routine (an immediate ping)."""
        self.stalking.Start()
        self.push_screen(StalkerScreen())

    def action_quit(self) -> None:
        # A screen holding unsaved work (the config editor) asks first
        confirmLeave = getattr(self.screen, "ConfirmLeave", None)
        if confirmLeave is None:
            self.exit()
        else:
            confirmLeave(self.exit)
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
