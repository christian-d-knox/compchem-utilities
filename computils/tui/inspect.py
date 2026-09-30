"""Read-only file inspection for the TUI. No Textual imports, so everything here can be tested headless.

Everything reuses the CLI's extractors, and nothing here writes files, prompts, or mutates the Catalog:
project files are always resolved with required=False (the required path can prompt, which would hang the TUI).
"""
from dataclasses import dataclass, field
from pathlib import Path

from rich.text import Text

from ..actions  import Action
from ..console  import console
from ..catalog  import Catalog, MatchTemplate, RenderRoute, RouteTemplate
from ..defaults import Defaults
from ..fileops  import (MapFile, FindInMap, HasContent, ExtractFrom, ExtractTermination, ExtractCoords, ExtractRouteLine,
                        ExtractResources, IdentifyMethod, SplitRoute, MoleculeElements, extensionGetter,
                        gaussianChargeFinder, PROGRAMS)
from ..jobs     import JobFileProblem
from ..molecule import Molecule
from ..spin     import ClassifySpin

def Styled(text: str, styleName: str) -> Text:
    """Text in one of the CLI's semantic styles (error, good, ...). ApplyTheme has already chosen lowColor or hexCode (D28)."""
    return Text(text, style=console.get_style(styleName) if styleName else "")


def OutputExtensions() -> tuple[str, ...]:
    return ".log", Defaults.outputExtension


def ActionExtensions(action: Action) -> tuple[str, ...]:
    """File types each action can take. Defaults is read at call time, since extensions come from the TOML config."""
    match action:
        case Action.RUN:        return Defaults.gaussianExtension, Defaults.orcaExtension
        case Action.CUBE:       return ".chk", ".fchk"
        case Action.FORM_CHECK: return (".chk",)
        case _:                 return OutputExtensions()


# The termination line is the last thing G16 and ORCA write (G16's archive entry, quote and timings come before it),
# so only the end of the file is searched. A file without one in its tail is running or was killed
TERMINATION_TAIL = 64 * 1024


def _Termination(path: Path) -> str:
    """The termination variant found in an output file, or '' if none (still running, killed, or empty)."""
    return ExtractFrom(path, ExtractTermination, -TERMINATION_TAIL, empty="")


def FileStatus(path: Path) -> str:
    """normal / error / unknown for output files, and '—' for anything else."""
    if path.suffix not in OutputExtensions():
        return "—"
    termination = _Termination(path)
    if not termination:
        return "unknown"
    return "error" if termination == Defaults.terminationVariants[2] else "normal"


def _Charge(path: Path) -> tuple[str, str]:
    # Only Gaussian outputs have the 'Charge = 0 Multiplicity = 1' line; anything else can fail to index
    try:
        return gaussianChargeFinder(path)
    except (IndexError, ValueError, OSError):
        return "", ""


def ProgramName(method: str, programOf: dict | None = None) -> str:
    # Same lookup as extensionGetter, without its console warning
    program = PROGRAMS.get((Catalog.programOf if programOf is None else programOf).get(method))
    return program[0] if program else ""


def FileDetails(path: Path) -> dict[str, str]:
    """The Details pane fields for one file (D12). Missing values are '—'."""
    details = {"Status": "—", "Charge": "—", "Mult": "—", "Spin": "—", "Method": "—", "Program": "—",
               "CPU": "—", "Mem": "—", "Keys": "—"}
    if not HasContent(path):
        return details
    if path.suffix in OutputExtensions():
        details["Status"] = _Termination(path).capitalize() or "Unknown (no termination line)"
    charge, multiplicity = _Charge(path)
    if multiplicity:
        details["Charge"], details["Mult"] = charge, multiplicity
        spinState, reason = ClassifySpin(path, path.stem, multiplicity, path.suffix)
        details["Spin"] = f"{spinState.name} ({reason})"

    with MapFile(path) as data:
        routeLine = ExtractRouteLine(data, path.suffix)
        method, basis, keys = SplitRoute(routeLine)
        if method:
            details["Method"] = f"{method}/{basis}" if basis else method
            details["Program"] = ProgramName(method) or "—"
        details["Keys"] = keys or "—"
        # ExtractResources silently falls back to Defaults, which would show made-up values for this file
        if FindInMap(data, r"%nproc|nprocs", ignoreCase=True):
            programExtension = Defaults.orcaExtension if details["Program"] == "ORCA" else Defaults.gaussianExtension
            cpus, jobRam = ExtractResources(data, programExtension)
            details["CPU"], details["Mem"] = str(cpus), f"{jobRam} GB"
    return details


# ─── Builder route preview ───────────────────────────────────────────────

@dataclass
class PreviewRow:
    fileName: str
    spin: str = ""
    spans: list[tuple[str, str]] = field(default_factory=list)   # (text, origin): origin is method / base / added
    tags: list[str] = field(default_factory=list)
    problem: str = ""                                               # non-empty -> this job would be skipped


def PreviewMolecule(path: Path) -> Molecule:
    """A Molecule for previewing, built the way dispatch builds one, but without writing the .xyz file."""
    charge, multiplicity = _Charge(path)
    # Atomic numbers are enough for MoleculeElements, which is all the preview needs the coordinates for
    atomicNumbers, _, _, _ = ExtractFrom(path, ExtractCoords, empty=([], [], [], []))
    molecule = Molecule(path, path.stem, charge, multiplicity, atomicNumbers, path.suffix, path.stem)
    molecule.spinState, _ = ClassifySpin(path, path.stem, multiplicity, path.suffix)
    return molecule


def RouteSpans(route: str, base: str) -> list[tuple[str, str]]:
    """Mark each part of a rendered route as the method, part of the written base route, or added by CompUtils."""
    baseTokens = {token.upper() for token in base.split()}
    method = IdentifyMethod(route)
    spans, methodSeen = [], False
    for position, token in enumerate(route.split()):
        if position:
            spans.append((" ", "base"))
        origin = "base" if token.upper() in baseTokens else "added"
        methodPart = token.split("/")[0]
        if method and not methodSeen and IdentifyMethod(methodPart) == method:
            methodSeen = True
            # A U/RO reference prefix CompUtils added sits in front of the method name
            prefixLength = len(methodPart) - len(method) if methodPart.upper().endswith(method.upper()) else 0
            if prefixLength:
                spans.append((methodPart[:prefixLength], origin))
            spans.append((methodPart[prefixLength:], "method"))
            if token[len(methodPart):]:
                spans.append((token[len(methodPart):], "base"))
            continue
        spans.append((token, origin))
    return spans


# How each part of a rendered route is coloured (D17 / D28)
SPAN_STYLES = {"method": "operation", "added": "good", "base": ""}


def RenderedRow(row: PreviewRow, template: RouteTemplate, molecule: Molecule, elements: set[str],
                texts: dict[str, str] | None = None) -> PreviewRow:
    """Fill row with the template rendered for molecule (spans, orcablocks tags) and why genFile would skip the job.
    texts: unsaved project-file text to check against instead of the files (the config editor)."""
    route, row.tags = RenderRoute(template, molecule)
    row.spans = RouteSpans(route, template.base)
    row.problem = JobFileProblem(route, row.tags, molecule.extensionType, elements, False, texts)
    return row


def RowText(row: PreviewRow) -> Text:
    """A rendered route, coloured by origin, then its orcablocks tags and any skip reason."""
    line = Text()
    for text, origin in row.spans:
        line.append_text(Styled(text, SPAN_STYLES[origin]))
    if row.tags:
        line.append_text(Styled(f"  ← orcablocks {' '.join('{' + tag + '}' for tag in row.tags)}", "good"))
    if row.problem:
        line.append_text(Styled(f"  ⚠ {row.problem}", "warning"))
    return line


def PreviewRowFor(action: Action, molecule: Molecule, index: int) -> PreviewRow:
    """The route one job would get, from the template its workflow would choose (Re-run shares genReRun's MatchTemplate).
    The template is rendered directly: the preview never sets molecule.template, nor mutates the Catalog."""
    row = PreviewRow(molecule.rootName, spin=molecule.spinState.name)
    if action == Action.RERUN:
        routeLine = ExtractFrom(molecule.sourcePath, ExtractRouteLine, molecule.sourcePath.suffix, empty="")
        method = IdentifyMethod(routeLine) if routeLine else ""
        if not method:
            row.problem = "no route card found" if not routeLine else "method not recognised"
            return row
        molecule.extensionType = extensionGetter(method)
        # No matching entry: genReRun uses the route verbatim, so only the U/RO reference can be added
        template = MatchTemplate(routeLine, molecule) or RouteTemplate(routeLine, method=method)
    else:
        template = Catalog.templates[index]
        molecule.extensionType = extensionGetter(template.method)
    if not molecule.coordinateList:
        # dispatch skips files with no geometry
        RenderedRow(row, template, molecule, set())
        row.problem = "no coordinates found"
        return row
    # The same check genFile skips jobs with, without its prompt for a missing project file
    return RenderedRow(row, template, molecule, MoleculeElements(molecule.coordinateList))


def MethodIndices(action: Action, index: int) -> list[int]:
    """Benchmark method indices that will run. -b -ovr N runs methods N..end (genBench); -sp -ovr N runs only N."""
    match action:
        case Action.BENCHMARK:    return list(range(index, len(Catalog.templates)))
        case Action.SINGLE_POINT: return [index]
        case _:                   return [0]
