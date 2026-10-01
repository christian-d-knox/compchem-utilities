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
from .catalog  import MethodKey, SplitReference
from .console  import console
from .defaults import Defaults
from .fileops  import ExtractSpinContamination, HasRestrictedInstability
from .project  import ResolveProjectFile

# Only CSS/OSS can be forced; high-spin states come from the multiplicity
_OVERRIDE_STATES = {"css": SpinState.CSS, "oss": SpinState.OSS}


def ParseSpinOverrides(text: str) -> tuple[list[tuple[str, SpinState]], list[tuple[int, str]]]:
    """spinstates.txt text -> ([(rootName glob, state)] in file order, [(line number, bad line)])."""
    overrides, problems = [], []
    for lineNumber, line in enumerate(text.splitlines(), start=1):
        fields = line.split("#", 1)[0].split()
        if not fields:
            continue
        if len(fields) != 2 or fields[1].lower() not in _OVERRIDE_STATES:
            problems.append((lineNumber, line.strip()))
            continue
        overrides.append((fields[0], _OVERRIDE_STATES[fields[1].lower()]))
    return overrides, problems


# Parsed once per run
@functools.cache
def _LoadSpinOverrides(overridePath: Path) -> list[tuple[str, SpinState]]:
    overrides, problems = ParseSpinOverrides(overridePath.read_text())
    for lineNumber, line in problems:
        console.print(f"[warning]{overridePath}:{lineNumber}: expected '<name or glob>  css|oss', got "
                      f"'{line}'. Ignoring this line.[/warning]")
    return overrides


def _RouteDeclaresBrokenSymmetry(molecule) -> str:
    """Return a reason string if the previous route card already declared an unrestricted/BS singlet, else ''."""
    for token in molecule.sourceRoute.split():
        upperToken = token.upper()
        # ORCA: explicit unrestricted reference on the ! line
        if upperToken in ("UKS", "UHF"):
            return f"Route Has {token}"
        # Gaussian: U-prefixed known method (e.g. UB3LYP/6-31G(d)), or guess=mix
        found = SplitReference(MethodKey(token))
        if found and found[0] == "U":
            return f"Route Has {token.split('/')[0]}"
        if regex.match(r"GUESS[=(]+\(?\s*MIX", upperToken):
            return f"Route Has {token}"
    # ORCA broken-symmetry block, in the input as written
    if regex.search(r"BrokenSym", molecule.sourceInput, regex.IGNORECASE):
        return "Input Has BrokenSym"
    return ""


def ClassifySpin(molecule, data) -> tuple[SpinState, str]:
    """Classify one molecule (fileops.ReadMolecule) from its source file's open map, the one ReadMolecule read.
    Returns (state, short reason for the log)."""
    rootName = molecule.rootName
    try:
        multiplicityValue = int(molecule.multiplicity)
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
        return SpinState.CSS, "Multiplicity Unknown, Assuming Closed-Shell"
    if multiplicityValue > 1:
        return SpinState.OPEN, f"Multiplicity {multiplicityValue}"

    # 3. Previous route card (user-declared)
    routeReason = _RouteDeclaresBrokenSymmetry(molecule)
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
    reason = "Multiplicity 1" if spinSquared is None else f"<S**2>={spinSquared:.3f}"
    return SpinState.CSS, reason
