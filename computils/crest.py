"""CREST conformer searches, with no Textual import: `cu -crest`, `cu -re` on a CREST output, and the TUI's Conformer
Search builder build a CrestJob here, check it with the same checks, write the same files and submit them the same
way (tracked like any job).

A search of 8a is the job 8a_CREST (Defaults.crestExtra), so its output feeds 8a.cudf:
    8a_CREST.xyz            the starting geometry
    8a_CREST.constraints    the constraints, in xtb's detailed-input ("xcontrol") format (only when there are any)
    8a_CREST.cmd            the job script: the submission header, crestNonVariant, then crest run in node scratch
    8a_CREST.out            CREST's output (SLURM -o)
    8a_CREST-scratch/       CREST's result files (crestResultFiles), copied back however the job ends

CREST's options are a Conformer Search Methods entry (keylists.cusf: '# @gfn2' then '--gfn2 --ewin 8 ...') or a line
typed in full; CompUtils writes the threads, charge, unpaired electrons and the constraints file itself (RESERVED).
Constraints are typed in the ModRed letters the lab already writes ('B 12 15', 'A 1 2 3', 'D 1 2 3 4', 'atoms 11-14',
'elements Pd', 'fix 2,4,7,34'); a bond, angle or dihedral without a value gets the starting geometry's, computed here
(the lab never uses xtb's 'auto').

Reading a CREST output (CrestFacts, for info.FileFacts) takes only what CREST printed: the command line it ran, the
constraints file it read (its echo, which is what the run used), the final ensemble (CREGEN) and the wall time. The
lowest conformer's geometry comes from crest_best.xyz, in <name>-scratch/ or beside the output (the lab's own layout)."""
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path

import regex

from .defaults import Defaults
from .fileops  import (CREST_PROGRAM, TERMINATION_TAIL, ElectronProblem, ExtractFrom, ExtractTermination, ExtractXyz,
                       FindInMap, Formula, HasContent, Symbols, fileCreation)
from .molecule import Molecule

# What a CREST job runs, as the stalker plans its one stage and the data file names its section
CONFORMER_SEARCH = "Conformer Search"
# CREST's flags CompUtils writes itself (without their dashes: CREST takes -chrg and --chrg alike)
RESERVED = {"t": "--T", "threads": "--T", "chrg": "--chrg", "uhf": "--uhf", "cinp": "--cinp", "scratch": "--scratch"}
# The tight-binding levels, by flag
LEVELS = {"gfn2": "GFN2-xTB", "gfn1": "GFN1-xTB", "gfn0": "GFN0-xTB", "gfnff": "GFN-FF", "gff": "GFN-FF",
          "gfn2//gfnff": "GFN2-xTB//GFN-FF"}
# Implicit solvation, by flag
SOLVATION = {"alpb": "ALPB", "gbsa": "GBSA", "g": "GBSA"}
# A constraint line's letter: xtb's keyword and how many atoms it names
GEOMETRY_CONSTRAINTS = {"B": ("distance", 2, "Bond"), "A": ("angle", 3, "Angle"), "D": ("dihedral", 4, "Dihedral")}
LIST_CONSTRAINTS = ("atoms", "elements", "fix")
CONSTRAINTS_SUFFIX = ".constraints"
DEFAULT_FORCE_CONSTANT = 1.0
BEST_FILE = "crest_best.xyz"
CONFORMERS_FILE = "crest_conformers.xyz"
CUSTOM = "Custom"


def Flag(token: str) -> str:
    """A command-line token's flag without its dashes, lower case ('' for a value)."""
    return token.lstrip("-").lower() if token.startswith("-") and not regex.fullmatch(r"-[\d.]+", token) else ""


def _FlagValue(tokens: list[str], *names: str) -> str | None:
    """The value after the first of these flags, or None without one."""
    for index, token in enumerate(tokens[:-1]):
        if Flag(token) in names:
            return tokens[index + 1]
    return None


# ─── An option line as settings (the Conformer Search builder's rows) ──
# The levels a search picks from, in the builder's order ('' writes none: CREST's own default, GFN2-xTB)
LEVEL_CHOICES = (("", "CREST's Default"), ("gfn2", "GFN2-xTB"), ("gfn1", "GFN1-xTB"), ("gfnff", "GFN-FF"),
                 ("gfn2//gfnff", "GFN2-xTB//GFN-FF"))
# The flags with a value the builder shows as rows: (flag, label, unit). Empty writes none (CREST's default)
VALUE_OPTIONS = (("ewin", "Energy Window", "kcal/mol"), ("rthr", "RMSD Threshold", "A"),
                 ("ethr", "Energy Threshold", "kcal/mol"), ("bthr", "Bconst Threshold", ""))
# The switches it shows as checkboxes: (flag, label)
FLAG_OPTIONS = (("noreftopo", "No Ref Topo"), ("notopo", "No Topo"), ("cmetal", "Constrain Metals"))
# The project's solvent, as an option line writes it (solvent.py; RenderOptions fills it in)
PROJECT_SOLVENT = "{alpb}"


@dataclass
class OptionFields:
    """An option line split into what the builder shows as rows. level: a LEVEL_CHOICES flag ('' none); solvent:
    PROJECT_SOLVENT, an ALPB solvent name, or '' (the gas phase); values: {VALUE_OPTIONS flag: text}; flags: the
    FLAG_OPTIONS set; other: every other token, as written."""
    level: str = ""
    solvent: str = ""
    values: dict = field(default_factory=dict)
    flags: set = field(default_factory=set)
    other: str = ""


def SplitOptions(options: str) -> OptionFields:
    """An option line as OptionFields; JoinOptions writes it back (in its own order: level, values, switches, the
    rest, the solvent)."""
    tokens, fields, other, index = options.split(), OptionFields(), [], 0
    levels, values = dict(LEVEL_CHOICES), {flag for flag, *_ in VALUE_OPTIONS}
    flags = {flag for flag, _ in FLAG_OPTIONS}
    while index < len(tokens):
        token, name = tokens[index], Flag(tokens[index])
        follows = index + 1 < len(tokens) and not Flag(tokens[index + 1])
        if token.lower() == PROJECT_SOLVENT:
            fields.solvent = PROJECT_SOLVENT
        elif name and name in levels and not fields.level:
            fields.level = name
        elif name == "alpb" and follows:
            fields.solvent, index = tokens[index + 1], index + 1
        elif name in values and follows:
            fields.values[name], index = tokens[index + 1], index + 1
        elif name in flags:
            fields.flags.add(name)
        else:
            other.append(token)
        index += 1
    fields.other = " ".join(other)
    return fields


def JoinOptions(fields: OptionFields) -> str:
    """OptionFields as an option line."""
    words = [f"--{fields.level}"] if fields.level else []
    for flag, *_ in VALUE_OPTIONS:
        if fields.values.get(flag, "").strip():
            words += [f"--{flag}", fields.values[flag].strip()]
    words += [f"--{flag}" for flag, _ in FLAG_OPTIONS if flag in fields.flags]
    words += fields.other.split()
    if fields.solvent == PROJECT_SOLVENT:
        words.append(PROJECT_SOLVENT)
    elif fields.solvent.strip():
        words += ["--alpb", fields.solvent.strip()]
    return " ".join(words)


def SameOptions(first: str, second: str) -> bool:
    """Whether two option lines ask for the same search (written in any order)."""
    return JoinOptions(SplitOptions(first)) == JoinOptions(SplitOptions(second))


def Level(options: str) -> str:
    """The tight-binding level an option line asks for ('' when it names none: CREST's own default)."""
    return next((LEVELS[Flag(token)] for token in options.split() if Flag(token) in LEVELS), "")


def Solvation(options: str) -> str:
    """'ALPB (toluene)', 'GBSA (water)', or '' for the gas phase. {alpb} reads as the project's solvent."""
    from .solvent import ALPB_TAG, HasTag, ProjectSolvent
    if HasTag(options, ALPB_TAG) and not ProjectSolvent():
        return "ALPB (the Project's Solvent: None Set Here)"
    tokens = RenderOptions(options).split()
    for index, token in enumerate(tokens[:-1]):
        if Flag(token) in SOLVATION:
            return f"{SOLVATION[Flag(token)]} ({tokens[index + 1]})"
    return ""


def RenderOptions(options: str) -> str:
    """An option line as CREST gets it: {alpb} becomes the project's solvent ('--alpb thf'; nothing in the gas phase or
    for a solvent ALPB lacks, which OptionsProblem reports). solvent.py."""
    from .solvent import ALPB_TAG, AlpbOptions
    return " ".join(regex.sub(r"(?i)\{" + ALPB_TAG + r"\}", AlpbOptions(), options).split())


def OptionsProblem(options: str) -> str:
    """Why an option line can't be used ('' if it can): the project's solvent where it asks for one ({alpb}) that ALPB
    can't write, a tag CREST has none of, a flag CompUtils writes itself, or an input file."""
    from .solvent import ALPB_TAG, HasTag, Problem
    if HasTag(options, ALPB_TAG) and Problem("alpb"):
        return Problem("alpb")
    stray = regex.findall(r"\{([\w-]+)\}", regex.sub(r"(?i)\{" + ALPB_TAG + r"\}", "", options))
    if stray:
        return f"Unknown Tag {{{stray[0]}}} (CREST Takes Only {{{ALPB_TAG}}})"
    for token in RenderOptions(options).split():
        name = Flag(token)
        if name in RESERVED:
            return f"{RESERVED[name]} Is Written by CompUtils"
        if not name and token.lower().endswith((".xyz", ".toml", ".coord")):
            return f"{token}: CompUtils Writes the Input"
    return ""


def WithoutReserved(options: str) -> str:
    """An option line without the flags CompUtils writes (and their values): a re-run's options as its output ran."""
    tokens, kept, skip = options.split(), [], False
    for index, token in enumerate(tokens):
        if skip:
            skip = False
            continue
        if Flag(token) in RESERVED:
            # --T 16, --chrg -1: the flag and its value
            skip = index + 1 < len(tokens) and not Flag(tokens[index + 1])
            continue
        kept.append(token)
    return " ".join(kept)


def Methods(texts: dict | None = None) -> dict[str, str]:
    """The Conformer Search Methods in force ({name: options}): the nearest level's, else the default search."""
    from .keylists import CONFORMER_SEARCH as SECTION, Keylist
    return Keylist(SECTION, texts)


# ─── Constraints ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class Constraint:
    """One typed constraint line: a bond, angle or dihedral (kind B/A/D, its atoms, its value or None for the starting
    geometry's), or a list (kind atoms/elements/fix: atom numbers, or element symbols)."""
    kind:   str
    atoms:  tuple = ()
    value:  float | None = None
    names:  tuple = ()


def _AtomList(text: str) -> list[int] | None:
    """'11-14,20' or '2 4 7' as numbers, None if it isn't one."""
    numbers = []
    for part in regex.split(r"[\s,]+", text.strip()):
        if not part:
            continue
        found = regex.fullmatch(r"(\d+)(?:-(\d+))?", part)
        if found is None:
            return None
        first, last = int(found.group(1)), int(found.group(2) or found.group(1))
        if last < first:
            return None
        numbers += range(first, last + 1)
    return numbers or None


def ConstraintLines(text: str) -> list[str]:
    """Typed constraints as lines: one per line, or separated by ';' (the command line's form). A line starting with
    '#' is a comment (the TUI's suggestions from the source, until uncommented)."""
    return [line.strip() for line in regex.split(r"[;\n]", text or "")
            if line.strip() and not line.strip().startswith("#")]


def ParseConstraints(text: str, atoms: int) -> tuple[list[Constraint], list[str]]:
    """(the constraints, problems: 'Line N: ...') of typed constraint lines, atom numbers checked against the geometry
    (1 to atoms, the .xyz's order)."""
    constraints, problems = [], []
    for number, line in enumerate(ConstraintLines(text), 1):
        fields = line.replace(":", " ").split()
        kind = fields[0]
        if kind.upper() in GEOMETRY_CONSTRAINTS:
            _, count, name = GEOMETRY_CONSTRAINTS[kind.upper()]
            numbers, rest = fields[1:count + 1], fields[count + 1:]
            if len(numbers) < count or not all(field.isdigit() for field in numbers) or len(rest) > 1:
                problems.append(f"Line {number}: {kind.upper()} Takes {count} Atom Numbers, Then a Value or Nothing")
                continue
            try:
                value = float(rest[0]) if rest else None
            except ValueError:
                problems.append(f"Line {number}: '{rest[0]}' Isn't a Value")
                continue
            found = tuple(int(field) for field in numbers)
            outside = [str(atom) for atom in found if not 1 <= atom <= atoms]
            if outside:
                problems.append(f"Line {number}: Atom {', '.join(outside)} Isn't in the Geometry (1–{atoms})")
                continue
            if len(set(found)) < count:
                problems.append(f"Line {number}: A {name} Needs {count} Different Atoms")
                continue
            constraints.append(Constraint(kind.upper(), found, value))
        elif kind.lower() in LIST_CONSTRAINTS:
            rest = line.split(None, 1)[1].lstrip(":") if len(fields) > 1 else ""
            if kind.lower() == "elements":
                names = tuple(name.capitalize() for name in regex.split(r"[\s,]+", rest.strip()) if name)
                if not names:
                    problems.append(f"Line {number}: Elements Takes Element Symbols (elements Pd)")
                    continue
                constraints.append(Constraint("elements", names=names))
                continue
            found = _AtomList(rest)
            if found is None:
                problems.append(f"Line {number}: {kind.capitalize()} Takes Atom Numbers (e.g. {kind.lower()} 11-14,20)")
                continue
            outside = sorted({str(atom) for atom in found if not 1 <= atom <= atoms}, key=int)
            if outside:
                problems.append(f"Line {number}: Atom {', '.join(outside)} Isn't in the Geometry (1–{atoms})")
                continue
            constraints.append(Constraint(kind.lower(), tuple(found)))
        else:
            problems.append(f"Line {number}: Unknown Constraint '{kind}' (B, A, D, atoms, elements or fix)")
    return constraints, problems


COORDINATE_LETTERS = {"Bond": "B", "Angle": "A", "Dihedral": "D"}


def Suggestions(facts: dict) -> list[str]:
    """Constraint lines a prior job suggests (info.FileFacts of its output), as comments for the human to keep (remove
    the #) or delete: its TS eigenvector's bonds and its ModRed constraints (their own choice for that job), each noted
    with what the output printed for it, the last stage first (its geometry is the one searched from; a line held
    without a value holds that geometry's). Never applied by themselves: which bonds a search holds is the human's
    call."""
    stages = facts["stages"] if isinstance((facts.get("stages") or [None])[0], dict) else [facts]
    found: dict[str, list[str]] = {}

    def Add(record: dict, note: str) -> None:
        letter = COORDINATE_LETTERS.get(record.get("coordinate", ""))
        numbers = [regex.sub(r"\D", "", atom) for atom in record.get("atoms", [])]
        if letter and all(numbers):
            found.setdefault(f"{letter} {' '.join(numbers)}", []).append(note)
    for number, stage in reversed(list(enumerate(stages, 1))):
        where = f"Stage {number} " if len(stages) > 1 else ""
        for record in stage.get("tsEigenvector", []):
            if record.get("coordinate") == "Bond":
                value = f" @ {record['value']:g} A" if "value" in record else ""
                Add(record, f"{where}TS Eigenvector {record['coefficient']:+.5f}{value}")
        for record in stage.get("constraints", []):
            value = f" @ {record['value']:g} {record.get('unit', '')}".rstrip() if "value" in record else ""
            Add(record, f"{where}ModRed {record.get('action', '')}{value}")
    return [f"# {line:<12} {'; '.join(notes)}" for line, notes in found.items()]


def _Point(coordinateList: list[str], atom: int) -> tuple[float, float, float]:
    x, y, z = (float(value) for value in coordinateList[atom - 1].split()[1:4])
    return x, y, z


def _Minus(a, b):
    return tuple(p - q for p, q in zip(a, b))


def _Dot(a, b) -> float:
    return sum(p * q for p, q in zip(a, b))


def _Cross(a, b):
    return a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]


def GeometryValue(coordinateList: list[str], kind: str, atoms: tuple[int, ...]) -> float:
    """A bond length (Å), angle or dihedral (degrees, -180 to 180) in this geometry."""
    points = [_Point(coordinateList, atom) for atom in atoms]
    if kind == "B":
        return math.dist(points[0], points[1])
    if kind == "A":
        first, second = _Minus(points[0], points[1]), _Minus(points[2], points[1])
        cosine = _Dot(first, second) / math.sqrt(_Dot(first, first) * _Dot(second, second))
        return math.degrees(math.acos(max(-1.0, min(1.0, cosine))))
    b1, b2, b3 = _Minus(points[1], points[0]), _Minus(points[2], points[1]), _Minus(points[3], points[2])
    n1, n2 = _Cross(b1, b2), _Cross(b2, b3)
    m1 = _Cross(n1, tuple(value / math.sqrt(_Dot(b2, b2)) for value in b2))
    return math.degrees(math.atan2(_Dot(m1, n2), _Dot(n1, n2)))


def _Ranges(numbers: tuple[int, ...]) -> str:
    """1,2,3,4,7 as '1-4,7' (xtb's list form)."""
    parts, start = [], None
    for index, number in enumerate(numbers):
        if start is None:
            start = number
        if index + 1 == len(numbers) or numbers[index + 1] != number + 1:
            parts.append(str(start) if start == number else f"{start}-{number}")
            start = None
    return ",".join(parts)


def ConstraintsText(constraints: list[Constraint], coordinateList: list[str], forceConstant: float) -> str:
    """The constraints file (xtb's detailed input, which CREST reads with --cinp): a $constrain block (its force
    constant, each bond/angle/dihedral at its value, atoms and elements), then a $fix block for fixed atoms. '' without
    constraints."""
    constrain, fixed = [], []
    for constraint in constraints:
        if constraint.kind in GEOMETRY_CONSTRAINTS:
            keyword = GEOMETRY_CONSTRAINTS[constraint.kind][0]
            value = (constraint.value if constraint.value is not None
                     else round(GeometryValue(coordinateList, constraint.kind, constraint.atoms), 4))
            constrain.append(f"   {keyword}: {', '.join(map(str, constraint.atoms))}, {float(value)!r}")
        elif constraint.kind == "atoms":
            constrain.append(f"   atoms: {_Ranges(constraint.atoms)}")
        elif constraint.kind == "elements":
            constrain.append(f"   elements: {','.join(constraint.names)}")
        else:
            fixed.append(f"   atoms: {_Ranges(constraint.atoms)}")
    lines = []
    if constrain:
        lines += ["$constrain", f"   force constant={float(forceConstant)!r}", *constrain, "$end"]
    if fixed:
        lines += ["$fix", *fixed, "$end"]
    return "\n".join(lines) + "\n" if lines else ""


# ─── One search ───────────────────────────────────────────────────────

def EnvironmentCrest() -> Path:
    """CompUtils' own environment's crest (crestExecutable empty)."""
    return Path(sys.prefix) / "bin" / "crest"


def JobName(name: str) -> str:
    """A species' search: 8a -> 8a_CREST. A search's own name is kept: one ending in the suffix, or in the suffix and a
    re-run's (8a_CREST_re, 8a_CREST_re2). Every CrestJob's name goes through it, however it was typed, so a search
    always feeds its species' data file (datafile.RootName strips the suffix) and is always known as a search."""
    pattern = regex.escape(Defaults.crestExtra) + "(?:" + regex.escape(Defaults.reRunExtra) + r"\d*)?"
    return name if regex.search(pattern + "$", name) else name + Defaults.crestExtra


@dataclass
class CrestJob:
    """Everything a conformer search's settings say. charge/multiplicity None: not valid yet (the TUI, mid-typing).
    options: CREST's option line (method: its Conformer Search Methods name, or Custom). constraints: the typed lines;
    constraintsText: a constraints file as written (a re-run's, carried over), used when no lines are typed. memory:
    GB per core (None: the config's memoryRatio). wallTime/partition/cluster: the job's SLURM settings (None: the
    config's)."""
    molecule:        Molecule
    name:            str
    charge:          int | None
    multiplicity:    int | None
    method:          str
    options:         str
    constraints:     str          = ""
    forceConstant:   float        = DEFAULT_FORCE_CONSTANT
    cpus:            int          = 0
    memory:          float | None = None
    wallTime:        str | None   = None
    partition:       str | None   = None
    cluster:         str | None   = None
    constraintsText: str          = ""

    def __post_init__(self) -> None:
        # The species' search, however its name was typed (8a -> 8a_CREST; JobName)
        self.name = JobName(self.name)

    # ─── Names ───
    def File(self, extension: str, extra: str = "") -> Path:
        return Path(fileCreation(self.name, extension, extra))

    @property
    def xyz(self) -> Path:
        return self.File(Defaults.coordExtension)

    @property
    def constraintsFile(self) -> Path:
        return self.File(CONSTRAINTS_SUFFIX)

    @property
    def script(self) -> Path:
        return self.File(Defaults.queueExtension)

    @property
    def output(self) -> Path:
        return self.File(Defaults.outputExtension)

    @property
    def resultsFolder(self) -> Path:
        return self.File("", Defaults.scratchFolderExtra)

    # ─── Settings ───
    @property
    def uhf(self) -> int:
        return int(self.multiplicity or self.molecule.multiplicity or 1) - 1

    @property
    def memoryRatio(self) -> float:
        return self.memory if self.memory is not None else Defaults.memoryRatio

    @property
    def jobRam(self) -> int:
        return int(self.cpus * self.memoryRatio + Defaults.memoryBuffer)

    def Apply(self) -> None:
        """The settings onto the molecule (a copy the job owns): charge, multiplicity, then its name, program and
        starting geometry's path."""
        molecule = self.molecule
        if self.charge is not None:
            molecule.charge, molecule.multiplicity = self.charge, self.multiplicity
        molecule.baseName, molecule.extensionType, molecule.fullPath = self.name, CREST_PROGRAM, self.xyz

    def ParsedConstraints(self) -> tuple[list[Constraint], list[str]]:
        return ParseConstraints(self.constraints, len(self.molecule.coordinateList or []))

    def ConstraintsFileText(self) -> str:
        """The constraints file as it will be written ('' for none): the typed lines, else a re-run's as written."""
        constraints, problems = self.ParsedConstraints()
        if constraints and not problems:
            return ConstraintsText(constraints, self.molecule.coordinateList, self.forceConstant)
        return "" if ConstraintLines(self.constraints) else self.constraintsText

    def Check(self) -> tuple[list[str], list[str]]:
        """(errors, warnings) after Apply. Errors stop the job; warnings don't."""
        from .actions  import SpinState
        from .optimize import SlurmProblems
        molecule, errors, warnings = self.molecule, [], []
        if not molecule.coordinateList:
            errors.append("No Coordinates")
        if self.cpus < 1:
            errors.append("CPUs Must Be a Whole Number Above 0")
        if self.memory is not None and self.memory <= 0:
            errors.append("Memory Must Be a Number Above 0")
        if self.forceConstant <= 0:
            errors.append("The Force Constant Must Be Above 0")
        errors += SlurmProblems(self.wallTime, self.partition, self.cluster)
        if self.charge is not None and molecule.coordinateList:
            electrons = ElectronProblem(molecule.coordinateList, self.charge, self.multiplicity)
            if electrons:
                errors.append(electrons)
        problem = OptionsProblem(self.options)
        if problem:
            errors.append(problem)
        errors += self.ParsedConstraints()[1]
        if (self.multiplicity or 1) == 1 and molecule.spinState == SpinState.OSS:
            warnings.append("xTB Has No Broken-Symmetry Singlets: Searched as a Closed Shell")
        return errors, warnings

    # ─── The job script ───
    @property
    def executable(self) -> str:
        return Defaults.crestExecutable or str(EnvironmentCrest()).replace("\\", "/")

    def CommandWords(self) -> list[str]:
        charge = self.charge if self.charge is not None else self.molecule.charge
        words = [self.executable, str(self.xyz), "--T", str(self.cpus), "--chrg", str(charge), "--uhf", str(self.uhf)]
        if self.ConstraintsFileText():
            words += ["--cinp", str(self.constraintsFile)]
        return words + RenderOptions(self.options).split()

    def CommandLine(self) -> str:
        return " ".join(self.CommandWords())

    def RunText(self) -> str:
        """The script after the submission header: crestNonVariant, the threads, CompUtils' environment on PATH (its
        crest), then crest run in node scratch with its result files copied back."""
        from .jobs import ScratchRunScript
        lines = ["".join(Defaults.crestNonVariant),
                 "# CREST's threads (--T) and OpenMP's and MKL's match the job's CPUs\n",
                 f"export MKL_NUM_THREADS={self.cpus}\nexport OMP_NUM_THREADS={self.cpus},1\n",
                 # conda-forge's xtb links OpenBLAS's pthreads build: called from xtb's OpenMP threads, a threaded
                 # OpenBLAS prints "Detect OpenMP Loop and this application may hang" on every call (800,000+ lines,
                 # 100 MB, in a 26-minute run) and oversubscribes the CPUs. The parallelism is OpenMP's, so BLAS runs
                 # one thread per call
                 "# OpenBLAS single-threaded inside CREST's OpenMP threads (else a warning per call, and "
                 "oversubscribed CPUs)\nexport OPENBLAS_NUM_THREADS=1\n"]
        if not Defaults.crestExecutable:
            lines.append("# CompUtils' environment's CREST, with its xtb\n"
                         f"export PATH=\"{Path(self.executable).parent.as_posix()}:$PATH\"\n")
        inputs = [str(self.xyz)] + ([str(self.constraintsFile)] if self.ConstraintsFileText() else [])
        lines.append("\n" + ScratchRunScript(inputs, "CREST", str(self.resultsFolder), Defaults.crestResultFiles,
                                             self.CommandLine()))
        return "".join(lines)

    def ScriptText(self) -> str:
        from .jobs import SlurmHeaderText
        return (SlurmHeaderText(self.name, self.output, self.cpus, self.jobRam, self.wallTime, self.partition,
                                self.cluster) + self.RunText())

    def Write(self, folder: Path | None = None) -> list[Path]:
        """Writes the starting geometry, the constraints file (a stale one of this name is removed) and the script into
        folder (default: the CWD). Returns what was written."""
        from .fileops import WriteXyz
        folder = Path(folder or Path.cwd())
        written = [folder / self.xyz]
        WriteXyz(self.molecule, written[0])
        constraints, path = self.ConstraintsFileText(), folder / self.constraintsFile
        if constraints:
            path.write_text(constraints, encoding="utf-8", newline="\n")
            written.append(path)
        elif path.exists():
            path.unlink()
        script = folder / self.script
        script.write_text(self.ScriptText(), encoding="utf-8", newline="\n")
        written.append(script)
        return written

    def Result(self) -> dict:
        """The job as a report's result."""
        from .jobs import HeaderSends
        result = {"job": self.name, "xyz": str(self.xyz), "method": self.method, "options": self.options,
                  "charge": self.charge, "multiplicity": self.multiplicity, "uhf": self.uhf,
                  "level": Level(self.options) or "CREST's Default (GFN2-xTB)",
                  "solvation": Solvation(RenderOptions(self.options)),
                  "command": self.CommandLine(), "cpus": self.cpus, "memoryPerCore": self.memoryRatio,
                  "wallTimeHours": self.wallTime or Defaults.wallTime, "partition": self.partition or Defaults.partition,
                  "output": str(self.output), "results": str(self.resultsFolder)}
        constraints = self.ConstraintsFileText()
        if constraints:
            result["constraintsFile"], result["constraints"] = str(self.constraintsFile), constraints.splitlines()
        if HeaderSends("cluster"):
            result["cluster"] = self.cluster or Defaults.cluster
        return {key: value for key, value in result.items() if value not in ("", None)}


def Submit(job: CrestJob, trackedJobs: list, folder: Path | None = None) -> bool:
    """Writes the job's files and submits its script (jobs.SubmitJob: --dry-run stops after writing), tracked like any
    job: one stage, planned Conformer Search. False if it wasn't submitted (a dry run counts as submitted)."""
    from .          import agent
    from .console   import console
    from .jobs      import DRY_RUN, SubmitJob
    from .stalk     import Stage, TrackedJob, TrackJobs, jobLocation
    import time
    folder = Path(folder or Path.cwd())
    usesEnvironment = not Defaults.crestExecutable
    if usesEnvironment and not EnvironmentCrest().exists():
        if not agent.dryRun:
            agent.Skip(job.name, "CREST isn't installed in this environment (cu -env refresh)")
            return False
        agent.Warn("CREST isn't installed in this environment (cu -env refresh): the script is written, but it couldn't "
                   "run from here.")
    for path in job.Write(folder)[:-1]:
        agent.Written(path)
    submitted = SubmitJob(folder / job.script)
    if submitted is None:
        console.print(f"[error]Skipping {job.name}.[/error]")
        return False
    if submitted == DRY_RUN:
        return True
    jobID, cluster = submitted
    outputPath = (folder / job.output).resolve()
    console.print(f"[good]Submitted job {job.name} to CREST (ID {jobID})[/good]")
    agent.Submitted(job.name, jobID, cluster, str(outputPath), "")
    tracked = TrackedJob(jobID, cluster, job.name, str(outputPath), jobLocation(job.partition, job.cluster),
                         CREST_PROGRAM, time.time(), [Stage(planned=CONFORMER_SEARCH)],
                         runsFrom=str(Path(sys.prefix)) if usesEnvironment else "")
    TrackJobs([tracked])
    trackedJobs.append(tracked)
    return True


# ─── Reading a CREST output ───────────────────────────────────────────

# The command it ran: '> crest ...' (2.x), '$ /path/crest ...' (3.x)
COMMAND_LINE = rb"Command line input:\s*\r?\n\s*[>$][ \t]*([^\r\n]*)"
CONSTRAINT_ECHO = rb"content of the constraining file \(sorted\):\s*\r?\n((?:>[^\n]*\n)+)"
# The stalker's progress: CREST's phase headers as it titles them, the last one read backwards (each led by a literal)
# (2.x and 3.x name some differently: 'MTD Iteration 2' / 'Meta-Dynamics Iteration 2', 'Multilevel Optimization' /
# 'Multilevel Ensemble Optimization', 'Starting a trial MTD' / 'Starting trial MTD')
PHASES = r"MTD Iteration(?=\s+\d)|Meta-Dynamics Iteration(?=\s+\d)|Multilevel Optimization|" \
         r"Multilevel Ensemble Optimization|Structure Crossing \(GC\)|Final Geometry Optimization|" \
         r"Starting a trial MTD|Starting trial MTD|xTB Geometry Optimization|Initial Geometry Optimization|" \
         r"Additional regular MDs"
# How a phase reads in the stalker's report
PHASE_NAMES = {"Meta-Dynamics Iteration": "MTD Iteration",
               "Multilevel Ensemble Optimization": "Multilevel Optimization",
               "Starting a trial MTD": "the Trial MTD", "Starting trial MTD": "the Trial MTD",
               "Initial Geometry Optimization": "the Initial Optimization",
               "xTB Geometry Optimization": "the Initial Optimization", "Additional regular MDs": "the Additional MDs"}
# The final ensemble's values, CREST's own words: (key, pattern, how many values)
FINAL_VALUES = (("temperature", r"T /K(?=\s*:\s*(\S+))"),
                ("lowestEnergy", r"E lowest(?=\s*:\s*(\S+))"),
                ("ensembleAverageEnergy", r"ensemble average energy \(kcal\)(?=\s*:\s*(\S+))"),
                ("ensembleEntropy", r"ensemble entropy \(J/mol K, cal/mol K\)(?=\s*:\s*\S+\s+(\S+))"),
                ("ensembleFreeEnergy", r"ensemble free energy \(kcal/mol\)(?=\s*:\s*(\S+))"),
                ("populationOfLowest", r"population of lowest in %(?=\s*:\s*(\S+))"),
                ("conformers", r"number of unique conformers for further calc(?=\s+(\d+))"),
                ("energyWindow", r"(?:conformer energy window\s+/kcal|sorting energy window \(EWIN\))"
                                 r"(?=\s*:\s*(\S+))"),
                ("rmsdThreshold", r"RMSD threshold(?=\s*:\s*(\S+))"),
                ("rotConstThreshold", r"Bconst threshold(?=\s*:\s*(\S+))"))


def ResultFolder(output: Path) -> Path:
    """Where a search's result files are: <name>-scratch/ (a CompUtils job), else beside the output (the lab's own
    runs: crest.out beside crest_best.xyz)."""
    output = Path(output)
    scratch = output.parent / fileCreation(output.stem, "", Defaults.scratchFolderExtra)
    return scratch if (scratch / BEST_FILE).is_file() else output.parent


def _Number(text: str):
    for number in (int, float):
        try:
            return number(text)
        except ValueError:
            pass
    return text


def ExtractCommandLine(data) -> list[str]:
    """The command CREST echoes it was run with, as tokens ([] if it isn't written yet)."""
    found = regex.search(COMMAND_LINE, data)
    return found.group(1).decode(errors="replace").split() if found else []


def ExtractConstraintEcho(data) -> list[str]:
    """The constraints file CREST read, as it echoes it (sorted), without the '> ' prefixes."""
    found = regex.search(CONSTRAINT_ECHO, data)
    if found is None:
        return []
    return [line.lstrip(">").strip() for line in found.group(1).decode(errors="replace").splitlines() if line.strip(">").strip()]


def ConstraintRecords(echo: list[str], symbols: list[str]) -> tuple[list[dict], float | None]:
    """The echoed constraints as the data file's Constraints rows ({coordinate, atoms, action, value, unit}: 'Bond C1
    C9 Constrained @ 2.512 A'), and the force constant. Atoms are named by element when the geometry is known."""
    def Name(number: int) -> str:
        return f"{symbols[number - 1]}{number}" if 0 < number <= len(symbols) else str(number)
    records, forceConstant, block = [], None, ""
    for line in echo:
        if line.startswith("$"):
            block = line.lower()
            continue
        keyword, _, value = line.partition(":") if ":" in line else line.partition("=")
        keyword, value = keyword.strip().lower(), value.strip()
        if keyword == "force constant":
            forceConstant = float(value)
        elif keyword in ("distance", "angle", "dihedral"):
            fields = [field.strip() for field in value.split(",")]
            name = {"distance": "Bond", "angle": "Angle", "dihedral": "Dihedral"}[keyword]
            count = {"Bond": 2, "Angle": 3, "Dihedral": 4}[name]
            record = {"coordinate": name, "atoms": [Name(int(atom)) for atom in fields[:count]], "action": "Constrained"}
            if len(fields) > count and fields[count].lower() != "auto":
                record["value"], record["unit"] = float(fields[count]), "A" if name == "Bond" else "deg"
            records.append(record)
        elif keyword == "atoms":
            numbers = _AtomList(value) or []
            records.append({"coordinate": "Atoms", "atoms": [Name(number) for number in numbers],
                            "action": "Fixed" if block == "$fix" else "Constrained"})
        elif keyword == "elements":
            records.append({"coordinate": "Elements", "atoms": [name for name in regex.split(r"[\s,]+", value) if name],
                            "action": "Constrained"})
    return records, forceConstant


def ExtractConformerEnergies(data) -> list[dict]:
    """The final ensemble's conformers (CREGEN's table, its last one), lowest first: each one's number, energy above the
    lowest (kcal/mol), total energy (Eh), population (its set's weight) and how many rotamers it has."""
    header = FindInMap(data, r"Erel/kcal", reverse=True)
    if header is None:
        return []
    data.seek(header.end())
    data.readline()
    conformers = []
    for rawLine in iter(data.readline, b""):
        fields = rawLine.split()
        # A set's first row: number, Erel, Etot, weight, set weight, set, degeneracy[, origin (2.x only)]
        if len(fields) in (7, 8):
            try:
                conformers.append({"conformer": int(fields[5]), "energy": float(fields[1]), "total": float(fields[2]),
                                   "population": float(fields[4]), "rotamers": int(fields[6])})
            except ValueError:
                break
        # The set's other rotamers: number, Erel, Etot, weight[, origin]
        elif len(fields) not in (4, 5):
            break
    return conformers


def CrestFacts(data, path: Path, full: bool = True) -> dict:
    """A CREST output's facts, every one as CREST printed it (info.FileFacts' record for it): the run (version, its
    level, solvation, options, charge/multiplicity from --chrg/--uhf, CPUs from --T, its constraints), how it ended,
    and the final ensemble (window, thresholds, conformers, energies, populations, wall time), plus the lowest
    conformer's geometry from crest_best.xyz. full=False: the run and its status only (the TUI's Details)."""
    facts: dict = {"file": Path(path).name, "kind": "output", "program": "CREST"}
    version = FindInMap(data, r"Version (\S+?),", end=4096)
    # 2.x names the xtb it calls; 3.x computes through the tblite library
    xtb = FindInMap(data, r"Compatible with xTB version (\S+)", end=4096)
    tblite = FindInMap(data, r"xTB calculation via tblite lib", end=16384)
    if version:
        facts["version"] = version.group(1).decode() + (f" (xTB {xtb.group(1).decode()})" if xtb else
                                                        " (xTB via tblite)" if tblite else "")
    termination = ExtractTermination(data, -TERMINATION_TAIL)
    facts["status"] = "Normal" if termination else "Unknown"
    if termination:
        facts["termination"] = termination
    tokens = ExtractCommandLine(data)
    options = tokens[2:] if len(tokens) > 1 and not Flag(tokens[1]) else tokens[1:]
    optionLine = " ".join(options)
    charge, uhf, threads = (_FlagValue(options, *names) for names in (("chrg",), ("uhf",), ("t", "threads")))
    if charge is not None:
        facts["charge"] = _Number(charge)
    # Only what the command line says: without --uhf, CREST may have read a .UHF file
    if uhf is not None and isinstance(_Number(uhf), int):
        facts["multiplicity"] = _Number(uhf) + 1
    best = ResultFolder(path) / BEST_FILE
    coordinates = ExtractFrom(best, ExtractXyz, empty=[]) if full else []
    if coordinates:
        facts["atoms"], facts["formula"] = len(coordinates), Formula(coordinates)
    if threads is not None:
        facts["cpus"] = _Number(threads)
    facts["runs"] = CONFORMER_SEARCH
    if Level(optionLine):
        facts["method"] = Level(optionLine)
    if Solvation(optionLine):
        facts["solvation"] = Solvation(optionLine)
    if optionLine:
        facts["options"] = optionLine
    if not full:
        return {key: value for key, value in facts.items() if value is not None}
    records, forceConstant = ConstraintRecords(ExtractConstraintEcho(data), Symbols(coordinates))
    if records:
        facts["constraints"] = records
    if forceConstant is not None:
        facts["forceConstant"] = forceConstant
    energyThreshold = _FlagValue(options, "ethr")
    mtds = FindInMap(data, r"\((\d+) MTDs\)")
    if mtds:
        facts["mtdRuns"] = int(mtds.group(1))
    early = len(regex.findall(rb"terminated EARLY", data))
    if early:
        facts["mtdEarly"] = early
    timeStep = FindInMap(data, r"Reducing the time step to(?= (\d+) fs)", reverse=True)
    if timeStep:
        facts["timeStep"] = int(timeStep.group(1))
    if termination:
        for key, pattern in FINAL_VALUES:
            found = FindInMap(data, pattern, reverse=True)
            if found:
                facts[key] = _Number(found.group(1).decode())
        if energyThreshold is not None:
            facts["energyThreshold"] = _Number(energyThreshold)
        # 2.x: 'Overall wall time  : 0h :16m :58s'; 3.x: 'CREST runtime (total)  0 d,  0 h, 26 min, 28.978 sec'
        wallTime = FindInMap(data, r"Overall wall time(?=\s*:\s*([^\r\n]+))|"
                                   r"CREST runtime \(total\)(?=\s+([^\r\n]+))", reverse=True)
        if wallTime:
            text = (wallTime.group(1) or wallTime.group(2)).decode().strip()
            facts["wallTime"] = " ".join(regex.sub(r"\s*:\s*", " ", text).split())
        energies = ExtractConformerEnergies(data)
        if energies:
            facts["conformerEnergies"] = energies
    if coordinates:
        facts["coordinates"] = [" ".join(row.split()) for row in coordinates]
    return {key: value for key, value in facts.items() if value is not None}


def ProgressReport(data, start: int = 0, end: int | None = None) -> str:
    """What a CREST job's stalker reports, as a sentence: once it ended, how many conformers it kept in what window (and
    MTDs that ended early); while it runs, its phase (the last header written, CREST's title for it)."""
    lines = []
    conformers = FindInMap(data, r"number of unique conformers for further calc(?=\s+(\d+))", True, start=start, end=end)
    if conformers:
        window = FindInMap(data, dict(FINAL_VALUES)["energyWindow"], True, start=start, end=end)
        count = int(conformers.group(1))
        lines.append(f"{count} conformer{'s' if count != 1 else ''}" + (
            f" within {float(window.group(1)):g} kcal/mol." if window else "."))
    else:
        phase = FindInMap(data, PHASES, True, start=start, end=end)
        if phase:
            found = phase.group().decode()
            text = PHASE_NAMES.get(found, found)
            if text == "MTD Iteration":
                number = regex.match(rb"\s+(\d+)", data[phase.end():phase.end() + 16])
                text += f" {int(number.group(1))}" if number else ""
            lines.append(f"Reached {text}.")
    early = len(regex.findall(rb"terminated EARLY", data[start:end] if end else data[start:]))
    if early:
        lines.append(f"{early} MTD{'s' if early != 1 else ''} terminated early.")
    return " ".join(lines)


def StartGeometry(output: Path) -> Path | None:
    """The starting geometry a CREST output ran from (its command line's input file, beside the output), or None."""
    tokens = ExtractFrom(Path(output), ExtractCommandLine, empty=[])
    if len(tokens) < 2 or Flag(tokens[1]):
        return None
    path = Path(output).parent / tokens[1]
    return path if HasContent(path) else None


def EchoText(echo: list[str]) -> str:
    """CREST's echo of a constraints file as a file again: its blocks closed with $end (the echo leaves it out), their
    lines indented as ConstraintsText writes them."""
    lines, open_ = [], False
    for line in echo:
        if line.startswith("$"):
            if open_:
                lines.append("$end")
            open_ = line.lower() != "$end"
            if open_:
                lines.append(line)
        else:
            lines.append(f"   {line}")
    if open_:
        lines.append("$end")
    return "\n".join(lines) + "\n"


def ConstraintsRun(output: Path) -> str:
    """The constraints a CREST output ran with ('' for none): the file as CREST echoes it (what the run read: the file
    beside it may have changed since), else that file as written."""
    def Read(data) -> tuple[list[str], list[str]]:
        return ExtractCommandLine(data), ExtractConstraintEcho(data)
    tokens, echo = ExtractFrom(Path(output), Read, empty=([], []))
    if echo:
        return EchoText(echo)
    name = _FlagValue(tokens, "cinp")
    path = Path(output).parent / name if name else None
    return path.read_text(encoding="utf-8", errors="replace") if path is not None and path.is_file() else ""


def IsCrestOutput(path: Path) -> bool:
    """Whether a file is a CREST output (its banner)."""
    from .fileops import ExtractProgram
    path = Path(path)
    return path.suffix in (".log", Defaults.outputExtension) and \
        ExtractFrom(path, ExtractProgram, path.suffix, empty="") == CREST_PROGRAM


def ReRunJob(output: Path, intent, chargeAndMultiplicity) -> tuple[CrestJob | None, str]:
    """A CREST output's re-run (`cu -re`, the species' hub rule: its next attempt, 8a_CREST_re): the same starting
    geometry, charge and multiplicity, with the options and constraints it ran with (CompUtils' own flags dropped; the
    constraints as CREST echoed them), unless intent corrects them (method: a Conformer Search Method; route: options typed in full;
    constrain/forceConstant). chargeAndMultiplicity(charge, multiplicity, molecule) fills in (asks for) what the
    command line didn't say. (None, why) when it can't be re-run."""
    import copy
    from .fileops import ReRunNameIn
    from .info    import SourceMolecule
    output = Path(output)
    start = StartGeometry(output)
    if start is None:
        return None, "Its starting geometry (the .xyz on its command line) isn't beside it"
    facts = ExtractFrom(output, lambda data: CrestFacts(data, output, full=False), empty={})
    read, _, _ = SourceMolecule(start)
    if not read.coordinateList:
        return None, f"No coordinates in {start.name}"
    molecule = copy.copy(read)
    if intent.method:
        methods = Methods()
        label = next((name for name in methods if name.lower() == intent.method.lower()), None)
        if label is None:
            return None, f"--method '{intent.method}' isn't a Conformer Search Method (cu -methods)"
        options = methods[label]
    elif intent.route:
        label, options = CUSTOM, " ".join(intent.route.split())
    else:
        label, options = "As It Ran", WithoutReserved(facts.get("options", ""))
    charge, multiplicity = chargeAndMultiplicity(facts.get("charge"), facts.get("multiplicity"), molecule)
    job = CrestJob(molecule, ReRunNameIn(output.stem, Path.cwd()), charge, multiplicity, label, options,
                   intent.constrain or "", intent.forceConstant or DEFAULT_FORCE_CONSTANT,
                   intent.cpus or facts.get("cpus") or Defaults.CPU, intent.memoryRatio, intent.wallTime,
                   intent.partition, intent.cluster,
                   constraintsText=ConstraintsRun(output) if intent.constrain is None else "")
    return job, ""
