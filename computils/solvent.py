"""A project's solvent (Defaults.solvent, project.toml's `solvent`), written in each program's own form.

A route or CREST option line asks for the project's solvent with a tag instead of typing one: `{smd}` in a Gaussian or
ORCA route (catalog.RenderRoute), `{alpb}` in CREST's options (crest.RenderOptions). Without a project solvent the tag
writes nothing (the gas phase), so one method entry serves every project:
    Gaussian  SCRF=(SMD,Solvent=TetraHydroFuran)
    ORCA      CPCM on the ! line, then %cpcm smd true SMDsolvent "thf" end
    CREST     --alpb thf
The names are each program's documented ones: Gaussian 16's SCRF solvent list (its keywords are case-insensitive),
ORCA 6.0's table of implicit-solvation solvents (manual section 7.50, the SMD column), and xtb's ALPB table (CREST
passes --alpb to xtb). A solvent a program doesn't have is a skip reason for that program's jobs, never a guess.
"""
from dataclasses import dataclass

import regex

from .defaults import Defaults

SMD_TAG = "smd"        # {smd}: the project's solvent by SMD (Gaussian, ORCA)
ALPB_TAG = "alpb"      # {alpb}: the project's solvent by ALPB (CREST)


@dataclass(frozen=True)
class Solvent:
    name: str           # how CompUtils names it (messages)
    gaussian: str       # Gaussian 16's SCRF Solvent= keyword
    orca: str           # ORCA's SMDsolvent name
    alpb: str = ""      # xtb's ALPB name ('' when ALPB has none)


# (the names a project may write, lower case), then the solvent
_TABLE: list[tuple[tuple[str, ...], Solvent]] = [
    (("water", "h2o"), Solvent("Water", "Water", "water", "water")),
    (("thf", "tetrahydrofuran"), Solvent("THF", "TetraHydroFuran", "thf", "thf")),
    (("toluene",), Solvent("Toluene", "Toluene", "toluene", "toluene")),
    (("dichloromethane", "dcm", "ch2cl2"), Solvent("Dichloromethane", "Dichloromethane", "ch2cl2", "ch2cl2")),
    (("acetonitrile", "mecn", "ch3cn"), Solvent("Acetonitrile", "Acetonitrile", "acetonitrile", "acetonitrile")),
    (("methanol", "meoh"), Solvent("Methanol", "Methanol", "methanol", "methanol")),
    (("ethanol", "etoh"), Solvent("Ethanol", "Ethanol", "ethanol", "ethanol")),
    (("dmso", "dimethylsulfoxide", "dimethyl sulfoxide"), Solvent("DMSO", "DiMethylSulfoxide", "dmso", "dmso")),
    (("dmf", "dimethylformamide", "n,n-dimethylformamide"),
     Solvent("DMF", "n,n-DiMethylFormamide", "dmf", "dmf")),
    (("chloroform", "chcl3"), Solvent("Chloroform", "Chloroform", "chloroform", "chcl3")),
    (("benzene",), Solvent("Benzene", "Benzene", "benzene", "benzene")),
    (("diethyl ether", "diethylether", "ether", "et2o"), Solvent("Diethyl Ether", "DiethylEther", "diethyl ether",
                                                                "ether")),
    (("acetone",), Solvent("Acetone", "Acetone", "acetone", "acetone")),
    (("hexane", "n-hexane"), Solvent("n-Hexane", "n-Hexane", "n-hexane", "hexane")),
    (("heptane", "n-heptane"), Solvent("n-Heptane", "Heptane", "n-heptane")),
    (("ccl4", "carbon tetrachloride"), Solvent("Carbon Tetrachloride", "CarbonTetraChloride", "ccl4")),
    (("dioxane", "1,4-dioxane"), Solvent("1,4-Dioxane", "1,4-Dioxane", "1,4-dioxane", "dioxane")),
    (("pyridine",), Solvent("Pyridine", "Pyridine", "pyridine")),
    (("ethyl acetate", "ethylacetate", "etoac"), Solvent("Ethyl Acetate", "EthylEthanoate", "ethyl acetate",
                                                        "ethylacetate")),
    (("1,2-dichloroethane", "dichloroethane", "dce"), Solvent("1,2-Dichloroethane", "DiChloroEthane",
                                                              "1,2-dichloroethane")),
    (("chlorobenzene",), Solvent("Chlorobenzene", "ChloroBenzene", "chlorobenzene")),
    (("nitromethane", "meno2"), Solvent("Nitromethane", "NitroMethane", "nitromethane", "nitromethane")),
    (("2-propanol", "isopropanol", "ipa"), Solvent("2-Propanol", "2-Propanol", "2-propanol")),
    (("cyclohexane",), Solvent("Cyclohexane", "CycloHexane", "cyclohexane")),
]
_BY_NAME = {alias: solvent for aliases, solvent in _TABLE for alias in aliases}


def Lookup(name: str) -> Solvent | None:
    """A solvent by any name the table knows (case and spacing ignored)."""
    return _BY_NAME.get(" ".join(name.strip().lower().split()))


def Known() -> list[str]:
    return [solvent.name for _, solvent in _TABLE]


def ProjectSolvent() -> str:
    """The solvent in force here, as written ('' for the gas phase)."""
    return Defaults.solvent.strip()


def Problem(program: str) -> str:
    """Why the project's solvent can't be written for a program ('gaussian', 'orca', 'alpb'), or ''."""
    name = ProjectSolvent()
    if not name:
        return ""
    solvent = Lookup(name)
    if solvent is None:
        return f"Unknown Solvent '{name}' (project.toml solvent; CompUtils Knows {', '.join(Known())})"
    if not getattr(solvent, program):
        return f"{solvent.name} Has No {'ALPB' if program == 'alpb' else program.title()} Solvent Model"
    return ""


def GaussianKeyword() -> str:
    """'SCRF=(SMD,Solvent=X)', or '' for the gas phase or a solvent Gaussian lacks (Problem says why)."""
    solvent = Lookup(ProjectSolvent()) if ProjectSolvent() else None
    return f"SCRF=(SMD,Solvent={solvent.gaussian})" if solvent else ""


def OrcaBlock() -> str:
    """ORCA's SMD block for the project's solvent ('' without one)."""
    solvent = Lookup(ProjectSolvent()) if ProjectSolvent() else None
    return f'%cpcm\n    smd true\n    SMDsolvent "{solvent.orca}"\nend\n' if solvent else ""


def AlpbOptions() -> str:
    """'--alpb X', or '' for the gas phase or a solvent ALPB lacks (Problem says why)."""
    solvent = Lookup(ProjectSolvent()) if ProjectSolvent() else None
    return f"--alpb {solvent.alpb}" if solvent and solvent.alpb else ""


def HasTag(text: str, tag: str) -> bool:
    return regex.search(r"\{" + tag + r"\}", text, regex.IGNORECASE) is not None
