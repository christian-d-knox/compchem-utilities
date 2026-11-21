"""The Neva-Touch Scheduler's step tabs, shared by every screen whose job can have a next job (the Optimization
builder; Re-Run): the top rule names the steps (`Optimization · Then: SP`), the left column shows one step's settings
at a time, and the Then: SP step's rows (the next job, its method, resources, SLURM settings, Checkpoint, NBO)."""
from rich.text import Text
from textual.message import Message

from ..defaults import Defaults
from ..optimize import CustomMethod, SinglePointStep
from .common    import TEXT, FrameRule, KeyHint, Setting, SettingsList
from .inspect   import Styled
from .rows      import FillValue, ListValue, MethodRows, OutputRows, ResourceRows, RouteMeaning

# The steps (step tabs on the top rule): the job's own, then the Neva-Touch Scheduler's next job
OPT_STEP, RERUN_STEP, SP_STEP = "opt", "rerun", "sp"
STEP_TITLES = {OPT_STEP: "Optimization", RERUN_STEP: "Re-Run", SP_STEP: "Then: SP"}


class StepRule(FrameRule):
    """A job screen's top rule, whose left label is its step tabs (Optimization · Then: SP): a click on a tab's name
    opens that step, as [ ] do (mouse parity). spans: each tab's (start, end, step) within the label."""
    spans: list[tuple[int, int, str]] = []

    class Picked(Message):
        def __init__(self, step: str) -> None:
            super().__init__()
            self.step = step

    def on_click(self, event) -> None:
        # The label starts after the corner, a rule and a space ("┌─ ")
        x = event.x - 3
        step = next((step for start, end, step in self.spans if start <= x < end), None)
        if step is not None:
            self.post_message(self.Picked(step))


class SchedulerSteps:
    """The step tabs and the Then: SP step for a screen whose main settings (self.settings, with an "nts" row and its
    partition/cluster rows) are MAIN_STEP's. The screen gives: settings, spSettings (#sp-settings), spChoices,
    spProblems, prefilled (FillValue's), then, ShowRightColumn(), and SchedulerWarning() (a project-file problem of
    the next job, '' by default). SHORT_NAME names the main job in the SP's notes ("Follows the Opt")."""
    MAIN_STEP = OPT_STEP
    SHORT_NAME = "Opt"
    step: str
    spLastMethod: str = ""
    spCustomRoute: str = ""

    def SinglePointSettings(self) -> list[Setting]:
        """The Then: SP step: the next job (a choice: later kinds are more rows), its method (Benchmark Methods, then
        Custom), resources and SLURM settings (partition and cluster follow the main job's until typed), checkpoint,
        NBO."""
        noMethods = "" if self.spChoices else "No Benchmark Methods: Add Them in Config › Job Keylists"
        methods = MethodRows(self.spChoices, self.spProblems, "sp-method", "sp-custom", noMethods)
        chosen = next(setting for setting in methods if setting.on)
        self.spLastMethod = chosen.key
        route = self.spChoices[chosen.key][3] if chosen.key in self.spChoices else ""
        if chosen.key in self.spChoices:
            chosen.meaning = RouteMeaning(route)
        return [
            Setting("next-sp", "Next Job", f"The {self.SHORT_NAME}'s Last Geometry", fixed="Single Point", on=True,
                    choice="next"),
            *methods,
            Setting("sp-route", "  └ Route", value=route, accepts=TEXT, under="sp-custom"),
            *ResourceRows("sp-"),
            *OutputRows("sp-"),
        ]

    @property
    def scheduled(self) -> bool:
        """Whether the Neva-Touch Scheduler submits a next job once this one ends."""
        return self.settings.Applies(self.settings.Row("nts"))

    @property
    def stepSettings(self) -> SettingsList:
        """The settings of the step shown."""
        return self.spSettings if self.step == SP_STEP else self.settings

    def SinglePointMethod(self) -> tuple[str, str, str, str]:
        """(name, route as written, input extension, why there is none) for the SP's chosen method."""
        sp = self.spSettings
        chosen = sp.Chosen("sp-method")
        if chosen != "sp-custom":
            name, _, extension, route = self.spChoices[chosen]
            return name, route, extension, ""
        route = sp.Row("sp-route").value.strip()
        template, extension, problem = CustomMethod(route)
        return "Custom", route, extension, problem

    def ReadSinglePoint(self, molecule, name: str) -> tuple[SinglePointStep | None, list[str]]:
        """The next job from its step's rows (None, and why, when its method can't be made). Partition and cluster
        follow the main job's until typed; memory is pre-filled per method, as the main job's is; Checkpoint and NBO
        are Gaussian's. molecule and name: a job it follows (its memory default renders it)."""
        sp = self.spSettings
        for key in ("partition", "cluster"):
            value = ListValue(self.settings, key)
            if value is not None:
                row = sp.Row(f"sp-{key}")
                FillValue(row, value, self.prefilled, f"sp-{key}")
                row.SetMeaning(f"Follows the {self.SHORT_NAME}" if row.value.strip() == value else "")
        stepName, route, extension, problem = self.SinglePointMethod()
        gaussian = extension == Defaults.gaussianExtension
        for key in ("sp-checkpoint", "sp-nbo"):
            if sp.Row(key).available != gaussian:
                sp.SetAvailable(key, gaussian)
        nbo = sp.Row("sp-nbo")
        nbo.SetMeaning("Gaussian Only" if not gaussian
                       else Text(f"Last Stage Reads {Defaults.nboKeylist}") if nbo.on else "")
        sp.Row("sp-checkpoint").SetMeaning("" if gaussian else "Gaussian Only")
        if problem:
            return None, [f"Then SP: {problem}"]
        step = SinglePointStep(stepName, route)
        if molecule is not None:
            FillValue(sp.Row("sp-memory"), f"{step.DefaultMemory(molecule, name):g}", self.prefilled, "sp-memory")
        try:
            step.cpus = int(sp.Row("sp-cpus").value)
            step.memory = float(sp.Row("sp-memory").value or 0) or None
        except ValueError:
            step.cpus, step.memory = 0, None
        errors = [] if step.memory is not None else ["Then SP: Memory Must Be a Number Above 0"]
        step.wallTime, step.partition, step.cluster = (ListValue(sp, "sp-wall-time"), ListValue(sp, "sp-partition"),
                                                       ListValue(sp, "sp-cluster"))
        step.nbo, step.checkpoint = sp.Applies(nbo), sp.Applies(sp.Row("sp-checkpoint"))
        return step, errors

    def SchedulerWarning(self) -> str:
        return ""

    def StepTitle(self, step: str) -> str:
        """A step tab's name (a screen named by its action gives its own for MAIN_STEP)."""
        return STEP_TITLES[step]

    def ShowSchedulerRow(self) -> None:
        """The main step's Neva-Touch Scheduler row says what comes next, and how to open it (its step tab)."""
        row = self.settings.Row("nts")
        if not self.scheduled:
            row.SetMeaning("Then: a Next Job, Once This One Ends")
            return
        name = self.then.name if self.then is not None else "?"
        meaning = Text.assemble(Text(f"Then: Single Point · {name} · "), KeyHint(self.app, "]", "Opens It"))
        warning = self.SchedulerWarning()
        if warning:
            meaning.append_text(Text.assemble("\n", Styled(f"⚠ {warning}", "warning")))
        row.SetMeaning(meaning)

    def ShowSinglePointRoute(self) -> None:
        """The next job's Route row, as the main job's: the chosen method's route, or Custom's as last typed."""
        row = self.spSettings.Row("sp-route")
        if row.target.has_focus:
            return
        chosen = self.spSettings.Chosen("sp-method")
        route = (self.spCustomRoute or row.value) if chosen == "sp-custom" else self.spChoices[chosen][3]
        if row.value != route:
            row.SetValue(route)

    # ─── The step tabs ────────────────────────────────────────────────

    def Steps(self) -> list[str]:
        return [self.MAIN_STEP, SP_STEP] if self.scheduled else [self.MAIN_STEP]

    def ShowSteps(self) -> None:
        """The top rule's step tabs: the step shown in bold and underlined, the others dimmed; a lone step plain."""
        rule = self.query_one("#inputs-top", StepRule)
        steps = self.Steps()
        label, spans = Text(), []
        for step in steps:
            if label:
                label.append(" · ")
            title = self.StepTitle(step)
            start = label.cell_len
            label.append(title, style="" if len(steps) == 1 else "bold underline" if step == self.step else "dim")
            spans.append((start, start + len(title), step))
        rule.spans, rule.label = spans, label

    def SwitchStep(self, step: str) -> None:
        """Show one step's settings, and its stages on the right (the fold: the main rows are hidden on Then: SP)."""
        if step not in self.Steps():
            step = self.MAIN_STEP
        changed, self.step = step != self.step, step
        self.settings.display = step == self.MAIN_STEP
        self.spSettings.display = step == SP_STEP
        if changed:
            # Back where the step was left: its current row in view (the main one's: the Scheduler row, ticked)
            settings = self.stepSettings
            settings.Choose(settings.current, focus=True)
            self.call_after_refresh(settings.current.scroll_visible, animate=False)
        self.ShowSteps()
        self.ShowRightColumn()
        self.refresh_bindings()

    def on_step_rule_picked(self, event: StepRule.Picked) -> None:
        self.SwitchStep(event.step)

    def action_step(self, delta: int) -> None:
        steps = self.Steps()
        if len(steps) > 1:
            self.SwitchStep(steps[(steps.index(self.step) + delta) % len(steps)])
