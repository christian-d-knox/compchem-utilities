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

   A bad or mistaken input is fixed the same way, as a re-run with the input corrected: a route of your own
   (`--route`) or an Optimization Method (`--method NAME`, `{cu} -methods` lists them), on the human's word. The re-run
   starts from the last geometry and is named as a re-run (8a_re, or 8a_re2 past one that ran), so it replaces the
   old attempt in the species' data file once it succeeds. A route given here is a fresh job's: it writes the
   geometry, so leave out `Geom=AllCheck` and `guess=read` unless the checkpoint is wanted (`-ch`).

       {cu} -re 8a.out --route 'opt=(ts,calcfc,noeigen) freq b3lyp/def2svp' --dry-run --json

   An optimization re-run can have its single point submitted when it ends, as `-opt --then-sp` does (the same
   `--sp-*` flags; a re-run that doesn't optimize gets no next job):

       {cu} -re 8a.out --then-sp --sp-method wb97xd --dry-run --json

4. A job that left the queue without a termination line was cancelled, ran out of wall time, or never wrote output:
   tell the human. When the human no longer needs ended jobs listed:

       {cu} -st --dismiss ended --json
