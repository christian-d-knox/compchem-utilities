"""The CompUtils data file (<root name>.cudf): a small summary, with a known structure, of what a job's outputs say.

8a.out, 8a_re.out, 8a_SP_re5.out and 8a-2-B3LYP_SP.out all feed 8a.cudf (RootName strips CompUtils' own suffixes). It
is written when `cu -info` reads an output and when a tracked job ends (RecordEndings: the stalker and the Neva-Touch
Scheduler hook), so an agent or a person can read a few hundred bytes instead of a large output. Outputs only.

**Sections are stages**: an Opt+Freq -> SP output gives an Optimization section and a Single Point
section, both naming that file (Stage 1 of 2, Stage 2 of 2), each holding only what its own stage printed
(info.StageFacts, read within fileops.StageSpans). There is one section per kind of optimization (Reaction
Intermediate, ModRedundant, Transition State), and one per other kind and level (Single Point: M062X/def2TZVP SMD
(DiethylEther)). **A section holds the final successful attempt** (Rank): a stage that ended normally beats one that
failed, then the later re-run (8a_SP_re5 over 8a_SP), the later stage, the newer file. The first section is the
identity (formula, charge, multiplicity, spin, the final geometry) of the lead job (the Transition State, else the
Reaction Intermediate, else the ModRedundant optimization); a job section repeats what differs from it. An optimization
lists its constraints (Bond C1 C4 Frozen @ 2.05 A), a Gaussian TS optimization its TS eigenvector, each coordinate with
its final value, all as the program printed them. A CREST search (8a_CREST.out) is a Conformer Search (CREST) section,
one per level: the run's options and constraints, the ensemble it kept and every conformer's energy and population (the
lowest conformer's geometry leads only when no DFT optimization does).

It holds exactly what info.FileFacts returns, through FIELDS: `python -m computils.datafile` fails on a fact without an
entry, and checks every sample output reads back as read. Each From line records its file's size and modification
time, so Lookup answers from the data file only while they match. The geometry and the full frequency list are here
only (DATA_FILE_ONLY): `cu -info` leaves them out of its report."""
import functools
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import regex

from .console  import console
from .defaults import Defaults
from .fileops  import MapFile, HasContent, FileLock, fileCreation
from .info     import FileFacts, COORDINATE_UNITS
from .crest    import BEST_FILE, CONFORMER_SEARCH, ResultFolder
from . import agent


@dataclass(frozen=True)
class Field:
    key:   str           # FileFacts' key
    label: str           # the line's label in the data file
    kind:  str           # text, int, float, bool, convergence, numbers (one line); lines under the label: rows,
                         # constraints, eigenvector, conformers (ROW_KINDS)
    scope: str           # identity (the first section's), file (each section of a file), stage (its own section)
    unit:  str = ""


# Every fact FileFacts can return for an output, in the order the data file writes them
FIELDS = (
    Field("formula",              "Formula",          "text",        "identity"),
    Field("atoms",                "Atoms",            "int",         "identity"),
    Field("charge",               "Charge",           "int",         "identity"),
    Field("multiplicity",         "Multiplicity",     "int",         "identity"),
    Field("spin",                 "Spin",             "text",        "identity"),
    Field("spinReason",           "Spin Reason",      "text",        "identity"),
    Field("electronProblem",      "Electron Problem", "text",        "identity"),
    Field("coordinates",          "Coordinates (Final)", "rows",     "identity"),
    Field("program",              "Program",          "text",        "file"),
    Field("version",              "Version",          "text",        "file"),
    Field("cpus",                 "CPUs",             "int",         "file"),
    Field("memoryGB",             "Memory",           "int",         "file",     "GB"),
    Field("runs",                 "Runs",             "text",        "stage"),
    Field("status",               "Status",           "text",        "stage"),
    Field("termination",          "Termination",      "text",        "stage"),
    Field("route",                "Route",            "text",        "stage"),
    Field("method",               "Method",           "text",        "stage"),
    Field("basis",                "Basis",            "text",        "stage"),
    Field("keys",                 "Keywords",         "text",        "stage"),
    Field("solvation",            "Solvation",        "text",        "stage"),
    Field("options",              "Options",          "text",        "stage"),
    Field("energy",               "E",                "float",       "stage",    "Eh"),
    Field("zeroPoint",            "ZPE",              "float",       "stage",    "Eh"),
    Field("energyZeroPoint",      "E+ZPE",            "float",       "stage",    "Eh"),
    Field("thermalEnergy",        "E (Thermal)",      "float",       "stage",    "Eh"),
    Field("enthalpy",             "H",                "float",       "stage",    "Eh"),
    Field("gibbs",                "G",                "float",       "stage",    "Eh"),
    Field("enthalpyCorrection",   "H Correction",     "float",       "stage",    "Eh"),
    Field("gibbsCorrection",      "G Correction",     "float",       "stage",    "Eh"),
    Field("convergence",          "Convergence",      "convergence", "stage"),
    Field("stability",            "Stable",           "bool",        "stage"),
    Field("outOfSteps",           "Out of Steps",     "bool",        "stage"),
    Field("s2",                   "<S**2>",           "float",       "stage"),
    Field("imaginaryFrequencies", "Imaginary",        "numbers",     "stage",    "cm-1"),
    Field("lowestFrequency",      "Lowest Frequency", "float",       "stage",    "cm-1"),
    Field("tsEigenvector",        "TS Eigenvector",   "eigenvector", "stage"),
    Field("tsEigenvalue",         "TS Eigenvalue",    "float",       "stage",    "(Should Be Negative)"),
    Field("constraints",          "Constraints",      "constraints", "stage"),
    Field("forceConstant",        "Force Constant",   "float",       "stage",    "Eh/bohr^2"),
    Field("energyWindow",         "Energy Window",    "float",       "stage",    "kcal/mol"),
    Field("rmsdThreshold",        "RMSD Threshold",   "float",       "stage",    "A"),
    Field("energyThreshold",      "Energy Threshold", "float",       "stage",    "kcal/mol"),
    Field("rotConstThreshold",    "Bconst Threshold", "float",       "stage"),
    Field("mtdRuns",              "MTDs",             "int",         "stage"),
    Field("mtdEarly",             "MTDs Ended Early", "int",         "stage"),
    Field("timeStep",             "MTD Time Step",    "int",         "stage",    "fs"),
    Field("conformers",           "Conformers",       "int",         "stage"),
    Field("lowestEnergy",         "E (Lowest)",       "float",       "stage",    "Eh"),
    Field("temperature",          "Temperature",      "float",       "stage",    "K"),
    Field("ensembleAverageEnergy", "Ensemble Average E", "float",    "stage",    "kcal/mol"),
    Field("ensembleEntropy",      "Ensemble S",       "float",       "stage",    "cal/mol K"),
    Field("ensembleFreeEnergy",   "Ensemble G",       "float",       "stage",    "kcal/mol"),
    Field("populationOfLowest",   "Lowest Population", "float",      "stage",    "%"),
    Field("wallTime",             "Wall Time",        "text",        "stage"),
    Field("frequencies",          "Frequencies",      "rows",        "stage",    "cm-1"),
    Field("conformerEnergies",    "Conformer Energies", "conformers", "stage"),
)
BY_KEY = {field.key: field for field in FIELDS}
BY_LABEL = {field.label: field for field in FIELDS}
SCOPE = {scope: tuple(field.key for field in FIELDS if field.scope == scope) for scope in ("identity", "file", "stage")}
# Never written as lines: the file's name and kind are the From line's (with a stage's number), an input's stages
# aren't results, and a staged record's stages are its sections
META = ("file", "kind", "stages", "stage", "dataFile")
# Written to the data file only: `cu -info` leaves these blocks out of its report (they'd bloat an agent's context)
DATA_FILE_ONLY = ("coordinates", "frequencies", "conformerEnergies")
ROW_KINDS = ("rows", "constraints", "eigenvector", "conformers")
LABEL_WIDTH = 18
FREQUENCIES_PER_ROW = 8
SECTION_HEADER = r"(?m)^=== (.+?) ===\r?$"
FROM_LINE = r"^(.+?)(?:, Stage (\d+) of (\d+))? \((\d+) bytes, modified (\S+ \S+)(?:, reader (\d+))?\)$"
# Which reading of the outputs a section holds: bump it whenever an extractor reads something differently (a program
# version it didn't know, a fixed pattern), so a data file written by an older reading is read again rather than
# answered from (Lookup). A section without one is from before it existed. 2: CREST 3.x (2026-11-21)
READER = 3
KIND_TITLES = {"SP": "Single Point", "Freq": "Frequencies", "IRC": "IRC", "Stability": "Stability",
               "Conformer Search": "Conformer Search (CREST)"}
OPTIMIZATION = "Optimization"
# Each kind of optimization (StepChecks.optKind) is its own section, in the order such a chain runs. Not "ground state":
# an intermediate can be an excited state (a triplet that crosses to a singlet)
OPTIMIZATION_KINDS = {"Opt": "Reaction Intermediate", "ModRed": "ModRedundant", "OptTS": "Transition State"}
OPTIMIZATION_SLOTS = tuple(f"{OPTIMIZATION} ({kind})" for kind in OPTIMIZATION_KINDS.values())
# Whose geometry is the final one: a ModRed is a step toward something
LEAD_ORDER = tuple(f"{OPTIMIZATION} ({OPTIMIZATION_KINDS[kind]})" for kind in ("OptTS", "Opt", "ModRed"))
COORDINATE_NAMES = r"Bond|Angle|Dihedral|Linear Bend|Cartesian|Atoms|Elements|\S+"
# An atom (C48, or a bare number), or an element (a CREST constraint on every atom of one: Elements Pd)
ATOM_LABEL = r"[A-Za-z]{0,2}\d+|[A-Z][a-z]?"
UNIT = r"(?: (?:A|deg))?"
CONSTRAINT_LINE = (rf"^({COORDINATE_NAMES})((?: (?:{ATOM_LABEL}))*) (Frozen|Scan|Constrained|Fixed)"
                   rf"(?:, (\d+) Steps of (\S+){UNIT})?"
                   rf"(?: @ (\S+){UNIT})?( \(From the Checkpoint\))?$")
EIGENVECTOR_LINE = rf"^(-?[\d.]+(?:[eE][-+]?\d+)?) ({COORDINATE_NAMES})((?: (?:{ATOM_LABEL}))*)(?: @ (\S+){UNIT})?$"
# A CREST conformer: its number, energy above the lowest, total energy, population (its set's weight), rotamers
CONFORMER_LINE = r"^(\d+)\s+(\S+) kcal/mol\s+(\S+) Eh\s+Population (\S+)\s+(\d+) Rotamers?$"
_FROM_LINE_RE = regex.compile(FROM_LINE)
_PAIR_SPLIT_RE = regex.compile(r"(.+?)(\s{2,}|$)(.*)")
_TRAILING_UNIT_RE = regex.compile(r" \([^)]*\)$")


# ─── Names and Ranks ──────────────────────────────────────────────────

@functools.lru_cache(maxsize=8)
def _CompiledSuffixes(singlePoint: str, reRun: str, crest: str) -> tuple:
    sp, rr, cr = regex.escape(singlePoint), regex.escape(reRun), regex.escape(crest)
    return tuple(regex.compile(p) for p in (
        rf"(.+?)-\d+-[^-]+{sp}", rf"(.+){sp}", rf"(.+){rr}(\d*)", rf"(.+){cr}"
    ))


def _Strip(stem: str) -> tuple[str, int]:
    # The root and the re-runs counted on the way down: 8a_re2_SP_re -> ('8a', 3)
    patterns = _CompiledSuffixes(Defaults.singlePointExtra, Defaults.reRunExtra, Defaults.crestExtra)
    attempts = 0
    while True:
        for pattern in patterns:
            found = pattern.fullmatch(stem)
            if found:
                stem = found.group(1)
                if found.re.groups == 2:
                    attempts += int(found.group(2) or 1)
                break
        else:
            return stem, attempts


def RootName(stem: str) -> str:
    """A job name without CompUtils' own suffixes, undoing the workflows' naming (SinglePointName, BenchmarkName,
    ReRunName, crest.JobName): 8a_SP, 8a-2-B3LYP_SP, 8a_re2, 8a_re_SP_re5 and 8a_CREST_re are all 8a. A name no suffix
    explains is its own root."""
    return _Strip(stem)[0]


def Attempt(stem: str) -> int:
    """How many re-runs a job name counts, over its whole chain: 8a 0, 8a_re 1, 8a_re2 2, 8a_SP_re5 5, 8a_re2_SP 2 (the
    single point of the second re-run's geometry)."""
    return _Strip(stem)[1]


def DataPath(path: Path) -> Path:
    """The data file an output feeds: <its root name><dataExtension>, in the output's own folder."""
    path = Path(path)
    return path.parent / fileCreation(RootName(path.stem), Defaults.dataExtension)


def SectionTitle(stage: dict) -> str:
    """What a stage's section is called, from what it runs (its `runs`, fileops.JobChecks' kinds): its kind of
    optimization (Optimization (Transition State), one per data file), else its kind, then its level and solvation
    (Single Point: M062X/def2TZVP SMD (DiethylEther), one per level)."""
    kinds = stage.get("runs", "SP").split("+")
    optimization = next((OPTIMIZATION_KINDS[kind] for kind in kinds if kind in OPTIMIZATION_KINDS), "")
    kind = (f"{OPTIMIZATION} ({optimization})" if optimization
            else next((KIND_TITLES[kind] for kind in ("Conformer Search", "IRC", "Stability", "Freq") if kind in kinds),
                      KIND_TITLES["SP"]))
    level = f"{stage['method']}/{stage['basis']}" if stage.get("basis") else stage.get("method", "")
    return " ".join(part for part in (f"{kind}:", level, stage.get("solvation", "")) if part).rstrip(":")


def _Slot(title: str) -> str:
    # Sections that compete for one place: each kind of optimization has one, whatever its level
    return title.split(":")[0] if title.startswith(OPTIMIZATION) else title


def _Order(title: str) -> int:
    # The optimizations first, in the order a chain runs them; the rest as they arrived (the sort is stable)
    slot = _Slot(title)
    return OPTIMIZATION_SLOTS.index(slot) if slot in OPTIMIZATION_SLOTS else len(OPTIMIZATION_SLOTS)


def Rank(section: dict) -> tuple:
    """Which of two sections for one place is kept (the higher): one that ended normally (and not out of optimizer
    steps: ORCA can end normally without converging), then the later re-run (Attempt), the later stage, the newer file.
    So a slot ends as the final successful attempt, and shows the latest failure until one succeeds."""
    succeeded = section.get("status") == "Normal" and not section.get("outOfSteps")
    return succeeded, Attempt(Path(section["file"]).stem), section["_stage"], _When(section["_stamp"])


# ─── Writing ───────────────────────────────────────────────────────────

def _Stamp(path: Path) -> tuple[int, str]:
    # The file's size and modification time as written on its From line (microseconds, with its UTC offset)
    stat = path.stat()
    return stat.st_size, datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(" ", "microseconds")


def _When(stamp: tuple[int, str]) -> datetime:
    # A From line's modification time as an instant (its UTC offset kept, so two time zones compare correctly)
    return datetime.fromisoformat(stamp[1])


def _Line(label: str, text: str) -> str:
    # Values start in one column, always at least two spaces after the label (how a line is split when read back)
    return label.ljust(LABEL_WIDTH - 2) + "  " + text


def _Number(value) -> str:
    return repr(value) if isinstance(value, float) else str(value)


def _At(record: dict) -> str:
    # ' @ 2.05 A': a coordinate's value, when there is one
    return f" @ {_Number(record['value'])} {record.get('unit', '')}".rstrip() if "value" in record else ""


def ConstraintText(record: dict) -> str:
    """A constraint as the data file writes it: 'Bond C1 C4 Frozen @ 2.05 A', a scan's 'Bond C1 C4 Scan, 10 Steps of
    -0.1 A @ 2.05 A', one a checkpoint carried in ending '(From the Checkpoint)'."""
    text = " ".join([record["coordinate"], *record["atoms"], record["action"]])
    if "steps" in record:
        text += f", {record['steps']} Steps of {_Number(record['step'])} {record.get('unit', '')}".rstrip()
    return text + _At(record) + (" (From the Checkpoint)" if record.get("inherited") else "")


def ConformerText(record: dict) -> str:
    """A CREST conformer as the data file writes it: '1  0.0 kcal/mol  -42.17306 Eh  Population 0.2314  6 Rotamers'."""
    rotamers = record["rotamers"]
    return (f"{record['conformer']}  {_Number(record['energy'])} kcal/mol  {_Number(record['total'])} Eh  "
            f"Population {_Number(record['population'])}  {rotamers} Rotamer{'s' if rotamers != 1 else ''}")


def ParseConformer(text: str) -> dict:
    """A ConformerText line back as its record."""
    found = regex.match(CONFORMER_LINE, text)
    if found is None:
        return {"conformer": 0, "energy": 0.0, "total": 0.0, "population": 0.0, "rotamers": 0}
    return {"conformer": int(found.group(1)), "energy": float(found.group(2)), "total": float(found.group(3)),
            "population": float(found.group(4)), "rotamers": int(found.group(5))}


def EigenvectorText(record: dict) -> str:
    """A TS eigenvector's coordinate as the data file writes it: '-0.68211  Bond C12 C15 @ 2.1046 A'."""
    return f"{_Number(record['coefficient']):>9}  " + " ".join([record["coordinate"], *record["atoms"]]) + _At(record)


def _Lines(field: Field, value) -> list[str]:
    if field.kind in ROW_KINDS:
        label = f"{field.label} ({field.unit})" if field.unit else field.label
        if field.key == "coordinates":
            # 13 columns a number (Gaussian's 6 decimals), wider when one needs it (an .xyz's 10), so a space always
            # separates them
            width = max([13] + [len(number) + 1 for row in value for number in row.split()[1:]])
            rows = ["{:<3}".format(row.split()[0]) + "".join(f"{number:>{width}}" for number in row.split()[1:])
                    for row in value]
        elif field.kind == "constraints":
            rows = [ConstraintText(record) for record in value]
        elif field.kind == "eigenvector":
            rows = [EigenvectorText(record) for record in value]
        elif field.kind == "conformers":
            rows = [ConformerText(record) for record in value]
        else:
            rows = [" ".join(_Number(number) for number in value[start:start + FREQUENCIES_PER_ROW])
                    for start in range(0, len(value), FREQUENCIES_PER_ROW)]
        return [label] + [f"  {row}" for row in rows] if rows else [_Line(label, "None")]
    unit = f" {field.unit}" if field.unit else ""
    match field.kind:
        case "bool":
            text = "Yes" if value else "No"
        case "convergence":
            text = f"{value['met']} of {value['total']}"
        case "numbers":
            text = " ".join(_Number(number) for number in value) + unit if value else "None"
        case _:
            text = _Number(value) + unit
    return [_Line(field.label, text)]


def _Sections(path: Path, facts: dict) -> list[tuple[str, dict]]:
    """An output's facts (FileFacts, full) as one section per stage: each with the file's identity and file facts, its
    own stage facts, its file, stage number and count, and the file's stamp."""
    stages = facts["stages"] if isinstance((facts.get("stages") or [None])[0], dict) else [
        {key: value for key, value in facts.items() if key in SCOPE["stage"]}]
    shared = {key: value for key, value in facts.items() if key in SCOPE["identity"] + SCOPE["file"]}
    stamp = _Stamp(path)
    sections = []
    for number, stage in enumerate(stages, 1):
        section = {**shared, **{key: value for key, value in stage.items() if key != "stage"}}
        section.update(file=path.name, _stamp=stamp, _stage=stage.get("stage", number), _of=len(stages))
        sections.append((SectionTitle(stage), section))
    return sections


def _Lead(sections: list[tuple[str, dict]]) -> dict:
    # Whose identity (the final geometry) the first section shows: the Transition State's, else the Reaction
    # Intermediate's, else the ModRedundant's, else the highest-ranked job's (a DFT one before a CREST search: its
    # geometry is xTB's)
    held = {_Slot(title): section for title, section in sections}
    return next((held[slot] for slot in LEAD_ORDER if slot in held), None) or max(
        held.values(), key=lambda section: (section.get("runs") != CONFORMER_SEARCH, Rank(section)))


def _Render(name: str, sections: list[tuple[str, dict]]) -> str:
    """The whole data file: the identity section, then each stage's, every value it has (identity values only where
    its file differs from the lead's; Absent where it has none)."""
    if not sections:
        return ""
    lead = _Lead(sections)
    lines = [f"=== {name} ==="]
    for key in SCOPE["identity"]:
        if key in lead:
            lines += _Lines(BY_KEY[key], lead[key])
    for title, section in sections:
        size, modified = section["_stamp"]
        where = f"{section['file']}, Stage {section['_stage']} of {section['_of']}" if section["_of"] > 1 else \
            section["file"]
        lines += ["", f"=== {title} ===", _Line("From", f"{where} ({size} bytes, modified {modified}, "
                                                         f"reader {section.get('_reader', READER)})")]
        for field in FIELDS:
            if field.key in section and not (field.scope == "identity" and lead.get(field.key) == section[field.key]):
                lines += _Lines(field, section[field.key])
        lines += [_Line(BY_KEY[key].label, "Absent") for key in SCOPE["identity"] if key in lead and key not in section]
    return "\n".join(lines) + "\n"


def Record(path: Path, facts: dict) -> Path | None:
    """Writes an output's facts (FileFacts, full) into its data file, a section per stage: each replaces this file's
    earlier sections, and takes its place from another file's only when it outranks it (Rank), or that file is gone.
    Returns the data file, None for a fact that isn't an output's. Raises OSError when it can't be written."""
    path = Path(path)
    if facts.get("kind") != "output":
        return None
    dataPath = DataPath(path)
    with FileLock(dataPath.with_name(dataPath.name + ".lock")):
        sections = [section for section in _ReadSections(dataPath) if section[1]["file"] != path.name]
        for title, section in _Sections(path, facts):
            held = next((index for index, (other, _) in enumerate(sections) if _Slot(other) == _Slot(title)), None)
            if held is None:
                sections.append((title, section))
            elif not (path.parent / sections[held][1]["file"]).is_file() or Rank(section) >= Rank(sections[held][1]):
                sections[held] = (title, section)
        sections.sort(key=lambda section: _Order(section[0]))
        temporary = dataPath.with_name(dataPath.name + ".tmp")
        temporary.write_text(_Render(RootName(path.stem), sections), encoding="utf-8", newline="\n")
        os.replace(temporary, dataPath)
    return dataPath


# ─── Reading ───────────────────────────────────────────────────────────

def _Value(field: Field, text: str):
    if text == "None" and field.kind in ROW_KINDS + ("numbers",):
        return []
    if field.unit and text.endswith(" " + field.unit):
        text = text[:-len(field.unit) - 1]
    match field.kind:
        case "int":
            for number in (int, float):
                try:
                    return number(text)
                except ValueError:
                    pass
            return text
        case "float":
            return float(text)
        case "bool":
            return text == "Yes"
        case "convergence":
            met, total = text.split(" of ")
            return {"met": int(met), "total": int(total)}
        case "numbers":
            return [float(number) for number in text.split()]
    return text


def _Coordinate(found: regex.Match, first: int) -> tuple[dict, str | None]:
    # The coordinate, atoms and value groups of a CONSTRAINT_LINE / EIGENVECTOR_LINE match, as info.StageFacts records
    # them (the unit follows from the coordinate)
    record = {"coordinate": found.group(first), "atoms": found.group(first + 1).split()}
    return record, COORDINATE_UNITS.get(record["coordinate"])


def ParseConstraint(text: str) -> dict:
    """A ConstraintText line back as its record."""
    found = regex.match(CONSTRAINT_LINE, text)
    if found is None:
        return {"coordinate": text, "atoms": [], "action": ""}
    record, unit = _Coordinate(found, 1)
    if found.group(6) is not None:
        record["value"] = float(found.group(6))
    if unit:
        record["unit"] = unit
    record["action"] = found.group(3)
    if found.group(4) is not None:
        record["steps"], record["step"] = int(found.group(4)), float(found.group(5))
    if found.group(7):
        record["inherited"] = True
    return record


def ParseEigenvector(text: str) -> dict:
    """An EigenvectorText line back as its record."""
    found = regex.match(EIGENVECTOR_LINE, text)
    if found is None:
        return {"coefficient": 0.0, "coordinate": text, "atoms": []}
    record, unit = _Coordinate(found, 2)
    if found.group(4) is not None:
        record["value"] = float(found.group(4))
    if unit:
        record["unit"] = unit
    return {"coefficient": float(found.group(1)), **record}


def _ParseBlock(text: str) -> tuple[dict, set]:
    """A section's lines as facts (with its From line's file, stage and stamp), and the identity keys it marks Absent."""
    facts, absent, rows, rowField = {}, set(), [], None

    def Close():
        if rowField is None:
            return
        if rowField.key == "coordinates":
            facts[rowField.key] = rows[:]
        elif rowField.kind == "constraints":
            facts[rowField.key] = [ParseConstraint(row) for row in rows]
        elif rowField.kind == "eigenvector":
            facts[rowField.key] = [ParseEigenvector(row) for row in rows]
        elif rowField.kind == "conformers":
            facts[rowField.key] = [ParseConformer(row) for row in rows]
        else:
            facts[rowField.key] = [float(number) for row in rows for number in row.split()]
    for line in text.splitlines():
        if line.startswith("  ") and rowField is not None:
            rows.append(" ".join(line.split()))
            continue
        Close()
        rowField, rows = None, []
        if not line.strip():
            continue
        label, _, value = (part.strip() for part in _PAIR_SPLIT_RE.match(line).groups())
        if label == "From":
            found = _FROM_LINE_RE.match(value)
            facts.update(file=found.group(1), _stage=int(found.group(2) or 1), _of=int(found.group(3) or 1),
                         _stamp=(int(found.group(4)), found.group(5)), _reader=int(found.group(6) or 0))
            continue
        # A block's label can carry its unit: 'Frequencies (cm-1)'
        field = BY_LABEL.get(label) or BY_LABEL.get(_TRAILING_UNIT_RE.sub("", label))
        if field is None:
            continue
        if field.kind in ROW_KINDS and not value:
            rowField = field
        elif value == "Absent":
            absent.add(field.key)
        else:
            facts[field.key] = _Value(field, value)
    Close()
    return facts, absent


def _ReadSections(dataPath: Path) -> list[tuple[str, dict]]:
    """Every stage section of a data file, each with its full facts (the identity's filled in), in file order; [] for a
    missing, empty or unreadable one."""
    if not HasContent(dataPath):
        return []
    with MapFile(dataPath) as data:
        headers = list(regex.finditer(SECTION_HEADER.encode(), data))
        blocks = [(header.group(1).decode(errors="replace"),
                   data[header.end():headers[index + 1].start() if index + 1 < len(headers) else len(data)]
                   .decode(errors="replace")) for index, header in enumerate(headers)]
    if not blocks:
        return []
    lead, _ = _ParseBlock(blocks[0][1])
    sections = []
    for title, text in blocks[1:]:
        facts, absent = _ParseBlock(text)
        if "file" not in facts:
            continue
        identity = {key: value for key, value in lead.items() if key not in facts and key not in absent}
        sections.append((title, {**identity, **facts}))
    return sections


def Lookup(path: Path) -> dict | None:
    """An output's facts from its data file, as FileFacts gives them, while every one of its stages has its section
    and they still match the file (same size and modification time) and this reader (READER); None otherwise (no data
    file, a stage whose place another attempt holds, the output has changed since, or an older reader wrote it)."""
    path = Path(path)
    if not path.is_file():
        return None
    try:
        stamp = _Stamp(path)
        mine = sorted((section for _, section in _ReadSections(DataPath(path)) if section["file"] == path.name
                       and section["_stamp"][0] == stamp[0] and _When(section["_stamp"]) == _When(stamp)
                       and section.get("_reader") == READER),
                      key=lambda section: section["_stage"])
    except (OSError, ValueError, AttributeError):
        return None
    if not mine or [section["_stage"] for section in mine] != list(range(1, mine[0]["_of"] + 1)):
        return None
    first = mine[0]
    facts = {"file": path.name, "kind": "output",
             **{key: first[key] for key in SCOPE["identity"] + SCOPE["file"] if key in first}}
    stages = [{key: value for key, value in section.items() if key in SCOPE["stage"]} for section in mine]
    if len(stages) == 1:
        facts.update(stages[0])
    else:
        facts.update({key: stages[-1][key] for key in ("status", "termination") if key in stages[-1]})
        facts["stages"] = [{"stage": number, **stage} for number, stage in enumerate(stages, 1)]
    return facts


def ForReport(facts: dict) -> dict:
    """An output's facts without the blocks kept only in the data file (DATA_FILE_ONLY), for `cu -info`'s report."""
    trimmed = {key: value for key, value in facts.items() if key not in DATA_FILE_ONLY}
    if isinstance((trimmed.get("stages") or [None])[0], dict):
        trimmed["stages"] = [{key: value for key, value in stage.items() if key not in DATA_FILE_ONLY}
                             for stage in trimmed["stages"]]
    return trimmed


# ─── Writers ───────────────────────────────────────────────────────────

def Facts(path: Path) -> tuple[dict, Path | None]:
    """`cu -info`'s read of one file: an output answered from its data file while that's current, else read (FileFacts)
    and recorded. The facts (in full: ForReport trims them), and the data file (None for an input, or one that
    couldn't be written: a warning)."""
    path = Path(path)
    found = Lookup(path)
    if found is not None:
        return found, DataPath(path)
    facts = FileFacts(path)
    try:
        return facts, Record(path, facts)
    except OSError as error:
        agent.Warn(f"{DataPath(path)} couldn't be written: {error}")
        return facts, None


def RecordEndings(jobs) -> None:
    """Writes each ended job's output into its data file, unless it's already current there (a read of the data file
    then). A stalker's ping, CompUtils' startup check, and the Neva-Touch Scheduler's hook call it; it never stops them."""
    for job in jobs:
        path = Path(job.outputPath)
        try:
            if not job.ended or not HasContent(path) or Lookup(path) is not None:
                continue
            Record(path, FileFacts(path))
        except Exception as error:  # a summary must never stop a stalker or a hook
            console.print(f"[warning]{DataPath(path)} couldn't be written: {error}[/warning]")


# ─── The check ─────────────────────────────────────────────────────────

def Check(folder: Path, scratch: Path) -> list[str]:
    """Problems: a FileFacts key (a record's or a stage's) with no FIELDS entry, and any output in folder whose facts
    don't come back from its data file as read (written into scratch, beside a copy of the output)."""
    import shutil
    problems = []
    for output in sorted(folder.glob(f"*{Defaults.outputExtension}")):
        copy = scratch / output.name
        shutil.copy2(output, copy)
        # A CREST search's lowest conformer, where its output finds it (<name>-scratch/, or beside it)
        best = ResultFolder(output) / BEST_FILE
        if best.is_file():
            target = scratch / best.relative_to(output.parent)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(best, target)
        facts = FileFacts(copy)
        records = [facts] + [stage for stage in facts.get("stages", []) if isinstance(stage, dict)]
        for key in {key for record in records for key in record}:
            if key not in BY_KEY and key not in META:
                problems.append(f"{output.name}: the fact {key!r} has no FIELDS entry (datafile.py).")
        Record(copy, facts)
        back = Lookup(copy)
        if back != facts:
            differ = sorted(key for key in set(facts) | set(back or {}) if (back or {}).get(key) != facts.get(key))
            problems.append(f"{output.name}: the data file gives back different facts: {', '.join(differ)}.")
    return problems


if __name__ == "__main__":
    import tempfile
    from .catalog import Catalog
    Defaults.Load()
    Catalog.Load()
    # The bundled samples when the checkout has them, and any folders of outputs named on the command line
    folders = [Path(__file__).parent / "simulations", *(Path(argument) for argument in sys.argv[1:])]
    found = []
    for folder in (folder for folder in folders if folder.is_dir()):
        with tempfile.TemporaryDirectory() as scratch:
            found += Check(folder, Path(scratch))
    for problem in found:
        print(problem)
    print("Data file check: " + ("passed." if not found else f"{len(found)} problem(s)."))
    sys.exit(1 if found else 0)
