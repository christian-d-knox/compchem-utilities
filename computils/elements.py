"""Periodic-table lookup tables and geometry element/electron helpers."""
import regex

# Atomic number -> symbol, 1-103. Shared by ExtractCoords and the mixed basis element checks
ATOMIC_SYMBOLS = dict(enumerate((
    "H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn Ga Ge As Se Br Kr "
    "Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb "
    "Lu Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr"
).split(), start=1))
ELEMENT_SET = set(ATOMIC_SYMBOLS.values())
# Element symbol -> atomic number
ATOMIC_NUMBERS = {symbol: number for number, symbol in ATOMIC_SYMBOLS.items()}

_ELEMENT_TOKEN_RE = regex.compile(r"[A-Za-z]{1,2}")


def NormalizeElement(token: str) -> str:
    return token.lstrip("-").capitalize()


def Symbols(coordinateList: list[str]) -> list[str]:
    # A coordinate line's element (fileops.CoordinateLines writes the symbol first)
    return [line.split()[0] for line in coordinateList if line.split()]


def Formula(coordinateList: list[str]) -> str:
    """The molecular formula in Hill order (C, then H, then alphabetical; alphabetical without carbon): C12H10N2."""
    counts = {}
    for symbol in Symbols(coordinateList):
        counts[symbol] = counts.get(symbol, 0) + 1
    order = sorted(counts, key=lambda symbol: (("C", "H").index(symbol) if "C" in counts and symbol in ("C", "H")
                                               else 2, symbol))
    return "".join(f"{symbol}{counts[symbol] if counts[symbol] > 1 else ''}" for symbol in order)


def ElectronProblem(coordinateList: list[str], charge: int, multiplicity: int) -> str:
    """Why this charge and multiplicity can't describe the molecule ('' if they can): Gaussian and ORCA stop at once."""
    electrons = sum(ATOMIC_NUMBERS.get(symbol, 0) for symbol in Symbols(coordinateList)) - charge
    if electrons < 1:
        return f"Charge {charge} Leaves No Electrons"
    # Multiplicity 2S + 1: an even electron count has odd multiplicities, at most one more than the electrons
    if multiplicity < 1 or multiplicity > electrons + 1 or (electrons + multiplicity) % 2 == 0:
        return f"Multiplicity {multiplicity} Can't Fit {electrons} Electrons"
    return ""


def MoleculeElements(coordinateList: list[str]) -> set[str]:
    """Element symbols in a coordinate list. Handles atomic numbers, 'C(Fragment=1)', 'C-Bq' (ghost atoms still carry
    basis functions), 'C1' labels; drops bare 'Bq' and dummy 'X' atoms."""
    elements = set()
    for line in coordinateList:
        fields = line.split()
        if not fields:
            continue
        token = fields[0]
        if token.isdigit():
            symbol = ATOMIC_SYMBOLS.get(int(token), token)
        else:
            symbolMatch = _ELEMENT_TOKEN_RE.match(token)
            if symbolMatch is None:
                continue
            symbol = symbolMatch.group(0).capitalize()
        # Bq and X are not in the table, so ghost-only and dummy atoms are dropped here
        if symbol in ELEMENT_SET:
            elements.add(symbol)
    return elements
