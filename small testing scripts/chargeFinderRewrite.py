#!/usr/bin/env python3

import regex
from contextlib import closing
from mmap import mmap, ACCESS_READ

chargeLine = "Charge"
chargeLineBytes = chargeLine.encode()
with open("benzene.out", 'r') as geomFile:
    with closing(mmap(geomFile.fileno(), 0, access=ACCESS_READ)) as data:
        chargeLineLocation = regex.search(chargeLineBytes, data)
        pointer = chargeLineLocation.starts()
        data.seek(pointer[0])
        targetLine = data.readline().decode()
        chargeSub = targetLine.strip().split()
        # This chunk handles the special case where a stupid non-breaking space is used for neutral charges?
        if chargeSub[2] == '':
            del chargeSub[2]
        charge = chargeSub[2]
        multiplicity = chargeSub[5]
print(str(charge) + " " + str(multiplicity))