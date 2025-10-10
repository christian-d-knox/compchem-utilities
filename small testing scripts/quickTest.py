#!/usr/bin/env python3

import os
import subprocess

# Simulates submitted jobs
setOne = {"7a", "8a", "9a", "10a"}
# Simulates jobs in queue
setTwo = {"8a", "10a", "9a", "7a","8a", "10a", "9a", "7a"}
# Testing empty set generation
setThree = set()
# Comparing SUBMITTED jobs to RUNNING jobs
setDifference = setOne.difference(setTwo)

print(setOne)
# Dupe entries are automatically removed/ignored
print(len(setTwo))
print(setTwo)
print(setThree)
print(setDifference)

# Testing adding an element from another set
#setThree.add(setOne[0])
# Adding a random string
randomString = "Hullabaloo"
setThree.add(randomString)
print(setThree)
# Suppose a job finished
setTwo.remove("9a")
setDifference = setOne.difference(setTwo)
print(setOne)
print(setDifference)

superLongString = "10a and is not for if yet so"
# The original string is still kept in full. Incoming line optimizations for main CompUtils for sure
setTwo.remove(superLongString.split()[0])
print(superLongString)
setDifference = setOne.difference(setTwo)
print(setDifference)

# Difference_update shrinks set to ONLY what's different
# This is because "update" causes the CALLED set() object to be edited
# error termination
# fatal error