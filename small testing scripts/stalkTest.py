#!/usr/bin/env python3

import os
#import sys
import subprocess
import time
#import re
from termcolor import cprint

duration = int(input("Enter the duration of the script in minutes: "))
frequency = float(input("Enter the frequency of the script in minutes (30s -> 0.5min): "))
testJobs = str(input("Enter some job names to help in testing: "))
testJobList = testJobs.split()
jobSet = set()
for job in testJobList:
    jobSet.add((job,"hulabaloo"))

def jobStalking(jobSet, duration, frequency):
    startTime = time.time()
    # Prints queue in format of JOBNAME STATUS NODE/REASON START_TIME CURRENT_DURATION
    command = ["squeue -h --me --format='%25j %10T %18R %S %20M'"]
    finishedJobs = set()
    while (time.time() - startTime) < duration * 60:
        stalkStatus = set()
        stalker = subprocess.run(command, shell=True, capture_output=True)
        result = stalker.stdout.splitlines()
        for index in range(len(result)):
            # Turns out that the queue commands return Byte objects, not Strings
            line = result[index].decode("utf-8")
            result[index] = line
            #modLine = line.replace("b'" ,"").replace("'" ,"")
            #result[index] = modLine
            # Adds job basename to stalkStatus for comparison
            stalkStatus.add(result[index].split()[0])
            stalkStatus.add("Stuff")

        for index in range(len(result)):
            match result[index].split()[1]:
                case "PENDING":
                    cprint("Job " + str
                        (result[index].split()[0]) + " is currently pending. Expected start time is " + str
                        (result[index].split()[3]), "light_yellow")
                    stalkStatus.remove(result[index].split()[0])
                case "RUNNING":
                    cprint("Job " + str
                        (result[index].split()[0]) + " is currently running. Current duration is " + str
                        (result[index].split()[4]), "light_magenta")
                    stalkStatus.remove(result[index].split()[0])

        jobCopy = jobSet.copy()
        for thing in jobCopy:
            if thing[0] in stalkStatus:
                jobSet.remove(thing)
                finishedJobs.add(thing)
        for thing in finishedJobs:
            print(thing)

        time.sleep(frequency * 60)

jobStalking(jobSet,duration,frequency)