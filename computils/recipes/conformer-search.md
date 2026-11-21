# Recipe: Conformer Searches With CREST

1. Where the search starts: a DFT output's last geometry, an .xyz, or a SMILES (`--smiles 'SMILES' --name NAME`). For
   a transition state, read what the TS printed: `tsEigenvector` names the bonds its mode moves (with their final
   lengths), and a ModRed stage's `constraints` the bonds it froze:

       {cu} -info TS1.out --json

2. The human decides the chemistry: the level and solvent (a Conformer Search Method: `{cu} -methods` lists each one's
   options; or CREST's options typed in full with `--route`), the charge and multiplicity (the output's unless they
   say otherwise; CREST takes unpaired electrons, written for you as multiplicity - 1), and for a TS which bonds to
   hold, usually the forming and breaking ones. Offer the eigenvector's leading bonds as a suggestion; never pick them.

3. Preview it: the starting .xyz, the constraints file (each bond held at the starting geometry's length, computed by
   CompUtils, or at a value given after the atoms) and the job script are written, and nothing is submitted:

       {cu} -crest TS1.out --method gfn2 --constrain 'B 12 15;B 15 21' --dry-run --json

   Show the human `command` and `constraints`. A few TS bonds take a stiff force constant: 1.0, or 2.0 if the
   conformers' bonds stray more than 0.05 A from the DFT values (`--force-constant 2.0`). Metal-ligand bonds that
   dissociate are held the same way, or with `--cmetal` in the options. The search of TS1 is TS1_CREST, so
   its output feeds TS1.cudf.

4. On their go, submit (`next` has the command), then wait without polling: run the `-st --wait` that the
   submission's `next` gives as a background command and end your turn.

5. When it ends, read it as one record (never crest's output itself): the conformers kept, the energy window,
   E (Lowest), the ensemble's free energy, the lowest conformer's population and the wall time. Every conformer's
   energy and population is in the species' data file (its Conformer Search section):

       {cu} -info TS1_CREST.out --json

   The conformers are in TS1_CREST-scratch/: crest_conformers.xyz (lowest first) and crest_best.xyz (the
   lowest). Which of them become DFT jobs, and how, is the human's call: CompUtils has no command for that yet.

6. A search to correct (other options, other constraints) is re-run, so it stays the species' search: the same
   starting geometry, as its next attempt (TS1_CREST_re), with what it ran with unless corrected:

       {cu} -re TS1_CREST.out --constrain 'B 12 15;B 15 21;B 1 9' --dry-run --json
