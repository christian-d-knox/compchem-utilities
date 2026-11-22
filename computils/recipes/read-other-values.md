# Recipe: Reading a Value -info Doesn't Report

`-info` reads the common results. For anything else an output prints, `-scrape` finds it: one read of the file,
returning only the matching lines and the lines you ask for. Never read a whole output, and never guess a pattern
twice: check the matched `text` before trusting a value.

1. One value, the last time it was printed: put it in a group, and `groups` gives it exactly as printed.

       {cu} -scrape 8a_SP.out --find 'T1 diagnostic\s+\.*\s+(\S+)' --json

2. The first time instead (a setting printed once near the top):

       {cu} -scrape 8a_SP.out --find 'alpha electrons' --earliest 1 --json

3. A header and the lines under it (a fixed-size block):

       {cu} -scrape 8a.out --find 'Alpha  occ\. eigenvalues' --latest 1 --after 1 --json

4. A table that ends at a known line: `--until` takes the block through it (at most 300 lines; `truncated` if cut).

       {cu} -scrape 8a.out --find 'Mulliken charges and spin densities:' --until 'Sum of Mulliken charges' --json

5. Several values in one call: alternatives with `|`, and `--latest N` for the last N matching lines.

       {cu} -scrape 8a.out --find 'Zero-point correction=|Sum of electronic and thermal Free Energies=' --latest 2 --json

Matching ignores case unless `--case` (programs differ in how they capitalize headings). `.` and `(` are regex
characters: escape them (`\.`, `\(`) to match them literally. Numbers come from `groups` or the text as printed;
any arithmetic on them is done with a calculator, never in your head.
