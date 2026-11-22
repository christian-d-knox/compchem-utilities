"""Step 1 (Coordinates) shared by the Input Builders: Optimization (tui/inputs.py) and Conformer Search
(tui/crestscreen.py)."""
from pathlib import Path

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Input, OptionList, Static, TextArea
from textual.widgets.option_list import Option
from textual.worker import get_current_worker

from ..defaults   import Defaults
from ..elements   import Formula
from ..fileops    import ExtractFrom, ExtractFromText, ExtractXyz
from ..info       import PreviewMolecule
from ..molecule   import Molecule
from ..smiles     import Available as RDKitAvailable
from .common      import (NAV_BINDINGS, Column, FrameRule, KeyHint, LeaveText, NavFooter, Notice, Setting,
                          SettingsList, TitleLine)
from .fromsmiles  import BUILDING, ConformerCount, ConformerTable, Indented, SmilesBuilder, StructureLines
from .inspect     import JobSummary, OutputExtensions, Styled
from .rows        import LEFT_WIDTH

STATUS_STYLES = {"Normal": "good", "Error": "error", "Unknown": "warning", "Input": "info"}


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

    def __init__(self, target: str = "Optimization") -> None:
        super().__init__()
        # The builder these coordinates are for: Optimization, or Conformer Search (its Job step is crestscreen's)
        self.target = target
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
        yield Static(TitleLine(f"{self.target}: Coordinates"), id="title")
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
        if self.target == "Conformer Search":
            from .crestscreen import ConformerSearchScreen
            self.app.push_screen(ConformerSearchScreen(self.molecule, self.reason, self.source == "prior"))
            return
        from .inputs import OptimizationScreen
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
