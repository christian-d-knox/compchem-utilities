# Recipe: Waiting for Jobs Without Polling

Wait for submitted jobs at no cost: one background command that exits according to `--wait-mode`, and wakes you. Every command
below runs as `ssh {host} "cd FOLDER && COMMAND"`. For the wait itself, add `-n -o ServerAliveInterval=60` to ssh
(`{cu} -agent ssh`).

1. Submit as usual. The report's `next` has the wait for the jobs it submitted (default `--wait-mode chain`):

       {cu} -st --wait 24769828 24769829 --json

   Choose `--wait-mode` when running the wait:
   - **`chain` (default):** wakes when any job's full Neva-Touch Scheduler chain ends (normal completion, failure at any stage, or out of NTS retries), suppressing intermediate wakeups on Opt -> SP or re-run steps. In a batch, wakes as each chain finishes.
   - **`all` (`--wait-mode all`):** suppresses all intermediate and individual wakeups until every job and chain in the wait list reaches a terminal state (normal or error):

         {cu} -st --wait 24769828 24769829 --wait-mode all --json

   - **`any` (`--wait-mode any`):** wakes as soon as any individual job ends, including intermediate Opt or re-run stages:

         {cu} -st --wait 24769828 24769829 --wait-mode any --json

2. Run that as a background command, tell the human what you're waiting for (and in which wait mode), and end your turn. Never poll `-st` or
   sleep in a loop: the wait checks the queue for you, and the human still gets their Telegram messages.

3. When it exits, you are woken with its report:
   - `results[0].wait.mode`: the wait mode (`chain`, `all`, or `any`).
   - `results[0].wait.ended`: each job or chain tail that ended, with its message (and `chain`: the list of job IDs in the chain when multi-step).
   - `results[0].wait.waiting`: the jobs still running, including any the Neva-Touch Scheduler just submitted (empty in `all` mode when all jobs finished).
   - `next`: `-info` for any failed job/chain (read it, then tell the human; what to do about a failed job
     is theirs, or follow `{cu} -agent failed-jobs`), and the command to wait again (preserving your `--wait-mode`).

4. Act on what ended, then run `next`'s wait again in the background, until nothing is left in `waiting`. Then carry
   on with the task (thermochemistry, SI coordinates) on the finished jobs.

5. If the wait ends with exit 255 (ssh's own: the connection dropped), nothing was lost. See what happened meanwhile,
   then wait again on the jobs still running:

       {cu} -st --json
