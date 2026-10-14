"""Input Builders (Home's Input Builders list): screens that build a new input and submit it. The Optimization builder
(TUI_DESIGN.md 4.11) is two steps: Coordinates (an .xyz, a prior job's geometry, or pasted rows), then the Job, its
settings beside an editable preview of the input. The saved input is submitted the way Run as Written submits one."""
import copy
from pathlib import Path

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import OptionList, Static, TextArea
from textual.widgets.option_list import Option
from textual.worker import get_current_worker

from ..actions  import Action, SpinState
from ..catalog  import ParseRouteTemplate, RenderRoute, RouteTemplate, UnknownSelectors
from ..defaults import Defaults
from ..fileops  import (ExtractFrom, ExtractFromText, ExtractXyz, MoleculeElements, ReRunTemplate, Retarget,
                        extensionGetter, fileCreation)
from ..intent   import IntentDraft
from ..jobs     import DefaultMemoryRatio, JobFileProblem, JobInputText, OptMethods
from ..molecule import Molecule
from ..spin     import SpinOverride
from .common    import (NAV_BINDINGS, NUMBER, TEXT, FrameRule, KeyHint, NavFooter, Notice, Popup, Setting, SettingsList,
                        ValueInput)
from .home      import TitleLine
from .inspect   import ElectronProblem, Formula, JobSummary, OutputExtensions, PreviewMolecule, Styled

LEFT_WIDTH = 30          # the settings column, its borders included (the frame's junction sits on its right edge)
STATUS_STYLES = {"Normal": "good", "Error": "error", "Unknown": "warning", "Input": "info"}
FILE_NAME = r"[\w.\-]"   # the characters a job name takes
# A method's name in its row, beside "(•) " and its program: the settings column less the list's scrollbar
METHOD_NAME_WIDTH = LEFT_WIDTH - 17


def ProgramOf(extensionType: str) -> str:
    return "ORCA" if extensionType == Defaults.orcaExtension else "G16"


def Clipped(text: str, width: int) -> str:
    """text cut to width, ending in … when it was longer."""
    return text if len(text) <= width else text[:max(width - 1, 0)] + "…"


class LeaveText(TextArea):
    """A text box in a builder: esc leaves it for the settings (⏎ types a newline here, as in every text box)."""
    BINDINGS = [Binding("escape", "screen.leave_text", "Done")]


# ─── Step 1: Coordinates ──────────────────────────────────────────────

def SourceSettings() -> list[Setting]:
    """Where the coordinates come from. SMILES waits for RDKit (TUI_DESIGN 4.11)."""
    return [
        Setting("xyz", "", fixed="XYZ File", on=True, choice="source"),
        Setting("prior", "", fixed="Prior Job", on=False, choice="source"),
        Setting("paste", "", fixed="Paste", on=False, choice="source"),
        Setting("smiles", "", "Needs RDKit (Coming Later)", fixed="SMILES", on=False, choice="source", available=False),
    ]


class SourceList(SettingsList):
    """The Source choice: ␣ (or a click on its box) chooses a source and moves on to its files (or the paste box), as
    choosing on Home moves on to the next pane; ←→ only switch it."""

    def action_toggle(self) -> None:
        chosen = self.current.available and self.current.setting.choice
        super().action_toggle()
        if chosen:
            self.screen.EnterSource()


class SourceFiles(OptionList):
    # The highlighted file is the one read; ⏎ continues with it, as from every field, and ␣ (choosing it) does too
    BINDINGS = [Binding("enter", "screen.continue", "Continue"),
                Binding("space", "screen.continue", "Continue", show=False)]


class CoordinatesScreen(Screen):
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

    def compose(self) -> ComposeResult:
        yield Static(TitleLine("Optimization: Coordinates"), id="title")
        yield FrameRule("┌┐", "Source", split=LEFT_WIDTH - 1, id="inputs-top")
        with Horizontal(id="inputs-body"):
            with VerticalScroll(id="inputs-left", classes="side"):
                yield SourceList(SourceSettings(), stacked=True, widest=LEFT_WIDTH - 5, id="settings")
                yield Static(id="structure")
            with Vertical(id="inputs-right"):
                yield SourceFiles(id="source-files")
                yield LeaveText(id="paste", soft_wrap=False)
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
        files.display, paste.display = source != "paste", source == "paste"
        self.molecule, self.reason, self.readPath = None, "", None
        if source == "paste":
            top.label2 = Text.assemble("Paste Coordinates · ", KeyHint(self.app, "esc", "Done"))
            self.ReadPasted(paste.text)
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

    def ReadPasted(self, text: str) -> None:
        coordinates = ExtractFromText(text, ExtractXyz, empty=[]) if text.strip() else []
        self.molecule = Molecule(Path(""), "", "0", "1", coordinates, "", "")
        self.ShowStructure()

    # ─── Status ───────────────────────────────────────────────────────

    def ShowStructure(self) -> None:
        source, molecule = self.source, self.molecule
        lines, problem = [Text("Structure", "bold")], ""
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

    # ─── Actions ──────────────────────────────────────────────────────

    def OnSource(self) -> bool:
        return self.focused is not None and self.query_one("#settings", SourceList) in self.focused.ancestors_with_self

    def check_action(self, action: str, parameters) -> bool | None:
        # ⏎ Continue shows dimmed until a structure has been read, except on the Source, where it moves to the files
        return None if action == "continue" and not (self.Ready() or self.OnSource()) else True

    def EnterSource(self) -> None:
        """Move on to the chosen source's files, or into the paste box."""
        if self.source == "paste":
            self.query_one("#paste", LeaveText).focus()
        elif self.paths:
            self.query_one("#source-files", SourceFiles).focus()
        else:
            self.app.bell()

    def action_continue(self) -> None:
        # ⏎ moves forward: from the Source to its files (or the paste box), from a file to the Job. The paste box takes
        # ⏎ as a newline, so once coordinates are pasted (esc out of it), ⏎ on the Source continues
        if self.OnSource() and not (self.source == "paste" and self.Ready()):
            self.EnterSource()
            return
        if not self.Ready():
            self.app.bell()
            return
        self.app.push_screen(OptimizationScreen(self.molecule, self.reason, self.source == "prior"))

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
            "esc in the Paste Box Return to the Source, Where enter Continues",
            "esc Back to Home", "ctrl+q Quit"]))


# ─── Step 2: the Job ──────────────────────────────────────────────────

class SubmitInput(ValueInput):
    """A typed setting whose ⏎ saves the input and submits it (the footer says so, not the Builder's Run)."""
    BINDINGS = [Binding("enter", "screen.run", "Save and Submit")]


class PreviewArea(LeaveText):
    BINDINGS = [Binding("escape", "screen.leave_text", "Done"),
                Binding("ctrl+r", "screen.regenerate", "Regenerate", key_display="^r")]


class OptimizationScreen(Screen):
    """The job's settings (name, charge, multiplicity, spin, method, resources) beside the input they make. The preview
    follows the settings until it is typed in; the edited text is then the user's, and ^r regenerates it."""
    AUTO_FOCUS = "#settings .-current"
    BINDINGS = [
        Binding("escape", "back", "Back"),
        Binding("enter", "run", "Save and Submit"),
        Binding("question_mark", "help", "Help"),
        *NAV_BINDINGS,
    ]

    def __init__(self, molecule: Molecule, reason: str = "", prior: bool = False) -> None:
        super().__init__()
        # A copy: the Job's settings are written onto it (Retarget, charge...), and going back must not change the read
        self.molecule, self.prior = copy.copy(molecule), prior
        self.source = molecule.sourcePath if prior else None
        # Why a singlet's spin starts where it does: spinstates.txt, else how the prior job was classified
        override = SpinOverride(molecule.rootName) if molecule.rootName else None
        if override is not None:
            self.molecule.spinState, reason = override[0], f"spinstates.txt: {override[1]}"
        decided = reason and not reason.startswith("Multiplicity") and str(molecule.multiplicity or 1) == "1"
        self.spinNote = f"({reason})" if decided else ""
        # The method choices: setting key -> (label, template, input extension, its route as written: what the Route
        # row shows and a Custom route starts from); a None template can't be made (fromProblem says why)
        self.methods: dict[str, tuple[str, RouteTemplate | None, str, str]] = {}
        self.fromProblem = ""
        if prior:
            template, self.fromProblem = ReRunTemplate(self.molecule)
            # ReRunTemplate sets the program the job ran in, which a verbatim route's first token may not name
            self.methods["method-from"] = ("From Source", template, self.molecule.extensionType,
                                           self.molecule.sourceRoute)
        self.optMethods = OptMethods()
        for index, (tag, route) in enumerate(self.optMethods.items()):
            template = ParseRouteTemplate(route)
            self.methods[f"method-{index}"] = (tag, template, extensionGetter(template.method), route)
        self.customRoute = ""        # the Custom route as last typed (the Route row shows the chosen method's otherwise)
        self.generated = ""          # the preview as last generated: the preview is edited when its text differs
        self.memoryDefault = ""      # the Memory value last pre-filled: replaced when the method changes it
        self.ready = False
        self.path: Path | None = None

    def JobSettings(self) -> list[Setting]:
        molecule = self.molecule
        oss = molecule.spinState == SpinState.OSS
        # The programs line up in their own column, which a long name never pushes off: names are cut (…) to the room
        # left beside "(•) " and the program, with the list's scrollbar showing
        width = min(max((len(label) for label, *_ in self.methods.values()), default=0), METHOD_NAME_WIDTH)
        methods = [Setting(key, "", self.RouteMeaning(route), on=False, choice="method",
                           fixed=f"{Clipped(label, width).ljust(width)}  {ProgramOf(extension)}",
                           available=template is not None)
                   for key, (label, template, extension, route) in self.methods.items()]
        methods.append(Setting("custom", "", "" if self.optMethods else "No optmethods.txt: Add One in Project Files",
                               fixed="Custom", on=False, choice="method"))
        # The first usable method starts chosen: a prior job's own route, else the first tag, else Custom
        chosen = next(setting for setting in methods if setting.available)
        chosen.on = True
        methods[0].label = "Method"
        # A prior job's route: why it can't be used, else (without routes listed) the file it is from
        if self.prior:
            methods[0].meaning = self.fromProblem or methods[0].meaning or self.source.name
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
            Setting("cpus", "CPUs", value=str(Defaults.CPU), accepts=r"\d"),
            Setting("memory", "Memory per Core", value="", unit="GB", accepts=NUMBER),
            Setting("checkpoint", "Checkpoint", on=False),
        ]

    @staticmethod
    def RouteMeaning(route: str) -> Text | str:
        # What a method runs, under its name (qol.toml: routeUnderEachMethod; in full, wrapped by the -route CSS).
        # Text: a str "[oss: guess=mix]" would be read as markup
        return Text(route) if Defaults.routeUnderEachMethod and route else ""

    def compose(self) -> ComposeResult:
        atoms = len(self.molecule.coordinateList)
        origin = f"{self.source.name if self.source else self.molecule.sourcePath.name or 'Pasted'} ({atoms} Atoms)"
        yield Static(TitleLine("Optimization: Job", Text(origin)), id="title")
        yield FrameRule("┌┐", "Job", split=LEFT_WIDTH - 1, id="inputs-top")
        with Horizontal(id="inputs-body"):
            with VerticalScroll(id="inputs-left", classes="side"):
                yield SettingsList(self.JobSettings(), stacked=True, widest=LEFT_WIDTH - 5, inputClass=SubmitInput,
                                   id="settings")
            with Vertical(id="inputs-right"):
                yield PreviewArea(id="input-preview", soft_wrap=False)
        yield FrameRule("└┘", split=LEFT_WIDTH - 1, id="submit")
        yield NavFooter()

    def on_mount(self) -> None:
        for key in self.methods:
            self.settings.Row(key).add_class("-route")
        self.Refresh()

    @property
    def settings(self) -> SettingsList:
        return self.query_one("#settings", SettingsList)

    @property
    def preview(self) -> PreviewArea:
        return self.query_one("#input-preview", PreviewArea)

    @property
    def edited(self) -> bool:
        return self.preview.text != self.generated

    # ─── The job ──────────────────────────────────────────────────────

    def Method(self) -> tuple[RouteTemplate | None, str, str]:
        """(template, input extension, why there is none) for the chosen method."""
        chosen = self.settings.Chosen("method")
        if chosen != "custom":
            _, template, extension, _ = self.methods[chosen]
            return template, extension, ""
        route = self.settings.Row("route").value.strip()
        if not route:
            return None, "", "Enter the Custom Route"
        unknown = UnknownSelectors(route)
        if unknown:
            return None, "", f"Unknown Selector {', '.join(unknown)}"
        template = ParseRouteTemplate(route)
        return template, extensionGetter(template.method), ""

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
        if cpus < 1:
            errors.append("CPUs Must Be a Whole Number Above 0")
        if charge is not None:
            electrons = ElectronProblem(molecule.coordinateList, charge, multiplicity)
            if electrons:
                errors.append(electrons)
        fileProblem = ""
        if template is not None:
            # Checkpoint is Gaussian's %chk
            if settings.Row("checkpoint").available != (extension == Defaults.gaussianExtension):
                settings.SetAvailable("checkpoint", extension == Defaults.gaussianExtension)
            if charge is not None:
                molecule.charge, molecule.multiplicity = charge, multiplicity
            molecule.spinState = (SpinState.OPEN if openShell else
                                  SpinState.OSS if settings.Chosen("spin") == "oss" else SpinState.CSS)
            Retarget(molecule, name or "untitled", template, extension)
            route, tags = RenderRoute(template, molecule)
            self.FillMemory(f"{DefaultMemoryRatio(route, extension):g}")
            if memory is None:
                errors.append("Memory Must Be a Number Above 0")
            # The same check genFile skips jobs with, without its prompt for a missing project file
            fileProblem = JobFileProblem(route, tags, extension, MoleculeElements(molecule.coordinateList), False)
            self.path = Path.cwd() / fileCreation(name, extension) if name else None
            # The preview needs only what goes into it: a name only names the file (and %chk)
            if charge is not None and cpus >= 1 and memory is not None and not self.edited:
                self.Regenerate(cpus, memory)
        # A project-file problem stops a generated input; an edited one is the user's to fix
        if fileProblem and not self.edited:
            errors.append(fileProblem)
        self.ShowTitle(extension)
        self.ready = not errors and self.path is not None
        more = f" (+{len(errors) - 1} More)" if len(errors) > 1 else ""
        warning = [Styled(f"⚠ {fileProblem}", "warning"), " · "] if fileProblem and self.edited else []
        self.query_one("#submit", FrameRule).right = (Styled(errors[0] + more, "error") if errors else Text.assemble(
            *warning, Styled("1 Job Ready", "good"), " · ", KeyHint(self.app, "⏎", "Save and Submit")))
        self.refresh_bindings()

    def FillMemory(self, default: str) -> None:
        # Pre-filled per method (ORCA's DLPNO takes highMemoryRatio); a typed value is kept
        row = self.settings.Row("memory")
        if row.value in ("", self.memoryDefault) and row.value != default:
            row.SetValue(default)
        self.memoryDefault = default

    def Regenerate(self, cpus: int | None = None, memory: float | None = None) -> None:
        settings = self.settings
        if cpus is None:
            cpus, memory = int(settings.Row("cpus").value), float(settings.Row("memory").value)
        self.generated = JobInputText(self.molecule, settings.Applies(settings.Row("checkpoint")), False, cpus, memory,
                                      required=False)
        if self.preview.text != self.generated:
            self.preview.load_text(self.generated)

    def ShowTitle(self, extension: str) -> None:
        if self.path is None:
            label = Text("Input")
        else:
            label = Text(f"Input: {self.path.name} · {ProgramOf(extension)}")
            if self.edited:
                label.append_text(Text.assemble(" · ", Styled("Edited", "warning")))
        self.query_one("#inputs-top", FrameRule).label2 = label

    # ─── Events ───────────────────────────────────────────────────────

    def on_input_changed(self, event) -> None:
        # The Custom route as typed, restored whenever Custom is chosen again
        row = getattr(event.input, "row", None)
        if row is not None and row.setting.key == "route" and row.typing:
            self.customRoute = event.value
        self.Refresh()

    def on_settings_list_changed(self, event: SettingsList.Changed) -> None:
        self.ShowRoute()
        self.Refresh()

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
        # Typing in the preview (or regenerating it) changes whether it is edited
        self.Refresh()

    # ─── Actions ──────────────────────────────────────────────────────

    def check_action(self, action: str, parameters) -> bool | None:
        if action == "run":
            return True if self.ready else None
        if action == "regenerate":
            return True if self.edited else None
        return True

    def action_regenerate(self) -> None:
        if not self.edited:
            return
        self.app.push_screen(Popup("Regenerate the Input?", "Your edits to the preview will be lost.",
                                   [("r", "Regenerate", "regenerate")], "warning"),
                             lambda result: self.Refreshed() if result == "regenerate" else None)

    def Refreshed(self) -> None:
        self.generated = ""
        self.preview.load_text("")
        self.Refresh()

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
            jobInput.write(self.preview.text)
        draft = IntentDraft(action=Action.RUN, files=[Path(path.name)])
        self.app.Submit(draft.Finalize(), 1)

    def action_leave_text(self) -> None:
        self.settings.current.target.focus()

    def action_back(self) -> None:
        self.dismiss(None)

    def action_help(self) -> None:
        Notice(self.app, "Help", "\n".join([
            "tab / shift+tab Move Between the Settings and the Input",
            "↑/↓ Move Between Settings (Type to Change a Value)",
            "←/→ or space Choose the Spin or the Method",
            "Method: a Prior Job's Own Route, the Routes in optmethods.txt, or a Custom Route",
            "Route Shows the Chosen Method's Route; Typing in It Makes a Custom Route From It",
            "The Input Follows the Settings Until You Type in It; ctrl+r Regenerates It",
            "esc in the Input Return to the Settings",
            "enter Save the Input and Submit It", "esc Back to the Coordinates", "ctrl+q Quit"]))
