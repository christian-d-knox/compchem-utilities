"""
Centralizes data and runtime state previously held in module-level globals.

Loaded explicitly from __main__.Main() at startup, after Defaults.Load().
In Step 4, the CLI-state attributes (isStalking, isCheck, isNBO,
indexOverride, isLooping, fileExtension) move into Intent fields; only
the loaded-data attributes (methodLine, methodList, etc.) stay here.
"""
from dataclasses import dataclass, field

import regex
from rich.markup import escape

from .actions  import SpinState
from .console  import console
from .defaults import Defaults

# {tag} tokens in a route card select blocks from orcablocks.txt
ROUTE_TAG_PATTERN = r"\{([\w-]+)\}"
# [selector, selector: keywords {tags}] groups apply only to matching spin states
ROUTE_GROUP_PATTERN = r"\[\s*([^\]:]*?)\s*:\s*([^\]]*)\]"
# Anything left over from tag/group syntax. Rendered routes containing this are never written
ROUTE_LEAK_PATTERN = r"\{[\w-]+\}|[\[\]]"

# Named multiplicity selectors
_MULTIPLICITY_NAMES = {"doublet": 2, "triplet": 3, "quartet": 4, "quintet": 5, "sextet": 6, "septet": 7}
# ORCA reference keywords. If the route already has one, CompUtils never adds another
_ORCA_REFERENCES = {"RKS", "UKS", "ROKS", "RHF", "UHF", "ROHF"}


def SplitRouteTags(routeLine: str) -> tuple[str, list[str]]:
    """Split a route card into (clean route, lowercased tags). Tags never survive into the clean route."""
    tags = [tag.lower() for tag in regex.findall(ROUTE_TAG_PATTERN, routeLine)]
    cleanRoute = " ".join(regex.sub(ROUTE_TAG_PATTERN, " ", routeLine).split())
    return cleanRoute, tags


def _IsValidSelector(selector: str) -> bool:
    return selector in ("css", "oss", "open") or selector in _MULTIPLICITY_NAMES \
        or regex.fullmatch(r"m\d+", selector) is not None


def _SelectorMatches(selector: str, spinState: SpinState, multiplicity: int) -> bool:
    match selector:
        case "css":  return spinState == SpinState.CSS
        case "oss":  return spinState == SpinState.OSS
        case "open": return spinState in (SpinState.OSS, SpinState.OPEN)
    if selector in _MULTIPLICITY_NAMES:
        return multiplicity == _MULTIPLICITY_NAMES[selector]
    return multiplicity == int(selector[1:])


@dataclass
class RouteTemplate:
    """A benchmarkMethods entry split into its unconditional part and its spin-state groups."""
    base: str                                   # clean route: no tags, no groups
    tags: list[str] = field(default_factory=list)
    groups: list[tuple[frozenset[str], str, list[str]]] = field(default_factory=list)  # (selectors, keywords, tags)


def ParseRouteTemplate(routeLine: str) -> RouteTemplate:
    groups = []
    for selectorText, content in regex.findall(ROUTE_GROUP_PATTERN, routeLine):
        selectors = frozenset(s.strip().lower() for s in selectorText.split(",") if s.strip())
        invalid = sorted(s for s in selectors if not _IsValidSelector(s))
        if invalid or not selectors:
            console.print(f"[warning]\\[config] Unknown spin selector(s) {invalid or ['<empty>']} in benchmarkMethods "
                          f"entry '{escape(routeLine)}'. Ignoring that group.[/warning]")
            continue
        keywords, tags = SplitRouteTags(content)
        groups.append((selectors, keywords, tags))
    base, tags = SplitRouteTags(regex.sub(ROUTE_GROUP_PATTERN, " ", routeLine))
    if regex.search(ROUTE_LEAK_PATTERN, base):
        console.print(f"[warning]\\[config] Malformed \\[ ] group in benchmarkMethods entry '{escape(routeLine)}'. "
                      f"Jobs using it will be skipped.[/warning]")
    return RouteTemplate(base, tags, groups)


def _MethodPart(token: str) -> str:
    # Gaussian joins method and basis as 'method/basis'; strip parentheses like IdentifyMethod does
    return token.split("/")[0].replace("(", "").replace(")", "").upper()


def IsKnownMethod(name: str) -> bool:
    return any(name == method.replace("(", "").replace(")", "").upper() for method in Catalog.methodList)


def _ApplyReference(tokens: list[str], spinState: SpinState, extensionType: str) -> list[str]:
    """Add the U/RO reference for open-shell species. Idempotent: routes that already declare one are untouched."""
    if spinState == SpinState.CSS or not tokens:
        return tokens
    # OSS is always U; RO is meaningless for a broken-symmetry singlet
    reference = "RO" if (spinState == SpinState.OPEN and Defaults.openShellReference == "RO") else "U"

    if extensionType == Defaults.orcaExtension:
        if any(token.upper() in _ORCA_REFERENCES for token in tokens):
            return tokens
        # ORCA already runs UKS/UHF when multiplicity > 1
        if spinState == SpinState.OPEN and reference == "U":
            return tokens
        method = _MethodPart(tokens[0])
        isHartreeFock = method == "HF" or any(key in method for key in ("MP2", "CC", "CAS", "NEVPT"))
        return tokens + [reference + ("HF" if isHartreeFock else "KS")]

    # Gaussian: prefix the method token (the first token, and only the part before any '/')
    method = _MethodPart(tokens[0])
    if not IsKnownMethod(method):
        # Known method behind a U/RO/R prefix, or an unlisted method that already starts with U/RO: already declared
        alreadyPrefixed = any(method.startswith(prefix) and IsKnownMethod(method[len(prefix):])
                              for prefix in ("RO", "U", "R"))
        if alreadyPrefixed or method.startswith(("U", "RO")):
            return tokens
    return [reference + tokens[0]] + tokens[1:]


def RenderRoute(index: int, molecule) -> tuple[str, list[str]]:
    """Render benchmarkMethods[index] for one molecule's spin state. Returns (route, orcablocks tags).

    Additive: base route + unconditional tags, then every matching group in written order, then the U/RO reference.
    Duplicate keywords and tags keep their first occurrence.
    """
    template = Catalog.templates[index]
    try:
        multiplicity = int(molecule.multiplicity)
    except (TypeError, ValueError):
        multiplicity = 0
    tokens, tags = template.base.split(), list(template.tags)
    seenTokens = {token.upper() for token in tokens}
    for selectors, keywords, groupTags in template.groups:
        if not any(_SelectorMatches(s, molecule.spinState, multiplicity) for s in selectors):
            continue
        for token in keywords.split():
            if token.upper() not in seenTokens:
                tokens.append(token)
                seenTokens.add(token.upper())
        tags += [tag for tag in groupTags if tag not in tags]
    tokens = _ApplyReference(tokens, molecule.spinState, molecule.extensionType)
    return " ".join(tokens), tags


class Catalog:
    # ── Data derived from Defaults at startup ────────────────────────────────
    fullMethodLine = []     # fullMethodLine[i] = templates[i].base (clean, spin-independent)
    templates = []          # templates[i] = parsed benchmarkMethods[i]; render per molecule with RenderRoute
    methodLine = []
    methodList = []
    targetProgram = []

    # ── Capability flags (set during Load) ──────────────────────────────────
    canBench = True

    @classmethod
    def Load(cls) -> None:
        """Populate Catalog from Defaults (loaded from TOML config)."""
        # Method → program mapping
        cls.methodList    = list(Defaults.methodNames)
        cls.targetProgram = list(Defaults.targetProgram)

        # Benchmark method lines. Tags and groups are parsed out here so no downstream code ever sees them
        cls.templates      = [ParseRouteTemplate(line) for line in Defaults.benchmarkMethods]
        cls.fullMethodLine = [template.base for template in cls.templates]
        # Method name only: Gaussian's 'method/basis' form would otherwise put the basis (and a '/') in filenames
        cls.methodLine     = [line.strip().split()[0].split("/")[0] for line in cls.fullMethodLine]
        cls.canBench       = len(cls.fullMethodLine) > 1
