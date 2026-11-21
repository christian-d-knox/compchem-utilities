"""SMILES in the TUI (smiles.py's RDKit conformers): the debounced build shared by the Optimization builder's
Coordinates screen and Home's Write XYZ pop-up, the RDKit Conformers table, and that pop-up."""
from pathlib import Path

from rich.text import Text
from textual import work
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Static
from textual.worker import get_current_worker

from ..console  import console
from ..defaults import Defaults
from ..fileops  import Formula, fileCreation
from ..intent   import XyzIntent
from ..smiles   import RDKitBuild, RDKitConformers
from .common    import Column, FrameRule, KeyHint, Setting, SettingsList, ValueInput
from .inspect   import Styled

BUILD_DELAY = 0.4            # s after the last keystroke before RDKit builds (a build is a fraction of a second)
BUILDING = "Building RDKit Conformers…"
SMILES_CHARACTER = r"[!-~]"  # printable ASCII, no space (␣ stays the list's; a control key such as esc never matches)
FILE_NAME = r"[\w.\-]"       # the characters a file name takes


def ConformerCount(text: str) -> int:
    """The typed RDKit Conformers count (0 when not a whole number: RDKitConformers says why)."""
    return int(text) if text.strip().isdigit() else 0


def Indented(lines: list[Text], width: int, indent: int = 1) -> list[Text]:
    """Each line wrapped to width, every wrapped line indented (a narrow column's Structure pane)."""
    wrapped = []
    for line in lines:
        wrapped += [Text(" " * indent) + part for part in line.wrap(console, width - indent)]
    return wrapped


def StructureLines(build: RDKitBuild, rank: int = 1) -> list[Text]:
    """What the build is, for the Structure pane and the pop-up: atoms and formula, charge and multiplicity, the
    conformer in use, then its warnings."""
    lines = build.conformers[rank - 1][1]
    return ([Text(f"{build.atoms} Atoms · {Formula(lines)}"), Text(f"Charge {build.charge} · Mult {build.multiplicity}"),
             Text(build.Conformer(rank).Label())]
            + [Styled(f"⚠ {warning}", "warning") for warning in build.warnings])


class SmilesBuilder:
    """A screen that builds a SMILES' RDKit conformers as it is typed: in a thread worker, after a pause, a result
    for an older SMILES (or count) dropped. The screen shows the result in ShowBuild()."""
    smilesWanted: tuple[str, int] | None = None   # what was last asked for
    smilesBuild: RDKitBuild | None = None         # its build, once done
    smilesProblem: str = ""                       # or why there is none
    smilesBuilding: bool = False
    _smilesTimer = None

    def ScheduleBuild(self, smiles: str, count: int) -> None:
        if (smiles, count) == self.smilesWanted:
            return
        if self._smilesTimer is not None:
            self._smilesTimer.stop()
        self.smilesWanted, self.smilesBuild, self.smilesProblem = (smiles, count), None, ""
        self.smilesBuilding = bool(smiles.strip())
        if not smiles.strip():
            self.smilesProblem = "Enter a SMILES"
        self.ShowBuild()
        if self.smilesBuilding:
            self._smilesTimer = self.set_timer(BUILD_DELAY, lambda: self.BuildSmiles(smiles, count))

    @work(thread=True, exclusive=True, group="smiles")
    def BuildSmiles(self, smiles: str, count: int) -> None:
        build, problem = RDKitConformers(smiles, count)
        if not get_current_worker().is_cancelled:
            self.app.call_from_thread(self.Built, smiles, count, build, problem)

    def Built(self, smiles: str, count: int, build: RDKitBuild | None, problem: str) -> None:
        if (smiles, count) != self.smilesWanted:
            return
        self.smilesBuild, self.smilesProblem, self.smilesBuilding = build, problem, False
        self.ShowBuild()

    def ShowBuild(self) -> None:
        raise NotImplementedError


class ConformerTable(DataTable):
    """The RDKit conformers of the SMILES, lowest energy first, ΔE from the lowest: • marks the one in use, ␣ picks
    the highlighted one, ⏎ continues with it."""
    BINDINGS = [Binding("space", "screen.pick_conformer", "Use This One", key_display="␣"),
                Binding("enter", "screen.continue", "Continue")]

    def on_mount(self) -> None:
        self.add_columns("", "#", "ΔE (kcal/mol)")
        self.border_title = " RDKit Conformers "

    def Show(self, build: RDKitBuild | None, rank: int) -> None:
        """Fill the table (a new build), or only move the mark (rank changed)."""
        cursor = self.cursor_row
        self.clear()
        for number, (energy, _) in enumerate(build.conformers if build else [], 1):
            self.add_row("•" if number == rank else "", str(number),
                         f"{energy:.2f}" if build.forceField else "—", key=str(number))
        if self.row_count:
            self.move_cursor(row=min(cursor, self.row_count - 1))
        self.border_title = (f" RDKit Conformers ({len(build.conformers)} of {build.built}"
                             f"{', ' + build.forceField if build.forceField else ''}) " if build else " RDKit Conformers ")


class SmilesInput(ValueInput):
    """The pop-up's typed rows: ⏎ writes the file (from every row, as a Builder's fields submit)."""
    BINDINGS = [Binding("enter", "screen.write", "Write")]


class SmilesPopup(SmilesBuilder, ModalScreen):
    """Home's Write XYZ From SMILES: the SMILES, the file's name and the RDKit Conformers count, the build as it is
    typed, then ⏎ writes NAME.xyz (the lowest-energy conformer) through cu -xyz --smiles' path (app.WriteXyz).
    Dismissed with the intent to run, or None."""
    BINDINGS = [Binding("escape", "cancel", "Cancel")]
    DEFAULT_CSS = """
    SmilesPopup { align: center middle; }
    SmilesPopup > Vertical { height: auto; width: 64; }
    SmilesPopup #smiles-body { height: auto; max-height: 20; }
    SmilesPopup #smiles-settings { height: auto; }
    SmilesPopup #smiles-status { padding: 1 1 0 1; }
    """

    def compose(self):
        with Vertical():
            yield FrameRule("┌┐", "Write XYZ From SMILES")
            with Column(id="smiles-body", classes="side"):
                yield SettingsList([
                    Setting("smiles", "SMILES", value="", accepts=SMILES_CHARACTER),
                    Setting("name", "Name", value="", accepts=FILE_NAME),
                    Setting("count", "RDKit Conformers", value=str(Defaults.rdkitConformers), accepts=r"\d"),
                ], widest=40, inputClass=SmilesInput, id="smiles-settings")
                yield Static(id="smiles-status")
            yield FrameRule("└┘", id="smiles-keys")

    def on_mount(self) -> None:
        self.ShowBuild()

    @property
    def settings(self) -> SettingsList:
        return self.query_one("#smiles-settings", SettingsList)

    def Value(self, key: str) -> str:
        return self.settings.Row(key).value.strip()

    def on_input_changed(self, event) -> None:
        self.ScheduleBuild(self.Value("smiles"), ConformerCount(self.Value("count")))
        self.ShowBuild()

    def Target(self) -> Path | None:
        return Path.cwd() / fileCreation(self.Value("name"), Defaults.coordExtension) if self.Value("name") else None

    def Problem(self) -> str:
        if self.smilesBuilding:
            return BUILDING
        if self.smilesBuild is None:
            return self.smilesProblem or "Enter a SMILES"
        return "" if self.Value("name") else "Enter a Name"

    def ShowBuild(self) -> None:
        build = self.smilesBuild
        problem, target = self.Problem(), self.Target()
        lines = (StructureLines(build) if build is not None and not self.smilesBuilding
                 # RDKit's reason in full (the bottom edge can cut it)
                 else [Styled(self.smilesProblem, "error")] if self.smilesProblem and not self.smilesBuilding else [])
        self.query_one("#smiles-status", Static).update(Text("\n").join(lines) if lines else Text(""))
        keys = Text.assemble(KeyHint(self.app, "esc", "Cancel"), " · ")
        if problem:
            right = Text.assemble(keys, Styled(problem, "dim" if problem == BUILDING else "error"))
        else:
            replaces = Styled(f"⚠ Replaces {target.name} · ", "warning") if target.exists() else Text()
            right = Text.assemble(keys, replaces, KeyHint(self.app, "⏎", f"Write {target.name}"))
        self.query_one("#smiles-keys", FrameRule).right = right

    def action_write(self) -> None:
        if self.Problem():
            self.app.bell()
            return
        # The bottom edge said it replaces the file, so ⏎ is the answer
        self.dismiss(XyzIntent(files=[], smiles=self.Value("smiles"), name=self.Value("name"),
                               rdkitConformers=ConformerCount(self.Value("count")), replace=self.Target().exists()))

    def action_cancel(self) -> None:
        self.dismiss(None)
