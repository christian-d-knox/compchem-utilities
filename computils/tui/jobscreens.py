"""The job screens for a batch of files: Single Point, Benchmark, Re-Run and Run as Written, on the
Optimization builder's template. The left column is its settings rows (rows.py: the method, CPUs, memory, the SLURM
settings, Checkpoint, NBO); the right column is a Jobs table, one row per job ⏎ would submit (planned.py: the names and
inputs the workflows write), over the highlighted job's Renders As (p: its whole input). Read only: changes go through
the settings. ⏎ submits the action's Intent, the path `cu -sp/-b/-re/-r` take. Re-Run has the Neva-Touch Scheduler's
Then: SP step (steps.SchedulerSteps) for each re-run that optimizes."""
from pathlib import Path

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import DataTable, Static, TextArea
from textual.worker import get_current_worker

from ..actions  import Action
from ..catalog  import Catalog
from ..defaults import Defaults
from ..intent   import IntentDraft
from ..jobs     import DefaultMemoryRatio
from ..optimize import (CustomMethod, JoinStages, MethodChoices, MethodProblems, SinglePointChoices, SlurmOverrides, SlurmProblems,
                        SplitStages)
from ..planned  import (LoadMolecules, PlanAsWritten, PlanBenchmark, PlannedJob, PlanReRuns, PlanSinglePoints,
                        Resources)
from .common    import NAV_BINDINGS, TEXT, Column, FrameRule, KeyHint, NavFooter, Notice, Setting, SettingsList, ValueInput
from .home      import ACTION_LABELS, TitleLine
from .inspect   import Styled
from .lists     import Cut
from .rows      import (LEFT_WIDTH, FillValue, ListValue, MethodRows, OutputRows, ProgramOf, RendersAsText,
                        ResourceRows, RouteMeaning)
from .steps     import SP_STEP, SchedulerSteps, StepRule

MAIN_STEP = "main"           # the screen's own step (its tab is named after the action)
PROBLEM_WIDTH = 44           # a skipped job's reason in its row, cut to this
# The Jobs table's columns per action (the last, unnamed, is a skipped job's reason)
COLUMNS = {
    Action.SINGLE_POINT: ("Job", "From", "Spin", "Runs"),
    Action.BENCHMARK:    ("Job", "Method", "Spin", "Runs"),
    Action.RERUN:        ("Job", "From", "Spin", "Runs", "Then"),
    Action.RUN:          ("Job", "Program", "Runs", "CPUs", "Memory"),
}
JOB_ACTIONS = tuple(COLUMNS)


class JobInput(ValueInput):
    """A typed setting whose ⏎ submits the batch."""
    BINDINGS = [Binding("enter", "screen.run", "Submit")]


class JobTable(DataTable):
    """One row per job ⏎ would submit; the highlighted one's input shows under it (p: whole)."""
    BINDINGS = [Binding("enter", "screen.run", "Submit"),
                Binding("p", "screen.toggle_view", "Full Preview", show=False)]


class JobPreview(TextArea):
    """The highlighted job's whole input, read only (its settings are on the left). esc or p goes back to the Jobs."""
    BINDINGS = [Binding("escape", "screen.toggle_view", "Jobs")]


class JobScreen(SchedulerSteps, Screen):
    AUTO_FOCUS = "#settings .-current"
    MAIN_STEP = MAIN_STEP
    SHORT_NAME = "Re-Run"
    BINDINGS = [
        Binding("escape", "back", "Back"),
        Binding("enter", "run", "Submit"),
        Binding("question_mark", "help", "Help"),
        Binding("p", "toggle_view", "Full Preview", show=False),
        # Re-Run's step tabs (hinted on the bottom edge: the footer is full at 84 columns)
        Binding("left_square_bracket", "step(-1)", "Steps", show=False),
        Binding("right_square_bracket", "step(1)", "Steps", show=False),
        *NAV_BINDINGS,
    ]

    def __init__(self, action: Action, files: list[Path]) -> None:
        super().__init__()
        self.action, self.files = action, list(files)
        self.molecules = None                  # [(path, Molecule | None)], read in a worker (Load)
        self.jobs: list[PlannedJob] = []       # the jobs ⏎ would submit, as last planned (Refresh)
        self.view = "jobs"                     # the right column: the Jobs and Renders As, or the Full Preview
        self.ready = False
        self.step = MAIN_STEP
        self.prefilled: dict[str, str] = {"sp-partition": Defaults.partition, "sp-cluster": Defaults.cluster}
        # Single Point and Benchmark: Single Point Methods, named as the Opt builder names them (keys method-N, N its
        # entry); a greyed one says why it can't be written
        self.methods = {key.replace("sp-method", "method", 1): value for key, value in SinglePointChoices().items()}
        if action == Action.RERUN:
            # Re-Run: a corrected route from the Optimization Methods (or Custom), else each file's own (As Each Ran)
            self.methods = MethodChoices(None, False)[0]
        self.problems = MethodProblems(self.methods)
        self.lastMethod, self.customRoute = "", ""
        # Re-Run's Then: SP step (steps.SchedulerSteps)
        self.spChoices = SinglePointChoices()
        self.spProblems = MethodProblems(self.spChoices)
        self.then = None
        self.wasScheduled = False

    # ─── Settings ─────────────────────────────────────────────────────

    def JobSettings(self) -> list[Setting]:
        none = "No Single Point Methods: Add Them in Config › Job Keylists" if not self.methods else ""
        match self.action:
            case Action.SINGLE_POINT:
                methods = MethodRows(self.methods, self.problems, "method", "custom", none)
                chosen = next(setting for setting in methods if setting.on)
                self.lastMethod = chosen.key
                route = self.methods[chosen.key][3] if chosen.key in self.methods else ""
                if chosen.key in self.methods:
                    chosen.meaning = RouteMeaning(route)
                return [*methods, Setting("route", "  └ Route", value=route, accepts=TEXT, under="custom"),
                        *ResourceRows(), *OutputRows()]
            case Action.BENCHMARK:
                # -ovr: the chosen method and every one after it run
                methods = MethodRows(self.methods, self.problems, "method", None, label="Starts At")
                chosen = next((setting for setting in methods if setting.on), None)
                self.lastMethod = chosen.key if chosen else ""
                return [*methods, *ResourceRows(), *OutputRows()]
            case Action.RERUN:
                # As Each Ran, or a corrected route (an Optimization Method, Custom): either way from the last
                # geometry, named as a re-run, so it feeds the species' data file as its next attempt
                methods = MethodRows(self.methods, self.problems, "method", "custom", label="")
                for row in methods:
                    row.on = False
                self.lastMethod = "as-ran"
                return [Setting("as-ran", "Route", "Each File's Own, From Its Last Geometry", fixed="As Each Ran",
                                choice="method", on=True),
                        *methods, Setting("route", "  └ Route", value="", accepts=TEXT, under="custom"),
                        *ResourceRows(), *OutputRows(),
                        Setting("nts", "Neva-Touch Scheduler", "", on=False)]
            case _:
                return [Setting("as-written", "Resources", "CPUs and Memory: Each Input's Own", fixed="As Written"),
                        *ResourceRows(resources=False)]

    def compose(self) -> ComposeResult:
        count = len(self.files)
        yield Static(TitleLine(ACTION_LABELS[self.action], Text(f"{count} File{'s' if count != 1 else ''}")),
                     id="title")
        yield StepRule("┌┐", ACTION_LABELS[self.action], split=LEFT_WIDTH - 1, id="inputs-top")
        with Horizontal(id="inputs-body"):
            with Column(id="inputs-left", classes="side"):
                yield SettingsList(self.JobSettings(), stacked=True, widest=LEFT_WIDTH - 5, inputClass=JobInput,
                                   id="settings")
                # Re-Run's Then: SP step, hidden from the start (it never takes the focus as the screen opens)
                spSettings = SettingsList(self.SinglePointSettings() if self.action == Action.RERUN else [],
                                          stacked=True, widest=LEFT_WIDTH - 5, inputClass=JobInput, id="sp-settings")
                spSettings.display = False
                yield spSettings
            with Vertical(id="inputs-right"):
                yield JobTable(id="job-table", cursor_type="row", zebra_stripes=False)
                with Column(id="renders-as"):
                    yield Static(id="renders-text")
                yield JobPreview(id="input-preview", read_only=True, soft_wrap=False)
        yield FrameRule("└┘", split=LEFT_WIDTH - 1, id="submit")
        yield NavFooter()

    def on_mount(self) -> None:
        self.preview.display = False
        if self.action == Action.BENCHMARK:
            self.settings.Row("memory").SetMeaning("Empty: As Each Method Needs")
        if self.action == Action.RERUN:
            self.settings.Row("memory").SetMeaning("Empty: As Each Job's Method Needs")
        self.ShowSteps()
        self.Refresh()
        self.Load()

    @work(thread=True, exclusive=True, group="read")
    def Load(self) -> None:
        """Each file read once, as dispatch reads it (planned.LoadMolecules; Run as Written: each input as written)."""
        loaded = PlanAsWritten(self.files) if self.action == Action.RUN else LoadMolecules(self.files)
        if not get_current_worker().is_cancelled:
            self.app.call_from_thread(self.Loaded, loaded)

    def Loaded(self, loaded) -> None:
        if self.action == Action.RUN:
            self.jobs, self.molecules = loaded, []
        else:
            self.molecules = loaded
        self.Refresh()

    @property
    def settings(self) -> SettingsList:
        return self.query_one("#settings", SettingsList)

    @property
    def spSettings(self) -> SettingsList:
        return self.query_one("#sp-settings", SettingsList)

    @property
    def table(self) -> JobTable:
        return self.query_one("#job-table", JobTable)

    @property
    def preview(self) -> JobPreview:
        return self.query_one("#input-preview", JobPreview)

    @property
    def scheduled(self) -> bool:
        return self.action == Action.RERUN and SchedulerSteps.scheduled.fget(self)

    def StepTitle(self, step: str) -> str:
        return ACTION_LABELS[self.action] if step == MAIN_STEP else super().StepTitle(step)

    # ─── The jobs ─────────────────────────────────────────────────────

    def MethodIndex(self) -> int | None:
        """The chosen Single Point Methods entry (its index), None for Custom."""
        chosen = self.settings.Chosen("method")
        return list(self.methods).index(chosen) if chosen in self.methods else None

    def ReRunRoute(self) -> str | None:
        """Re-Run's corrected route: the chosen Optimization Method's as written, Custom's as typed, None for As Each
        Ran (each file's own)."""
        chosen = self.settings.Chosen("method")
        if chosen in self.methods:
            return self.methods[chosen][3]
        return self.settings.Row("route").value if chosen == "custom" else None

    def ReadResources(self) -> tuple[Resources, list[str]]:
        settings, errors = self.settings, []
        resources = Resources()
        if self.action == Action.RUN:
            return resources, errors
        try:
            resources.cpus = int(settings.Row("cpus").value)
            if resources.cpus < 1:
                raise ValueError
        except ValueError:
            errors.append("CPUs Must Be a Whole Number Above 0")
        memory = settings.Row("memory").value.strip()
        try:
            resources.memory = float(memory) if memory else None
            if resources.memory is not None and resources.memory <= 0:
                raise ValueError
        except ValueError:
            errors.append("Memory Must Be a Number Above 0")
        if memory == "" and self.action == Action.SINGLE_POINT:
            errors.append("Memory Must Be a Number Above 0")
        resources.checkpoint = settings.Applies(settings.Row("checkpoint"))
        resources.nbo = settings.Applies(settings.Row("nbo"))
        return resources, errors

    def Slurm(self) -> tuple[str | None, str | None, str | None]:
        return (ListValue(self.settings, "wall-time"), ListValue(self.settings, "partition"),
                ListValue(self.settings, "cluster"))

    def Plan(self, resources: Resources) -> tuple[list[PlannedJob], list[str]]:
        """The jobs the settings make, and what stops them all (a Custom route that can't be made)."""
        molecules = self.molecules or []
        match self.action:
            case Action.SINGLE_POINT:
                index = self.MethodIndex()
                if index is not None:
                    return PlanSinglePoints(molecules, Catalog.templates[index], resources), []
                template, _, problem = CustomMethod(self.settings.Row("route").value)
                return PlanSinglePoints(molecules, template, resources, problem), [problem] if problem else []
            case Action.BENCHMARK:
                if not Catalog.canBench:
                    return [], ["Benchmarking Needs at Least 2 Single Point Methods (Config › Job Keylists)"]
                index = self.MethodIndex()
                return (PlanBenchmark(molecules, index, resources), []) if index is not None else ([], [])
            case Action.RERUN:
                route = self.ReRunRoute()
                if route is None:
                    return PlanReRuns(molecules, resources), []
                template, _, problem = CustomMethod(route)
                return PlanReRuns(molecules, resources, template, problem), [problem] if problem else []
        return self.jobs, []

    def ShowOptions(self) -> None:
        """Checkpoint and NBO are Gaussian's: greyed for an ORCA method, and said so where some jobs are ORCA's.
        Memory per core is pre-filled per method for a single point, as the Opt builder's is."""
        if self.action == Action.RUN:
            return
        settings = self.settings
        programs = {job.extension for job in self.jobs if not job.problem}
        if self.action == Action.SINGLE_POINT:
            index = self.MethodIndex()
            if index is not None:
                _, _, extension, route = list(self.methods.values())[index]
            else:
                template, extension, _ = CustomMethod(settings.Row("route").value)
                route = settings.Row("route").value
            if extension:
                FillValue(settings.Row("memory"), f"{DefaultMemoryRatio(route, extension):g}", self.prefilled, "memory")
            programs = {extension} if extension else programs
        gaussian = Defaults.gaussianExtension in programs or not programs
        for key in ("checkpoint", "nbo"):
            if settings.Row(key).available != gaussian:
                settings.SetAvailable(key, gaussian)
        mixed = gaussian and Defaults.orcaExtension in programs
        note = "Gaussian Only" if not gaussian else "Gaussian Jobs Only" if mixed else ""
        nbo = settings.Row("nbo")
        nbo.SetMeaning(note or (Text(f"Last Stage Reads {Defaults.nboKeylist}") if nbo.on else ""))
        settings.Row("checkpoint").SetMeaning(note)

    def ShowMethods(self) -> None:
        """Only the chosen method shows its route under it; Benchmark greys the methods before the one it starts at."""
        if self.action not in (Action.SINGLE_POINT, Action.BENCHMARK, Action.RERUN) or not self.methods:
            return
        chosen = self.settings.Chosen("method") if any(row.on for row in self.settings.rows
                                                         if row.setting.choice == "method") else ""
        keys = list(self.methods)
        start = keys.index(chosen) if chosen in keys else 0
        for position, (key, (_, _, _, route)) in enumerate(self.methods.items()):
            meaning = self.problems.get(key, "")
            if not meaning and key == chosen:
                after = len(keys) - position - 1
                meaning = (Text.assemble(RouteMeaning(route), "\n" if RouteMeaning(route) else "",
                                         f"Runs This and the {after} After It" if after else "Runs This One")
                           if self.action == Action.BENCHMARK else RouteMeaning(route))
            elif not meaning and self.action == Action.BENCHMARK and position < start:
                meaning = Styled(f"Skipped: Starts at {self.methods[chosen][0]}", "dim")
            self.settings.Row(key).SetMeaning(meaning)

    def Refresh(self) -> None:
        """Plan the jobs from the settings and show them, then what ⏎ would submit, or the first thing stopping it."""
        resources, errors = self.ReadResources()
        errors += SlurmProblems(*self.Slurm())
        loading = self.molecules is None
        if not loading:
            self.jobs, stops = self.Plan(resources)
            errors += stops
        self.ShowMethods()
        self.ShowOptions()
        # Re-Run's next job: its rows read against the first job that optimizes (its memory default renders it)
        self.then, thenErrors = None, []
        if self.scheduled:
            first = next((job for job in self.jobs if job.molecule is not None and job.optimizes), None)
            self.then, thenErrors = self.ReadSinglePoint(first.molecule if first else None,
                                                         first.name if first else "")
            errors += thenErrors
            if not any(job.optimizes for job in self.jobs if not job.problem) and not loading:
                errors.append("No Job Here Optimizes: the Then: SP Step Follows an Optimization")
        if self.action == Action.RERUN:
            self.ShowSchedulerRow()
        self.ShowTable()
        self.ShowRightColumn()
        count = sum(1 for job in self.jobs if not job.problem)
        skipped = len(self.jobs) - count
        if not loading and not count and not errors:
            errors.append("No Job Would Be Submitted")
        self.ready = not loading and not errors
        steps = Text.assemble(KeyHint(self.app, "[ ]", "Steps"), " · ") if self.scheduled else Text()
        if loading:
            right = Styled(f"Reading {len(self.files)} File{'s' if len(self.files) != 1 else ''}…", "dim")
        elif errors:
            more = f" (+{len(errors) - 1} More)" if len(errors) > 1 else ""
            right = Text.assemble(steps, Styled(errors[0] + more, "error"))
        else:
            note = f" ({skipped} Skipped)" if skipped else ""
            right = Text.assemble(steps, Styled(f"{count} Job{'s' if count != 1 else ''} Ready{note}", "good"), " · ",
                                  KeyHint(self.app, "⏎", "Submit"))
        self.query_one("#submit", FrameRule).right = right
        self.refresh_bindings()

    # ─── The right column ─────────────────────────────────────────────

    def Row(self, job: PlannedJob) -> list:
        """A job's cells, as COLUMNS names them."""
        then = ("SP" if self.then is not None and job.optimizes else "—") if self.scheduled else ""
        match self.action:
            case Action.SINGLE_POINT:
                cells = [job.name, job.source.name, job.spin, job.runs]
            case Action.BENCHMARK:
                cells = [job.name, job.method, job.spin, job.runs]
            case Action.RERUN:
                cells = [job.name, job.source.name, job.spin, job.runs, then]
            case _:
                cells = [job.name, ProgramOf(job.extension) if job.extension in (Defaults.gaussianExtension,
                                                                                  Defaults.orcaExtension) else "—",
                         job.runs, str(job.cpus or "—"), f"{job.memoryGB} GB" if job.memoryGB else "—"]
        return [Text(str(cell)) for cell in cells] + [Styled(f"⚠ {Cut(job.problem, PROBLEM_WIDTH)}", "warning")
                                                       if job.problem else Text("")]

    def ShowTable(self) -> None:
        """The Jobs, fitted to the pane (a DataTable's columns only ever grow, so they are rebuilt at fixed widths):
        the widest column gives way first, its cells cut with …. A skipped job's reason is the last column, there only
        when a job has one."""
        table, current = self.table, self.table.cursor_row
        titles = list(COLUMNS[self.action]) + ([""] if any(job.problem for job in self.jobs) else [])
        rows = [self.Row(job)[:len(titles)] for job in self.jobs]
        widths = [max([len(title)] + [cell.cell_len for cell in (row[column] for row in rows)])
                  for column, title in enumerate(titles)]
        # The table's own width, less each column's cell padding and the vertical scrollbar's room once rows outgrow it
        available = ((table.content_region.width or 50) - len(titles) * 2 * table.cell_padding
                     - (2 if len(rows) > 11 else 0))
        while sum(widths) > available and max(widths) > 4:
            widths[widths.index(max(widths))] -= 1
        table.clear(columns=True)
        for title, width in zip(titles, widths):
            table.add_column(title, width=width)
        for index, row in enumerate(rows):
            for column, cell in enumerate(row):
                if cell.cell_len > widths[column]:
                    cell.truncate(widths[column], overflow="ellipsis")
            table.add_row(*row, key=str(index))
        if table.row_count:
            table.move_cursor(row=min(current, table.row_count - 1))

    def on_resize(self, event) -> None:
        # The columns take the pane's width
        self.call_after_refresh(self.ShowTable)

    def Highlighted(self) -> PlannedJob | None:
        return self.jobs[self.table.cursor_row] if self.jobs and 0 <= self.table.cursor_row < len(self.jobs) else None

    def ShownText(self, job: PlannedJob) -> tuple[str, str]:
        """(the text the right column shows for a job, its program): its input, or on the Then: SP step its single
        point as it will be written once it ends (coordinates marked)."""
        if self.step == SP_STEP:
            if self.then is None or job.molecule is None or not job.optimizes:
                return "", ""
            return self.then.InputText(job.molecule, job.name, "Re-Run"), self.then.extension
        return job.text, job.extension

    def RendersAs(self, job: PlannedJob) -> Text:
        if self.step == SP_STEP and (job.molecule is None or not job.optimizes):
            return Styled(f"{job.name} Doesn't Optimize: the Neva-Touch Scheduler Has No Next Job for It", "dim")
        if job.problem and self.step != SP_STEP:
            return Styled(f"✗ Skipped: {job.problem}", "error")
        text, extension = self.ShownText(job)
        stages = SplitStages(text, extension) if text else []
        template = self.then.template if self.step == SP_STEP and self.then is not None else job.template
        rows = []
        for number, stage in enumerate(stages, 1):
            base = template.stages[number - 1].base if template is not None and number <= len(template.stages) else ""
            if len(stages) > 1:
                rows.append(Styled(f"Stage {number}", "bold"))
            rows.append(RendersAsText(stage, extension, base))
        if self.step == SP_STEP:
            rows.append(Styled("Spin (U/RO, [oss: …]) Is Classified From the Re-Run's Output When It Ends", "warning"))
        return Text("\n").join(rows)

    def ShowRightColumn(self) -> None:
        """The highlighted job's Renders As (or its Full Preview), and the right column's title."""
        job = self.Highlighted()
        holder = self.query_one("#renders-as", Column)
        name = (job.name + Defaults.singlePointExtra if self.step == SP_STEP else job.name) if job else ""
        holder.border_title = f" {name} · Renders As " if job else " Renders As "
        self.query_one("#renders-text", Static).update(self.RendersAs(job) if job else Text(""))
        if self.view == "full":
            text = self.ShownText(job)[0] if job else ""
            if self.preview.text != text:
                self.preview.load_text(text)
        count = len(self.jobs)
        if self.view == "jobs":
            label = Text.assemble(f"Jobs ({count}) · ", KeyHint(self.app, "esc", "Change Files"), " · ",
                                  KeyHint(self.app, "p", "Full Preview"))
        else:
            extension = self.ShownText(job)[1] if job else ""
            label = Text.assemble(f"Input: {name}{extension} · {ProgramOf(extension)}" if extension else "Input", " · ",
                                  KeyHint(self.app, "p", "Jobs"))
        self.query_one("#inputs-top", FrameRule).label2 = label

    # ─── Events ───────────────────────────────────────────────────────

    def on_settings_list_changed(self, event: SettingsList.Changed) -> None:
        if self.action == Action.RERUN:
            spChosen = self.spSettings.Chosen("sp-method")
            if spChosen != self.spLastMethod:
                self.spLastMethod = spChosen
                self.ShowSinglePointRoute()
            # The Neva-Touch Scheduler turned on opens its step (the Re-Run's rows fold away); off, the step goes
            if self.scheduled != self.wasScheduled:
                self.wasScheduled = self.scheduled
                self.Refresh()
                self.SwitchStep(SP_STEP if self.scheduled else MAIN_STEP)
                return
        if self.action in (Action.SINGLE_POINT, Action.RERUN):
            chosen = self.settings.Chosen("method")
            if chosen != self.lastMethod:
                self.lastMethod = chosen
                self.ShowRoute()
        self.Refresh()

    def ShowRoute(self) -> None:
        """The Route row follows the chosen method: its route as written, or Custom's as last typed (else the route
        shown when Custom was chosen, so Custom starts from it). Never while it is being typed in."""
        row = self.settings.Row("route")
        if row.target.has_focus:
            return
        chosen = self.settings.Chosen("method")
        if chosen not in self.methods and chosen != "custom":
            return
        route = (self.customRoute or row.value) if chosen == "custom" else self.methods[chosen][3]
        if row.value != route:
            row.SetValue(route)

    def on_input_changed(self, event) -> None:
        row = getattr(event.input, "row", None)
        if row is not None and row.typing:
            if row.setting.key == "route":
                self.customRoute = event.value
            elif row.setting.key == "sp-route":
                self.spCustomRoute = event.value
        self.Refresh()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self.ShowRightColumn()

    # ─── Actions ──────────────────────────────────────────────────────

    def check_action(self, action: str, parameters) -> bool | None:
        if action == "run":
            return True if self.ready else None
        if action == "step":
            return True if self.scheduled else None
        if action == "toggle_view":
            return True if self.jobs else None
        return True

    def action_toggle_view(self) -> None:
        """p: the Jobs and Renders As, or the highlighted job's whole input (read only)."""
        self.view = "full" if self.view == "jobs" else "jobs"
        jobs = self.view == "jobs"
        self.table.display = jobs
        self.query_one("#renders-as").display = jobs
        self.preview.display = not jobs
        self.ShowRightColumn()
        (self.table if jobs else self.preview).focus()
        self.refresh_bindings()

    def Draft(self) -> IntentDraft:
        """The settings as the command line gives them: -ovr / --route, --cpus, --memory, -ch, -nbo, the SLURM flags
        (only where they differ from the config, as the Opt builder passes them), and Re-Run's next job."""
        draft = IntentDraft(action=self.action, files=list(self.files))
        if self.action in (Action.SINGLE_POINT, Action.BENCHMARK):
            index = self.MethodIndex()
            if index is not None:
                draft.indexOverride = index
            else:
                draft.route = self.settings.Row("route").value.strip()
        if self.action == Action.RERUN and self.ReRunRoute() is not None:
            draft.route = self.ReRunRoute().strip()
        if self.action != Action.RUN:
            resources, _ = self.ReadResources()
            draft.cpus, draft.memoryRatio = resources.cpus, resources.memory
            draft.checkpoint, draft.nbo7 = resources.checkpoint, resources.nbo
        slurm = SlurmOverrides(*self.Slurm())
        draft.wallTime, draft.partition, draft.cluster = slurm["wallTime"], slurm["partition"], slurm["cluster"]
        if self.scheduled and self.then is not None:
            draft.thenSteps = [self.then.ChainStep()]
        return draft

    def action_run(self) -> None:
        if not self.ready:
            self.app.bell()
            return
        draft = self.Draft()
        errors = draft.Validate()
        if errors:
            Notice(self.app, "Can't Submit", "\n".join(errors), "error")
            return
        self.app.Submit(draft.Finalize(), sum(1 for job in self.jobs if not job.problem))

    def action_back(self) -> None:
        # Home keeps its selection, since it was never unmounted
        self.dismiss(False)

    def action_help(self) -> None:
        lines = ["tab / shift+tab Move Between the Settings and the Jobs", "↑/↓ Move Between Settings or Jobs",
                 "space Turn a Setting On/Off or Choose a Method · Type to Change a Value",
                 "The Highlighted Job's Input Shows Under the Jobs, as It Will Be Written (p: Whole)"]
        if self.action == Action.SINGLE_POINT:
            lines.append("Custom: Type a Route of Your Own (Typing in the Route Row Chooses It)")
        if self.action == Action.BENCHMARK:
            lines.append("Starts At: the Chosen Method and Every One After It Run (-ovr)")
        if self.action == Action.RERUN:
            lines.append("Route: As Each Ran, or a Corrected One (an Optimization Method, or Custom)")
            lines.append("Each Re-Run Starts From Its File's Last Geometry, Named as a Re-Run (8a_re, 8a_re2…)")
            lines.append("Neva-Touch Scheduler: Then a Single Point for Each Re-Run That Optimizes ([ ] Its Step)")
        if self.action == Action.RUN:
            lines.append("Each Input Runs as Written: Its CPUs and Memory Are Its Own")
        lines += ["enter Submit", "esc Back to the File List", "ctrl+q Quit"]
        Notice(self.app, "Help", "\n".join(lines))
