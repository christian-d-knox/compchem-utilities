# Recipe: SI Coordinates

Writes SI_coordinates.txt (each species' opt and single point energies, enthalpies and free energies, then its
coordinates) and list.txt (the order) in the folder.

1. How the SI names the two methods is the human's wording: ask for it (e.g. `(U)B3LYP-D3/6-31G(d)`).

       {cu} -si --opt-label 'B3LYP-D3/6-31G(d)' --sp-label 'M06-2X/def2-TZVP' --json

2. Exit 6 (stopped): the qh-G lines need a current GoodVibes run. Run `next[0].command` (a GoodVibes run on every
   species it gives), then the same `-si` call again.

3. Species that can't be written (no SP, no frequencies, an error) are left out and listed in `skipped`. To choose
   and order them yourself: `--species 8a,8b,TS1`. `--free g` writes G from the outputs instead of qh-G.
