#!/usr/bin/env python3

import pandas
import regex
from contextlib import closing
from mmap import mmap, ACCESS_READ

file = "Goodvibes_output.dat"
outputData = []
header = "Structure"
headerBytes = header.encode()
with open(file, 'r') as inFile:
    with closing(mmap(inFile.fileno(), 0, access=ACCESS_READ)) as data:
        headerLocation = regex.search(headerBytes,data,regex.IGNORECASE)
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
dataFrame.columns = dataFrame.iloc[0]
dataFrame = dataFrame[1:]
writer = pandas.ExcelWriter("GoodVibes.xlsx",engine='xlsxwriter',engine_kwargs={'options':{'strings_to_numbers':True}})
dataFrame.to_excel(writer,index=False)
workBook = writer.book
workSheet = writer.sheets['Sheet1']
formatNumber = workBook.add_format({'num_format':'#,##0.000000'})
workSheet.set_column('B:J',12,formatNumber)
writer.close()