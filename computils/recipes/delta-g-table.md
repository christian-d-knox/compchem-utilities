# Recipe: A Free Energy (ΔG) Table

Free energies come from GoodVibes, as GoodVibes wrote them. Never compute corrections, shifts or unit conversions of
corrections yourself: differences of its values (and kcal/mol: × 627.5095) are fine.

1. Every species needs its optimization (with frequencies) and its single point (8a.out and 8a_SP.out). `next`
   lists the ones without a single point (`-sp`):

       {cu} -info 8a.out 8b.out 8a_SP.out 8b_SP.out --json

2. Run GoodVibes on the structures (their _SP files are found by name). Defaults: Head-Gordon qh-H, Grimme qh-S,
   298.15 K, 1 atm, scale factor 1.0. Temperature and concentration (`--temperature`, `--concentration`) are the
   study's: ask the human.

       {cu} -gv 8a.out 8b.out --spc SP --json

3. `results[0].rows` holds each structure's values (Hartree): `E_SPC`, `H_SPC`, `qh-H_SPC`, `G(T)_SPC`,
   `qh-G(T)_SPC`, with the run's `temperature` and `concentration`. ΔG between species is the difference of their
   `qh-G(T)_SPC` (the lab's default free energy); say which column and conditions you used.

4. For the paper's SI, the SI Scraper writes the same energies with the coordinates (see `si-coordinates`).
