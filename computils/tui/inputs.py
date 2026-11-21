"""Input Builders (Home's Input Builders list): screens that build a new input and submit it. The Optimization builder
(TUI_DESIGN.md 4.11) is two steps: Coordinates (an .xyz, a prior job's geometry, pasted rows, or a SMILES' RDKit
conformer), then the Job, its
settings beside an editable preview of the input. The saved input is submitted the way Run as Written submits one."""
import copy
from pathlib import Path

import regex
from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import Screen
from textual.widgets import DataTable, Input, OptionList, Static, TextArea
from textual.widgets.option_list import Option
from textual.worker import get_current_worker

from ..actions  import SpinState
from ..catalog  import ROUTE_LEAK_PATTERN, STAGE_JOIN, Catalog, RouteTemplate
from ..defaults import Defaults
from ..fileops  import (ExtractFrom, ExtractFromText, ExtractXyz, InputRoutes, JobChecks, extensionGetter,
                        fileCreation)
from ..jobs     import HeaderSends, OptMethods
from ..molecule import Molecule
from ..smiles   import Available as RDKitAvailable
from ..optimize import (FROM_SOURCE as FROM_SOURCE_KEY, COORDINATES_MARKER, ModRedCheck, ModRedStages, MethodChoices,
                        MethodProblems, CustomMethod, StartingSpin, SinglePointChoices, SinglePointStep, SplitStages,
                        JoinStages, OptimizationJob)
from .fromsmiles import BUILDING, ConformerCount, ConformerTable, Indented, SmilesBuilder, StructureLines
from .common    import (NAV_BINDINGS, NUMBER, TEXT, Column, FrameRule, KeyHint, NavFooter, Notice, Popup, Setting, SettingsList,
                        ValueInput)
from .home      import TitleLine
from .inspect   import SPAN_STYLES, Formula, JobSummary, OutputExtensions, PreviewMolecule, RouteSpans, Styled
from .lists     import Cut, ProblemText, TextEditor
from .rows      import (COORDINATE_ROW, FILE_NAME, LEFT_WIDTH, FillValue, ListValue, MethodRows, OutputRows, ProgramOf,
                        RendersAsText, ResourceRows, RouteMeaning)
from .steps     import OPT_STEP, SP_STEP, STEP_TITLES, SchedulerSteps, StepRule

STATUS_STYLES = {"Normal": "good", "Error": "error", "Unknown": "warning", "Input": "info"}
# The ModRed editor's example (32 columns)
MODRED_EXAMPLE = """\
B 1 2 F
A 1 2 3 F
D 1 2 3 4 F
B 5 9 S 10 0.05

One line per coordinate: B
bond, A angle, D dihedral, by
atom number (as in the
geometry), then F to freeze it,
or S steps step-size to scan.

They go after the geometry, a
blank line before them."""
# The stage editor's example for a stage without ModRed lines (32 columns)
STAGE_EXAMPLE = """\
%nprocshared=24
%mem=48GB
%chk=8a.chk
# M06/SDD stable=opt
  geom=allcheck guess=read

One stage of the input, as it
is written: Link 0 (%), the
route (#), then its title,
charge and geometry (stage 1,
or geom=check), then any lines
the route reads.

Typing here keeps the stage as
you wrote it: ^r in the Stages
view brings back the settings'."""
class LeaveText(TextArea):
    """A text box in a builder: esc leaves it for the settings (⏎ types a newline here, as in every text box)."""
    BINDINGS = [Binding("escape", "screen.leave_text", "Done")]


# ─── Step 1: Coordinates ──────────────────────────────────────────────

def SourceSettings() -> list[Setting]:
    """Where the coordinates come from. A SMILES is built by RDKit (smiles.py: the lowest-energy of its RDKit Conformers,
    unless another is picked), greyed with why where RDKit isn't installed."""
    missing = RDKitAvailable()
    return [
        Setting("xyz", "", fixed="XYZ File", on=True, choice="source"),
        Setting("prior", "", fixed="Prior Job", on=False, choice="source"),
        Setting("paste", "", fixed="Paste", on=False, choice="source"),
        Setting("smiles", "", missing, fixed="SMILES", on=False, choice="source", available=not missing),
        Setting("rdkit-conformers", "RDKit Conformers", value=str(Defaults.rdkitConformers), accepts=r"\d",
                under="smiles", available=not missing),
    ]


class SourceList(SettingsList):
    """The Source choice: ␣ (or a click on its box) chooses a source and moves on to its files (or the paste box), as
    choosing on Home moves on to the next pane; ←→ only switch it."""

    def action_toggle(self) -> None:
        chosen = self.current.available and self.current.setting.choice
        changed = chosen and not self.current.on
        super().action_toggle()
        if chosen:
            # The new source's files are listed now (its Changed message comes after), so it moves to those
            if changed:
                self.screen.ShowSource()
            self.screen.EnterSource()


class SmilesBox(Input):
    """The SMILES, typed: its RDKit conformers are built after a pause. esc returns to the Source; ⏎ continues."""
    BINDINGS = [Binding("escape", "screen.leave_text", "Done"), Binding("enter", "screen.continue", "Continue")]

    def __init__(self, **kwargs) -> None:
        super().__init__(placeholder="A SMILES, e.g. CC(=O)OC1CCCC1", compact=True, **kwargs)


class SourceFiles(OptionList):
    # The highlighted file is the one read; ⏎ continues with it, as from every field, and ␣ (choosing it) does too
    BINDINGS = [Binding("enter", "screen.continue", "Continue"),
                Binding("space", "screen.continue", "Continue", show=False)]


class CoordinatesScreen(SmilesBuilder, Screen):
    AUTO_FOCUS = "#settings .-current"
    BINDINGS = [
        Binding("escape", "back", "Home"),
        Binding("enter", "continue", "Continue"),
        Binding("question_mark", "help", "Help"),
        *NAV_BINDINGS,
    ]

    def __init__(self) -> None:
        super().__init__()
        self.folder = Path.cwd()
        self.paths: list[Path] = []
        # What ⏎ continues with: the molecule read from the highlighted file (or the pasted text), and for a prior job
        # why its spin state is what it is
        self.molecule: Molecule | None = None
        self.reason = ""
        self.readPath: Path | None = None
        self._readTimer = None
        # The SMILES source: the RDKit conformer in use (1 = the lowest energy), for the build last shown
        self.rank, self._shownBuild = 1, None

    def compose(self) -> ComposeResult:
        yield Static(TitleLine("Optimization: Coordinates"), id="title")
        yield FrameRule("┌┐", "Source", split=LEFT_WIDTH - 1, id="inputs-top")
        with Horizontal(id="inputs-body"):
            with Column(id="inputs-left", classes="side"):
                yield SourceList(SourceSettings(), stacked=True, widest=LEFT_WIDTH - 5, id="settings")
                yield Static(id="structure")
            with Vertical(id="inputs-right"):
                yield SourceFiles(id="source-files")
                yield LeaveText(id="paste", soft_wrap=False)
                yield SmilesBox(id="smiles-box")
                yield ConformerTable(id="conformers", cursor_type="row", zebra_stripes=False)
        yield FrameRule("└┘", split=LEFT_WIDTH - 1, id="submit")
        yield NavFooter()

    def on_mount(self) -> None:
        self.ShowSource()

    @property
    def source(self) -> str:
        return self.query_one("#settings", SettingsList).Chosen("source")

    # ─── Sources ──────────────────────────────────────────────────────

    def ShowSource(self) -> None:
        """List the files the source reads (or show the paste box), and read the first."""
        source, top = self.source, self.query_one("#inputs-top", FrameRule)
        files, paste = self.query_one("#source-files", SourceFiles), self.query_one("#paste", LeaveText)
        files.display, paste.display = source not in ("paste", "smiles"), source == "paste"
        self.query_one("#smiles-box").display = self.query_one("#conformers").display = source == "smiles"
        self.molecule, self.reason, self.readPath = None, "", None
        if source == "paste":
            top.label2 = Text.assemble("Paste Coordinates · ", KeyHint(self.app, "esc", "Done"))
            self.ReadPasted(paste.text)
            return
        if source == "smiles":
            top.label2 = Text.assemble("SMILES · ", KeyHint(self.app, "esc", "Done"))
            self.ReadSmiles()
            return
        extensions = ((Defaults.coordExtension,) if source == "xyz" else
                      (Defaults.gaussianExtension, Defaults.orcaExtension, *OutputExtensions()))
        self.paths = sorted(path for path in self.folder.iterdir() if path.is_file() and path.suffix in extensions)
        width = max((len(path.name) for path in self.paths), default=0) + 3
        files.clear_options()
        files.add_options([Option(Text(path.name.ljust(width) + "…"), id=path.name) for path in self.paths])
        top.label2 = f"{'XYZ Files' if source == 'xyz' else 'Prior Jobs'} ({len(self.paths)})"
        self.Summarize(self.paths, source, width)
        if self.paths:
            files.highlighted = 0
        self.ScheduleRead(self.paths[0] if self.paths else None, 0)

    # Paths are absolute, and the source is passed in: the choice can change while a worker runs
    @work(thread=True, exclusive=True, group="rows")
    def Summarize(self, paths: list[Path], source: str, width: int) -> None:
        """Fill each row in: an .xyz's atoms and formula, a prior job's program and status (one map per file)."""
        worker = get_current_worker()
        for index, path in enumerate(paths):
            if worker.is_cancelled:
                return
            try:
                if source == "xyz":
                    coordinates = ExtractFrom(path, ExtractXyz, empty=[])
                    # The atom count in a fixed column, so the formulas line up
                    summary = Text(f"{f'{len(coordinates)} Atoms':<11}{Formula(coordinates)}" if coordinates
                                   else "No Coordinates")
                else:
                    program, status = JobSummary(path)
                    summary = Text.assemble(f"{program or '—':<7}", Styled(status, STATUS_STYLES.get(status, "dim")))
            except (OSError, ValueError, IndexError):
                summary = Styled("Unreadable", "warning")
            self.app.call_from_thread(self.SetRow, index, path.name, Text(path.name.ljust(width)) + summary)

    def SetRow(self, index: int, name: str, prompt: Text) -> None:
        files = self.query_one("#source-files", SourceFiles)
        # The list may have been refilled (another source) since the worker read this file
        if index < files.option_count and files.get_option_at_index(index).id == name:
            files.replace_option_prompt_at_index(index, prompt)

    def ScheduleRead(self, path: Path | None, delay: float = 0.15) -> None:
        # Debounced, so holding an arrow key doesn't read every file it passes
        if self._readTimer is not None:
            self._readTimer.stop()
        self.readPath, self.molecule = path, None
        self.ShowStructure()
        if path is not None:
            self._readTimer = self.set_timer(delay, lambda: self.Read(path, self.source)) if delay else None
            if not delay:
                self.Read(path, self.source)

    @work(thread=True, exclusive=True, group="read")
    def Read(self, path: Path, source: str) -> None:
        try:
            if source == "xyz":
                molecule = Molecule(path, path.stem, "0", "1", ExtractFrom(path, ExtractXyz, empty=[]), path.suffix,
                                    path.stem)
                reason = ""
            else:
                # As dispatch reads it, without writing the .xyz: last geometry, charge, multiplicity, spin, route
                molecule, reason = PreviewMolecule(path)
        except (OSError, ValueError, IndexError):
            molecule, reason = None, ""
        if not get_current_worker().is_cancelled:
            self.app.call_from_thread(self.ShowRead, path, molecule, reason)

    def ShowRead(self, path: Path, molecule: Molecule | None, reason: str) -> None:
        # A newer highlight (or another source) has been chosen since this file was read
        if path != self.readPath:
            return
        self.molecule, self.reason = molecule, reason
        self.ShowStructure()

    def ReadSmiles(self) -> None:
        """Build the typed SMILES (unless it, and the count, are what was last built), then show what there is."""
        count = self.query_one("#settings", SettingsList).Row("rdkit-conformers").value
        self.ScheduleBuild(self.query_one("#smiles-box", SmilesBox).value.strip(), ConformerCount(count))
        self.ShowBuild()

    def ShowBuild(self) -> None:
        # SmilesBuilder's: a build (or its problem) arrived, or the conformer in use changed. A new build starts at
        # its lowest-energy conformer
        build = None if self.smilesBuilding else self.smilesBuild
        if build is not self._shownBuild:
            self.rank, self._shownBuild = 1, build
            self.query_one("#conformers", ConformerTable).Show(build, self.rank)
        if self.source != "smiles":
            return
        self.molecule = build.Molecule("", self.rank) if build is not None else None
        self.ShowStructure()

    def ReadPasted(self, text: str) -> None:
        coordinates = ExtractFromText(text, ExtractXyz, empty=[]) if text.strip() else []
        self.molecule = Molecule(Path(""), "", "0", "1", coordinates, "", "")
        self.ShowStructure()

    # ─── Status ───────────────────────────────────────────────────────

    def ShowStructure(self) -> None:
        source, molecule = self.source, self.molecule
        lines, problem = [Text("Structure", "bold")], ""
        if source == "smiles":
            lines.append(Text(" SMILES"))
            if self.smilesBuilding:
                lines.append(Text(f" {BUILDING}", "dim"))
                problem = BUILDING
            elif molecule is None:
                problem = self.smilesProblem or "Enter a SMILES"
                # In full here: the bottom edge cuts a long one (RDKit's valence messages)
                lines += Indented([Styled(problem, "error")], LEFT_WIDTH - 3)
            else:
                lines += Indented(StructureLines(self.smilesBuild, self.rank), LEFT_WIDTH - 3)
            self.query_one("#structure", Static).update(Text("\n").join([Text("")] + lines))
            self.query_one("#submit", FrameRule).right = (
                Styled(problem, "dim" if problem == BUILDING else "error") if problem
                else Text.assemble(Styled(f"{len(molecule.coordinateList)} Atoms Built", "good"), " · ",
                                   KeyHint(self.app, "⏎", "Continue")))
            self.refresh_bindings()
            return
        if source == "paste":
            lines.append(Text(" Pasted"))
        elif self.readPath is None:
            problem = "No .xyz Files Here" if source == "xyz" else "No Inputs or Outputs Here"
        else:
            lines.append(Text(f" {self.readPath.name}"))
            if source == "prior":
                lines.append(Text(" Input Geometry" if self.readPath.suffix in (Defaults.gaussianExtension,
                                                                                Defaults.orcaExtension) else " Last Geometry"))
        count = len(molecule.coordinateList) if molecule else 0
        if molecule is None and self.readPath is not None:
            lines.append(Text(" Reading…", "dim"))
            problem = "Reading…"
        elif molecule is not None:
            if count:
                lines.append(Text(f" {count} Atoms · {Formula(molecule.coordinateList)}"))
            else:
                problem = "No Coordinates Found"
            if source == "prior" and count:
                lines.append(Text(f" Charge {molecule.charge or '—'} · Mult {molecule.multiplicity or '—'}"))
                lines.append(Text(f" {molecule.spinState.name} ({self.reason})" if self.reason else f" {molecule.spinState.name}"))
        self.query_one("#structure", Static).update(Text("\n").join([Text("")] + lines))
        self.query_one("#submit", FrameRule).right = (Styled(problem, "dim" if problem == "Reading…" else "error")
            if problem else Text.assemble(Styled(f"{count} Atoms Read", "good"), " · ", KeyHint(self.app, "⏎", "Continue")))
        self.refresh_bindings()

    def Ready(self) -> bool:
        return self.molecule is not None and bool(self.molecule.coordinateList)

    # ─── Events ───────────────────────────────────────────────────────

    def on_settings_list_changed(self, event: SettingsList.Changed) -> None:
        self.ShowSource()

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        path = self.folder / event.option.id
        if path != self.readPath:
            self.ScheduleRead(path)

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if self.source == "paste":
            self.ReadPasted(event.text_area.text)

    def on_input_changed(self, event: Input.Changed) -> None:
        # The SMILES, or the RDKit Conformers count (typing it chooses SMILES: its Changed message rebuilds too)
        if self.source == "smiles":
            self.ReadSmiles()

    # ─── Actions ──────────────────────────────────────────────────────

    def OnSource(self) -> bool:
        return self.focused is not None and self.query_one("#settings", SourceList) in self.focused.ancestors_with_self

    def check_action(self, action: str, parameters) -> bool | None:
        # ⏎ Continue shows dimmed until a structure has been read, except on the Source, where it moves to the files
        return None if action == "continue" and not (self.Ready() or self.OnSource()) else True

    def EnterSource(self) -> None:
        """Move on to the chosen source's files, or into the paste box (the SMILES box)."""
        if self.source == "paste":
            self.query_one("#paste", LeaveText).focus()
        elif self.source == "smiles":
            self.query_one("#smiles-box", SmilesBox).focus()
        elif self.paths:
            self.query_one("#source-files", SourceFiles).focus()
        else:
            self.app.bell()

    def action_continue(self) -> None:
        # ⏎ moves forward: from the Source to its files (or the paste box), from a file to the Job. The paste box takes
        # ⏎ as a newline, so once coordinates are pasted (esc out of it), ⏎ on the Source continues
        if self.OnSource() and not (self.source in ("paste", "smiles") and self.Ready()):
            self.EnterSource()
            return
        if not self.Ready():
            self.app.bell()
            return
        self.app.push_screen(OptimizationScreen(self.molecule, self.reason, self.source == "prior"))

    def action_pick_conformer(self) -> None:
        """␣ on an RDKit conformer: use it (the lowest-energy one is used until another is picked)."""
        table = self.query_one("#conformers", ConformerTable)
        if self.smilesBuild is None or not table.row_count:
            return
        self.rank = table.cursor_row + 1
        table.Show(self.smilesBuild, self.rank)
        self.ShowBuild()

    def action_leave_text(self) -> None:
        self.query_one("#settings", SourceList).current.target.focus()

    def action_back(self) -> None:
        self.dismiss(None)

    def action_help(self) -> None:
        Notice(self.app, "Help", "\n".join([
            "tab / shift+tab Move Between the Source and the Files (or the Paste Box)",
            "↑/↓ Move Between Sources or Files (the Highlighted File Is Read)",
            "←/→ Switch the Source",
            "space / enter on a Source: Choose It and Go to Its Files (or the Paste Box)",
            "space / enter on a File: Continue to the Job Settings With It",
            "Paste: Rows of Symbol x y z (Count and Comment Lines Optional; the Last Block Is Used)",
            "SMILES: Built by RDKit as RDKit Conformers (Count Under It; qol.toml rdkitConformers), the Lowest Energy "
            "Used; space on Another Uses That One. Charge and Multiplicity Come From the SMILES",
            "esc in the Paste Box Return to the Source, Where enter Continues",
            "esc Back to Home", "ctrl+q Quit"]))


# ─── Step 2: the Job ──────────────────────────────────────────────────

class SubmitInput(ValueInput):
    """A typed setting whose ⏎ saves the input and submits it (the footer says so, not the Builder's Run)."""
    BINDINGS = [Binding("enter", "screen.run", "Save and Submit")]


class PreviewArea(LeaveText):
    BINDINGS = [Binding("escape", "screen.leave_text", "Done"),
                Binding("ctrl+r", "screen.regenerate", "Regenerate", key_display="^r")]


class StageTable(DataTable):
    """The Stages view: a row per stage of the input (and a └ ModRed row under a ModRed stage). ␣ edits the highlighted
    stage alone, one --Link1-- section of the input, as Config's Job Keylists edit one section of a file; ^r brings an
    edited stage back to the settings; p shows the Full Preview."""
    BINDINGS = [Binding("space", "screen.edit_stage", "Edit", key_display="␣"),
                Binding("ctrl+r", "screen.regenerate_stage", "Regenerate", key_display="^r"),
                Binding("enter", "screen.run", "Save and Submit"),
                Binding("p", "screen.toggle_view", "Full Preview", show=False)]

    def StageAt(self) -> int | None:
        """The stage number of the highlighted row (a └ ModRed row is its stage's)."""
        if not self.row_count:
            return None
        key = self.coordinate_to_cell_key((self.cursor_row, 0)).row_key.value
        return int(key.rsplit("-", 1)[1])


class OptimizationScreen(SchedulerSteps, Screen):
    """The job's settings (name, charge, multiplicity, spin, method, resources) beside the input they make, shown stage
    by stage (the Stages view) or whole (the Full Preview, p). A stage follows the settings until it is typed in (in its
    editor or the Full Preview); it is then kept as typed, marked Edited, until ^r regenerates it. ModRed lines typed
    after a stage's geometry are read back as that stage's ModRed input, so they don't stop it following the settings."""
    AUTO_FOCUS = "#settings .-current"
    BINDINGS = [
        Binding("escape", "back", "Back"),
        Binding("enter", "run", "Save and Submit"),
        Binding("question_mark", "help", "Help"),
        Binding("p", "toggle_view", "Full Preview", show=False),
        # The step tabs (shown on the bottom edge while there is a next step: the footer is full at 84 columns)
        Binding("left_square_bracket", "step(-1)", "Steps", show=False),
        Binding("right_square_bracket", "step(1)", "Steps", show=False),
        *NAV_BINDINGS,
    ]

    def __init__(self, molecule: Molecule, reason: str = "", prior: bool = False) -> None:
        super().__init__()
        # A copy: the Job's settings are written onto it (Retarget, charge...), and going back must not change the read
        self.molecule, self.prior = copy.copy(molecule), prior
        self.source = molecule.sourcePath if prior else None
        # Why a singlet's spin starts where it does: spinstates.txt, else how the prior job was classified
        decided = StartingSpin(self.molecule, reason)
        self.spinNote = f"({decided})" if decided else ""
        # The method choices (optimize.MethodChoices, shared with cu -opt): setting key -> (label, template, input
        # extension, its route as written: what the Route row shows and a Custom route starts from); a None template
        # can't be made (fromProblem says why)
        self.methods, self.fromProblem = MethodChoices(self.molecule, prior)
        self.optMethods = OptMethods()
        # Why a method's stages can't be written (ORCA stages, mixed programs): its row is greyed with the reason
        self.problems = MethodProblems(self.methods)
        self.job: OptimizationJob | None = None   # the settings as last checked (Refresh)
        self.stageInputs: dict[int, str] = {}   # stage number -> its ModRed lines (kept across method changes)
        self.generatedStages: list[str] = []     # each stage as the settings write it (optimize.SplitStages)
        self.stageEdits: dict[int, str] = {}     # stage number -> the stage as typed (kept until ^r)
        # The whole input as typed in the Full Preview, when its stages no longer line up with the method's (a stage
        # added or removed there): every stage is then the user's
        self.fullEdit: str | None = None
        self.view = "stages"                     # the right column: the Stages view, or the Full Preview ("full")
        self.extension = Defaults.gaussianExtension
        self.lastMethod = ""                     # the method the stages were made for (a change may ask first)
        self.chained = False                     # the chosen method has stages (Checkpoint is then required)
        self.checkpointChoice = False            # Checkpoint as chosen before stages required it
        self.customRoute = ""        # the Custom route as last typed (the Route row shows the chosen method's otherwise)
        # Each pre-filled row's last pre-fill (FillValue), by key: the SP's partition and cluster start as the config's,
        # so they follow the Opt's from the start
        self.prefilled: dict[str, str] = {"sp-partition": Defaults.partition, "sp-cluster": Defaults.cluster}
        self.ready = False
        self.path: Path | None = None
        # The Neva-Touch Scheduler's next job (the Then: SP step): its method choices (Benchmark Methods, then Custom),
        # as the Opt's, and its stages as they will be written (read only: coordinates marked)
        self.step = OPT_STEP
        self.spChoices = SinglePointChoices()
        self.spProblems = MethodProblems(self.spChoices)
        self.spLastMethod = ""
        self.spCustomRoute = ""
        self.then: SinglePointStep | None = None
        self.spStages: list[str] = []
        self.wasScheduled = False

    def JobSettings(self) -> list[Setting]:
        molecule = self.molecule
        oss = molecule.spinState == SpinState.OSS
        # One line each: only the chosen method shows its stages under it (ShowMethodStages), and a greyed one why. The
        # first usable method starts chosen: a prior job's own route, else the first tag, else Custom
        methods = MethodRows(self.methods, self.problems, "method", "custom",
                             "" if self.optMethods else "No Optimization Methods: Add Them in Config › Job Keylists")
        chosen = next(setting for setting in methods if setting.on)
        self.lastMethod = chosen.key
        # A prior job's route that can't be used says why
        if self.prior and self.fromProblem:
            methods[0].meaning = self.fromProblem
        if chosen.key in self.methods:
            chosen.meaning = RouteMeaning(self.methods[chosen.key][3])
        route = self.methods[chosen.key][3] if chosen.key in self.methods else ""
        return [
            Setting("name", "Name", value=molecule.rootName, accepts=FILE_NAME),
            Setting("charge", "Charge", value=str(molecule.charge or 0), accepts=r"[\d-]"),
            Setting("multiplicity", "Multiplicity", value=str(molecule.multiplicity or 1), accepts=r"\d"),
            Setting("css", "Spin", fixed="CSS", on=not oss, choice="spin"),
            Setting("oss", "", fixed="OSS", on=oss, choice="spin"),
            *methods,
            # The chosen method's route, so a small change to it is one edit (typing here chooses Custom)
            Setting("route", "  └ Route", value=route, accepts=TEXT, under="custom"),
            # CPUs, memory, and the job's SLURM settings, pre-filled from the config
            *ResourceRows(),
            *OutputRows(),
            # The Neva-Touch Scheduler: the next job, once this one ends normally. On, its settings are the Then: SP step
            Setting("nts", "Neva-Touch Scheduler", "", on=Defaults.autoNTS),
        ]

    def SlurmValue(self, key: str) -> str | None:
        return ListValue(self.settings, key)

    def ShowMethodStages(self) -> None:
        """Only the chosen method's row shows its stages; the others are one line (a greyed one keeps why)."""
        chosen = self.settings.Chosen("method")
        for key, (_, _, _, route) in self.methods.items():
            meaning = self.problems.get(key) or (self.fromProblem if key == FROM_SOURCE_KEY and self.prior else "")
            if key == chosen and not meaning:
                meaning = RouteMeaning(route)
            self.settings.Row(key).SetMeaning(meaning)
        chosen = self.spSettings.Chosen("sp-method")
        for key, (_, _, _, route) in self.spChoices.items():
            meaning = self.spProblems.get(key) or (RouteMeaning(route) if key == chosen else "")
            self.spSettings.Row(key).SetMeaning(meaning)

    def compose(self) -> ComposeResult:
        atoms = len(self.molecule.coordinateList)
        conformer = self.molecule.conformer
        named = (self.source.name if self.source else conformer.Label(forceField=False) if conformer
                 else self.molecule.sourcePath.name)
        origin = f"{named or 'Pasted'} ({atoms} Atoms)"
        yield Static(TitleLine("Optimization: Job", Text(origin)), id="title")
        yield StepRule("┌┐", STEP_TITLES[OPT_STEP], split=LEFT_WIDTH - 1, id="inputs-top")
        with Horizontal(id="inputs-body"):
            with Column(id="inputs-left", classes="side"):
                # One step's settings at a time (the step tabs): the optimization, then the Neva-Touch Scheduler's next
                yield SettingsList(self.JobSettings(), stacked=True, widest=LEFT_WIDTH - 5, inputClass=SubmitInput,
                                   id="settings")
                # Hidden from the start, so it never takes the focus as the screen opens
                spSettings = SettingsList(self.SinglePointSettings(), stacked=True, widest=LEFT_WIDTH - 5,
                                          inputClass=SubmitInput, id="sp-settings")
                spSettings.display = False
                yield spSettings
            with Vertical(id="inputs-right"):
                # The Stages view (the default): the stages, then the highlighted one as it will be written
                yield StageTable(id="stage-table", cursor_type="row", zebra_stripes=False)
                with Column(id="renders-as"):
                    yield Static(id="renders-text")
                # The Full Preview (p): the whole input, editable
                yield PreviewArea(id="input-preview", soft_wrap=False)
        yield FrameRule("└┘", split=LEFT_WIDTH - 1, id="submit")
        yield NavFooter()

    def on_mount(self) -> None:
        self.preview.display = False
        self.Refresh()
        self.wasScheduled = self.scheduled
        self.ShowSteps()

    @property
    def settings(self) -> SettingsList:
        return self.query_one("#settings", SettingsList)

    @property
    def spSettings(self) -> SettingsList:
        return self.query_one("#sp-settings", SettingsList)

    @property
    def preview(self) -> PreviewArea:
        return self.query_one("#input-preview", PreviewArea)

    @property
    def table(self) -> StageTable:
        return self.query_one("#stage-table", StageTable)

    @property
    def edited(self) -> bool:
        """Whether any stage is kept as typed (it no longer follows the settings)."""
        return bool(self.stageEdits) or self.fullEdit is not None

    def Stages(self) -> list[str]:
        """Each stage as it will be written: as typed where it was, else as the settings make it."""
        if self.fullEdit is not None:
            return SplitStages(self.fullEdit, self.extension)
        return [self.stageEdits.get(number, text) for number, text in enumerate(self.generatedStages, 1)]

    def FullText(self) -> str:
        """The input as ⏎ writes it."""
        return self.fullEdit if self.fullEdit is not None else JoinStages(self.Stages())

    def StageEdited(self, number: int) -> bool:
        return self.fullEdit is not None or number in self.stageEdits

    def ModRedNumbers(self) -> list[int]:
        return ModRedStages(self.job.template, self.extension) if self.job is not None else []

    def StageRoute(self, text: str, extension: str | None = None) -> str:
        """A stage's route as written (its marker left out); extension: its program's (default: the Opt's)."""
        routes = ExtractFromText(text, InputRoutes, extension or self.extension, empty=[]) or [""]
        return routes[0]

    def ReadStageText(self, number: int, text: str) -> None:
        """A stage typed by hand: ModRed lines alone are read back as its ModRed input (it keeps following the
        settings); anything else keeps it as typed."""
        lines = self.job.ReadStage(number, text) if self.job is not None else None
        if lines is None:
            self.stageEdits[number] = text
            return
        self.stageEdits.pop(number, None)
        if number in self.ModRedNumbers():
            self.stageInputs[number] = lines

    def ReadFullPreview(self, typed: str) -> None:
        """The Full Preview typed in: each stage read as if typed in its own editor, unless the stages no longer line
        up with the method's (one added or removed), when the whole text is the user's."""
        stages = SplitStages(typed, self.extension)
        if len(stages) != len(self.generatedStages):
            self.fullEdit = typed
            return
        self.fullEdit = None
        for number, text in enumerate(stages, 1):
            if text != self.Stages()[number - 1]:
                self.ReadStageText(number, text)

    # ─── The job ──────────────────────────────────────────────────────

    def Method(self) -> tuple[RouteTemplate | None, str, str]:
        """(template, input extension, why there is none) for the chosen method."""
        chosen = self.settings.Chosen("method")
        if chosen != "custom":
            _, template, extension, _ = self.methods[chosen]
            return template, extension, ""
        return CustomMethod(self.settings.Row("route").value)

    def Refresh(self) -> None:
        """Re-validate live: write the settings onto the molecule, regenerate the preview (unless it was edited), and
        show what ⏎ would save, or the first thing stopping it."""
        settings, molecule, errors = self.settings, self.molecule, []
        name = settings.Row("name").value.strip()
        if not name:
            errors.append("Enter a Name")
        try:
            charge, multiplicity = int(settings.Row("charge").value), int(settings.Row("multiplicity").value)
        except ValueError:
            charge = multiplicity = None
            errors.append("Charge and Multiplicity Must Be Whole Numbers")
        # Spin only means something for singlets: a higher multiplicity is open shell
        openShell = multiplicity is not None and multiplicity > 1
        for key in ("css", "oss"):
            if settings.Row(key).available == openShell:
                settings.SetAvailable(key, not openShell)
        settings.Row("oss").SetMeaning(f"Mult {multiplicity}: Open Shell" if openShell else self.spinNote)
        template, extension, problem = self.Method()
        if problem:
            errors.append(problem)
        try:
            cpus = int(settings.Row("cpus").value)
            memory = float(settings.Row("memory").value or 0) or None
        except ValueError:
            cpus, memory = 0, None
        fileProblem, warnings = "", []
        self.extension = extension
        self.ShowMethodStages()
        if template is not None:
            self.ShowStages(template, extension)
            # The settings as one job (optimize.OptimizationJob, cu -opt's too), on this screen's molecule
            self.job = job = OptimizationJob(
                molecule, name, charge, multiplicity, SpinState.OSS if settings.Chosen("spin") == "oss" else SpinState.CSS,
                template, extension, cpus, memory, settings.Applies(settings.Row("checkpoint")),
                {number: self.stageInputs.get(number, "") for number in ModRedStages(template, extension)},
                nbo=settings.Applies(settings.Row("nbo")), wallTime=self.SlurmValue("wall-time"),
                partition=self.SlurmValue("partition"), cluster=self.SlurmValue("cluster"))
            job.Apply()
            FillValue(settings.Row("memory"), f"{job.DefaultMemory():g}", self.prefilled, "memory")
            # The Neva-Touch Scheduler's next job, read from its step's rows (after Apply: it renders this molecule)
            self.then, thenErrors = self.ReadSinglePoint(molecule, name) if self.scheduled else (None, [])
            job.then = self.then
            # Its checks: CPUs, electrons, ModRed lines (atom numbers outside the geometry stop it; empty lines are only
            # a warning), the SLURM settings, the project files, then the next job's (named as its step)
            checkErrors, warnings, fileProblem = job.Check()
            errors += checkErrors + thenErrors
            if memory is None:
                errors.append("Memory Must Be a Number Above 0")
            # The next job's project-file problem is a warning (it is written when the opt ends), named in full
            warnings = [f"Then SP: {job.spProblem}" if warning == "Scheduled SP Problem" else warning
                        for warning in warnings]
            self.path = Path.cwd() / fileCreation(name, extension) if name else None
            # The stages need only what goes into them: a name only names the file (and %chk). Edited stages are kept
            if charge is not None and cpus >= 1 and memory is not None:
                self.Regenerate()
            self.spStages = (SplitStages(self.then.InputText(molecule, name), self.then.extension)
                             if self.then is not None and charge is not None and not thenErrors else [])
        self.ShowSchedulerRow()
        if self.edited:
            warnings.append("Using Custom Parameters")
        self.ShowRightColumn()
        # A project-file problem stops a generated input; an edited one is the user's to fix
        if fileProblem and not self.edited:
            errors.append(fileProblem)
        self.ready = not errors and self.path is not None
        more = f" (+{len(errors) - 1} More)" if len(errors) > 1 else ""
        if fileProblem and self.edited:
            warnings.insert(0, fileProblem)
        # A warning takes the place of '1 Job Ready' (the job is still ready), so the edge keeps its ⏎ hint. With a
        # next job, the step keys come first (the footer is full at 84 columns)
        ready = Styled(f"⚠ {warnings[0]}", "warning") if warnings else Styled("1 Job Ready", "good")
        steps = Text.assemble(KeyHint(self.app, "[ ]", "Steps"), " · ") if self.scheduled else Text()
        self.query_one("#submit", FrameRule).right = (
            Text.assemble(steps, Styled(errors[0] + more, "error")) if errors
            else Text.assemble(steps, ready, " · ", KeyHint(self.app, "⏎", "Save and Submit")))
        self.refresh_bindings()

    # ─── The Neva-Touch Scheduler's next job (steps.SchedulerSteps: the Then: SP step) ──

    def SchedulerWarning(self) -> str:
        return self.job.spProblem if self.job is not None else ""

    def Regenerate(self) -> None:
        """Each stage as the settings write it (optimize.OptimizationJob.StageTexts: cu -opt writes the same). Edited
        stages are kept; one past the new stage count goes (a Custom route typed shorter)."""
        self.generatedStages = self.job.StageTexts()
        for number in [number for number in self.stageEdits if number > len(self.generatedStages)]:
            del self.stageEdits[number]
        # The Full Preview follows, except while it is being typed in (its text is read back as the stages)
        text = self.FullText()
        if self.step == OPT_STEP and self.preview.text != text and not self.preview.has_focus:
            self.preview.load_text(text)

    # ─── The right column: the shown step's stages ────────────────────

    def ViewStages(self) -> list[str]:
        """The shown step's stages: the Opt's (editable), or the next job's as they will be written (read only)."""
        return self.spStages if self.step == SP_STEP else self.Stages()

    def ViewExtension(self) -> str:
        return self.then.extension if self.step == SP_STEP and self.then is not None else self.extension

    def ViewFullText(self) -> str:
        return JoinStages(self.spStages) if self.step == SP_STEP else self.FullText()

    def ShowRightColumn(self) -> None:
        """The shown step's Stages view (or Full Preview, read only for the next job) and the right column's title."""
        if self.view == "full":
            self.preview.read_only = self.step == SP_STEP
            text = self.ViewFullText()
            if self.preview.text != text and not (self.step == OPT_STEP and self.preview.has_focus):
                self.preview.load_text(text)
        self.ShowStagesView()
        self.ShowTitle(self.ViewExtension())

    # ─── Stages ───────────────────────────────────────────────────────

    def ShowStages(self, template: RouteTemplate, extension: str) -> None:
        """Checkpoint follows the chosen method's stages: they require it (they pass on what they made through it), so
        it is ticked and greyed with that reason."""
        settings, stages = self.settings, template.stages
        gaussian = extension == Defaults.gaussianExtension
        chained = gaussian and len(stages) > 1
        row = settings.Row("checkpoint")
        if chained != self.chained:
            if chained:
                self.checkpointChoice, row.on = row.on, True
            else:
                row.on = self.checkpointChoice
            self.chained = chained
        row.SetMeaning("Required: Later Stages Read the Geometry and Orbitals From It" if chained else "")
        # Checkpoint is Gaussian's %chk
        if row.available != (gaussian and not chained):
            settings.SetAvailable("checkpoint", gaussian and not chained)
        # So is the $NBO keylist (JobInputText writes none for ORCA)
        nbo = settings.Row("nbo")
        if nbo.available != gaussian:
            settings.SetAvailable("nbo", gaussian)
        nbo.SetMeaning("Gaussian Only" if not gaussian
                       else Text(f"Last Stage Reads {Defaults.nboKeylist}") if nbo.on else "")

    def StageRuns(self, text: str, extension: str | None = None) -> str:
        """What a stage runs (fileops.JobChecks: ModRed, Stability, OptTS+Freq, SP...)."""
        extension = extension or self.extension
        return JobChecks(self.StageRoute(text, extension), extension, text).kinds

    def ShowStagesView(self) -> None:
        """The Stages view's table, and the highlighted stage's Renders As. A DataTable's columns only ever grow, so
        they are rebuilt at fixed widths to the pane (the route takes what the others leave)."""
        table, stages, extension = self.table, self.ViewStages(), self.ViewExtension()
        # The next job's stages are read only: no ModRed rows (its lines are the Opt's) and nothing Edited
        opt = self.step == OPT_STEP
        # The table's own width, less each column's cell padding, and the vertical scrollbar's room once the rows
        # outgrow the table (#stage-table: 10 lines): wider and a horizontal scrollbar appears
        fixed = (("Stage", 5), ("Runs", 12), ("", 6))
        modRed = set(self.ModRedNumbers()) if opt else set()
        rows = len(stages) + len(modRed & set(range(1, len(stages) + 1)))
        available = (table.content_region.width or 50) - 4 * 2 * table.cell_padding - (2 if rows >= 9 else 0)
        routeWidth = max(available - sum(width for _, width in fixed), 12)
        current = table.cursor_row
        table.clear(columns=True)
        for title, columnWidth in (fixed[0], fixed[1], ("Route", routeWidth), fixed[2]):
            table.add_column(title, width=columnWidth)
        atoms = len(self.molecule.coordinateList)
        for number, text in enumerate(stages, 1):
            mark = Styled("Edited", "warning") if opt and self.StageEdited(number) else Text("")
            table.add_row(str(number), self.StageRuns(text, extension),
                          Text(Cut(self.StageRoute(text, extension), routeWidth)), mark, key=f"stage-{number}")
            if number in modRed:
                if self.StageEdited(number):
                    summary = Text("In the Edited Stage")
                else:
                    lines, problems = ModRedCheck(self.stageInputs.get(number, ""), atoms)
                    summary = Styled(f"{lines} · ✗ {problems[0].removeprefix('✗ ')}" if problems else lines,
                                     "error" if problems else "warning" if lines == "Empty" else "")
                table.add_row("", "└ ModRed", summary, Text(""), key=f"modred-{number}")
        if table.row_count:
            table.move_cursor(row=min(current, table.row_count - 1))
        self.ShowRendersAs()

    def RendersAs(self, number: int) -> Text:
        """One stage as it will be written, shortened: Link 0 on one line, the geometry as its atom count, blank lines
        left out; the route coloured as the Builder's preview colours one (the method, and what CompUtils adds to the
        route as written: the U/RO reference, geom=allcheck, guess=read, nofreeze)."""
        stages = self.ViewStages()
        if not 1 <= number <= len(stages):
            return Text("")
        base = ""
        if self.step == SP_STEP:
            if self.then is not None and number <= len(self.then.template.stages):
                base = self.then.template.stages[number - 1].base
        elif self.job is not None and self.fullEdit is None and number <= len(self.job.template.stages):
            base = self.job.template.stages[number - 1].base
        # The next job's spin is the one thing the preview can't know: it is classified from the Opt's output then
        note = ("Spin (U/RO, [oss: …]) Is Classified From the Optimization's Output When It Ends"
                if self.step == SP_STEP else "")
        return RendersAsText(stages[number - 1], self.ViewExtension(), base, note)

    def ShowRendersAs(self) -> None:
        number = self.table.StageAt()
        holder = self.query_one("#renders-as", VerticalScroll)
        holder.border_title = f" Stage {number} Renders As " if number else " Renders As "
        self.query_one("#renders-text", Static).update(self.RendersAs(number) if number else Text(""))

    def EditStage(self, number: int) -> None:
        """One stage in its own editor: its --Link1-- section of the input, as Config's Job Keylists edit one section of
        a file (esc is Done). ModRed lines typed after the geometry stay live; anything else keeps the stage as typed."""
        text, atoms = self.Stages()[number - 1], len(self.molecule.coordinateList)
        modRed = number in self.ModRedNumbers()

        def Check(typed: str) -> Text:
            route = self.StageRoute(typed)
            rows = [Text(f"Stage {number} · Runs {JobChecks(route, self.extension, typed).kinds}")]
            lines = self.job.ReadStage(number, typed) if self.job is not None and self.fullEdit is None else None
            if lines is None:
                rows.append(Styled("Using Custom Parameters (^r in Stages Restores the Settings)", "warning"))
            else:
                rows.append(Styled("Follows the Settings", "good"))
                if modRed:
                    summary, problems = ModRedCheck(lines, atoms)
                    rows.append(Text(f"ModRed: {summary} · {atoms} Atoms"))
                    if problems:
                        rows.append(ProblemText(problems))
            if route and regex.search(ROUTE_LEAK_PATTERN, route):
                rows.append(Styled("✗ The Route Still Has Template Syntax ({tag}, [group] or %)", "error"))
            return Text("\n").join(rows)

        def Done(typed) -> None:
            if typed is None or typed == text:
                return
            if self.fullEdit is not None:
                stages = SplitStages(self.fullEdit, self.extension)
                stages[number - 1] = typed
                self.fullEdit = JoinStages(stages)
            else:
                self.ReadStageText(number, typed)
            self.Refresh()
        title = f"Stage {number} · {self.StageRuns(text)}"
        self.app.push_screen(TextEditor(title, text, Check, MODRED_EXAMPLE if modRed else STAGE_EXAMPLE), Done)

    def ShowTitle(self, extension: str) -> None:
        name = self.path.stem if self.path is not None else ""
        if self.step == SP_STEP:
            # The next job, named as cu -sp names it, written when the Opt ends
            spName = f"{name}{Defaults.singlePointExtra}"
            if self.view == "stages":
                label = Text.assemble(f"{spName} · Stages ({len(self.spStages)})", " · ",
                                      KeyHint(self.app, "p", "Full Preview"))
            else:
                label = Text.assemble(f"Input: {fileCreation(spName, extension)} · {ProgramOf(extension)}", " · ",
                                      KeyHint(self.app, "p", "Stages"))
            self.query_one("#inputs-top", FrameRule).label2 = label
            return
        if self.view == "stages":
            label = Text.assemble(f"Stages ({len(self.Stages())})", " · ", KeyHint(self.app, "p", "Full Preview"))
        else:
            label = Text(f"Input: {self.path.name} · {ProgramOf(extension)}" if self.path is not None else "Input")
            label.append_text(Text.assemble(" · ", KeyHint(self.app, "p", "Stages")))
        if self.edited:
            label.append_text(Text.assemble(" · ", Styled("Edited", "warning")))
        self.query_one("#inputs-top", FrameRule).label2 = label

    # ─── Events ───────────────────────────────────────────────────────

    def on_input_changed(self, event) -> None:
        # The Custom route as typed, restored whenever Custom is chosen again
        row = getattr(event.input, "row", None)
        if row is not None and row.setting.key == "route" and row.typing:
            self.customRoute = event.value
        if row is not None and row.setting.key == "sp-route" and row.typing:
            self.spCustomRoute = event.value
        self.Refresh()

    def on_settings_list_changed(self, event: SettingsList.Changed) -> None:
        # The next job's method: its Route row follows it
        spChosen = self.spSettings.Chosen("sp-method")
        if spChosen != self.spLastMethod:
            self.spLastMethod = spChosen
            self.ShowSinglePointRoute()
        # The Neva-Touch Scheduler turned on opens its step (the Opt's rows fold away); off, the step goes
        if self.scheduled != self.wasScheduled:
            self.wasScheduled = self.scheduled
            self.Refresh()
            self.SwitchStep(SP_STEP if self.scheduled else OPT_STEP)
            return
        chosen = self.settings.Chosen("method")
        if chosen != self.lastMethod and self.edited:
            template, extension, _ = self.Method()
            count = len(template.stages) if template is not None and extension == Defaults.gaussianExtension else 1
            # Edited stages no longer line up with a method whose stages differ: asked first, never lost quietly
            if count != len(self.generatedStages):
                previous = self.lastMethod
                which = "All Stages" if self.fullEdit is not None else \
                    "Stage" + ("s " if len(self.stageEdits) > 1 else " ") + ", ".join(map(str, sorted(self.stageEdits)))
                self.app.push_screen(Popup(f"Discard Edits to {which}?",
                                           "This method's stages differ, so the edited ones would no longer line up.",
                                           [("d", "Discard", "discard")], "warning"),
                                     lambda result: self.MethodDecided(result == "discard", previous))
                return
        self.lastMethod = chosen
        self.ShowRoute()
        self.Refresh()

    def MethodDecided(self, discard: bool, previous: str) -> None:
        if discard:
            self.stageEdits.clear()
            self.fullEdit = None
            self.lastMethod = self.settings.Chosen("method")
            self.ShowRoute()
            self.Refresh()
        else:
            # Back to the method the edits were made for (its Changed message finds nothing to ask)
            self.settings.Select(self.settings.Row(previous))

    def ShowRoute(self) -> None:
        """The Route row follows the chosen method: its route as written, or Custom's as last typed (else the route
        shown when Custom was chosen, so Custom starts from it). Never while it is being typed in."""
        row = self.settings.Row("route")
        if row.target.has_focus:
            return
        chosen = self.settings.Chosen("method")
        route = (self.customRoute or row.value) if chosen == "custom" else self.methods[chosen][3]
        if row.value != route:
            row.SetValue(route)

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        # Typing in the Full Preview: its stages are read back (ModRed lines stay live, anything else is kept as typed).
        # Text loaded from the stages is already theirs
        if (event.text_area.id != "input-preview" or self.step != OPT_STEP
                or event.text_area.text == self.FullText()):
            return
        self.ReadFullPreview(event.text_area.text)
        self.Refresh()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self.ShowRendersAs()
        self.refresh_bindings()

    def on_resize(self, event) -> None:
        # The route column takes the pane's width
        self.call_after_refresh(self.ShowStagesView)

    # ─── Actions ──────────────────────────────────────────────────────

    def check_action(self, action: str, parameters) -> bool | None:
        if action == "run":
            return True if self.ready else None
        if action == "step":
            return True if self.scheduled else None
        # The next job's stages are read only: written when the Opt ends
        if action in ("regenerate", "regenerate_stage") and self.step == SP_STEP:
            return False
        if action == "regenerate":
            return True if self.edited else None
        if action == "regenerate_stage":
            number = self.table.StageAt()
            return True if number is not None and self.StageEdited(number) else False
        if action == "edit_stage":
            return True if self.table.row_count else None
        return True

    def action_toggle_view(self) -> None:
        """p: the Stages view, or the Full Preview (the whole input, editable)."""
        self.view = "full" if self.view == "stages" else "stages"
        stages = self.view == "stages"
        self.table.display = stages
        self.query_one("#renders-as").display = stages
        self.preview.display = not stages
        if not stages:
            self.preview.read_only = self.step == SP_STEP
            self.preview.load_text(self.ViewFullText())
        (self.table if stages else self.preview).focus()
        self.ShowTitle(self.ViewExtension())
        self.refresh_bindings()

    def action_edit_stage(self) -> None:
        number = self.table.StageAt()
        if number is None:
            return
        if self.step == SP_STEP:
            Notice(self.app, "Written When the Optimization Ends",
                   "The single point is written from these settings once the optimization ends, as cu -sp writes "
                   "one: its coordinates are the optimization's last geometry, and its spin is classified from its "
                   "output then. Change its settings on the left.")
            return
        self.EditStage(number)

    def action_regenerate_stage(self) -> None:
        """^r in the Stages view: an edited stage back to what the settings write (the others are untouched)."""
        number = self.table.StageAt()
        if number is None or not self.StageEdited(number):
            return
        if self.fullEdit is not None:
            # Its stages don't line up with the method's: only all of them can be regenerated
            self.action_regenerate()
            return
        self.app.push_screen(Popup(f"Regenerate Stage {number}?", "Your edits to this stage will be lost.",
                                   [("r", "Regenerate", "regenerate")], "warning"),
                             lambda result: self.Regenerated(number) if result == "regenerate" else None)

    def action_regenerate(self) -> None:
        """^r in the Full Preview: every stage back to what the settings write."""
        if not self.edited:
            return
        self.app.push_screen(Popup("Regenerate the Input?", "Your edits to every stage will be lost.",
                                   [("r", "Regenerate", "regenerate")], "warning"),
                             lambda result: self.Regenerated() if result == "regenerate" else None)

    def Regenerated(self, number: int | None = None) -> None:
        if number is None:
            self.stageEdits.clear()
            self.fullEdit = None
        else:
            self.stageEdits.pop(number, None)
        self.Refresh()
        if self.view == "full" and self.step == OPT_STEP:
            self.preview.load_text(self.FullText())

    def action_run(self) -> None:
        if not self.ready:
            self.app.bell()
            return
        path = self.path
        if path.exists():
            source = self.source is not None and path.resolve() == self.source.resolve()
            message = (f"{path.name} is the file these coordinates came from." if source
                       else f"{path.name} already exists in this folder.")
            self.app.push_screen(Popup(f"Replace {path.name}?", message, [("r", "Replace", "replace")], "warning"),
                                 lambda result: self.Save(path) if result == "replace" else None)
            return
        self.Save(path)

    def Save(self, path: Path) -> None:
        # Written as genFile writes an input, then submitted as Run as Written submits one: tracked, broadcast, and
        # the Results pop-up (which offers the Job Stalker) back on Home
        with open(path, "w") as jobInput:
            jobInput.write(self.FullText())
        # The Neva-Touch Scheduler: the chain file names the next job, which the job's hook submits once it ends
        self.job.WriteChain(path)
        self.app.Submit(self.job.Draft(path).Finalize(), 1)

    def action_leave_text(self) -> None:
        self.stepSettings.current.target.focus()

    def action_back(self) -> None:
        self.dismiss(None)

    def action_help(self) -> None:
        Notice(self.app, "Help", "\n".join([
            "tab / shift+tab Move Between the Settings and the Stages (or the Full Preview)",
            "↑/↓ Move Between Settings (Type to Change a Value)",
            "←/→ or space Choose the Spin or the Method; the Chosen Method Lists Its Stages Under It",
            "Method: a Prior Job's Own Route, the Optimization Methods (Config › Job Keylists), or a Custom Route",
            "Route Shows the Chosen Method's Route; Typing in It Makes a Custom Route From It",
            "Stages: 'route -> route' (Gaussian --Link1--); Later Stages Read the Checkpoint (Geom=AllCheck, Guess=Read)",
            "Stages View: a Row per Stage; Renders As Shows the Highlighted One as It Will Be Written",
            "space on a Stage Edits It Alone (Its Part of the Input); ModRed Lines Go After Its Geometry",
            "A Stage You Type In Is Kept as Typed (Edited) Until ctrl+r; ModRed Lines Alone Keep It Following the Settings",
            "p Switches Between the Stages and the Full Preview (the Whole Input, Editable; ctrl+r There Regenerates All)",
            "Wall Time, Partition, Cluster: This Job's SLURM Settings (the Config's to Start; Only the Lines Your "
            "Cluster's Header Has). A Re-run Repeats Them",
            "NBO: the $NBO Keylist (programs.toml nboKeylist), Read by the Last Stage (Gaussian Only)",
            "Neva-Touch Scheduler: a Next Job Once the Optimization Ends Normally (Default: qol.toml autoNTS). On, Its "
            "Settings Are the Then: SP Step: [ ] or a Click on the Top Rule Switches Steps",
            "Then: SP: a Benchmark Method or a Custom Route; Partition and Cluster Follow the Optimization's Until "
            "Typed. Its Stages Are Read Only: Written When the Optimization Ends, From Its Last Geometry",
            "esc in the Full Preview Returns to the Settings",
            "enter Save the Input and Submit It", "esc Back to the Coordinates", "ctrl+q Quit"]))
