import os
from contextlib import closing
from mmap import mmap, ACCESS_READ
import pandas, regex

from .console  import console
from .catalog  import Catalog
from .prompts import *

# Because everyone hates remembering manuals. Walks through the most common use-cases with catch-all final custom keylist
def goodVibesInteractive() -> list[str]:
    keyList = ["-v", "1.0"]
    isQuasiHarmonic = AskBool("Utilize quasiharmonic S and H correction (Grimme)?", "Y")
    if isQuasiHarmonic:
        keyList.append("-q")
    isFreqCut = AskBool("Utilize a frequency cutoff?", "Y")
    if isFreqCut:
        freqCutoff = AskFloat("Enter the frequency cutoff (wavenumbers)", 100)
        keyList.extend(["-f", str(freqCutoff)])
    isTempCorrection = AskBool("Utilize a temperature correction?", "N")
    if isTempCorrection:
        tempCorrection = AskFloat("Enter temperature (K)")
        keyList.extend(["-t", str(tempCorrection)])
    isConcCorrection = AskBool("Utilize a concentration correction?", "N")
    if isConcCorrection:
        concCorrection = AskFloat("Enter concentration (mol/l)")
        keyList.extend(["-c", str(concCorrection)])
    isVibeScale = AskBool("Utilize a non-default (i.e. not 1.0) vibrational scale factor?", "N")
    if isVibeScale:
        vibeScale = AskFloat("Enter vibrational scale factor")
        keyList.extend(["-v", str(vibeScale)])
    isSinglePoint = AskBool("Run program with single point corrections?", "Y")
    if isSinglePoint:
        isNonDefault = AskBool("Is your filemask pattern different than the CompUtils default (_SP)?", "N")
        if isNonDefault:
            singlePoint = AskStr("Enter your filemask pattern without the underscore")
            keyList.extend(["--spc", str(singlePoint)])
        else:
            keyList.extend(["--spc", "SP"])
    isNonCommonKeys = AskBool("Do you want to run with additional, less common keys?", "N")
    if isNonCommonKeys:
        nonCommonKeys = AskStr("Enter all of your non-common keys exactly as GoodVibes must receive them, separated by spaces.")
        keyList.extend(nonCommonKeys.split())
    return keyList

# An improved version of goodVibesToExcelv3 that now properly formats the numbers in Excel as numbers
def goodVibesProcessor(inputFile) -> None:
    outputData = []
    header = "Structure"
    headerBytes = header.encode()
    with open(inputFile, 'r') as inFile:
        with closing(mmap(inFile.fileno(), 0, access=ACCESS_READ)) as data:
            headerLocation = regex.search(headerBytes, data, regex.IGNORECASE)
            pointer = headerLocation.starts()
            data.seek(pointer[0])
            line = data.readline()
            tempSubs = line.decode().strip().split()
            outputData.append(tempSubs)
            data.readline()
            line = data.readline().decode().strip()
            while '*' not in line:
                subLines = line.split()
                subLines.pop(0)
                outputData.append(subLines)
                line = data.readline().decode().strip()
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