#Obsessive Queue - Running your queue command arbitrarily since 10-30-2023
#Created by Christian Drew Knox, for the Peng Liu group

import os
import time
import math

#User prompts
command = input("Enter the exact queue command to be executed (aliases do not work): ")
duration = int(input("Enter the duration of the script in minutes: "))
frequency = float(input("Enter the frequency of the script in minutes (30s -> 0.5min): "))

#Gets starting time
start_time = time.time()

#Loop execution every frequency during duration
while (time.time() - start_time) < duration * 60:
    
    #Execute command
    os.system(f'{command}')

    #Print execution time
    os.system('echo "The current time is $(date)"')

    #Print termination command
    print("Press Control+C to stop me at any time!")

    #Calculates time remaining on script execution, converts to string
    remainder = round(math.ceil(start_time + duration * 60 - time.time())/60,2)
    remainderString = str(remainder)

    #Prints remaining execution time
    print("This script will continue to run for " + remainderString + " minutes!")

    #Waits next frequency of duration
    time.sleep(frequency * 60)

#Alerts user of script completion
print("Obsessive Queue has finished running! Re-run the command to keep constantly checking the queue!")