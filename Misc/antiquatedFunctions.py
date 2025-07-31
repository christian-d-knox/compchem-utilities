# Houkmol XYZ file Generator
# Originally scripted in AWK by Jan Lanbowski, translated to PERL and hacked by Paul Ha-Yeon Cheong
# Completely (painstakingly) re-written by Christian Drew Knox in Python
def getCoords(fileName, outputFileName):
    startTime = time.time()
    # Atomic Symbol dictionary for file creation
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

    # This opens both the *.out and the *.xyz files for reading and writing
    with open(os.path.abspath(fileName), 'r') as inFile, open(os.path.abspath(outputFileName), 'w') as outputFile:
        # Seek to the END of the *.out
        inFile.seek(0, 2)
        position = inFile.tell()
        # Initialize the loop termination condition
        foundCoords = False

        # Iterate through the file BACKWARDS to find the final instance of the atomic coordinates
        while foundCoords != True and position > 0:
            position -= 1
            if position >= 0:
                inFile.seek(position)
            line = inFile.readline().strip()
            lineSubs = line.split(" ")
            # All coordinate sections have a header like this (Input orientation?)
            if lineSubs[0] == 'Standard' and lineSubs[1] == 'orientation:':
                # Signals for loop termination once coordinates are extracted
                foundCoords = True
                # Skips the stupid lines between header and data
                for _ in range(4):
                    inFile.readline()
                # Pre-emptively read the first data line before the loop. Doing it the other way around breaks it.
                line = inFile.readline().strip()
                lineSubs = line.split()
                while len(lineSubs) > 2:
                    #print(len(lineSubs))
                    #print(lineSubs)
                    # Extracts the Atomic Number, and X Y Z coordinates into their respective lists
                    at.append(str(lineSubs[1]))
                    #print("Atom number is " + str(lineSubs[1]))
                    X.append(str(lineSubs[3]))
                    #print("X coord is " + str(lineSubs[3]))
                    Y.append(str(lineSubs[4]))
                    #print("Y coord is " + str(lineSubs[4]))
                    Z.append(str(lineSubs[5]))
                    #print("Z coord is " + str(lineSubs[5]))
                    # Reads the next line of data within the loop
                    line = inFile.readline().strip()
                    lineSubs = line.split()
            elif line:
                # Skips lines regardless of if they contain any text or not
                position -= 1
                inFile.seek(position)

        # This loops through the entire coordinate list to write to the XYZ file
        outputFile.write(str(len(at))+"\nPointless Comment Line")
        for k in range(len(at)):
            # Ensures the list elements are integers for dictionary pairing
            at[k] = int(at[k])
            # Translates from Atomic Number to Atomic Symbol, along with ensuring all elements of each list are strings
            atomSym = str(atSymbol[at[k]])
            xCoord = str(X[k])
            yCoord = str(Y[k])
            zCoord = str(Z[k])
            # Build the entire line to be written, and ensure it's properly formatted
            expectedOutput = atomSym + "   " + xCoord + "   " + yCoord + "   " + zCoord
            expectedOutput = expectedOutput.replace(' ', ' ')
            #print(expectedOutput)
            outputFile.write("\n " + expectedOutput)
        # Close XYZ upon termination
        outputFile.close()
        endTime = time.time()
        processLength = str(round(endTime - startTime, 2))
        cprint("Time taken to scrape coordinates by normal method is " + processLength + " seconds.", "light_cyan")