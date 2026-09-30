from pathlib import Path

import pandas

from .console  import console
from .defaults import Defaults
from .prompts import AskBool, AskFloat, AskStr
from .fileops import ExtractFrom, ExtractGoodVibes

# GoodVibes 3.2 (the conda build) writes its table here, in the CWD
GOODVIBES_OUTPUT = "Goodvibes_output.dat"
# GoodVibes' own default temperature: -t is only passed for any other value
DEFAULT_TEMPERATURE = 298.15


# Because everyone hates remembering manuals. Walks through the most common use-cases with catch-all final custom keylist.
# Fills the GoodVibes fields of an IntentDraft; GoodVibesArguments turns them into the goodvibes command
def goodVibesInteractive(draft) -> None:
    draft.headGordonEnthalpy = AskBool("Apply the Head-Gordon quasi-harmonic enthalpy correction?", "Y")
    if AskBool("Utilize a frequency cutoff?", "Y"):
        draft.freqCutoff = AskFloat("Enter the frequency cutoff (wavenumbers)", 100)
    if AskBool("Utilize a temperature correction?", "N"):
        draft.tempCorrection = AskFloat("Enter temperature (K)")
    if AskBool("Utilize a concentration correction?", "N"):
        draft.concCorrection = AskFloat("Enter concentration (mol/l)")
    if AskBool("Utilize a non-default (i.e. not 1.0) vibrational scale factor?", "N"):
        draft.vibeScale = AskFloat("Enter vibrational scale factor")
    if AskBool("Run program with single point corrections?", "Y"):
        default = Defaults.singlePointExtra.lstrip("_")
        isNonDefault = AskBool(f"Is your filemask pattern different than the CompUtils default (_{default})?", "N")
        draft.singlePointPattern = AskStr("Enter your filemask pattern without the underscore") if isNonDefault else default
    if AskBool("Do you want to run with additional, less common keys?", "N"):
        draft.extraKeys = AskStr("Enter all of your non-common keys exactly as GoodVibes must receive them, separated by spaces.")


def GoodVibesArguments(intent) -> list[str]:
    """The goodvibes flags for a GoodVibesIntent (or a draft): what the CLI runs and the TUI previews."""
    arguments = []
    # No scale factor leaves GoodVibes to look one up from the level of theory
    # A scale factor reads as one (1.0); the other values as short as they go (100, 298.15)
    if intent.vibeScale is not None:        arguments += ["-v", str(intent.vibeScale)]
    # 3.2 always applies Grimme's quasi-harmonic entropy; -q adds Head-Gordon's enthalpy
    if intent.headGordonEnthalpy:           arguments.append("-q")
    if intent.freqCutoff is not None:       arguments += ["-f", f"{intent.freqCutoff:g}"]
    if intent.tempCorrection is not None and intent.tempCorrection != DEFAULT_TEMPERATURE:
        arguments += ["-t", f"{intent.tempCorrection:g}"]
    if intent.concCorrection is not None:   arguments += ["-c", f"{intent.concCorrection:g}"]
    if intent.truhlarEntropy:               arguments += ["--qs", "truhlar"]
    if intent.checkConsistency:             arguments.append("--check")
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


# An improved version of goodVibesToExcelv3 that now properly formats the numbers in Excel as numbers
def goodVibesProcessor(inputFile: Path) -> None:
    outputData = ExtractFrom(Path(inputFile), ExtractGoodVibes, empty=[])
    if len(outputData) < 2:
        console.print(f"[error]No GoodVibes table found in {inputFile}. No Excel file was written.[/error]")
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
