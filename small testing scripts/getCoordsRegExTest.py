import time
import regex
from contextlib import closing
from mmap import mmap, ACCESS_READ

inputFile = "benzene.out"
outputFileName = "benzene.xyz"

def getCoords(fileName, outputFileName):
    startTime = time.time()
    coordinateList = []
    atSymbol = {
        1: 'H', 2: 'He', 3: 'Li', 4: 'Be', 5: 'B', 6: 'C', 7: 'N', 8: 'O', 9: 'F', 10: 'Ne',
        11: 'Na', 12: 'Mg', 13: 'Al', 14: 'Si', 15: 'P', 16: 'S', 17: 'Cl', 18: 'Ar', 19: 'K',
        20: 'Ca', 21: 'Sc', 22: 'Ti', 23: 'V', 24: 'Cr', 25: 'Mn', 26: 'Fe', 27: 'Co', 28: 'Ni',
        29: 'Cu', 30: 'Zn', 31: 'Ga', 32: 'Ge', 33: 'As', 34: 'Se', 35: 'Br', 36: 'Kr',

        42: 'Mo', 44: 'Ru', 45: 'Rh', 46: 'Pd', 47: 'Ag', 48: 'Cd', 50: 'Sn', 51: 'Sb',
        53: 'I', 54: 'Xe', 77: 'Ir', 78: 'Pt', 79: 'Au', 80: 'Hg', 81: 'Tl', 82: 'Pb',
        83: 'Bi'
    }

    # Initialize local empty lists
    at = []
    X = []
    Y = []
    Z = []

    with open(fileName, 'r+') as inFile, open(outputFileName, 'w') as outputFile:
        with closing(mmap(inFile.fileno(), 0, access=ACCESS_READ)) as data:
            tableHeader = "                         Standard orientation:                         "
            tableBytes = tableHeader.encode()
            finalTableHeader = regex.search(tableBytes, data, regex.REVERSE)
            pointer = finalTableHeader.ends()
            data.seek(pointer[0])
            data.read(2)
            for index in range(4):
                data.readline()
            line = data.readline().decode().strip()
            while len(line.split()) > 2:
                # Extracts the Atomic Number, and X Y Z coordinates into their respective lists
                at.append(str(line.split()[1]))
                X.append(str(line.split()[3]))
                Y.append(str(line.split()[4]))
                Z.append(str(line.split()[5]))
                line = data.readline().decode().strip()

        outputFile.write(str(len(at))+"\nPointless Comment Line\n")
        for k in range(len(at)):
            # Ensures the list elements are integers for dictionary pairing
            at[k] = int(at[k])
            # Translates from Atomic Number to Atomic Symbol, along with ensuring all elements of each list are strings
            # Build the entire line to be written, and ensure it's properly formatted
            coordLine = str(atSymbol[at[k]]) + "   " + str(X[k]) + "   " + str(Y[k]) + "   " + str(Z[k]) + "\n"
            coordLine = coordLine.replace(' ', ' ')
            outputFile.write(coordLine)
            coordinateList.append(coordLine)
    endTime = time.time()
    processLength = str(round(endTime - startTime, 2))
    print("Time taken to scrape coordinates by modified method is " + processLength + " seconds.")
    return coordinateList

finalOutput = getCoords(inputFile, outputFileName)
for line in finalOutput:
    print(line)