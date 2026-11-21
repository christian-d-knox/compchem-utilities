"""The Conformer Search builder (Input Builders › Conformer Search): the Optimization builder's
Coordinates step, then the search's settings beside what they write. Its engine is crest.py (no Textual), shared with
`cu -crest`: the screen builds a CrestJob and submits it in the app (CrestIntent with the job), tracked like any job.
Re-Run on a CREST output opens it preset from that output (crest.ReRunJob: its next attempt, 8a_CREST_re)."""
import copy
from pathlib import Path

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Static

from ..crest    import (CUSTOM, DEFAULT_FORCE_CONSTANT, CrestJob, ConstraintLines, JobName, Level, Methods,
                        OptionsProblem, ParseConstraints, Solvation, Suggestions)
from ..defaults import Defaults
from ..fileops  import ReRunNameIn, fileCreation
from ..intent   import CrestIntent
from ..molecule import Molecule
from .common    import (NAV_BINDINGS, NUMBER, TEXT, Column, FrameRule, KeyHint, NavFooter, Notice, Setting, SettingsList,
                        ValueInput)
from .home      import TitleLine
from .inspect   import Styled
from .lists     import Cut, ProblemText, TextEditor
from .rows      import FILE_NAME, LEFT_WIDTH, FillValue, ListValue, ResourceRows

# The constraints editor's example (32 columns, as every editor's example is written)
CONSTRAINTS_EXAMPLE = """\
B 12 15      a bond, held at
             the start's length
B 1 9 2.05   ...or at 2.05 A
A 1 2 3      an angle (degrees)
D 1 2 3 4    a dihedral
atoms 11-14  their distances
             to each other
elements Pd  every Pd atom's
fix 2,4,7    fixed in place
# a comment  not used

Atoms count from 1, in the
geometry's order (Gaussian's).
Written in xtb's xcontrol
format: $constrain and $fix."""
OPTIONS_ROW = "options"


class SubmitInput(ValueInput):
    """A typed setting whose ⏎ writes the search and submits it."""
    BINDINGS = [Binding("enter", "screen.run", "Save and Submit")]


class ConformerSearchScreen(Screen):
    """A search's settings (name, charge, multiplicity, the Conformer Search Method or CREST's options typed in full,
    constraints, force constant, resources and SLURM settings) beside Renders As: the crest command and the constraints
    file as they will be written (p: the whole job script, read only)."""
    AUTO_FOCUS = "#settings .-current"
    DEFAULT_CSS = """
    ConformerSearchScreen #crest-renders { height: 1fr; padding: 0 1; }
    """
    BINDINGS = [
        Binding("escape", "back", "Back"),
        Binding("enter", "run", "Save and Submit"),
        Binding("question_mark", "help", "Help"),
        Binding("p", "toggle_view", "Script", show=False),
        *NAV_BINDINGS,
    ]

    def __init__(self, molecule: Molecule, reason: str = "", prior: bool = False, preset: CrestJob | None = None) -> None:
        super().__init__()
        # A copy: the settings are written onto it (CrestJob.Apply), and going back must not change the read
        self.molecule, self.prior, self.preset = copy.copy(molecule), prior, preset
        self.source = molecule.sourcePath if prior else None
        self.methods = Methods()
        self.methodKeys = {f"method-{index}": name for index, name in enumerate(self.methods)}
        self.constraints = preset.constraints if preset is not None else ""
        # A re-run's constraints file as it ran: kept unless constraint lines are typed
        self.asRan = preset.constraintsText if preset is not None else ""
        self.customOptions = preset.options if preset is not None and preset.method not in self.methods else ""
        self.prefilled: dict[str, str] = {}
        self.job: CrestJob | None = None
        self.ready, self.view = False, "renders"

    # ─── Settings ─────────────────────────────────────────────────────

    def DefaultName(self) -> str:
        """The species' search (8a_CREST), or its next attempt when that one has run here."""
        if self.preset is not None:
            return self.preset.name
        from ..datafile import RootName
        name = JobName(RootName(self.molecule.rootName or "untitled"))
        return ReRunNameIn(name, Path.cwd()) if (Path.cwd() / fileCreation(name, Defaults.outputExtension)).exists() \
            else name

    def Settings(self) -> list[Setting]:
        molecule, preset = self.molecule, self.preset
        chosen = (next((key for key, name in self.methodKeys.items() if preset is not None and name == preset.method),
                       "custom") if preset is not None else next(iter(self.methodKeys), "custom"))
        methods = [Setting(key, "", OptionsProblem(self.methods[name]), fixed=Cut(name, LEFT_WIDTH - 9),
                           on=key == chosen, choice="method", available=not OptionsProblem(self.methods[name]))
                   for key, name in self.methodKeys.items()]
        methods.append(Setting("custom", "", "" if self.methods else "No Conformer Search Methods: Config › Job "
                               "Keylists", fixed="Custom" if preset is None or chosen != "custom" or not self.asRanMethod
                               else "As It Ran", on=chosen == "custom", choice="method"))
        methods[0].label = "Method"
        options = self.methods[self.methodKeys[chosen]] if chosen in self.methodKeys else self.customOptions
        charge = preset.charge if preset is not None else molecule.charge or 0
        multiplicity = preset.multiplicity if preset is not None else molecule.multiplicity or 1
        return [
            Setting("name", "Name", value=self.DefaultName(), accepts=FILE_NAME),
            Setting("charge", "Charge", value=str(charge), accepts=r"[\d-]"),
            Setting("multiplicity", "Multiplicity", value=str(multiplicity), accepts=r"\d"),
            *methods,
            # The chosen method's options, so a small change is one edit (typing here chooses Custom)
            Setting(OPTIONS_ROW, "  └ Options", value=options, accepts=TEXT, under="custom"),
            Setting("constraints", "Constraints", fixed="None", opens=True),
            Setting("force", "Force Constant", value=f"{preset.forceConstant if preset else DEFAULT_FORCE_CONSTANT!r}",
                    unit="Eh/bohr^2", accepts=NUMBER),
            *ResourceRows(cpus=preset.cpus if preset is not None else None),
        ]

    @property
    def asRanMethod(self) -> bool:
        return self.preset is not None and self.preset.method not in self.methods

    def compose(self) -> ComposeResult:
        atoms = len(self.molecule.coordinateList)
        conformer = self.molecule.conformer
        named = (self.source.name if self.source else conformer.Label(forceField=False) if conformer
                 else self.molecule.sourcePath.name if self.molecule.sourcePath else "")
        origin = f"{named or 'Pasted'} ({atoms} Atoms)"
        yield Static(TitleLine("Conformer Search: Job", Text(origin)), id="title")
        yield FrameRule("┌┐", "Search", split=LEFT_WIDTH - 1, id="inputs-top")
        with Horizontal(id="inputs-body"):
            with Column(id="inputs-left", classes="side"):
                yield SettingsList(self.Settings(), stacked=True, widest=LEFT_WIDTH - 5, inputClass=SubmitInput,
                                   id="settings")
            with Vertical(id="inputs-right"):
                # What the settings write (its name is the top rule's): never a tab stop, only text beside the settings
                with Column(id="crest-renders"):
                    yield Static(id="renders-text")
        yield FrameRule("└┘", split=LEFT_WIDTH - 1, id="submit")
        yield NavFooter()

    def on_mount(self) -> None:
        if self.preset is not None:
            self.settings.Row("memory").SetValue(f"{self.preset.memoryRatio:g}")
        self.Refresh()
        # A prior job's constraints and TS mode, offered in the constraints editor as comments (read in the background)
        if self.prior and self.source is not None and not self.constraints:
            self.ReadSuggestions(self.source)

    @work(thread=True, exclusive=True, group="suggestions")
    def ReadSuggestions(self, path: Path) -> None:
        from ..datafile import Lookup
        from ..info     import FileFacts
        try:
            facts = Lookup(path) or FileFacts(path)
        except (OSError, ValueError):
            return
        lines = Suggestions(facts)
        if lines:
            self.app.call_from_thread(self.Suggested, lines)

    def Suggested(self, lines: list[str]) -> None:
        if ConstraintLines(self.constraints) or self.constraints.strip():
            return
        self.constraints = "\n".join(["# From the source: remove the # to hold one, or delete it", *lines]) + "\n"
        self.Refresh()

    @property
    def settings(self) -> SettingsList:
        return self.query_one("#settings", SettingsList)

    # ─── The search ───────────────────────────────────────────────────

    def Options(self) -> tuple[str, str]:
        """(the method's label, CREST's options) for the chosen row."""
        chosen = self.settings.Chosen("method")
        if chosen in self.methodKeys:
            name = self.methodKeys[chosen]
            return name, self.methods[name]
        return ("As It Ran" if self.asRanMethod else CUSTOM), " ".join(self.settings.Row(OPTIONS_ROW).value.split())

    def ConstraintsSummary(self) -> str:
        lines = ConstraintLines(self.constraints)
        if lines:
            return f"{len(lines)} Line{'s' if len(lines) != 1 else ''}"
        if self.asRan:
            return "As It Ran"
        return "Suggested (␣ to See)" if self.constraints.strip() else "None"

    def Refresh(self) -> None:
        """Re-validate live: the settings as one CrestJob (crest.py, cu -crest's too), what it writes, and what ⏎ would
        submit, or the first thing stopping it."""
        settings, errors = self.settings, []
        name = settings.Row("name").value.strip()
        if not name:
            errors.append("Enter a Name")
        elif (Path.cwd() / fileCreation(name, Defaults.outputExtension)).exists():
            errors.append(f"{name} Has Run Here: Use {ReRunNameIn(name, Path.cwd())}, Its Next Attempt")
        try:
            charge, multiplicity = int(settings.Row("charge").value), int(settings.Row("multiplicity").value)
        except ValueError:
            charge = multiplicity = None
            errors.append("Charge and Multiplicity Must Be Whole Numbers")
        settings.Row("multiplicity").SetMeaning(f"--uhf {multiplicity - 1}" if multiplicity else "")
        label, options = self.Options()
        for key, method in self.methodKeys.items():
            settings.Row(key).SetMeaning(OptionsProblem(self.methods[method])
                                         or (Cut(self.methods[method], LEFT_WIDTH - 7)
                                             if key == settings.Chosen("method") else ""))
        if settings.Chosen("method") == "custom" and not options:
            errors.append("Enter CREST's Options")
        constraintsRow = settings.Row("constraints")
        if constraintsRow.setting.fixed != self.ConstraintsSummary():
            constraintsRow.setting.fixed = self.ConstraintsSummary()
            constraintsRow.Show()
        constraintsRow.SetMeaning("" if not self.asRan or ConstraintLines(self.constraints)
                                               else "Typed Lines Replace Them")
        try:
            force = float(settings.Row("force").value)
        except ValueError:
            force = 0.0
        try:
            cpus = int(settings.Row("cpus").value)
            memory = float(settings.Row("memory").value or 0) or None
        except ValueError:
            cpus, memory = 0, None
        FillValue(settings.Row("memory"), f"{Defaults.memoryRatio:g}", self.prefilled, "memory")
        if memory is None:
            errors.append("Memory Must Be a Number Above 0")
        self.job = job = CrestJob(copy.copy(self.molecule), name or "untitled", charge, multiplicity, label, options,
                                  self.constraints, force, cpus, memory, ListValue(settings, "wall-time"),
                                  ListValue(settings, "partition"), ListValue(settings, "cluster"),
                                  constraintsText=self.asRan)
        job.Apply()
        checkErrors, warnings = job.Check()
        errors += checkErrors
        self.ready = not errors
        self.ShowRightColumn()
        more = f" (+{len(errors) - 1} More)" if len(errors) > 1 else ""
        ready = Styled(f"⚠ {warnings[0]}", "warning") if warnings else Styled("1 Job Ready", "good")
        self.query_one("#submit", FrameRule).right = (
            Styled(errors[0] + more, "error") if errors
            else Text.assemble(ready, " · ", KeyHint(self.app, "⏎", "Save and Submit")))
        self.refresh_bindings()

    def RendersAs(self) -> Text:
        """The crest command and the constraints file as they will be written, then where the results go."""
        job, rows = self.job, []
        level = Level(job.options) or "CREST's Default (GFN2-xTB)"
        rows.append(Styled(f"{level} · {Solvation(job.options) or 'Gas Phase'} · {job.cpus} Threads", "info"))
        rows.append(Text(job.CommandLine()))
        constraints = job.ConstraintsFileText()
        if constraints:
            rows += [Text(""), Styled(f"{job.constraintsFile}", "info"), Text(constraints.rstrip("\n"))]
        _, problems = job.ParsedConstraints()
        if problems:
            rows += [Text(""), ProblemText(problems)]
        rows += [Text(""), Styled(f"Output: {job.output} · Results: {job.resultsFolder}/", "info")]
        return Text("\n").join(rows)

    def ShowRightColumn(self) -> None:
        if self.view == "renders":
            self.query_one("#renders-text", Static).update(self.RendersAs())
            label = Text.assemble("Renders As · ", KeyHint(self.app, "p", "Script"))
        else:
            self.query_one("#renders-text", Static).update(Text(self.job.ScriptText()))
            label = Text.assemble(f"Script: {self.job.script} · ", KeyHint(self.app, "p", "Renders As"))
        self.query_one("#inputs-top", FrameRule).label2 = label

    # ─── Events ───────────────────────────────────────────────────────

    def on_input_changed(self, event) -> None:
        row = getattr(event.input, "row", None)
        if row is not None and row.setting.key == OPTIONS_ROW and row.typing:
            self.customOptions = event.value
        self.Refresh()

    def on_settings_list_changed(self, event: SettingsList.Changed) -> None:
        # The Options row follows the chosen method (Custom's as last typed), never while it is typed in
        row = self.settings.Row(OPTIONS_ROW)
        chosen = self.settings.Chosen("method")
        if not row.target.has_focus:
            options = self.methods[self.methodKeys[chosen]] if chosen in self.methodKeys else (
                self.customOptions or row.value)
            if row.value != options:
                row.SetValue(options)
        self.Refresh()

    def on_settings_list_open(self, event: SettingsList.Open) -> None:
        if event.key == "constraints":
            self.EditConstraints()

    def EditConstraints(self) -> None:
        """The constraint lines in their own editor: each checked against the geometry as typed, with the file they
        write. A re-run's constraints as it ran show as comments (typed lines replace them)."""
        atoms = len(self.molecule.coordinateList)
        text = self.constraints
        if not text.strip() and self.asRan:
            text = "\n".join(["# It ran with these (typed lines replace them):",
                              *(f"# {line}" for line in self.asRan.splitlines())]) + "\n"

        def Check(typed: str) -> Text:
            constraints, problems = ParseConstraints(typed, atoms)
            lines = ConstraintLines(typed)
            rows = [Text(f"{len(lines)} Line{'s' if len(lines) != 1 else ''} · {atoms} Atoms")]
            if problems:
                rows.append(ProblemText(problems))
            elif constraints and self.job is not None:
                job = copy.copy(self.job)
                job.constraints = typed
                rows.append(Text(job.ConstraintsFileText().rstrip("\n")))
            elif self.asRan:
                rows.append(Styled("As It Ran", "info"))
            return Text("\n").join(rows)

        def Done(typed) -> None:
            if typed is None or typed == text:
                return
            self.constraints = typed
            self.Refresh()
        self.app.push_screen(TextEditor("Constraints", text, Check, CONSTRAINTS_EXAMPLE), Done)

    # ─── Actions ──────────────────────────────────────────────────────

    def check_action(self, action: str, parameters) -> bool | None:
        return None if action == "run" and not self.ready else True

    def action_toggle_view(self) -> None:
        self.view = "script" if self.view == "renders" else "renders"
        self.ShowRightColumn()

    def action_run(self) -> None:
        if not self.ready or self.job is None:
            self.app.bell()
            return
        self.app.Submit(CrestIntent(files=[], job=self.job), 1)

    def action_back(self) -> None:
        self.dismiss(None)

    def action_help(self) -> None:
        Notice(self.app, "Help", "\n".join([
            "↑/↓ Move Between Settings (Type to Change a Value)",
            "←/→ or space Choose the Method; the Chosen One Shows Its Options Under It",
            "Method: the Conformer Search Methods (Config › Job Keylists), or Custom: CREST's Options Typed in Full "
            "(CompUtils Writes --T, --chrg, --uhf and --cinp Itself)",
            "Name: the Species' Search (8a_CREST), So Its Output Feeds 8a's Data File; One That Has Run Here Is "
            "Re-run as Its Next Attempt (8a_CREST_re)",
            "Multiplicity: CREST Takes Unpaired Electrons (--uhf), Multiplicity − 1; xTB Has No Broken-Symmetry "
            "Singlets",
            "Constraints: space Opens Its Editor: B i j (a Bond), A i j k, D i j k l, atoms, elements, fix; a Bond, "
            "Angle or Dihedral Without a Value Is Held at the Starting Geometry's",
            "From a Prior Job, Its TS Eigenvector's Bonds and ModRed Constraints Are Offered There as Comments",
            "Force Constant: How Stiff the Constraints Are (1.0 for a Few TS Bonds; 2.0 if They Stray)",
            "p Switches Between Renders As and the Whole Job Script",
            "Results: Copied Back to NAME-scratch/ However the Job Ends; the Output Is NAME.out",
            "enter Save and Submit (Tracked Like Any Job)", "esc Back", "ctrl+q Quit"]))
