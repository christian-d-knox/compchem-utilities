"""Method and route-template data derived from Defaults. Loaded from __main__.Main() after the config (and any
project.toml) is applied, and reloaded when the TUI moves into another project."""
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
    method: str = ""                            # picks the program (extensionGetter) and names benchmark files
    blocks: list[str] = field(default_factory=list)  # literal ORCA %blocks written after the tag blocks (a re-run's own)


def _GroupSelectors(selectorText: str) -> tuple[frozenset[str], list[str]]:
    """A [group]'s selectors, and the invalid ones ('<empty>' for a group with none)."""
    selectors = frozenset(s.strip().lower() for s in selectorText.split(",") if s.strip())
    return selectors, sorted(s for s in selectors if not _IsValidSelector(s)) or ([] if selectors else ["<empty>"])


def UnknownSelectors(routeLine: str) -> list[str]:
    """Selectors in a route card's [groups] that ParseRouteTemplate would reject (it drops those groups)."""
    return [invalid for selectorText, _ in regex.findall(ROUTE_GROUP_PATTERN, routeLine)
            for invalid in _GroupSelectors(selectorText)[1]]


def ParseRouteTemplate(routeLine: str) -> RouteTemplate:
    groups = []
    for selectorText, content in regex.findall(ROUTE_GROUP_PATTERN, routeLine):
        selectors, invalid = _GroupSelectors(selectorText)
        if invalid:
            console.print(f"[warning]\\[config] Unknown spin selector(s) {invalid} in benchmarkMethods "
                          f"entry '{escape(routeLine)}'. Ignoring that group.[/warning]")
            continue
        keywords, tags = SplitRouteTags(content)
        groups.append((selectors, keywords, tags))
    base, tags = SplitRouteTags(regex.sub(ROUTE_GROUP_PATTERN, " ", routeLine))
    if regex.search(ROUTE_LEAK_PATTERN, base):
        console.print(f"[warning]\\[config] Malformed \\[ ] group in benchmarkMethods entry '{escape(routeLine)}'. "
                      f"Jobs using it will be skipped.[/warning]")
    # The first token without Gaussian's '/basis' half, which would otherwise put the basis (and a '/') in filenames
    method = base.split()[0].split("/")[0] if base else ""
    return RouteTemplate(base, tags, groups, method)


def MethodKey(token: str) -> str:
    """The form method names are compared in: Gaussian's '/basis' half dropped, no parentheses, upper case."""
    return token.split("/")[0].replace("(", "").replace(")", "").upper()


def SplitReference(key: str) -> tuple[str, str] | None:
    """A MethodKey as (reference prefix, known method key): ('', 'B3LYP'), ('U', 'B3LYP'), ('RO', 'B3LYP'), or None if
    no known method is found. The exact name is checked first, so a listed method starting with U or R stays whole."""
    for prefix in ("", "RO", "U", "R"):
        if key.startswith(prefix) and key[len(prefix):] in Catalog.methodKeys:
            return prefix, key[len(prefix):]
    return None


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
        # ORCA routes often lead with the basis (def2-TZVP ... DLPNO-CCSD(T)), so judge HF vs KS by the known method
        found = next(filter(None, (SplitReference(MethodKey(token)) for token in tokens)), None)
        method = found[1] if found else MethodKey(tokens[0])
        isHartreeFock = method == "HF" or any(key in method for key in ("MP2", "CC", "CAS", "NEVPT"))
        return tokens + [reference + ("HF" if isHartreeFock else "KS")]

    # Gaussian: prefix the method token (the first token, and only the part before any '/')
    method = MethodKey(tokens[0])
    found = SplitReference(method)
    # Known method behind a U/RO/R prefix, or an unlisted method that already starts with U/RO: already declared
    if (found and found[0]) or (not found and method.startswith(("U", "RO"))):
        return tokens
    return [reference + tokens[0]] + tokens[1:]


def RenderRoute(template: RouteTemplate, molecule) -> tuple[str, list[str]]:
    """Render a route template for one molecule's spin state. Returns (route, orcablocks tags).

    Additive: base route + unconditional tags, then every matching group in written order, then the U/RO reference.
    Duplicate keywords and tags keep their first occurrence.
    """
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


def MatchTemplate(routeLine: str, molecule) -> RouteTemplate | None:
    """The benchmarkMethods entry that renders to this (already rendered) route for this molecule, so a re-run can
    recover its orcablocks tags. None if no entry matches. Shared by genReRun and the TUI's Re-run preview."""
    tokens = routeLine.upper().split()
    return next((template for template in Catalog.templates
                 if RenderRoute(template, molecule)[0].upper().split() == tokens), None)


class Catalog:
    # ── Data derived from Defaults at startup ────────────────────────────────
    templates = []          # templates[i] = parsed benchmarkMethods[i] (.base clean route, .method); see RenderRoute
    methodList = []
    targetProgram = []
    methodKeys = {}         # MethodKey(name) -> name, first listed wins (SplitReference, IdentifyMethod)
    programOf = {}          # name -> targetProgram entry

    # ── Capability flags (set during Load) ──────────────────────────────────
    canBench = True

    @classmethod
    def Load(cls) -> None:
        """Populate Catalog from Defaults (loaded from TOML config)."""
        # Method → program mapping
        cls.methodList    = list(Defaults.methodNames)
        cls.targetProgram = list(Defaults.targetProgram)
        cls.methodKeys    = {}
        for method in cls.methodList:
            cls.methodKeys.setdefault(MethodKey(method), method)
        cls.programOf     = dict(zip(cls.methodList, cls.targetProgram))

        # Benchmark method lines. Tags and groups are parsed out here so no downstream code ever sees them
        cls.templates = [ParseRouteTemplate(line) for line in Defaults.benchmarkMethods]
        cls.canBench  = len(cls.templates) > 1
