"""Spin-state classification: CSS vs OSS vs high-spin (OPEN).

Checks run in priority order, first match wins. User-declared input always comes before inference:
    1. spinstates.txt override (project file)
    2. multiplicity > 1                          -> OPEN
    3. previous route card declares broken-symmetry -> OSS
    4. <S**2> spin contamination in the output   -> OSS   (the only heuristic)
    5. otherwise                                 -> CSS   (warns on an R -> U instability)
"""
import functools
from fnmatch import fnmatchcase
from pathlib import Path

import regex

from .actions  import SpinState
from .catalog  import IsKnownMethod
from .console  import console
from .defaults import Defaults
from .fileops  import MapFile, FindInMap, ExtractRouteLine, ExtractSpinContamination, HasRestrictedInstability
from .project  import ResolveProjectFile

# Only CSS/OSS can be forced; high-spin states come from the multiplicity
_OVERRIDE_STATES = {"css": SpinState.CSS, "oss": SpinState.OSS}


# Parsed once per run: [(rootName glob, state)], in file order
@functools.cache
def _LoadSpinOverrides(overridePath: Path) -> list[tuple[str, SpinState]]:
    overrides = []
    for lineNumber, line in enumerate(overridePath.read_text().splitlines(), start=1):
        fields = line.split("#", 1)[0].split()
        if not fields:
            continue
        if len(fields) != 2 or fields[1].lower() not in _OVERRIDE_STATES:
            console.print(f"[warning]{overridePath}:{lineNumber}: expected '<name or glob>  css|oss', got "
                          f"'{line.strip()}'. Ignoring this line.[/warning]")
            continue
        overrides.append((fields[0], _OVERRIDE_STATES[fields[1].lower()]))
    return overrides


def _RouteDeclaresBrokenSymmetry(data, extensionType: str) -> str:
    """Return a reason string if the previous route card already declared an unrestricted/BS singlet, else ''."""
    routeLine = ExtractRouteLine(data, extensionType)
    for token in routeLine.split():
        upperToken = token.upper()
        # ORCA: explicit unrestricted reference on the ! line
        if upperToken in ("UKS", "UHF"):
            return f"route has {token}"
        # Gaussian: U-prefixed known method (e.g. UB3LYP/6-31G(d)), or guess=mix
        methodPart = upperToken.split("/")[0].replace("(", "").replace(")", "")
        if methodPart.startswith("U") and IsKnownMethod(methodPart[1:]):
            return f"route has {token.split('/')[0]}"
        if regex.match(r"GUESS[=(]+\(?\s*MIX", upperToken):
            return f"route has {token}"
    # ORCA broken-symmetry block
    if FindInMap(data, r"BrokenSym", ignoreCase=True):
        return "input has BrokenSym"
    return ""


def ClassifySpin(sourcePath: Path, rootName: str, multiplicity, extensionType: str) -> tuple[SpinState, str]:
    """Classify one molecule. Returns (state, short reason for the log)."""
    try:
        multiplicityValue = int(multiplicity)
    except (TypeError, ValueError):
        multiplicityValue = None

    # 1. User override file. It only decides CSS vs OSS, so it can't apply to a high-spin molecule
    overridePath = ResolveProjectFile("spinstates.txt", False)
    if overridePath is not None:
        for pattern, state in _LoadSpinOverrides(overridePath):
            if fnmatchcase(rootName, pattern):
                if multiplicityValue is not None and multiplicityValue > 1:
                    console.print(f"[warning]{rootName}: spinstates.txt entry '{pattern}' ignored, because the "
                                  f"multiplicity is {multiplicityValue} (css/oss only apply to singlets).[/warning]")
                    break
                return state, f"spinstates.txt: {pattern}"

    # 2. Multiplicity
    if multiplicityValue is None:
        return SpinState.CSS, "multiplicity unknown, assuming closed-shell"
    if multiplicityValue > 1:
        return SpinState.OPEN, f"multiplicity {multiplicityValue}"

    # mmap can't map an empty file; nothing more to learn from it anyway
    if not sourcePath.is_file() or sourcePath.stat().st_size == 0:
        return SpinState.CSS, "multiplicity 1"

    with MapFile(sourcePath) as data:
        # 3. Previous route card (user-declared)
        routeReason = _RouteDeclaresBrokenSymmetry(data, extensionType)
        if routeReason:
            return SpinState.OSS, routeReason

        # 4. Spin contamination (heuristic, last resort)
        spinSquared = ExtractSpinContamination(data)
        if spinSquared is not None and spinSquared > Defaults.ossSpinThreshold:
            return SpinState.OSS, f"<S**2>={spinSquared:.3f}"

        # 5. Closed-shell, but flag an unresolved instability for the user to decide
        if HasRestrictedInstability(data):
            console.print(f"[warning]{rootName}: the output reports a restricted -> unrestricted instability, but it "
                          f"is being treated as closed-shell. Add '{rootName}  oss' to spinstates.txt if it is an "
                          f"open-shell singlet.[/warning]")
    reason = "multiplicity 1" if spinSquared is None else f"<S**2>={spinSquared:.3f}"
    return SpinState.CSS, reason
