# Recipe: A Free Energy (Delta G) Table

Free energies come from GoodVibes, as GoodVibes wrote them. Never compute corrections or shifts yourself. **Never do
arithmetic in your head:** every difference, sum and unit conversion (kcal/mol: × 627.5095) goes through a calculator
tool (a spreadsheet or a short script), and you say which one you used.

1. Every species needs its optimization (with frequencies) and its single point (8a.out and 8a_SP.out). `next`
   lists the ones without a single point (`-sp`). Each file's `formula` is its molecular formula:

       {cu} -info 8a.out 8b.out 8a_SP.out 8b_SP.out --json

2. Check each step's mass balance before taking a difference: put the formulas of both sides in the calculator tool and
   compare atom counts element by element. If a step doesn't balance, stop and tell the human which species is
   missing or extra.

3. Run GoodVibes on the structures (their _SP files are found by name). Defaults: Head-Gordon qh-H, Grimme qh-S,
   298.15 K, 1 atm, scale factor 1.0. Temperature and concentration (`--temperature`, `--concentration`) are the
   study's: ask the human.

       {cu} -gv 8a.out 8b.out --spc SP --json

4. `results[0].rows` holds each structure's values (Hartree): `E_SPC`, `H_SPC`, `qh-H_SPC`, `G(T)_SPC`,
   `qh-G(T)_SPC`, with the run's `temperature` and `concentration`. ΔG between species is the difference of their
   `qh-G(T)_SPC` sums (the lab's default free energy). Copy the values into the calculator tool exactly as written,
   compute there, and say which column and conditions you used.

5. For the paper's SI, the SI Scraper writes the same energies with the coordinates (see `si-coordinates`).
