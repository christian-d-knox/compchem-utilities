import os, subprocess, time, regex
from contextlib import closing
from mmap import mmap, ACCESS_READ
from pathlib import Path

from .console  import console
from .defaults import Defaults
from .catalog  import Catalog
from .notify   import NotifyPersonal

# Finally implemented in a way I can be proud of.
def jobStalking(jobSet: set, duration: int, frequency: int) -> None:
    startTime = time.monotonic()
    # Prints queue in format of JOBNAME STATUS NODE/REASON START_TIME CURRENT_DURATION courtesy of my own improved
    # obsessiveQueuev2
    command = ["squeue -h --me --format='%25j %10T %18R %S %20M'"]
    finishedJobs = []
    # Repeats every frequency over duration
    pingTime = time.monotonic()
    while (pingTime - startTime) < duration * 60:
        stalkStatus = set()
        # Tells the function that jobs you submitted are the ones to track
        for job in jobSet:
            stalkStatus.add(job[0])
        # Executes the qeueue command for further processing
        stalker = subprocess.run(command, shell=True, capture_output=True)
        result = stalker.stdout.splitlines()
        for index in range(len(result)):
            line = result[index].decode("utf-8")
            # noinspection PyTypeChecker
            result[index] = line
            # Adds job basename to stalkStatus for comparison
            stalkStatus.add(result[index].split()[0])

        # The heart of the magic, iterates over the output of the queue command using JOBNAME, STATUS, and CURRENT_DURATION
        for index in range(len(result)):
            match result[index].split()[1]:
                case "PENDING":
                    console.print("Job " + str(result[index].split()[0]) + " is currently pending. Expected start time is "
                       + str(result[index].split()[3])) #light_yellow warning
                    stalkStatus.remove(result[index].split()[0])
                case "RUNNING":
                    for job in jobSet:
                        convergeCriteria = "Unknown"
                        if job[0] == result[index].split()[0]:
                            if Path(job[1]).is_file() and Path(job[1]).stat().st_size > 0:
                                with open(job[1],'r+') as file:
                                    with closing(mmap(file.fileno(),0,access=ACCESS_READ)) as data:
                                        hasStability = "Stability analysis"
                                        hasStabBytes = hasStability.encode()
                                        isStable = "The wavefunction is already stable."
                                        isStabBytes = isStable.encode()
                                        containsStability = regex.search(hasStabBytes, data)
                                        if containsStability is not None:
                                            hasStabilized = regex.search(isStabBytes, data, regex.REVERSE)
                                            if hasStabilized is not None:
                                                stabilityInsert = "Wavefunction has stabilized."
                                            else:
                                                stabilityInsert = "Wavefunction has not stabilized."
                                        else:
                                            stabilityInsert = ""
                                        # Checks for convergence section header, defaults to Unknown or Not Found
                                        tableHeader = "         Item               Value     Threshold  Converged?"
                                        tableBytes = tableHeader.encode()
                                        finalTableHeader = regex.search(tableBytes, data, regex.REVERSE)
                                        if finalTableHeader is not None:
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
                                            console.print("Job " + str(result[index].split()[0]) + " is currently running, and "
                                                "has converged on " + str(convergeCriteria) + " out of 4 criteria.\n    "
                                                   + stabilityInsert + " Current duration is " + str(result[index].split()[4])) #light_magenta info
                                            stalkStatus.remove(result[index].split()[0])
                                        else:
                                            console.print("Job " + str(result[index].split()[0]) + " is currently running. Convergence "
                                                "criterion header not found.\n    " + stabilityInsert + " Current duration is "
                                                   + str(result[index].split()[4])) #light_magenta info
                                            stalkStatus.remove(result[index].split()[0])
                            break

        jobCopy = jobSet.copy()

        # Finds how the job terminated and stores the data
        for job in jobCopy:
            # If the output is created during the execution of the subroutine, it won't be detected in the prior
            # block and it will be size 0
            if job[0] in stalkStatus and Path(job[1]).is_file() and Path(job[1]).stat().st_size > 0:
                with open(job[1], "r+") as file:
                    with closing(mmap(file.fileno(), 0, access=ACCESS_READ)) as data:
                        for termination in Defaults.terminationVariants:
                            termBytes = termination.encode()
                            termLine = regex.search(termBytes, data, regex.IGNORECASE)
                            if termLine is not None:
                                finishedJobs.append((job[0], termination))
                                NotifyPersonal(f"Job {job} has finished!")
                                break
                jobSet.remove(job)
            elif job[0] in stalkStatus and Path(job[1]).is_file() and Path(job[1]).stat().st_size == 0:
                console.print("Job " + job[0] + " started running during stalk subroutine execution.") #light_magenta info

        # Reports job termination data
        for job in finishedJobs:
            if job[1] == Defaults.terminationVariants[0] or job[1] == Defaults.terminationVariants[1]:
                console.print("Job " + str(job[0]) + " has encountered " + Defaults.terminationVariants[0]) #light_green good
            if job[1] == Defaults.terminationVariants[2]:
                console.print("Job " + str(job[0]) + " has encountered " + Defaults.terminationVariants[2]) #light_red error

        # If all jobs for stalking are done, finish execution and release the terminal
        if len(jobSet) == 0:
            console.print("All jobs tagged for stalking have finished.") #light_cyan operation
            break


        lastPing = time.strftime("%a %I:%M:%S",time.localtime())
        console.print("Waiting " + str(frequency*60) + " seconds to ping the queue again. Last ping at " + lastPing
               + " local time.") #light_blue operation
        time.sleep(frequency * 60)
        pingTime = time.monotonic()

        # Timeout warning
        if (pingTime - startTime) > duration * 60:
            if Catalog.isLooping:
                startTime = pingTime
            else:
                console.print("Job stalking terminated by timeout. Your jobs are still running.") #light_red error
                console.print("Consider editing the default stalk duration and frequency if your jobs regularly timeout.") #light_red error