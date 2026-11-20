from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import regex

import pandas

from .defaults import Defaults
from .prompts import AskBool, AskFloat, AskStr
from .fileops import ExtractFrom, ExtractGoodVibes
from .        import agent

# GoodVibes 3.2 (the conda build) writes its table here, in the CWD
GOODVIBES_OUTPUT = "Goodvibes_output.dat"
# GoodVibes' own default temperature: -t is only passed for any other value
DEFAULT_TEMPERATURE = 298.15
# The GoodVibes Builder's starting values, which `cu -gv` without the wizard uses too: Head-Gordon's
# quasi-harmonic enthalpy with its cutoff, Grimme's entropy, a scale factor of 1.0, no concentration correction
DEFAULT_CUTOFF, DEFAULT_SCALE, DEFAULT_CONCENTRATION = 100.0, 1.0, 1.0

# The consistency check: a first GoodVibes pass, --check with no corrections (only the SP pairing), before the
# real run. Its 'x' lines in these categories stop the real run unless waived (-gv --gv-allow, the Builder's Allow rows):
# they mean the numbers wouldn't be comparable. Every other 'x' line (charge and multiplicity, which differ in any
# reaction; program versions; duplicates; GoodVibes' TS/GS guess; linear molecules; concentration notes) is a warning.
# category -> (label, what its line says in GoodVibes 3.2)
CHECK_CATEGORIES = {
    "theory":     ("Methods", r"Different levels of theory"),
    "solvation":  ("Solvation", r"Different solvation models|combination of gas and solvent phases|Different solvents used"),
    "dispersion": ("Dispersion", r"Different dispersion models"),
    "geometry":   ("SP Geometries", r"differences found between frequency and single-point geometries"
                                    r"|geometries from single-point corrections are missing"),
}
ALLOW_ALL = "all"
# Where GoodVibes' checks start (the table above them can carry 'x' rows of its own)
CHECKS_HEADER = "Checks for thermochemistry calculations"


@dataclass
class CheckFinding:
    """One 'x' line of GoodVibes' check, with its continuation lines (each group's files). category: a key of
    CHECK_CATEGORIES (stops the run unless waived), or '' (a warning)."""
    category: str
    text:     str


def CheckArguments(intent) -> list[str]:
    """The check pass's goodvibes flags: --check, and the SP suffix it pairs files by. No corrections (the first pass is
    only a safety layer; the real one carries them)."""
    return ["--check", *(["--spc", intent.singlePointPattern] if intent.singlePointPattern else [])]


def ReadCheck(output: str) -> list[CheckFinding]:
    """The findings in a check pass's printout: each 'x' line after the checks header, joined with the indented lines
    that follow it, classified by CHECK_CATEGORIES."""
    if CHECKS_HEADER not in output:
        return []
    findings, current = [], None
    for line in output.split(CHECKS_HEADER, 1)[1].splitlines():
        if line.startswith("x  "):
            text = regex.sub(r"^Caution[!:]\s*", "", line[3:].strip())
            category = next((key for key, (_, pattern) in CHECK_CATEGORIES.items() if regex.search(pattern, text)), "")
            current = CheckFinding(category, text)
            findings.append(current)
        elif current is not None and line.startswith("  ") and line.strip() and not line.strip().startswith("*"):
            # A group of files ('-B3LYP/6-31G(d): a.out, b.out'), or the next names of a long line
            group = line.strip().lstrip("-")
            current.text += (" " if current.text.endswith(":") else "; ") + group
        elif line.strip():
            # An 'o' or '-' line, a section header or the stars: the finding above it is complete
            current = None
    return findings


def AllowArgument(text: str) -> list[str]:
    """--gv-allow's categories (comma separated), checked: [ALLOW_ALL] skips the check pass."""
    allowed = [word.strip().lower() for word in text.split(",") if word.strip()]
    unknown = [word for word in allowed if word not in CHECK_CATEGORIES and word != ALLOW_ALL]
    if unknown or not allowed:
        raise agent.UsageError(f"--gv-allow takes {', '.join(CHECK_CATEGORIES)} or {ALLOW_ALL}, separated by commas "
                               f"(got '{text}').")
    return [ALLOW_ALL] if ALLOW_ALL in allowed else allowed


def GoodVibesDefaults(draft, suffix: str | None) -> None:
    """Fill a draft with the Builder's starting values (and the single point suffix it would detect), for a run
    without the wizard: an agent's, or any run given a GoodVibes flag."""
    draft.headGordonEnthalpy, draft.freqCutoff, draft.truhlarEntropy = True, DEFAULT_CUTOFF, False
    draft.tempCorrection, draft.concCorrection, draft.vibeScale = DEFAULT_TEMPERATURE, None, DEFAULT_SCALE
    draft.allowDifferences, draft.singlePointPattern, draft.extraKeys = [], suffix, None


# Because everyone hates remembering manuals. Walks through the most common use-cases with catch-all final custom keylist.
# Fills the GoodVibes fields of an IntentDraft; GoodVibesArguments turns them into the goodvibes command
def goodVibesInteractive(draft) -> None:
    draft.headGordonEnthalpy = AskBool("Apply the Head-Gordon quasi-harmonic enthalpy correction?", "Y", key="gv-qh")
    # The cutoff is asked (and passed) only with the Head-Gordon correction, as in the TUI
    if draft.headGordonEnthalpy and AskBool("Utilize a frequency cutoff?", "Y", key="gv-use-cutoff"):
        draft.freqCutoff = AskFloat("Enter the frequency cutoff (wavenumbers)", 100, key="gv-cutoff")
    if AskBool("Utilize a temperature correction?", "N", key="gv-use-temperature"):
        draft.tempCorrection = AskFloat("Enter temperature (K)", key="gv-temperature")
    if AskBool("Utilize a concentration correction?", "N", key="gv-use-concentration"):
        draft.concCorrection = AskFloat("Enter concentration (mol/l)", key="gv-concentration")
    if AskBool("Utilize a non-default (i.e. not 1.0) vibrational scale factor?", "N", key="gv-use-scale"):
        draft.vibeScale = AskFloat("Enter vibrational scale factor", key="gv-scale")
    if AskBool("Run program with single point corrections?", "Y", key="gv-use-spc"):
        default = Defaults.singlePointExtra.lstrip("_")
        isNonDefault = AskBool(f"Is your filemask pattern different than the CompUtils default (_{default})?", "N", key="gv-custom-spc")
        draft.singlePointPattern = AskStr("Enter your filemask pattern without the underscore", key="gv-spc") if isNonDefault else default
    if AskBool("Do you want to run with additional, less common keys?", "N", key="gv-use-extra"):
        draft.extraKeys = AskStr("Enter all of your non-common keys exactly as GoodVibes must receive them, separated by spaces.", key="gv-extra")


def GoodVibesArguments(intent) -> list[str]:
    """The goodvibes flags for a GoodVibesIntent (or a draft): what the CLI runs and the TUI previews."""
    arguments = []
    # No scale factor leaves GoodVibes to look one up from the level of theory
    # A scale factor reads as one (1.0); the other values as short as they go (100, 298.15)
    if intent.vibeScale is not None:        arguments += ["-v", str(intent.vibeScale)]
    # 3.2 always applies Grimme's quasi-harmonic entropy; -q adds Head-Gordon's enthalpy
    if intent.headGordonEnthalpy:           arguments.append("-q")
    # The cutoff is a sub-option of the Head-Gordon correction; without it, GoodVibes' default (100) applies
    if intent.headGordonEnthalpy and intent.freqCutoff is not None:
        arguments += ["-f", f"{intent.freqCutoff:g}"]
    if intent.tempCorrection is not None and intent.tempCorrection != DEFAULT_TEMPERATURE:
        arguments += ["-t", f"{intent.tempCorrection:g}"]
    if intent.concCorrection is not None:   arguments += ["-c", f"{intent.concCorrection:g}"]
    if intent.truhlarEntropy:               arguments += ["--qs", "truhlar"]
    if intent.singlePointPattern:           arguments += ["--spc", intent.singlePointPattern]
    if intent.extraKeys:                    arguments += intent.extraKeys.split()
    return arguments


def SpcPartners(files: list[Path], suffix: str | None) -> tuple[list[Path], list[Path], list[Path]]:
    """Split a selection into (structures, their single point files, structures without one).

    A single point file is name_<suffix> next to its structure; GoodVibes reads it by that name and stops if one is missing.
    """
    if not suffix:
        return list(files), [], []
    marker = f"_{suffix}"
    partners = [path for path in files if path.stem.endswith(marker)]
    inputs = [path for path in files if not path.stem.endswith(marker)]
    missing = [path for path in inputs if not path.with_name(path.stem + marker + path.suffix).exists()]
    return inputs, partners, missing


def Outputs(folder: Path) -> list[Path]:
    return sorted(path for path in folder.iterdir() if path.is_file() and path.suffix == Defaults.outputExtension)


def DetectSuffix(outputs: list[Path], among: set[str] | None = None) -> str | None:
    """The single point suffix the outputs use (without the underscore): the most common Y among stems X_Y whose X is
    also an output (among: only these X, e.g. a selection's structures). None when nothing pairs up."""
    stems = {path.stem for path in outputs}
    found = Counter(stem.rsplit("_", 1)[1] for stem in stems if "_" in stem
                    and stem.rsplit("_", 1)[0] in stems and (among is None or stem.rsplit("_", 1)[0] in among))
    return found.most_common(1)[0][0] if found else None


def SelectionSuffix(files: list[Path]) -> str | None:
    """The suffix a GoodVibes selection's structures use, among the CWD's outputs (a selected SP file counts for its
    structure): the GoodVibes Builder's and `cu -gv`'s starting value. None when nothing pairs up."""
    selected = {path.stem for path in files} | {path.stem.rsplit("_", 1)[0] for path in files}
    return DetectSuffix(Outputs(Path.cwd()), selected)


# An improved version of goodVibesToExcelv3 that now properly formats the numbers in Excel as numbers
def goodVibesProcessor(inputFile: Path) -> None:
    outputData = ExtractFrom(Path(inputFile), ExtractGoodVibes, empty=[])
    if len(outputData) < 2:
        agent.Fail(f"No GoodVibes table found in {inputFile}. No Excel file was written.")
        return
    dataFrame = pandas.DataFrame(outputData)
    # Sets the headers to the table header from GoodVibes
    dataFrame.columns = dataFrame.iloc[0]
    # Removes the header from the rest of the data
    dataFrame = dataFrame[1:]
    # This whole block is just to get the numbers to number properly
    writer = pandas.ExcelWriter("GoodVibes.xlsx", engine='xlsxwriter', engine_kwargs={'options': {'strings_to_numbers': True}})
    dataFrame.to_excel(writer, index=False)
    workBook = writer.book
    workSheet = writer.sheets['Sheet1']
    formatNumber = workBook.add_format({'num_format': '#,##0.000000'})
    workSheet.set_column('B:J', 12, formatNumber)
    writer.close()
    agent.Written("GoodVibes.xlsx")
