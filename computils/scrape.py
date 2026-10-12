"""Scrapers: read a folder's outputs into one file. The SI Scraper writes an SI-ready energies + coordinates list (the
group's coordinates2.sh, without a GoodVibes run per structure). No Textual import: the TUI's SI Scraper screen
(tui/scrapers.py) and any later CLI flag share this."""
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import regex

from .analysis import GOODVIBES_OUTPUT, SpcPartners
from .defaults import Defaults
from .fileops  import (ExtractCoords, ExtractEnergy, ExtractFrequencies, ExtractFrom, ExtractGoodVibes, ExtractProgram,
                       ExtractTermination, ExtractThermalCorrections, HasContent, MapFile)

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
    def singleAtom(self) -> bool:
        return len(self.symbols) == 1

    @property
    def needsGoodVibes(self) -> bool:
        # A lone atom has no vibrations, so its qh-G is its G (and GoodVibes finds no frequencies for it)
        return self.program == Defaults.gaussianExtension and not self.singleAtom

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
        if quasiHarmonic and self.program == Defaults.orcaExtension and not self.singleAtom:
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


# ─── qh-G from GoodVibes' table ───────────────────────────────────────

def GoodVibesTable(folder: Path) -> tuple[dict[str, dict[str, str]], float]:
    """GoodVibes' last table in this folder: {structure: {column: value}}, and when it was written (0 without one)."""
    tablePath = folder / GOODVIBES_OUTPUT
    table = ExtractFrom(tablePath, ExtractGoodVibes, empty=[])
    if len(table) < 2:
        return {}, 0.0
    header = table[0]
    return {row[0]: dict(zip(header, row)) for row in table[1:] if row}, tablePath.stat().st_mtime


def _QuasiHarmonic(species: Species, row: dict[str, str] | None) -> tuple[float, float] | None:
    """(opt, single point) qh-G from a table row. A --spc run's qh-G(T)_SPC is the single point's; the opt's is the
    same correction on the opt energy (what coordinates2.sh does with plain qh-G(T) the other way round)."""
    try:
        if row and "qh-G(T)" in row:
            optimized = float(row["qh-G(T)"])
            return optimized, optimized - species.energy + species.spEnergy
        if row and "qh-G(T)_SPC" in row:
            singlePoint = float(row["qh-G(T)_SPC"])
            return singlePoint - float(row["E_SPC"]) + species.energy, singlePoint
    except (KeyError, ValueError):
        pass
    return None


def GoodVibesNeeded(species: list[Species], folder: Path) -> list[Species]:
    """The species whose qh-G the folder's GoodVibes table can't give: no table, no row, or a table older than the
    species' output or single point file (re-optimized since)."""
    table, written = GoodVibesTable(folder)

    def Current(one: Species) -> bool:
        newest = max(path.stat().st_mtime for path in (one.path, one.spPath) if path is not None)
        return written >= newest and _QuasiHarmonic(one, table.get(one.name)) is not None
    return [one for one in species if one.needsGoodVibes and not Current(one)]


def QuasiHarmonicEnergies(species: list[Species], folder: Path) -> dict[str, tuple[float, float]]:
    """{name: (opt qh-G, single point qh-G)} for every species the table (or, for a lone atom, its own G) gives."""
    table, _ = GoodVibesTable(folder)
    energies = {}
    for one in species:
        if one.needsGoodVibes:
            values = _QuasiHarmonic(one, table.get(one.name))
            if values is not None:
                energies[one.name] = values
        elif one.corrections is not None:
            energies[one.name] = (one.energy + one.corrections[1], one.spEnergy + one.corrections[1])
    return energies


# ─── The SI file ──────────────────────────────────────────────────────

def SIText(species: list[Species], optMethod: str, spMethod: str, phase: str,
           quasiHarmonic: dict[str, tuple[float, float]] | None) -> str:
    """coordinates2.sh's layout, one entry per species in order. quasiHarmonic: the qh-G energies, or None for G."""
    where = f" {phase.strip()}" if phase.strip() else ""
    entries = []
    for one in species:
        if len(one.imaginary) > 1:
            entries.append(f"{one.path.name} has {len(one.imaginary)} imaginary frequencies, "
                           "please double check the file!\n\n")
            continue
        enthalpy, gibbs = one.corrections
        freeOpt, freeSp = (quasiHarmonic[one.name] if quasiHarmonic is not None
                           else (one.energy + gibbs, one.spEnergy + gibbs))
        rows = [(f"{optMethod} SCF energy{where} (au):", one.energy),
                (f"{optMethod} enthalpy{where} (au):", one.energy + enthalpy),
                (f"{optMethod} free energy{where} (au):", freeOpt),
                (f"{spMethod} SCF energy{where} (au):", one.spEnergy),
                (f"{spMethod} enthalpy{where} (au):", one.spEnergy + enthalpy),
                (f"{spMethod} free energy{where} (au):", freeSp)]
        width = max(len(label) for label, _ in rows) + 4
        lines = [one.name] + [f"{label:<{width}}{value:.6f}" for label, value in rows]
        if one.imaginary:
            lines.append(f"{'Imaginary frequency:':<{width}}{one.imaginary[0]:.1f} cm-1")
        lines += ["", "Cartesian coordinates", "ATOM\tX             Y           Z"]
        lines += [f"{symbol}    {x:9.6f}    {y:9.6f}    {z:9.6f}" for symbol, (x, y, z) in zip(one.symbols, one.xyz)]
        entries.append("\n".join(lines) + "\n\n")
    return "".join(entries)
