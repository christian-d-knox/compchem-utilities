#!/usr/bin/env python3

import re
from contextlib import closing
from mmap import mmap, ACCESS_READ

terminationVariants = ["normal termination", "error termination", "terminated normally"]
finishedJobs = []

with open("benzene.out", "r+") as file:
    print("Opened benzene.out file.")
    with closing(mmap(file.fileno(), 0, access=ACCESS_READ)) as data:
        for termination in terminationVariants:
            termLine = ""
            termBytes = termination.encode()
            print(termBytes)
            termLine = re.search(termBytes, data, re.IGNORECASE)
            print(termLine)
            termLine = termLine.group().decode("utf-8")
            if len(termLine) > 0:
                finishedJobs.append(("benzene", termination))
                break

for job in finishedJobs:
    print(job)