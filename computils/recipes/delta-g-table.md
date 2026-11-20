# Recipe: A Free Energy (Delta G) Table

Free energies come from GoodVibes, as GoodVibes wrote them. Never compute corrections or shifts yourself, and never
do arithmetic in your head. **Every number that goes into something the human keeps (a Delta G table, an energy
profile, SI values, figure labels) is an Excel formula in a workbook**: never a value computed by a script or
`python -c`. A quick check that ends up nowhere may use any calculator tool; say which.

1. Every species needs its optimization (with frequencies) and its single point (8a.out and 8a_SP.out). `next`
   lists the ones without a single point (`-sp`). Each file's `formula` is its molecular formula:

       {cu} -info 8a.out 8b.out 8a_SP.out 8b_SP.out --json

2. Run GoodVibes on the structures (their _SP files are found by name). Defaults: Head-Gordon qh-H, Grimme qh-S,
   298.15 K, 1 atm, scale factor 1.0. Temperature and concentration (`--temperature`, `--concentration`) are the
   study's: ask the human. It writes GoodVibes.xlsx in the folder:

       {cu} -gv 8a.out 8b.out --spc SP --json

3. **The master copy.** Copy GoodVibes.xlsx to the human's computer, into the project's folder there, under a dated
   name (GoodVibes_20261119-1430.xlsx). Never edit or overwrite a master; each GoodVibes run gives a new one.

4. **The project workbook** (its name agreed with the human), built with openpyxl (every CompUtils environment has
   it):
   - **a raw tab** (GoodVibes Raw): an exact cell-for-cell copy of the master's sheet, with nothing added, removed or
     reformatted;
   - **a Sources tab:** which master the raw tab is from, its temperature and concentration, and the date;
   - **one tab per document** (Delta G Table, REP Pathway A, ...). Its formulas look each value up in the raw tab by
     structure name and column header (INDEX with MATCH), so a re-run that reorders rows can't break them. Delta G is
     the difference of `qh-G(T)_SPC` sums (Hartree, the lab's default free energy) times 627.5095, a labelled
     constant cell;
   - **mass balance** in the same tab: each species' element counts typed in from its `formula` (step 1), summed and
     compared by formulas for both sides of every step. If a step doesn't balance, stop and tell the human which
     species is missing or extra.

   A script may copy cells and write formulas and labels; **it never writes a number it computed**.

5. **Let Excel calculate.** Open the workbook in Excel, recalculate, save and close it, then read the results back
   (openpyxl with `data_only=True`, which reads the values Excel saved). Without Excel at hand, ask the human to open
   and save the workbook. When a new GoodVibes run arrives, replace the raw tab with an exact copy of the new master
   and update Sources: the formulas pick up the new values.

6. Report every value with its tab, the column it came from and the run's conditions. For the paper's SI, the SI
   Scraper writes the same energies with the coordinates (see `si-coordinates`).
