# Recipe: Which Jobs Failed, and Why

1. The tracked jobs and how each ended (`termination`, each stage's `status`, `convergence`, `stable`, `outOfSteps`):

       {cu} -st --json

2. What an output says, in a few hundred tokens instead of a 100 MB file (`status`, `termination`, `convergence`,
   `outOfSteps`, `imaginaryFrequencies`, `stability`, `s2`):

       {cu} -info 8a.out --json

3. `next` suggests `-re` for an error termination or an optimization out of steps: a re-run from the last geometry
   with the same route (8a_re). Preview it, and ask the human first unless the cause is clearly mechanical (out of
   steps, out of time); a wrong charge, multiplicity or method is theirs to fix.

       {cu} -re 8a.out --dry-run --json

4. A job that left the queue without a termination line was cancelled, ran out of wall time, or never wrote output:
   tell the human. When the human no longer needs ended jobs listed:

       {cu} -st --dismiss ended --json
