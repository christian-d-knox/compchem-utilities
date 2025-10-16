#!/usr/bin/env python3
import regex
from contextlib import closing
from mmap import mmap, ACCESS_READ
import os

stuff = [("apple","1"),("banana","2"),("sugar","triangle"),("spice","oblong"),("and everything nice","dillydally")]
things = ["apple","perriwinkle","shacka lacka","spice","pumpkin pie","sugar","bugs"]
thingsCopy = things.copy()

for index in range(len(thingsCopy)):
    for job in stuff:
        convergeCriteria = "Unknown"
        if job[0] == thingsCopy[index]:
            print("Matched " + job[0] + " with " + thingsCopy[index])
            if os.path.isfile(job[1]):
                print(job[1] + " is in fact a file")
                with open(job[1],'r+') as file:
                    print("Opened file " + job[1])
                    with closing(mmap(file.fileno(),0,access=ACCESS_READ)) as data:
                        tableHeader = "Simulated header"
                        tableBytes = tableHeader.encode()
                        finalTableHeader = regex.search(tableBytes, data, regex.REVERSE)
                        if len(finalTableHeader.group().decode()) != 0:
                            convergeCriteria = 0
                            pointer = finalTableHeader.ends()
                            data.seek(pointer[0])
                            data.read(2)
                            convergeMet = []
                            for outdex in range(0,4):
                                convergeLine = data.readline().decode()
                                convergeMet.append(convergeLine.split()[4])
                                convergeCriteria = convergeMet.count("YES")
                print("Job " + str(thingsCopy[index]) + " is currently running, and has converged on " + str(convergeCriteria) + " out of 4 criteria. Current duration is nonsense")
                things.remove(things[index])
                break