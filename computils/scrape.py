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
                       ExtractProgram, ExtractTemperature, ExtractTermination, ExtractThermalCorrections, HasContent,
                       MapFile)

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
    temperature: float | None = None                   # of its thermochemistry (K)

    @property
    def imaginary(self) -> list[float]:
        return [frequency for frequency in self.frequencies if frequency < 0]

    @property
    def singleAtom(self) -> bool:
        return len(self.symbols) == 1

    @property
    def needsGoodVibes(self) -> bool:
        # A lone atom has no vibrations, so its qh-G is its G (and GoodVibes finds no frequencies for it)
        return self.program == Defaults.gaussianExtension and not self.singleAtom

    def Flags(self, quasiHarmonic: bool, tableTemperature: str | None = None) -> list[tuple[str, bool]]:
        """(flag, blocks writing) for the species list: what's missing blocks, what's worth a second look doesn't.
        tableTemperature: that of the GoodVibes run the free energies come from, if any (a lone atom's G is its own)."""
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
        if quasiHarmonic and self.program == Defaults.orcaExtension and not self.singleAtom:
            flags.append(("No qh-G", True))
        # A lone atom's G can follow the table's concentration (FreeEnergies), but not another temperature
        if (self.singleAtom and tableTemperature is not None and self.temperature is not None
                and abs(self.temperature - float(tableTemperature)) > 0.01):
            flags.append((f"Not at {tableTemperature} K", True))
        if len(self.imaginary) == 1:
            flags.append((f"TS {self.imaginary[0]:.1f}", False))
        elif self.imaginary:
            flags.append((f"{len(self.imaginary)} Imaginary", False))
        if self.termination.lower() == "error termination":
            flags.append(("Error", False))
        return flags

    def Blocks(self, quasiHarmonic: bool, tableTemperature: str | None = None) -> str | None:
        return next((flag for flag, blocks in self.Flags(quasiHarmonic, tableTemperature) if blocks), None)


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
            species.temperature = ExtractTemperature(data, species.program)
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


# ─── Free energies: the outputs, or GoodVibes' table ──────────────────

# GoodVibes 3.2's constants, so a lone atom's standard-state shift matches its table: c° = ATMOS / (R T) mol/l
ATMOS, GAS_CONSTANT, J_TO_AU = 101.325, 8.3144621, 4.184 * 627.509541 * 1000


@dataclass
class GoodVibesRun:
    """GoodVibes' last table in a folder: {structure: {column: value}}, when it was written (0 without one), and the
    conditions it was computed at, as GoodVibes wrote them (concentration None: its default 1 atm)."""
    rows: dict[str, dict[str, str]] = field(default_factory=dict)
    written: float = 0.0
    temperature: str = str(DEFAULT_TEMPERATURE)
    concentration: str | None = None

    @property
    def label(self) -> str:
        """What the free energy lines add: ' at 1.0 M', ' at 1.0 M and 273.15 K', ' at 273.15 K', or ''."""
        parts = [f"{self.concentration} M"] if self.concentration else []
        if float(self.temperature) != DEFAULT_TEMPERATURE:
            parts.append(f"{self.temperature} K")
        return f" at {' and '.join(parts)}" if parts else ""

    @property
    def note(self) -> str:
        """The same, for the screen: '1.0 M', '1.0 M, 273.15 K', '1 atm'."""
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


def _TableFree(species: Species, row: dict[str, str] | None, column: str) -> tuple[float, float] | None:
    """(opt, single point) free energy from a table row's column (G(T) or qh-G(T)). A --spc run's column_SPC is the
    single point's; the opt's is the same correction on the opt energy (what coordinates2.sh does the other way round)."""
    # GoodVibes 3.2 writes nan for T.qh-S and qh-G(T) when it picks the vibrational scale factor itself (no -v): no value
    try:
        if row and column in row:
            optimized = float(row[column])
            values = optimized, optimized - species.energy + species.spEnergy
        elif row and f"{column}_SPC" in row:
            singlePoint = float(row[f"{column}_SPC"])
            values = singlePoint - float(row["E_SPC"]) + species.energy, singlePoint
        else:
            return None
    except (KeyError, ValueError):
        return None
    return None if any(math.isnan(value) for value in values) else values


def GoodVibesNeeded(species: list[Species], folder: Path, run: GoodVibesRun | None = None,
                    column: str = "qh-G(T)") -> list[Species]:
    """The species whose free energy (column: qh-G(T) or G(T)) the folder's GoodVibes table can't give: no table, no
    row or value, or a table older than the species' output or single point file (re-optimized since)."""
    run = run or GoodVibesTable(folder)

    def Current(one: Species) -> bool:
        newest = max(path.stat().st_mtime for path in (one.path, one.spPath) if path is not None)
        return run.written >= newest and _TableFree(one, run.rows.get(one.name), column) is not None
    return [one for one in species if one.needsGoodVibes and not Current(one)]


@dataclass
class FreeSource:
    """Where the free energy lines come from: energies {name: (opt, single point)}, the conditions their label names,
    the GoodVibes run (None: each species' own output), and the species the table doesn't give (qh-G only)."""
    energies: dict[str, tuple[float, float]]
    label: str = ""
    run: GoodVibesRun | None = None
    missing: list[str] = field(default_factory=list)


def UsesTable(species: list[Species], folder: Path, quasiHarmonic: bool,
              run: GoodVibesRun | None = None) -> GoodVibesRun | None:
    """The GoodVibes run the free energies come from, or None for the outputs. qh-G always uses the table; G uses it
    when it is current for every species it can cover and none it can't (an ORCA-optimized one) is included, so one
    file never mixes standard states."""
    run = run or GoodVibesTable(folder)
    if quasiHarmonic:
        return run
    coverable = all(one.program == Defaults.gaussianExtension or one.singleAtom for one in species)
    if (run.rows and coverable and any(one.needsGoodVibes for one in species)
            and not GoodVibesNeeded(species, folder, run, "G(T)")):
        return run
    return None


def FreeEnergies(species: list[Species], folder: Path, quasiHarmonic: bool) -> FreeSource:
    run = UsesTable(species, folder, quasiHarmonic)
    if run is None:
        return FreeSource({one.name: (one.energy + one.corrections[1], one.spEnergy + one.corrections[1])
                           for one in species if one.corrections is not None})
    column = "qh-G(T)" if quasiHarmonic else "G(T)"
    # A lone atom isn't in the table (no vibrations: its qh-G is its G). At a concentration its G moves by RT ln(c/c°),
    # its only concentration dependence (translational)
    shift = 0.0
    if run.concentration:
        temperature = float(run.temperature)
        standard = ATMOS / (GAS_CONSTANT * temperature)
        shift = GAS_CONSTANT * temperature * math.log(float(run.concentration) / standard) / J_TO_AU
    source = FreeSource({}, run.label, run)
    for one in species:
        if one.needsGoodVibes:
            values = _TableFree(one, run.rows.get(one.name), column)
            if values is None:
                source.missing.append(one.name)
            else:
                source.energies[one.name] = values
        elif one.corrections is not None:
            gibbs = one.corrections[1] + shift
            source.energies[one.name] = (one.energy + gibbs, one.spEnergy + gibbs)
    return source


# ─── The SI file ──────────────────────────────────────────────────────

def SIText(species: list[Species], optMethod: str, spMethod: str, phase: str, free: FreeSource) -> str:
    """coordinates2.sh's layout, one entry per species in order. The free energy lines name the conditions of a
    GoodVibes run that isn't 1 atm and 298.15 K ('free energy in solution at 1.0 M (au)')."""
    where = f" {phase.strip()}" if phase.strip() else ""
    entries = []
    for one in species:
        if len(one.imaginary) > 1:
            entries.append(f"{one.path.name} has {len(one.imaginary)} imaginary frequencies, "
                           "please double check the file!\n\n")
            continue
        enthalpy = one.corrections[0]
        freeOpt, freeSp = free.energies[one.name]
        rows = [(f"{optMethod} SCF energy{where} (au):", one.energy),
                (f"{optMethod} enthalpy{where} (au):", one.energy + enthalpy),
                (f"{optMethod} free energy{where}{free.label} (au):", freeOpt),
                (f"{spMethod} SCF energy{where} (au):", one.spEnergy),
                (f"{spMethod} enthalpy{where} (au):", one.spEnergy + enthalpy),
                (f"{spMethod} free energy{where}{free.label} (au):", freeSp)]
        width = max(len(label) for label, _ in rows) + 4
        lines = [one.name] + [f"{label:<{width}}{value:.6f}" for label, value in rows]
        if one.imaginary:
            lines.append(f"{'Imaginary frequency:':<{width}}{one.imaginary[0]:.1f} cm-1")
        lines += ["", "Cartesian coordinates", "ATOM\tX             Y           Z"]
        lines += [f"{symbol}    {x:9.6f}    {y:9.6f}    {z:9.6f}" for symbol, (x, y, z) in zip(one.symbols, one.xyz)]
        entries.append("\n".join(lines) + "\n\n")
    return "".join(entries)
