#!/usr/bin/env python3

testSet = set()
testSet.add(("Thing","0"))
testSet.add(("Thing","1"))
testSet.add(("Stuff","0"))
testSet.add("Single")
for test in testSet:
    print(test)
iterSet = testSet.copy()
for test in iterSet:
    if test[0] == "Thing":
        testSet.remove(test)
print(testSet)
setTwo = set()
setTwo.add("Stuff")
testCopy = testSet.copy()
for value in testCopy:
    if value[0] in setTwo:
        testSet.remove(value)
print(testSet)