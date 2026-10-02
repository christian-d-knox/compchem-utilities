"""The Job Stalker: every tracked job, grouped by state, pinged on the stalker's interval (TUI_DESIGN.md 4.7).

The stalking routine belongs to the app, not this screen: it starts the first time stalking is opened (an immediate
ping, then one every stalkFrequency minutes), keeps pinging while the TUI is open (Home included), and is never pinged
early by new jobs, which wait for the next scheduled ping. Only `p` pings early, and it restarts the interval. It only
runs while a tracked job is live: once none is, it goes idle (no timer, no pings) until a submission resumes it (no
ping) or opening the stalker with live jobs starts it again (an immediate ping)."""
import time
from pathlib import Path

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import DataTable, Static

from ..defaults import Defaults
from ..stalk    import (AWAITING_PING, LoadTracked, HasLiveJobs, PollQueue, DismissJobs, HeadLine, ProgressLines,
                        StagePosition, TrackedJob)
from .common    import NAV_BINDINGS, NavFooter, Notice, Popup
from .home      import ShortPath, TitleLine
from .inspect   import Styled

# The three state panes: id, title, column headers
GROUPS = [("running", "Running", ["Job", "Elapsed", "Stage", "Conv"]),
          ("pending", "Pending", ["Job", "Stages", "Expected Start"]),
          ("ended", "Ended", ["Job", "Termination"])]


def Group(job: TrackedJob) -> str:
    if job.ended:
        return "ended"
    return "pending" if job.state in ("PENDING", AWAITING_PING) else "running"


def Cells(job: TrackedJob) -> list:
    """A job's row in its state pane."""
    group = Group(job)
    if group == "pending":
        return [job.name, str(len(job.stages)), "Awaiting Ping" if job.state == AWAITING_PING else job.startAt]
    if group == "ended":
        if not job.termination:
            return [job.name, Styled("No Termination Line", "warning")]
        error = job.termination == Defaults.terminationVariants[2]
        return [job.name, Styled("Error" if error else "Normal", "error" if error else "good")]
    stage = job.stages[job.stage]
    conv = "–"
    if stage.convergence is not None:
        met, total = stage.convergence
        # ✓ once the optimization itself is over (Gaussian's freq step runs on after it)
        conv = f"{met}/{total}" + (" ✓" if stage.label != "Opt" else "")
    return [job.name, job.elapsed, StagePosition(job).replace(", ", " "), conv]


class StalkingRoutine:
    """The app's one stalking routine: the interval timer, and when it last and next pings. The timer only runs while a
    tracked job is live: it stops (idle) once none is, and a submission resumes it."""
    def __init__(self, app) -> None:
        self.app = app
        self.timer = None
        self.started = False
        self.nextPing = 0.0
        self.lastPing = ""
        self.failed = False

    @property
    def interval(self) -> float:
        return Defaults.stalkFrequency * 60

    @property
    def idle(self) -> bool:
        return self.timer is None

    def _StartTimer(self) -> None:
        self.started = True
        self.timer = self.app.set_interval(self.interval, self.Ping)
        self.nextPing = time.monotonic() + self.interval

    def Start(self) -> None:
        """Starts an idle routine with an immediate ping, if any job is live; a running routine keeps its interval."""
        if self.idle and HasLiveJobs():
            self._StartTimer()
            self.Ping()

    def Resume(self) -> None:
        """After a submission: an idle routine that has run this session starts its interval again, without a ping
        (new jobs wait for the next scheduled one). Before the first open, the first open starts it."""
        if self.started and self.idle and HasLiveJobs():
            self._StartTimer()

    def Idle(self) -> None:
        """Stops the timer once no job is live."""
        if not self.idle and not HasLiveJobs():
            self.timer.stop()
            self.timer, self.nextPing = None, 0.0

    def PingNow(self) -> None:
        """Pings now and starts a fresh interval (idle: starts the routine, if any job is live)."""
        if self.idle:
            self.Start()
            return
        self.timer.reset()
        self.Ping()

    def Ping(self) -> None:
        self.nextPing = time.monotonic() + self.interval
        self.app.run_worker(self._Poll, thread=True, exclusive=True, group="stalking", exit_on_error=False)

    def _Poll(self) -> None:
        # The store is re-read every ping, so jobs submitted since (here or by a CLI run) join at this one
        failed = PollQueue(LoadTracked()) is None
        self.app.call_from_thread(self._Polled, failed)

    def _Polled(self, failed: bool) -> None:
        self.failed, self.lastPing = failed, time.strftime("%H:%M:%S")
        self.Idle()
        for screen in self.app.screen_stack:
            if isinstance(screen, StalkerScreen):
                screen.Reload()

    def SecondsLeft(self) -> int:
        return max(0, int(self.nextPing - time.monotonic()))


class StateTable(DataTable):
    """One state's jobs; ↑↓ move, and the highlighted job is shown in Details."""
    def on_focus(self) -> None:
        self.screen.ShowDetails(self)


class StalkerScreen(Screen):
    AUTO_FOCUS = "#running"
    BINDINGS = [
        Binding("p", "ping", "Ping Now"),
        Binding("d", "dismiss_job", "Dismiss"),
        Binding("D", "dismiss_ended", "Dismiss Ended", key_display="D"),
        Binding("escape", "home", "Home"),
        Binding("question_mark", "help", "Help"),
        *NAV_BINDINGS,
    ]

    def __init__(self) -> None:
        super().__init__()
        self.jobs: dict[str, TrackedJob] = {}

    @property
    def routine(self) -> StalkingRoutine:
        return self.app.stalking

    def compose(self) -> ComposeResult:
        yield Static(id="title")
        with Horizontal(id="stalker-body"):
            with VerticalScroll(id="stalker-left"):
                for groupId, title, columns in GROUPS:
                    with Vertical(id=f"{groupId}-pane", classes="pane state-pane"):
                        table = StateTable(id=groupId, cursor_type="row", zebra_stripes=False)
                        table.add_columns(*columns)
                        yield table
            yield Static(id="stalker-details", classes="pane")
        yield NavFooter()

    def on_mount(self) -> None:
        self.query_one("#stalker-details").border_title = "Details"
        self.Reload()
        self.RefreshTitle()
        # The countdown ticks every second; the panes change only when a ping lands (or a job is dismissed)
        self.set_interval(1, self.RefreshTitle)

    def on_screen_resume(self) -> None:
        # Jobs submitted while away show as Awaiting Ping until the next scheduled ping
        self.Reload()

    # ─── Content ──────────────────────────────────────────────────────

    def RefreshTitle(self) -> None:
        live = sum(1 for job in self.jobs.values() if not job.ended)
        seconds = self.routine.SecondsLeft()
        if self.routine.failed:
            status = Styled("Squeue Failed at the Last Ping", "warning")
        elif self.routine.idle:
            status = Text("Idle (No Live Jobs)")
        else:
            status = Text(f"Next Ping {seconds // 60}:{seconds % 60:02d}")
        self.query_one("#title", Static).update(TitleLine("Job Stalker", Styled(f"Stalking {live} Job{'s' if live != 1 else ''}", "info"), status))

    def Reload(self) -> None:
        """Re-reads the tracked jobs into their panes, keeping each pane's highlighted job where it can."""
        self.jobs = {job.key: job for job in LoadTracked()}
        for groupId, title, _ in GROUPS:
            table = self.query_one(f"#{groupId}", StateTable)
            highlighted = self.HighlightedKey(table)
            table.clear()
            for job in self.jobs.values():
                if Group(job) == groupId:
                    table.add_row(*Cells(job), key=job.key)
            self.query_one(f"#{groupId}-pane").border_title = f"{title} ({table.row_count})"
            if highlighted is not None and highlighted in self.jobs and Group(self.jobs[highlighted]) == groupId:
                table.move_cursor(row=table.get_row_index(highlighted))
        # An empty pane has nothing to show: move to the first one with jobs
        focused = self.focused if isinstance(self.focused, StateTable) else None
        if focused is None or not focused.row_count:
            withJobs = [table for table in self.query(StateTable) if table.row_count]
            if withJobs:
                withJobs[0].focus()
                focused = withJobs[0]
        self.ShowDetails(focused or self.query_one("#running", StateTable))
        self.RefreshTitle()

    @staticmethod
    def HighlightedKey(table: DataTable) -> str | None:
        if not table.row_count:
            return None
        return table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value

    def HighlightedJob(self) -> TrackedJob | None:
        table = self.focused if isinstance(self.focused, StateTable) else None
        key = self.HighlightedKey(table) if table is not None else None
        return self.jobs.get(key) if key else None

    def ShowDetails(self, table: DataTable) -> None:
        details = self.query_one("#stalker-details", Static)
        key = self.HighlightedKey(table)
        job = self.jobs.get(key) if key else None
        if job is None:
            details.border_title = "Details"
            details.update(Text("No Jobs Here" if not self.jobs else "No Job Highlighted", "dim"))
            return
        details.border_title = f"Details: {job.name}"
        head, style = HeadLine(job)
        where = " · ".join(part for part in (f"Job ID {job.jobID}", job.cluster, job.location) if part)
        lines = [Text(f"Output: {ShortPath(Path(job.outputPath))}"), Text(where), Text(""), Styled(head, style)]
        lines += [Text(line) for line in ProgressLines(job)]
        footer = ("No pings while no job is live" if self.routine.idle else
                  f"Last ping {self.routine.lastPing}" if self.routine.lastPing else "Awaiting first ping")
        lines += [Text(""), Text(footer, "dim")]
        details.update(Text("\n").join(lines))

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.data_table is self.focused:
            self.ShowDetails(event.data_table)

    # ─── Actions ──────────────────────────────────────────────────────

    def action_ping(self) -> None:
        self.routine.PingNow()
        self.RefreshTitle()

    def action_dismiss_job(self) -> None:
        job = self.HighlightedJob()
        if job is None:
            return
        if job.ended:
            DismissJobs({job.key})
            self.Reload()
            return

        def Answered(result: str) -> None:
            if result == "stop":
                DismissJobs({job.key})
                # The last live job: nothing left to ping for
                self.routine.Idle()
                self.Reload()
        self.app.push_screen(Popup("Stop Stalking?", f"{job.name} keeps running in the queue, but the Job Stalker and "
                                   f"`cu -st` won't follow it any more.", [("s", "Stop Stalking", "stop")], "warning"),
                             Answered)

    def action_dismiss_ended(self) -> None:
        DismissJobs({job.key for job in self.jobs.values() if job.ended})
        self.Reload()

    def action_home(self) -> None:
        # The routine keeps pinging while away
        self.app.pop_screen()

    def action_help(self) -> None:
        Notice(self.app, "Help", "\n".join([
            "tab / shift+tab Move Between Running, Pending and Ended", "↑/↓ Highlight a Job (Shown in Details)",
            "p Ping Now (Starts a Fresh Interval)", "d Dismiss the Highlighted Job", "D Dismiss Every Ended Job",
            "esc Back to Home (Stalking Continues)", "ctrl+q Quit"]))
