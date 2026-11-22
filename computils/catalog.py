"""Method and route-template data derived from Defaults. Loaded from __main__.Main() after the config (and any
project.toml) is applied, and reloaded when the TUI moves into another project."""
from dataclasses import dataclass, field

import regex
from rich.markup import escape

from .actions  import SpinState
from .console  import console
from .defaults import Defaults

# {tag} tokens in a route card: an ORCA route's select ORCA Blocks, a Gaussian route's a Mixed Basis set; {smd} is the
# project's solvent in either (solvent.py)
ROUTE_TAG_PATTERN = r"\{([\w-]+)\}"
# [selector, selector: keywords {tags}] groups apply only to matching spin states (or, [metal: ...], to a molecule
# with a d- or f-block metal)
ROUTE_GROUP_PATTERN = r"\[\s*([^\]:]*?)\s*:\s*([^\]]*)\]"
# Anything left over from tag/group/directive syntax. Rendered routes containing this are never written
ROUTE_LEAK_PATTERN = r"\{[\w-]+\}|[\[\]]|(?:^|\s)%"
# A method's stages, one job: 'route -> route' (a method section's '>' lines are joined into this form)
STAGE_JOIN = " -> "
STAGE_SPLIT = r"\s+->\s+"
# A stage's CompUtils directive (parsed out like {tags}): it starts a new checkpoint, %OldChk-copied from the one so far
NEW_CHECKPOINT = r"(?i)(?:^|\s)%newchk(?=\s|$)"
_ROUTE_TAG_RE = regex.compile(ROUTE_TAG_PATTERN)
_ROUTE_GROUP_RE = regex.compile(ROUTE_GROUP_PATTERN)
_ROUTE_LEAK_RE = regex.compile(ROUTE_LEAK_PATTERN)
_STAGE_SPLIT_RE = regex.compile(STAGE_SPLIT)
_NEW_CHECKPOINT_RE = regex.compile(NEW_CHECKPOINT)

# Named multiplicity selectors
_MULTIPLICITY_NAMES = {"doublet": 2, "triplet": 3, "quartet": 4, "quintet": 5, "sextet": 6, "septet": 7}
# [metal: ...]: a molecule with a d- or f-block metal (Sc-Zn, Y-Cd, La-Hg, Ac-Lr by atomic number); main-group metals
# don't count
METAL_SELECTOR = "metal"
_METAL_NUMBERS = (range(21, 31), range(39, 49), range(57, 81), range(89, 104))
# ORCA reference keywords. If the route already has one, CompUtils never adds another
_ORCA_REFERENCES = {"RKS", "UKS", "ROKS", "RHF", "UHF", "ROHF"}
# Gaussian's compound (composite) methods, with any reference prefix written: CBS-QB3, ROCBS-QB3, G4, G3MP2B3, W1BD...
# They pick their own reference (unrestricted for open shells), and a U prefix makes no keyword (UCBS-QB3)
_COMPOUND_METHOD = r"(?i)(RO|U|R)?(CBS-(?:QB3|4M|APNO|Q|Lq|4)|G[1-4](?:MP2)?(?:B3)?|W1(?:U|BD|RO)?)(?:\(.*\))?"
# The compound methods Gaussian has a restricted open-shell form of, written with an RO prefix
_RO_COMPOUNDS = {"CBS-QB3"}
_COMPOUND_METHOD_RE = regex.compile(_COMPOUND_METHOD)
_MULTIPLICITY_SEL_RE = regex.compile(r"m\d+")
_KEYWORD_SPLIT_RE = regex.compile(r"[=(]")
_CPCM_RE = regex.compile(r"(?i)cpcm(\(.*\))?")


def SplitRouteTags(routeLine: str) -> tuple[str, list[str]]:
    """Split a route card into (clean route, lowercased tags). Tags never survive into the clean route."""
    tags = [tag.lower() for tag in _ROUTE_TAG_RE.findall(routeLine)]
    cleanRoute = " ".join(_ROUTE_TAG_RE.sub(" ", routeLine).split())
    return cleanRoute, tags


def _IsValidSelector(selector: str) -> bool:
    return selector in ("css", "oss", "open", METAL_SELECTOR) or selector in _MULTIPLICITY_NAMES \
        or _MULTIPLICITY_SEL_RE.fullmatch(selector) is not None


def MetalSymbols() -> set[str]:
    from .fileops import ATOMIC_SYMBOLS
    return {ATOMIC_SYMBOLS[number] for numbers in _METAL_NUMBERS for number in numbers if number in ATOMIC_SYMBOLS}


def _SelectorMatches(selector: str, spinState: SpinState, multiplicity: int, elements: set[str] = frozenset()) -> bool:
    match selector:
        case "css":  return spinState == SpinState.CSS
        case "oss":  return spinState == SpinState.OSS
        case "open": return spinState in (SpinState.OSS, SpinState.OPEN)
        case "metal": return bool(elements & MetalSymbols())
    if selector in _MULTIPLICITY_NAMES:
        return multiplicity == _MULTIPLICITY_NAMES[selector]
    return multiplicity == int(selector[1:])


@dataclass
class RouteTemplate:
    """A method's route (keylists.cusf) split into its unconditional part and its spin-state groups. A method with
    stages ('route -> route') is its first stage, carrying the rest in `later`: everything that takes a template works
    on stage 1, and jobs.RenderStages renders them all."""
    base: str                                   # clean route: no tags, no groups
    tags: list[str] = field(default_factory=list)
    groups: list[tuple[frozenset[str], str, list[str]]] = field(default_factory=list)  # (selectors, keywords, tags)
    method: str = ""                            # picks the program (extensionGetter) and names benchmark files
    blocks: list[str] = field(default_factory=list)  # literal ORCA %blocks written after the tag blocks (a re-run's own)
    later: list["RouteTemplate"] = field(default_factory=list)  # stages 2+, in order
    newChk: bool = False                        # %newchk: this stage starts a new checkpoint

    @property
    def stages(self) -> list["RouteTemplate"]:
        return [self, *self.later]


def _GroupSelectors(selectorText: str) -> tuple[frozenset[str], list[str]]:
    """A [group]'s selectors, and the invalid ones ('<empty>' for a group with none)."""
    selectors = frozenset(s.strip().lower() for s in selectorText.split(",") if s.strip())
    return selectors, sorted(s for s in selectors if not _IsValidSelector(s)) or ([] if selectors else ["<empty>"])


def UnknownSelectors(routeLine: str) -> list[str]:
    """Selectors in a route card's [groups] that ParseRouteTemplate would reject (it drops those groups)."""
    return [invalid for selectorText, _ in _ROUTE_GROUP_RE.findall(routeLine)
            for invalid in _GroupSelectors(selectorText)[1]]


def ParseRouteTemplate(routeLine: str) -> RouteTemplate:
    """A route card as a template: each stage ('route -> route') parsed on its own (groups, tags, %newchk), the first
    carrying the rest."""
    first, *later = [_ParseStage(stage) for stage in _STAGE_SPLIT_RE.split(routeLine.strip()) if stage.strip()] or [
        _ParseStage("")]
    first.later = later
    return first


def _ParseStage(routeLine: str) -> RouteTemplate:
    newChk = _NEW_CHECKPOINT_RE.search(routeLine) is not None
    routeLine = _NEW_CHECKPOINT_RE.sub(" ", routeLine)
    groups = []
    for selectorText, content in _ROUTE_GROUP_RE.findall(routeLine):
        selectors, invalid = _GroupSelectors(selectorText)
        if invalid:
            console.print(f"[warning]\\[config] Unknown selector(s) {invalid} in the route "
                          f"'{escape(routeLine)}'. Ignoring that group.[/warning]")
            continue
        keywords, tags = SplitRouteTags(content)
        groups.append((selectors, keywords, tags))
    base, tags = SplitRouteTags(_ROUTE_GROUP_RE.sub(" ", routeLine))
    if _ROUTE_LEAK_RE.search(base):
        console.print(f"[warning]\\[config] Malformed \\[ ] group or unknown % directive in the route "
                      f"'{escape(routeLine)}'. Jobs using it will be skipped.[/warning]")
    # The first token without Gaussian's '/basis' half, which would otherwise put the basis (and a '/') in filenames
    method = base.split()[0].split("/")[0] if base else ""
    return RouteTemplate(base, tags, groups, method, newChk=newChk)


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

    # Gaussian's compound methods are never U-prefixed: they run unrestricted for an open shell on their own. RO (a
    # high-spin species, openShellReference = "RO") takes their RO form where Gaussian has one (ROCBS-QB3); otherwise
    # the route is left as written
    compound = next(((index, match) for index, token in enumerate(tokens)
                     if (match := _COMPOUND_METHOD_RE.fullmatch(token))), None)
    if compound:
        index, match = compound
        if match.group(1) or reference != "RO" or match.group(2).upper() not in _RO_COMPOUNDS:
            return tokens
        return tokens[:index] + ["RO" + tokens[index]] + tokens[index + 1:]

    # Gaussian: prefix the method token, the first known one (a route Gaussian echoes often leads with its keywords:
    # 'opt freq B3LYP 6-31G(d)'), else the first token. Only the part before any '/' is the method
    position = next((index for index, token in enumerate(tokens) if SplitReference(MethodKey(token))), 0)
    method = MethodKey(tokens[position])
    found = SplitReference(method)
    # Known method behind a U/RO/R prefix, or an unlisted method that already starts with U/RO: already declared
    if (found and found[0]) or (not found and method.startswith(("U", "RO"))):
        return tokens
    return tokens[:position] + [reference + tokens[position]] + tokens[position + 1:]


def _KeywordName(token: str) -> str:
    return _KEYWORD_SPLIT_RE.split(token, maxsplit=1)[0].upper()


def _KeywordOptions(token: str) -> list[str]:
    """A keyword's options as written: 'opt=(ts,calcfc)' and 'opt(ts,calcfc)' -> ['ts', 'calcfc']; 'freq' -> []."""
    rest = token[len(_KEYWORD_SPLIT_RE.split(token, maxsplit=1)[0]):].lstrip("=")
    rest = rest[1:-1] if rest.startswith("(") and rest.endswith(")") else rest
    return [option for option in rest.split(",") if option]


def _AddKeyword(tokens: list[str], token: str) -> None:
    """A group's keyword joins the route: a keyword the route already has gets its options merged into the route's own
    (freq + freq=noraman -> freq=noraman; opt=(ts,calcfc) + opt=noeigen -> opt=(ts,calcfc,noeigen)), never a second
    copy; anything else is appended. Method tokens (with '/') are never merged."""
    name = _KeywordName(token)
    index = next((i for i, existing in enumerate(tokens) if "/" not in existing and "/" not in token
                  and _KeywordName(existing) == name), None)
    if index is None:
        if token.upper() not in {existing.upper() for existing in tokens}:
            tokens.append(token)
        return
    existing = tokens[index]
    options = _KeywordOptions(existing)
    added = [option for option in _KeywordOptions(token) if option.lower() not in {o.lower() for o in options}]
    if not added:
        return
    options += added
    keyword = _KEYWORD_SPLIT_RE.split(existing, maxsplit=1)[0]
    tokens[index] = f"{keyword}={options[0]}" if len(options) == 1 else f"{keyword}=({','.join(options)})"


def RenderRoute(template: RouteTemplate, molecule) -> tuple[str, list[str]]:
    """Render a route template for one molecule. Returns (route, tags): an ORCA job's ORCA Blocks tags, a Gaussian
    job's Mixed Basis set (and {smd} when the project's solvent can't be written: JobFileProblem says why).

    Additive: base route + unconditional tags, then every matching group in written order (spin state, or [metal: ...]
    by the molecule's elements; a keyword the route has already gets the group's options merged in), then the project's
    solvent for {smd} (solvent.py; nothing in the gas phase), then the U/RO reference. Tags keep their first occurrence.
    """
    from . import solvent
    try:
        multiplicity = int(molecule.multiplicity)
    except (TypeError, ValueError):
        multiplicity = 0
    elements = set()
    if template.groups and isinstance(molecule.coordinateList, list):
        from .fileops import MoleculeElements
        elements = MoleculeElements(molecule.coordinateList)
    tokens, tags = template.base.split(), list(template.tags)
    for selectors, keywords, groupTags in template.groups:
        if not any(_SelectorMatches(s, molecule.spinState, multiplicity, elements) for s in selectors):
            continue
        for token in keywords.split():
            _AddKeyword(tokens, token)
        tags += [tag for tag in groupTags if tag not in tags]
    if solvent.SMD_TAG in tags:
        program = "orca" if molecule.extensionType == Defaults.orcaExtension else "gaussian"
        problem = solvent.Problem(program)
        if not solvent.ProjectSolvent():
            tags.remove(solvent.SMD_TAG)
        elif not problem and program == "gaussian":
            tags.remove(solvent.SMD_TAG)
            _AddKeyword(tokens, solvent.GaussianKeyword())
        elif not problem and not any(_CPCM_RE.fullmatch(token) for token in tokens):
            # ORCA: the SMD block (JobInputText writes it for the tag) needs CPCM on the ! line
            tokens.append("CPCM")
    tokens = _ApplyReference(tokens, molecule.spinState, molecule.extensionType)
    return " ".join(tokens), tags


def MatchTemplate(routeLine: str, molecule) -> RouteTemplate | None:
    """The Single Point Methods entry that renders to this (already rendered) route for this molecule, so a re-run can
    recover its orcablocks tags. None if no entry matches. Shared by genReRun and the TUI's Re-run preview."""
    tokens = routeLine.upper().split()
    return next((template for template in Catalog.templates
                 if RenderRoute(template, molecule)[0].upper().split() == tokens), None)


class Catalog:
    # ── Data derived from Defaults at startup ────────────────────────────────
    benchmarkLines = []     # the Single Point Methods in force (keylists.cusf, keylists.Keylist), as written
    templates = []          # templates[i] = parsed benchmarkLines[i] (.base clean route, .method); see RenderRoute
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

        # Benchmark method lines: the nearest keylists.cusf level with Single Point Methods. Tags and groups are parsed
        # out here so no downstream code ever sees them
        from .keylists import BENCHMARK, Keylist
        cls.benchmarkLines = list(Keylist(BENCHMARK))
        cls.templates = [ParseRouteTemplate(line) for line in cls.benchmarkLines]
        cls.canBench  = len(cls.templates) > 1
