import os
from contextlib import closing
from mmap import mmap, ACCESS_READ
import pandas, regex

from .console  import console
from .catalog  import Catalog

# Because everyone hates remembering manuals. Walks through the most common use-cases with catch-all final custom keylist
def goodVibesInteractive() -> list[str]:
    keyList = ["-v", "1.0"]
    isQuasiHarmonic = str(input("Utilize quasiharmonic S and H correction (Grimme)? (y/n)"))
    if isQuasiHarmonic.lower() == "y":
        keyList.append("-q")
    isFreqCut = str(input("Utilize a frequency cutoff? (y/n)"))
    if isFreqCut.lower() == "y":
        freqCutoff = float(input("Enter the frequency cutoff (wavenumbers): "))
        keyList.extend(["-f", str(freqCutoff)])
    isTempCorrection = str(input("Utilize a temperature correction? (y/n)"))
    if isTempCorrection.lower() == "y":
        tempCorrection = float(input("Enter temperature (K): "))
        keyList.extend(["-t", str(tempCorrection)])
    isConcCorrection = str(input("Utilize a concentration correction? (y/n)"))
    if isConcCorrection.lower() == "y":
        concCorrection = float(input("Enter concentration (mol/l): "))
        keyList.extend(["-c", str(concCorrection)])
    isVibeScale = str(input("Utilize a non-default (i.e. not 1.0) vibrational scale factor? (y/n)"))
    if isVibeScale.lower() == "y":
        vibeScale = float(input("Enter vibrational scale factor: "))
        keyList.extend(["-v", str(vibeScale)])
    isSinglePoint = str(input("Run program with single point corrections? (y/n)"))
    if isSinglePoint.lower() == "y":
        isNonDefault = str(input("Is your filemask pattern different than the CompUtils default (_SP)? (y/n)"))
        if isNonDefault.lower() == "y":
            singlePoint = str(input("Enter your filemask pattern without the underscore:"))
            keyList.extend(["--spc", str(singlePoint)])
        else:
            keyList.extend(["--spc", "SP"])
    isNonCommonKeys = str(input("Do you want to run with additional, less common keys? (y/n)"))
    if isNonCommonKeys.lower() == "y":
        nonCommonKeys = input("Enter all of your non-common keys exactly as GoodVibes must receive them, separated by spaces.")
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