"""Scrapers (Home's Scrapers list): screens that read the whole CWD into one file. The SI Scraper (TUI_DESIGN.md 4.10)
writes an SI-ready energies + coordinates file, the group's coordinates2.sh without its GoodVibes run per structure."""
from pathlib import Path

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Input, Static

from ..actions import Action
from ..analysis import GOODVIBES_OUTPUT, DetectSuffix, Outputs
from ..defaults import Defaults
from ..scrape  import (ORDER_FILE, PROGRAM_NAMES, SI_FILE, FindSpecies, GoodVibesNeeded, GoodVibesRun,
                       GoodVibesTable, NaturalKey, ReadOrder, ReadSpecies, SIText, SPThermo, Species,
                       TableUsed, WriteOrder)
from .common   import (NAME, NAV_BINDINGS, TEXT, FrameRule, KeyHint, MoveRow, NavFooter, Notice, Popup,
                       ReorderableTable, Setting, SettingsList, ValueInput)
from .home     import TitleLine
from .inspect  import Styled

LEFT_WIDTH = 30          # the Settings column, its borders included (the frame's junction sits on its right edge)
_MOVE = Binding.Group("Move", compact=True)


class WriteInput(ValueInput):
    """A typed setting whose ⏎ writes the SI file (the footer says Write, not the Builder's Run)."""
    BINDINGS = [Binding("enter", "screen.run", "Write")]


def SISettings(suffix: str) -> list[Setting]:
    """The method names are what the file's energy lines start with, e.g. (U)B3LYP SCF energy in solution (au)."""
    return [
        Setting("opt", "Opt Method", value="", accepts=TEXT),
        Setting("sp", "SP Method", value="", accepts=TEXT),
        Setting("phase", "Phase", value="in solution", accepts=TEXT),
        Setting("suffix", "SP Suffix", "(Detected)", value=suffix, accepts=NAME),
        Setting("qh", "Free Energy", fixed="qh-G (GoodVibes)", on=True, choice="free"),
        Setting("rrho", "", fixed="G", on=False, choice="free"),
        Setting("output", "Output", value=SI_FILE, accepts=r"[\w.\-]"),
    ]


class SpeciesTable(ReorderableTable):
    """The species in the order they'll be written: ␣ includes or leaves one out, [ ] (or a drag) moves it."""
    BINDINGS = [
        Binding("space", "screen.include", "Include", key_display="␣"),
        Binding("left_square_bracket", "screen.move(-1)", "Move", key_display="[", group=_MOVE),
        Binding("right_square_bracket", "screen.move(1)", "Move", key_display="]", group=_MOVE),
        Binding("enter", "screen.run", "Write"),
    ]


class SIScraperScreen(Screen):
    AUTO_FOCUS = "#settings .-current"
    BINDINGS = [
        Binding("escape", "back", "Home"),
        Binding("question_mark", "help", "Help"),
        *NAV_BINDINGS,
    ]

    def __init__(self) -> None:
        super().__init__()
        self.folder = Path.cwd()
        self.detected = DetectSuffix(Outputs(self.folder))     # None: nothing pairs up, so the default suffix
        self.suffix = self.detected or Defaults.singlePointExtra.lstrip("_")
        self.species: list[Species] = []    # in the order they'll be written
        self.included: set[str] = set()
        self.touched: set[str] = set()      # included or left out by hand: the defaults no longer apply to them
        self.listed = ReadOrder(self.folder)
        self.dropped: list[str] = []        # names in list.txt with no output here
        self.ready = False

    def compose(self) -> ComposeResult:
        yield Static(TitleLine("SI Scraper"), id="title")
        yield FrameRule("┌┐", "Settings", split=LEFT_WIDTH - 1, id="si-top")
        with Horizontal(id="si-body"):
            with VerticalScroll(id="si-left", classes="side"):
                yield SettingsList(SISettings(self.suffix), stacked=True, widest=LEFT_WIDTH - 5,
                                   inputClass=WriteInput, id="settings")
            with Vertical(id="si-right"):
                yield SpeciesTable(id="species", cursor_type="row", zebra_stripes=False)
        yield FrameRule("└┘", split=LEFT_WIDTH - 1, id="submit")
        yield NavFooter()

    def on_mount(self) -> None:
        table = self.query_one("#species", SpeciesTable)
        for title, width in (("", 3), ("Species", 14), ("", 4), ("SP", 5), ("Flags", 16)):
            table.add_column(title, width=width)
        self.Relist(self.suffix)

    # ─── Species ──────────────────────────────────────────────────────

    def Relist(self, suffix: str) -> None:
        """List the species for this suffix, keeping the order and inclusions of names already listed. The first time,
        list.txt (when there is one) gives the order and what's included; otherwise natural order, all included."""
        found = {one.name: one for one in FindSpecies(self.folder, suffix)}
        # Reading is kept for a species whose files are the same
        for one in self.species:
            if one.name in found and found[one.name].spPath == one.spPath:
                found[one.name] = one
        if not self.species:
            names = self.listed or []
            self.dropped = [name for name in names if name not in found]
            first = [name for name in names if name in found]
            rest = sorted((name for name in found if name not in first), key=NaturalKey)
            self.included = set(first) if self.listed is not None else set(found)
            ordered = first + rest
        else:
            ordered = [one.name for one in self.species if one.name in found]
            ordered += sorted((name for name in found if name not in ordered), key=NaturalKey)
            self.included |= {name for name in found if name not in {one.name for one in self.species}}
        self.species = [found[name] for name in ordered]
        self.Redraw()
        self.ReadAll([one for one in self.species if not one.read])

    @work(thread=True, exclusive=True, group="read")
    def ReadAll(self, unread: list[Species]) -> None:
        for one in unread:
            ReadSpecies(one)
        self.app.call_from_thread(self.Read)

    def Read(self) -> None:
        # Without list.txt, what can't be written (or ended in error) starts left out, unless already chosen by hand
        if self.listed is None:
            for one in self.species:
                if one.name not in self.touched and (one.Blocks(self.quasiHarmonic) or
                                                     any(flag == "Error" for flag, _ in one.Flags(False))):
                    self.included.discard(one.name)
        self.Redraw()

    def Redraw(self, cursor: int | None = None) -> None:
        table = self.query_one("#species", SpeciesTable)
        row = table.cursor_row if cursor is None else cursor
        table.clear()
        for one in self.species:
            included = one.name in self.included
            flags = one.Flags(self.quasiHarmonic)
            # Text, not str: "[X]" would be read as markup. No SP shows in its own column, so not again as a flag
            if one.read:
                sp = Styled("No SP", "error") if one.spPath is None else Styled("✓", "good")
                shown = Text("  ").join(Styled(flag, "error" if blocks else "warning")
                                        for flag, blocks in flags if flag != "No SP")
            else:
                sp, shown = Text("…"), Text("…")
            cells = [Text("[X]" if included else "[ ]"), Text(one.name), Text(PROGRAM_NAMES.get(one.program, "")),
                     sp, shown]
            if not included:
                for cell in cells:
                    cell.stylize("dim")
            table.add_row(*cells, key=one.name)
        if self.species:
            table.move_cursor(row=max(0, min(row, len(self.species) - 1)), animate=False)
        self.query_one("#si-top", FrameRule).label2 = Text.assemble(
            f"Species ({len(self.species)}) · ", KeyHint(self.app, "␣", "Include"), " · ",
            KeyHint(self.app, "[ ]", "or Drag to Move"))
        self.Refresh()

    # ─── Settings and status ──────────────────────────────────────────

    @property
    def settings(self) -> SettingsList:
        return self.query_one("#settings", SettingsList)

    @property
    def quasiHarmonic(self) -> bool:
        return self.settings.Chosen("free") == "qh"

    def Included(self) -> list[Species]:
        return [one for one in self.species if one.name in self.included]

    def Source(self) -> tuple[GoodVibesRun, GoodVibesRun | None]:
        """The folder's GoodVibes run, and the one the single point lines would come from (None: the outputs)."""
        run = GoodVibesTable(self.folder)
        readable = [one for one in self.Included() if one.read]
        return run, TableUsed(readable, self.folder, self.quasiHarmonic, run)

    def Problems(self) -> list[str]:
        """What stops writing, in the order to fix it."""
        settings, problems = self.settings, []
        for key, label in (("opt", "Opt Method"), ("sp", "SP Method"), ("output", "Output File")):
            if not settings.Row(key).value.strip():
                problems.append(f"Enter the {label}")
        if not settings.Row("suffix").value.strip():
            problems.append("Enter the SP Suffix")
        included = self.Included()
        if not included:
            problems.append("No Species Included")
        if any(not one.read for one in included):
            problems.append("Reading Outputs…")
        blocked: dict[str, list[str]] = {}
        for one in included:
            reason = one.Blocks(self.quasiHarmonic)
            if reason:
                blocked.setdefault(reason, []).append(one.name)
        problems += [f"{reason}: {', '.join(names)}" for reason, names in blocked.items()]
        return problems

    def Refresh(self) -> None:
        settings = self.settings
        suffix = settings.Row("suffix")
        suffix.SetMeaning("(Detected)" if suffix.value.strip() == self.detected else "")
        # Where the single point lines come from, at what conditions: the folder's GoodVibes table (qh-G: or a run
        # first; G: when it carries a correction for every species), or each output
        run, used = self.Source()
        if self.quasiHarmonic:
            readable = [one for one in self.Included() if one.read and not one.Blocks(True)]
            needed = GoodVibesNeeded(readable, self.folder, run)
            note = (f"Opens GoodVibes for {len(needed)}" if needed
                    else f"Reusing .dat ({run.note})" if any(one.needsGoodVibes for one in readable) else "")
        else:
            note = f"From .dat ({used.note})" if used is not None else "From the Outputs (1 atm)"
        settings.Row("rrho").SetMeaning(note)
        problems = self.Problems()
        self.ready = not problems
        more = f" (+{len(problems) - 1} More)" if len(problems) > 1 else ""
        count = len(self.Included())
        dropped = [" · ", Styled(f"{ORDER_FILE}: {len(self.dropped)} Not Found", "warning")] if self.dropped else []
        self.query_one("#submit", FrameRule).right = (
            Styled(problems[0] + more, "error") if problems else
            Text.assemble(Styled(f"{count} Species Ready", "good"), *dropped, " · ", KeyHint(self.app, "⏎", "Write")))
        self.refresh_bindings()

    # ─── Events ───────────────────────────────────────────────────────

    def on_input_changed(self, event: Input.Changed) -> None:
        if isinstance(event.input, ValueInput) and event.input.row.setting.key == "suffix":
            suffix = event.value.strip().lstrip("_")
            if suffix:
                self.Relist(suffix)
                return
        self.Refresh()

    def on_settings_list_changed(self, event: SettingsList.Changed) -> None:
        # The free energy choice changes which species can be written (No qh-G)
        self.Redraw()

    # ─── Actions ──────────────────────────────────────────────────────

    def action_include(self) -> None:
        table = self.query_one("#species", SpeciesTable)
        if not self.species:
            return
        name = self.species[table.cursor_row].name
        self.included ^= {name}
        self.touched.add(name)
        self.Redraw()

    def action_move(self, step: int) -> None:
        row = self.query_one("#species", SpeciesTable).cursor_row
        self.DragRow(row, row + step)

    def DragRow(self, row: int, target: int) -> None:
        # [ ] and a mouse drag (ReorderableTable): the cursor follows the moved species
        if MoveRow(self.species, row, target):
            self.Redraw(target)

    def action_run(self) -> None:
        """⏎ Write: GoodVibes first if qh-G needs a run (its Builder, preset, so the run is the user's), then the file."""
        if not self.ready:
            self.app.bell()
            return
        included = self.Included()
        needed = GoodVibesNeeded(included, self.folder) if self.quasiHarmonic else []
        if needed:
            from .builder import BuilderScreen
            # GoodVibes rewrites the whole table, so the run covers every included species it gives, not only the stale
            run = [one for one in included if one.needsGoodVibes]
            files = [Path(one.path.name) for one in run] + [Path(one.spPath.name) for one in run]
            suffix = self.settings.Row("suffix").value.strip().lstrip("_")
            self.app.push_screen(BuilderScreen(Action.GOODVIBES, files, {"singlePoint": suffix}, returns=True),
                                 lambda succeeded: self.ConfirmWrite() if succeeded else self.Refresh())
            return
        self.ConfirmWrite()

    def ConfirmWrite(self) -> None:
        self.Refresh()
        target = self.folder / self.settings.Row("output").value.strip()
        if target.exists():
            self.app.push_screen(Popup(f"Replace {target.name}?", f"{target.name} already exists in this folder.",
                                       [("r", "Replace", "replace")], "warning"),
                                 lambda result: self.Write(target) if result == "replace" else None)
            return
        self.Write(target)

    def Write(self, target: Path) -> None:
        settings, included = self.settings, self.Included()
        thermo = SPThermo(included, self.folder, self.quasiHarmonic)
        missing = [name for name in thermo.missing if len(next(one for one in included if one.name == name).imaginary) < 2]
        if missing:
            Notice(self.app, "No qh-G Found", f"{GOODVIBES_OUTPUT} has no qh-G(T) for: {', '.join(missing)}. "
                   "Nothing was written.", "error")
            self.Refresh()
            return
        target.write_text(SIText(included, settings.Row("opt").value.strip(), settings.Row("sp").value.strip(),
                                 settings.Row("phase").value, thermo))
        WriteOrder(self.folder, [one.name for one in included])
        self.listed, self.dropped = [one.name for one in included], []
        # Done: back to Home, which re-lists its files
        Notice(self.app, "SI File Written", f"Wrote {target.name} ({len(included)} Species) and {ORDER_FILE}.", "good",
               then=lambda: self.dismiss(None))
        self.Refresh()

    def action_back(self) -> None:
        self.dismiss(None)

    def action_help(self) -> None:
        Notice(self.app, "Help", "\n".join([
            "tab / shift+tab Move Between Settings and Species",
            "↑/↓ Move Between Settings (Type to Change a Value) or Species",
            "←/→ Switch the Free Energy", "space Include or Leave Out a Species",
            "[ / ] or Drag Move a Species", "enter Write the SI File", "esc Back to Home", "ctrl+q Quit"]))
