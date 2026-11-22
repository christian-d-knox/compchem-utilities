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

3. **The master copy and project workbook:** Follow `{cu} -agent excel-workbooks` for building and updating the
   workbook (dated `GoodVibes_YYYYMMDD-HHMM.xlsx` master copy, never edited; `GoodVibes Raw` exact copy; `Sources` tab
   recording master filename, temperature, concentration, and date; formula tabs per document using `INDEX`/`MATCH` on
   structure name and column header such as `qh-G(T)_SPC`, labelled `627.5095` conversion cell, and formula-based mass
   balance from `formula` in `{cu} -info '*.out' --json`). Remember to run your local `cu -env --json` once per session before building
   workbooks with `openpyxl`.

   A script may copy cells and write formulas and labels; **it never writes a number it computed**.

4. **Let Excel calculate:** `{cu} -agent excel-workbooks` gives the Windows PowerShell COM and macOS AppleScript
   commands to trigger `CalculateFull`, save the workbook in Excel, and read evaluated values back via
   `openpyxl(..., data_only=True)`. Without Excel at hand, ask the human to open and save the workbook. When a new
   GoodVibes run arrives, replace the raw tab with an exact copy of the new master and update `Sources` (or save a new
   version `..._v2.xlsx` if the human added charts or formatting).

5. Report every value with its workbook, tab, column, and `Sources` conditions. For the paper's SI, the SI Scraper writes
   the same energies with coordinates (see `{cu} -agent si-coordinates`).
