#!/usr/bin/env python3
# Obsessive Queue - Running your queue command arbitrarily since 10-30-2023
# Revamped on 2025-08-08 through a brain-blast
# Created by Christian Drew Knox, for the Peng Liu group

import os
import time

# User prompts
duration = int(input("Enter the duration of the script in minutes: "))
frequency = float(input("Enter the frequency of the script in minutes (30s -> 0.5min): "))

command = "squeue --me --format='%25j %10T %18R %20S %18M'"

# Gets starting time
start_time = time.time()

# Loop execution every frequency during duration
while (time.time() - start_time) < duration * 60:
    os.system(f'{command}')

    # Print execution time
    os.system('echo "The current time is $(date)"')
    print("Press Ctrl+C to stop me at any time!")

    # Calculates time remaining on script execution, converts to string
    remainder = round((start_time + (duration * 60) - time.time()) / 60,2)
    print("This script will continue to run for " + str(remainder) + " minutes!")

    # Waits next frequency of duration
    time.sleep(frequency * 60)

print("Obsessive Queue has finished running! Re-run the command to keep constantly checking the queue!")
