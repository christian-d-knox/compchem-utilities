# Recipe: Waiting for Jobs Without Polling

Wait for submitted jobs at no cost: one background command that exits when a job ends, and wakes you. Every command
below runs as `ssh {host} "cd FOLDER && COMMAND"`. For the wait itself, add `-n -o ServerAliveInterval=60` to ssh
(`{cu} -agent ssh`).

1. Submit as usual. The report's `next` has the wait for the jobs it submitted:

       {cu} -st --wait 24769828 24769829 --json

2. Run that as a background command, tell the human what you're waiting for, and end your turn. Never poll `-st` or
   sleep in a loop: the wait checks the queue for you, and the human still gets their Telegram messages.

3. When it exits, you are woken with its report:
   - `results[0].wait.ended`: each job that ended, with its message (how it ended, and what the Neva-Touch Scheduler
     did: submitted the single point, re-ran a stage that ran out of optimizer steps, or stopped).
   - `results[0].wait.waiting`: the jobs still running, including any the Neva-Touch Scheduler just submitted.
   - `next`: `-info` for a job that didn't end normally (read it, then tell the human; what to do about a failed job
     is theirs, or follow `{cu} -agent failed-jobs`), and the command to wait again.

4. Act on what ended, then run `next`'s wait again in the background, until nothing is left in `waiting`. Then carry
   on with the task (thermochemistry, SI coordinates) on the finished jobs.

5. If the wait ends with exit 255 (ssh's own: the connection dropped), nothing was lost. See what happened meanwhile,
   then wait again on the jobs still running:

       {cu} -st --json
