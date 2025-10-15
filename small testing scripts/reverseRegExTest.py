#!/usr/bin/env python3

import regex
from contextlib import closing
from mmap import mmap, ACCESS_READ

with open("testOpt.out", 'r+') as file:
    with closing(mmap(file.fileno(), 0, access=ACCESS_READ)) as data:
        tableHeader = "         Item               Value     Threshold  Converged?"
        tableBytes = tableHeader.encode()
        finalTableHeader = regex.search(tableBytes, data, regex.REVERSE)
        pointer = finalTableHeader.ends()
        data.seek(pointer[0])
        data.read(2)
        convergenceMet = []
        for index in range(0,4):
            convergeLine = data.readline().decode()
            convergenceMet.append(convergeLine.split()[4])
        convergeCriteria = convergenceMet.count("YES")
        print("Job has converged on " + str(convergeCriteria) + " out of 4 criteria.")