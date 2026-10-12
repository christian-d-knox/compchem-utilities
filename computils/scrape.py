"""Scrapers: read a folder's outputs into one file. The SI Scraper writes an SI-ready energies + coordinates list (the
group's coordinates2.sh, without a GoodVibes run per structure). No Textual import: the TUI's SI Scraper screen
(tui/scrapers.py) and any later CLI flag share this."""
import math
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import regex

from .analysis import DEFAULT_TEMPERATURE, GOODVIBES_OUTPUT, SpcPartners
from .defaults import Defaults
from .fileops  import (ExtractCoords, ExtractEnergy, ExtractFrequencies, ExtractGoodVibes, ExtractGoodVibesConditions,
                       ExtractProgram, ExtractTermination, ExtractThermalCorrections, HasContent, MapFile)

SI_FILE = "SI_coordinates.txt"
# coordinates2.sh's species order: one name per line. Read when the scraper opens, rewritten on Write
ORDER_FILE = "list.txt"
PROGRAM_NAMES = {Defaults.gaussianExtension: "G16", Defaults.orcaExtension: "ORCA"}


@dataclass
class Species:
    """One structure (its output, name.out) and its single point file (name_<suffix>.out), read once each."""
    name: str
    path: Path
    spPath: Path | None
    read: bool = False
    program: str = ""
    termination: str = ""
    energy: float | None = None
    corrections: tuple[float, float] | None = None     # (enthalpy, Gibbs free energy) corrections
    frequencies: list[float] = field(default_factory=list)
    symbols: list[str] = field(default_factory=list)
    xyz: list[tuple[float, float, float]] = field(default_factory=list)
    spEnergy: float | None = None

    @property
    def imaginary(self) -> list[float]:
        return [frequency for frequency in self.frequencies if frequency < 0]

    @property
    def needsGoodVibes(self) -> bool:
        # GoodVibes' table gives what it corrects (qh, concentration, temperature) for every Gaussian-optimized species,
        # lone atoms included (no --check, so no frequency analysis to fail)
        return self.program == Defaults.gaussianExtension

    def Flags(self, quasiHarmonic: bool) -> list[tuple[str, bool]]:
        """(flag, blocks writing) for the species list: what's missing blocks, what's worth a second look doesn't."""
        if not self.read:
            return []
        flags = []
        if self.spPath is None:
            flags.append(("No SP", True))
        elif self.spEnergy is None:
            flags.append(("No SP Energy", True))
        if self.energy is None:
            flags.append(("No Energy", True))
        if self.corrections is None:
            flags.append(("No Freq", True))
        if not self.symbols:
            flags.append(("No Geometry", True))
        # GoodVibes 3.2 can't read an ORCA 6 opt+freq output (cclib's ORCA parser crashes, and the whole run with it).
        # Stub until Christian has tested it (WORKING_AGENDA: SI Scraper, qh-G for ORCA-optimized species)
        if quasiHarmonic and self.program == Defaults.orcaExtension:
            flags.append(("No qh-G", True))
        if len(self.imaginary) == 1:
            flags.append((f"TS {self.imaginary[0]:.1f}", False))
        elif self.imaginary:
            flags.append((f"{len(self.imaginary)} Imaginary", False))
        if self.termination.lower() == "error termination":
            flags.append(("Error", False))
        return flags

    def Blocks(self, quasiHarmonic: bool) -> str | None:
        return next((flag for flag, blocks in self.Flags(quasiHarmonic) if blocks), None)


def NaturalKey(name: str) -> list:
    # 8a, 8b, 10 (not 10, 8a, 8b)
    return [int(part) if part.isdigit() else part.lower() for part in regex.split(r"(\d+)", name)]


def Outputs(folder: Path) -> list[Path]:
    return sorted(path for path in folder.iterdir() if path.is_file() and path.suffix == Defaults.outputExtension)


def DetectSuffix(outputs: list[Path]) -> str:
    """The single point suffix the folder uses (without the underscore): the most common Y among stems X_Y whose X is
    also an output here. The CompUtils default when nothing pairs up."""
    stems = {path.stem for path in outputs}
    found = Counter(stem.rsplit("_", 1)[1] for stem in stems if "_" in stem and stem.rsplit("_", 1)[0] in stems)
    return found.most_common(1)[0][0] if found else Defaults.singlePointExtra.lstrip("_")


def FindSpecies(folder: Path, suffix: str) -> list[Species]:
    """One Species per structure (single point files fold into theirs: 8a and 8a_SP are the species 8a), natural order."""
    structures, _, missing = SpcPartners(Outputs(folder), suffix)
    species = [Species(path.stem, path, None if path in missing or not suffix
                       else path.with_name(f"{path.stem}_{suffix}{path.suffix}")) for path in structures]
    return sorted(species, key=lambda one: NaturalKey(one.name))


def ReadSpecies(species: Species) -> None:
    """Everything the SI entry needs, from one map of the output and one of its single point file."""
    if HasContent(species.path):
        with MapFile(species.path) as data:
            species.program = ExtractProgram(data, species.path.suffix)
            species.termination = ExtractTermination(data)
            species.energy = ExtractEnergy(data, species.program)
            species.corrections = ExtractThermalCorrections(data, species.program)
            species.frequencies = ExtractFrequencies(data, species.program)
            symbols, X, Y, Z = ExtractCoords(data, species.program)
            species.symbols = symbols
            species.xyz = [(float(x), float(y), float(z)) for x, y, z in zip(X, Y, Z)]
    if species.spPath is not None and HasContent(species.spPath):
        with MapFile(species.spPath) as data:
            species.spEnergy = ExtractEnergy(data, ExtractProgram(data, species.spPath.suffix))
    species.read = True


def ReadOrder(folder: Path) -> list[str] | None:
    """The names in list.txt, in order, or None without one."""
    orderFile = folder / ORDER_FILE
    if not orderFile.is_file():
        return None
    return [line.strip() for line in orderFile.read_text().splitlines() if line.strip()]


def WriteOrder(folder: Path, names: list[str]) -> None:
    (folder / ORDER_FILE).write_text("".join(f"{name}\n" for name in names))


# ─── Single point thermochemistry: the outputs, or what GoodVibes corrected ──

@dataclass
class GoodVibesRun:
    """GoodVibes' last table in a folder: {structure: {column: value}}, when it was written (0 without one), and the
    conditions it was computed at, as GoodVibes wrote them (concentration None: its default 1 atm)."""
    rows: dict[str, dict[str, str]] = field(default_factory=dict)
    written: float = 0.0
    temperature: str = str(DEFAULT_TEMPERATURE)
    concentration: str | None = None

    @property
    def corrected(self) -> bool:
        """Whether GoodVibes applied a correction the outputs don't have: a concentration or another temperature."""
        return self.concentration is not None or float(self.temperature) != DEFAULT_TEMPERATURE

    def Label(self, enthalpy: bool = False) -> str:
        """What a single point line from the table adds: ' at 1.0 M', ' at 1.0 M and 273.15 K', ' at 273.15 K', or ''.
        An enthalpy names the temperature only (a concentration changes only the entropy)."""
        parts = [f"{self.concentration} M"] if self.concentration and not enthalpy else []
        if float(self.temperature) != DEFAULT_TEMPERATURE:
            parts.append(f"{self.temperature} K")
        return f" at {' and '.join(parts)}" if parts else ""

    @property
    def note(self) -> str:
        """The conditions, for the screen: '1.0 M', '1.0 M, 273.15 K', '1 atm'."""
        parts = [f"{self.concentration} M" if self.concentration else "1 atm"]
        if float(self.temperature) != DEFAULT_TEMPERATURE:
            parts.append(f"{self.temperature} K")
        return ", ".join(parts)


def GoodVibesTable(folder: Path) -> GoodVibesRun:
    tablePath = folder / GOODVIBES_OUTPUT
    if not HasContent(tablePath):
        return GoodVibesRun()
    # One map for the table and the header lines above it
    with MapFile(tablePath) as data:
        table = ExtractGoodVibes(data)
        temperature, concentration = ExtractGoodVibesConditions(data)
    if len(table) < 2:
        return GoodVibesRun()
    header = table[0]
    return GoodVibesRun({row[0]: dict(zip(header, row)) for row in table[1:] if row}, tablePath.stat().st_mtime,
                        temperature, concentration)


def _TableValue(row: dict[str, str] | None, column: str) -> float | None:
    """GoodVibes' single point value of a column (H, qh-H, G(T), qh-G(T)) as it wrote it: a --spc run's column_SPC.
    None without one (no row, a table without --spc, or nan: GoodVibes 3.2 writes nan for T.qh-S and qh-G(T) when it
    picks the vibrational scale factor itself, no -v)."""
    try:
        value = float(row[f"{column}_SPC"])
    except (KeyError, TypeError, ValueError):
        return None
    return None if math.isnan(value) else value


def GoodVibesNeeded(species: list[Species], folder: Path, run: GoodVibesRun | None = None,
                    column: str = "qh-G(T)") -> list[Species]:
    """The species whose single point free energy (column: qh-G(T) or G(T)) the folder's GoodVibes table can't give:
    no table, no row or value, or a table older than the species' output or single point file (re-optimized since)."""
    run = run or GoodVibesTable(folder)

    def Current(one: Species) -> bool:
        newest = max(path.stat().st_mtime for path in (one.path, one.spPath) if path is not None)
        return run.written >= newest and _TableValue(run.rows.get(one.name), column) is not None
    return [one for one in species if one.needsGoodVibes and not Current(one)]


@dataclass
class ThermoSource:
    """The single point enthalpy and free energy lines: values {name: (H, G)}, the labels naming the table's
    conditions, the GoodVibes run (None: each species' own outputs), and the species the table doesn't give (qh-G)."""
    values: dict[str, tuple[float, float]]
    hLabel: str = ""
    gLabel: str = ""
    run: GoodVibesRun | None = None
    missing: list[str] = field(default_factory=list)


def TableUsed(species: list[Species], folder: Path, quasiHarmonic: bool,
              run: GoodVibesRun | None = None) -> GoodVibesRun | None:
    """The GoodVibes run the single point lines come from, or None for the outputs. qh-G always uses the table (only
    GoodVibes gives it); G only when the table carries a correction and is current for every species (an
    ORCA-optimized one never is), so one file never mixes the two."""
    run = run or GoodVibesTable(folder)
    if quasiHarmonic:
        return run
    if (run.corrected and all(one.needsGoodVibes for one in species)
            and not GoodVibesNeeded(species, folder, run, "G(T)")):
        return run
    return None


def SPThermo(species: list[Species], folder: Path, quasiHarmonic: bool) -> ThermoSource:
    """Default: E_sp + the output's thermal corrections (coordinates2.sh). From the table: GoodVibes' values as written,
    never corrected here. The enthalpy is qh-H when the run printed it (-q), else the table's H, so every single point
    line is at the table's temperature."""
    outputs = {one.name: (one.spEnergy + one.corrections[0], one.spEnergy + one.corrections[1])
               for one in species if one.corrections is not None and one.spEnergy is not None}
    run = TableUsed(species, folder, quasiHarmonic)
    if run is None:
        return ThermoSource(outputs)
    source = ThermoSource({}, run.Label(enthalpy=True), run.Label(), run)
    for one in species:
        row = run.rows.get(one.name)
        free = _TableValue(row, "qh-G(T)" if quasiHarmonic else "G(T)")
        enthalpy = (_TableValue(row, "qh-H") if quasiHarmonic else None) or _TableValue(row, "H")
        if free is None or enthalpy is None:
            source.missing.append(one.name)
        else:
            source.values[one.name] = (enthalpy, free)
    return source


# ─── The SI file ──────────────────────────────────────────────────────

def SIText(species: list[Species], optMethod: str, spMethod: str, phase: str, thermo: ThermoSource) -> str:
    """coordinates2.sh's layout, one entry per species in order. The opt lines are the output's own; the single point
    lines taken from a GoodVibes table name its conditions ('free energy in solution at 1.0 M (au)')."""
    where = f" {phase.strip()}" if phase.strip() else ""
    entries = []
    for one in species:
        if len(one.imaginary) > 1:
            entries.append(f"{one.path.name} has {len(one.imaginary)} imaginary frequencies, "
                           "please double check the file!\n\n")
            continue
        enthalpy, gibbs = one.corrections
        spEnthalpy, spFree = thermo.values[one.name]
        rows = [(f"{optMethod} SCF energy{where} (au):", one.energy),
                (f"{optMethod} enthalpy{where} (au):", one.energy + enthalpy),
                (f"{optMethod} free energy{where} (au):", one.energy + gibbs),
                (f"{spMethod} SCF energy{where} (au):", one.spEnergy),
                (f"{spMethod} enthalpy{where}{thermo.hLabel} (au):", spEnthalpy),
                (f"{spMethod} free energy{where}{thermo.gLabel} (au):", spFree)]
        width = max(len(label) for label, _ in rows) + 4
        lines = [one.name] + [f"{label:<{width}}{value:.6f}" for label, value in rows]
        if one.imaginary:
            lines.append(f"{'Imaginary frequency:':<{width}}{one.imaginary[0]:.1f} cm-1")
        lines += ["", "Cartesian coordinates", "ATOM\tX             Y           Z"]
        lines += [f"{symbol}    {x:9.6f}    {y:9.6f}    {z:9.6f}" for symbol, (x, y, z) in zip(one.symbols, one.xyz)]
        entries.append("\n".join(lines) + "\n\n")
    return "".join(entries)
