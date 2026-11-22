# Recipe: Check Structures (Minimum or TS, Spin, Stability)

Whether each optimized structure is what it should be, read without opening the outputs. What a structure is meant
to be (a minimum, a transition state, a closed- or open-shell singlet) is the human's: ask when you don't know.

1. Read every output at once:

       {cu} -info '*.out' --json

2. Per output, in `results` (a staged output, e.g. Opt+Freq then SP in one file, has these per stage in `stages`:
   read the optimization stage's frequencies and the last stage's energy):
   - `status` and `termination`: Normal, Error, or Unknown (still running, or killed). An optimization that ran out
     of optimizer steps has `outOfSteps`; whether it finished is the termination and `outOfSteps`, not
     `convergence` (the last table's met/total: ORCA can end converged without every criterion met).
   - `imaginaryFrequencies` (cm⁻¹, negative; `[]` for none) and `lowestFrequency`: a minimum has none, a TS exactly
     one. Neither field means the job ran no frequencies.
   - A Gaussian TS's `tsEigenvector`: the internal coordinates its mode is made of, largest coefficient first, each
     with its final value (a bond's in A). Check that its leading coordinates are the bonds the reaction makes or
     breaks; `tsEigenvalue` is there only when Gaussian printed that it should be negative.
   - An optimization's `constraints`: the coordinates it held (Frozen or Scan) with their final values; `inherited`
     ones came with the checkpoint, not from the stage's own route.
   - `spin` (CSS, OSS or OPEN), with `spinReason`; `s2`, the last `<S**2>`; `stability`, a stability analysis's
     verdict (`false`: the wave function didn't stabilize).
   - On hand-written inputs (before `cu -r`), `-info` also reports `electronProblem` (impossible charge/multiplicity),
     `cpus`, and `memoryGB`; `constraints` marked `inherited` catch ModRed-to-TS links missing `nofreeze`.

3. Report to the human, per structure, what doesn't match what it should be. Never fix one on your own:
   - Out of steps, or an error termination: a re-run from the last geometry is mechanical. Preview it, then submit
     with `next[0]`:

         {cu} -re 8a.out --dry-run --json

   - A minimum with an imaginary frequency, a TS with none or several, or one whose `tsEigenvector` doesn't lead
     with the reacting bonds: the human decides (a
     displaced re-optimization, a new guess).
   - A singlet read as CSS with `stability` false, or a high `s2`: it may be open shell. Re-optimizing it as an
     open-shell singlet is the human's call; on their word, preview with a new name:

         {cu} -opt 8a.out --method 'From Source' --spin oss --name 8a_oss --dry-run --json
