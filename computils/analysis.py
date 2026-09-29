from pathlib import Path

import pandas

from .prompts import AskBool, AskFloat, AskStr
from .fileops import ExtractFrom, ExtractGoodVibes


# Because everyone hates remembering manuals. Walks through the most common use-cases with catch-all final custom keylist.
# Fills the GoodVibes fields of an IntentDraft; _DispatchGoodVibes turns them into the goodvibes command
def goodVibesInteractive(draft) -> None:
    draft.quasiharmonic = AskBool("Utilize quasiharmonic S and H correction (Grimme)?", "Y")
    if AskBool("Utilize a frequency cutoff?", "Y"):
        draft.freqCutoff = AskFloat("Enter the frequency cutoff (wavenumbers)", 100)
    if AskBool("Utilize a temperature correction?", "N"):
        draft.tempCorrection = AskFloat("Enter temperature (K)")
    if AskBool("Utilize a concentration correction?", "N"):
        draft.concCorrection = AskFloat("Enter concentration (mol/l)")
    if AskBool("Utilize a non-default (i.e. not 1.0) vibrational scale factor?", "N"):
        draft.vibeScale = AskFloat("Enter vibrational scale factor")
    if AskBool("Run program with single point corrections?", "Y"):
        isNonDefault = AskBool("Is your filemask pattern different than the CompUtils default (_SP)?", "N")
        draft.singlePointPattern = AskStr("Enter your filemask pattern without the underscore") if isNonDefault else "SP"
    if AskBool("Do you want to run with additional, less common keys?", "N"):
        draft.extraKeys = AskStr("Enter all of your non-common keys exactly as GoodVibes must receive them, separated by spaces.")

# An improved version of goodVibesToExcelv3 that now properly formats the numbers in Excel as numbers
def goodVibesProcessor(inputFile: Path) -> None:
    outputData = ExtractFrom(Path(inputFile), ExtractGoodVibes, empty=[])
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