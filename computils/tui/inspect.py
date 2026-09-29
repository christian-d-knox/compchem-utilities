"""Read-only file inspection for the TUI. No Textual imports, so everything here can be tested headless.

Everything reuses the CLI's extractors, and nothing here writes files, prompts, or mutates the Catalog:
project files are always resolved with required=False (the required path can prompt, which would hang the TUI).
"""
from dataclasses import dataclass, field
from pathlib import Path

import regex
from rich.text import Text

from ..actions  import Action
from ..console  import console
from ..catalog  import Catalog, RenderRoute, ROUTE_LEAK_PATTERN, _ApplyReference
from ..defaults import Defaults
from ..fileops  import (MapFile, FindInMap, ExtractStalking, ExtractGaussianCharge, ExtractCoords, ExtractRouteLine,
                        ExtractResources, IdentifyMethod, SplitRoute, MoleculeElements, extensionGetter)
from ..jobs     import _UsesMixedBasis, _LoadMixedBasis, _FilterMixedBasis, _LoadOrcaBlocks
from ..molecule import Molecule
from ..project  import ResolveProjectFile
from ..spin     import ClassifySpin

# Actions that build jobs from a batch of files. Everything else on the Home screen is a separate screen
BATCH_ACTIONS = [Action.RUN, Action.SINGLE_POINT, Action.BENCHMARK, Action.RERUN, Action.CUBE, Action.FORM_CHECK]


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


def _Termination(path: Path) -> str:
    """The termination variant found in an output file, or '' if none (still running, killed, or empty)."""
    # mmap can't map an empty file
    if path.stat().st_size == 0:
        return ""
    with MapFile(path) as data:
        hasTerminated, termination = ExtractStalking(data, "termination")
    return termination if hasTerminated else ""


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
        with MapFile(path) as data:
            return ExtractGaussianCharge(data)
    except (IndexError, ValueError, OSError):
        return "", ""


def ProgramName(method: str) -> str:
    # Same lookup as extensionGetter, without its console warning
    if method in Catalog.methodList:
        return {"G16": "G16", "O": "ORCA"}.get(Catalog.targetProgram[Catalog.methodList.index(method)], "")
    return ""


def FileDetails(path: Path) -> dict[str, str]:
    """The Details pane fields for one file (D12). Missing values are '—'."""
    details = {"Status": "—", "Charge": "—", "Mult": "—", "Spin": "—", "Method": "—", "Program": "—",
               "CPU": "—", "Mem": "—", "Keys": "—"}
    if not path.is_file() or path.stat().st_size == 0:
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
    with MapFile(path) as data:
        # Atomic numbers are enough for MoleculeElements, which is all the preview needs the coordinates for
        atomicNumbers, _, _, _ = ExtractCoords(data)
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


def _ProjectFileProblem(route: str, tags: list[str], extensionType: str, elements: set[str]) -> str:
    """Why genFile would skip this job (D24), or ''. Mirrors genFile's checks using its own helpers."""
    if regex.search(ROUTE_LEAK_PATTERN, route):
        return "malformed [ ] group in benchmarkMethods"
    if extensionType == Defaults.gaussianExtension and _UsesMixedBasis(route):
        basisPath = ResolveProjectFile("mixedbasis.txt", False)
        if basisPath is None:
            return "mixedbasis.txt not found"
        _, _, missing = _FilterMixedBasis(_LoadMixedBasis(basisPath), elements)
        if missing:
            return f"mixedbasis.txt has no basis for {' '.join(sorted(missing))}"
    if extensionType == Defaults.orcaExtension and tags:
        blocksPath = ResolveProjectFile("orcablocks.txt", False)
        if blocksPath is None:
            return "orcablocks.txt not found"
        missingTags = [tag for tag in tags if tag not in _LoadOrcaBlocks(blocksPath)]
        if missingTags:
            return f"orcablocks.txt has no {', '.join(missingTags)}"
    return ""


def PreviewRowFor(action: Action, molecule: Molecule, index: int) -> PreviewRow:
    """The route one job would get. Re-run mirrors genReRun's template matching, without mutating the Catalog."""
    row = PreviewRow(molecule.rootName, spin=molecule.spinState.name)
    if action == Action.RERUN:
        with MapFile(molecule.fullPath) as data:
            routeLine = ExtractRouteLine(data, molecule.fullPath.suffix)
        method = IdentifyMethod(routeLine) if routeLine else ""
        if not method:
            row.problem = "no route card found" if not routeLine else "method not recognised"
            return row
        molecule.extensionType = extensionGetter(method)
        extractedTokens = routeLine.upper().split()
        matchedIndex = next((i for i in range(len(Catalog.templates))
                             if RenderRoute(i, molecule)[0].upper().split() == extractedTokens), None)
        if matchedIndex is not None:
            route, row.tags = RenderRoute(matchedIndex, molecule)
            base = Catalog.templates[matchedIndex].base
        else:
            # genReRun renders the extracted route as a bare template: only the U/RO reference can be added
            route = " ".join(_ApplyReference(routeLine.split(), molecule.spinState, molecule.extensionType))
            base = routeLine
    else:
        molecule.extensionType = extensionGetter(Catalog.methodLine[index])
        route, row.tags = RenderRoute(index, molecule)
        base = Catalog.templates[index].base
    row.spans = RouteSpans(route, base)
    if not molecule.coordinateList:
        # dispatch skips files with no geometry
        row.problem = "no coordinates found"
        return row
    row.problem = _ProjectFileProblem(route, row.tags, molecule.extensionType, MoleculeElements(molecule.coordinateList))
    return row


def MethodIndices(action: Action, index: int) -> list[int]:
    """Benchmark method indices that will run. -b -ovr N runs methods N..end (genBench); -sp -ovr N runs only N."""
    match action:
        case Action.BENCHMARK:    return list(range(index, len(Catalog.methodLine)))
        case Action.SINGLE_POINT: return [index]
        case _:                   return [0]
