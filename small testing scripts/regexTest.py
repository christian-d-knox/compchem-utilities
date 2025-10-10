#!/usr/bin/env python3

import re
from contextlib import closing
from mmap import mmap, ACCESS_READ

with open("bull.txt", "r+") as file:
    with closing(mmap(file.fileno(), 0, access=ACCESS_READ)) as data:
        goodLine = ""
        badLine = ""
        term = "Normal termination"
        termBytes = term.encode()
        goodMatch = re.search(termBytes, data)
        goodLine = goodMatch.group().decode("utf-8")
        if len(goodLine) > 0:
            print(goodLine)
        elif len(badLine) > 0:
            print(badLine)
        else:
            print("Something has gone horribly wrong")