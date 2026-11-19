# Recipe: Optimize, Then the Single Point

Optimize molecules, and have each one's single point submitted on its own when its optimization ends (the Neva-Touch
Scheduler). Every command below runs as `ssh {host} "cd FOLDER && COMMAND"`.

1. See which methods this project has, and agree on one with the human (the method is their call):

       {cu} -methods --json

2. Per molecule (one call each), preview. Charge, multiplicity and a singlet's spin (`--spin css|oss`) are the
   human's: ask when you don't know them. A prior output works as the source too (its charge and multiplicity are
   read from it; `--method "From Source"` repeats its route).

       {cu} -opt 8a.xyz --method wb97xd --charge 0 --mult 1 --then-sp --dry-run --json

3. Show the human the input (`written[0]`: inputs are small, read it) and the scheduled single point. On their
   go-ahead, run `next[0].command` (the same call, submitting; it answers replacing the previewed input).

4. Follow the jobs now and then, one snapshot per call (never a loop). The single point is submitted when the
   optimization ends normally; a stage that ran out of optimizer steps is re-run from its last geometry (up to the
   lab's limit), and `nevaTouch` says so.

       {cu} -st --json

5. When they're done, read the results (energy, imaginary frequencies, convergence) without opening the outputs:

       {cu} -info 8a.out 8a_SP.out --json
