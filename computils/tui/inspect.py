"""Read-only file inspection for the TUI. No Textual imports, so everything here can be tested headless.

Everything reuses the CLI's extractors, and nothing here writes files, prompts, or mutates the Catalog:
project files are always resolved with required=False (the required path can prompt, which would hang the TUI).
"""
from dataclasses import dataclass, field
from pathlib import Path

from rich.text import Text

from ..actions  import Action
from ..console  import console
from ..catalog  import Catalog, RenderRoute, RouteTemplate
from ..defaults import Defaults
from ..fileops  import (MapFile, FindInMap, HasContent, ExtractFrom, ExtractTermination, ExtractResources, IdentifyMethod,
                        SplitRoute, MoleculeElements, extensionGetter, ReadMolecule, ReRunTemplate, OrcaBlockName,
                        PROGRAMS)
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
        # GoodVibes reads program outputs, and .out is the only one this group ever feeds it
        case Action.GOODVIBES:  return (Defaults.outputExtension,)
        case _:                 return OutputExtensions()


# The termination line is the last thing G16 and ORCA write (G16's archive entry, quote and timings come before it),
# so only the end of the file is searched. A file without one in its tail is running or was killed
TERMINATION_TAIL = 64 * 1024


def _Termination(path: Path) -> str:
    """The termination variant found in an output file, or '' if none (still running, killed, or empty)."""
    return ExtractFrom(path, ExtractTermination, -TERMINATION_TAIL, empty="")


def FileStatus(path: Path) -> str:
    """Normal / Error / Unknown for output files, and '—' for anything else."""
    if path.suffix not in OutputExtensions():
        return "—"
    termination = _Termination(path)
    if not termination:
        return "Unknown"
    return "Error" if termination == Defaults.terminationVariants[2] else "Normal"


def ProgramName(method: str, programOf: dict | None = None) -> str:
    # Same lookup as extensionGetter, without its console warning
    program = PROGRAMS.get((Catalog.programOf if programOf is None else programOf).get(method))
    return program[0] if program else ""


def _SourceProgramName(program: str) -> str:
    """Display name of the program that wrote a file (ReadMolecule's sourceProgram, an input extension), or ''."""
    return next((name for name, extensionKey in PROGRAMS.values() if program and getattr(Defaults, extensionKey) == program), "")


def FileDetails(path: Path) -> dict[str, str]:
    """The Details pane fields for one file (D12). Missing values are '—'. The file is mapped once for all of them."""
    details = {"Status": "—", "Charge": "—", "Mult": "—", "Spin": "—", "Method": "—", "Program": "—",
               "CPU": "—", "Mem": "—", "Keys": "—"}
    if not HasContent(path):
        return details
    with MapFile(path) as data:
        if path.suffix in OutputExtensions():
            details["Status"] = ExtractTermination(data, -TERMINATION_TAIL).title() or "Unknown (No Termination Line)"
        molecule = ReadMolecule(data, path)
        if molecule.multiplicity:
            details["Charge"], details["Mult"] = molecule.charge, molecule.multiplicity
            spinState, reason = ClassifySpin(molecule, data)
            details["Spin"] = f"{spinState.name} ({reason})"
        method, basis, keys = SplitRoute(molecule.sourceRoute)
        if method:
            details["Method"] = f"{method}/{basis}" if basis else method
        # The program that wrote the file; for a file of neither program's, the one its method runs in
        details["Program"] = _SourceProgramName(molecule.sourceProgram) or (ProgramName(method) if method else "") or "—"
        details["Keys"] = keys or "—"
        # ExtractResources silently falls back to Defaults, which would show made-up values for this file
        if molecule.sourceProgram and FindInMap(data, r"%nproc|nprocs", ignoreCase=True):
            cpus, jobRam = ExtractResources(data, molecule.sourceProgram)
            details["CPU"], details["Mem"] = str(cpus), f"{jobRam} GB"
    return details


# ─── Builder route preview ───────────────────────────────────────────────

@dataclass
class PreviewRow:
    fileName: str
    spin: str = ""
    spans: list[tuple[str, str]] = field(default_factory=list)   # (text, origin): origin is method / base / added
    tags: list[str] = field(default_factory=list)
    carried: list[str] = field(default_factory=list)                # names of the template's own %blocks (re-run)
    problem: str = ""                                               # non-empty -> this job would be skipped


def PreviewMolecule(path: Path) -> Molecule:
    """A Molecule for previewing, read the way dispatch reads one (one map), but without writing the .xyz file."""
    if not HasContent(path):
        return Molecule(path, path.stem, "", "", [], path.suffix, path.stem)
    with MapFile(path) as data:
        molecule = ReadMolecule(data, path)
        molecule.spinState, _ = ClassifySpin(molecule, data)
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
    row.carried = [OrcaBlockName(block) for block in template.blocks]
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
    if row.carried:
        line.append_text(Styled(f"  ← carried {' '.join('%' + name for name in row.carried)}", "good"))
    if row.problem:
        line.append_text(Styled(f"  ⚠ {row.problem}", "warning"))
    return line


def PreviewRowFor(action: Action, molecule: Molecule, index: int) -> PreviewRow:
    """The route one job would get, from the template its workflow would choose (Re-run shares genReRun's ReRunTemplate).
    The template is rendered directly: the preview never sets molecule.template, nor mutates the Catalog."""
    row = PreviewRow(molecule.rootName, spin=molecule.spinState.name)
    if action == Action.RERUN:
        # No matching entry: the route verbatim (only the U/RO reference can be added), with an ORCA job's own blocks
        template, row.problem = ReRunTemplate(molecule)
        if template is None:
            return row
    else:
        template = Catalog.templates[index]
        molecule.extensionType = extensionGetter(template.method)
    if not molecule.coordinateList:
        # dispatch skips files with no geometry
        RenderedRow(row, template, molecule, set())
        row.problem = "No Coordinates Found"
        return row
    # The same check genFile skips jobs with, without its prompt for a missing project file
    return RenderedRow(row, template, molecule, MoleculeElements(molecule.coordinateList))


def MethodIndices(action: Action, index: int) -> list[int]:
    """Benchmark method indices that will run. -b -ovr N runs methods N..end (genBench); -sp -ovr N runs only N."""
    match action:
        case Action.BENCHMARK:    return list(range(index, len(Catalog.templates)))
        case Action.SINGLE_POINT: return [index]
        case _:                   return [0]
