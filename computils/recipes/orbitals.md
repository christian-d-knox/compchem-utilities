# Recipe: Orbitals and Densities to Look At (Cube Files)

Cube files for a Gaussian job, made by cubegen jobs on the cluster. Which ones to make is the human's: ask.

1. The checkpoint must be formatted: `-cu` runs formchk on a `.chk` itself (a `.fchk` is used as it is). Name the
   cubes in `cube-options`, separated by spaces: `Pot` (electrostatic potential), `Den` (electron density), `Val`
   (valence orbitals), `Spin` (spin density, open shell only), `Range` (the orbitals numbered in `orbital-range`).
   Preview the cubegen scripts:

       {cu} -cu 8a.chk --answer 'cube-options=Den Spin' --dry-run --json

2. Orbitals by number: `Range`, with the numbers as `FIRST-LAST`. Orbital numbers are the human's to give: with an
   ECP, the core electrons it replaces aren't numbered, so the HOMO isn't half the electron count.

       {cu} -cu 8a.chk --answer 'cube-options=Range' --answer orbital-range=40-45 --dry-run --json

3. Submit with `next[0]`. Cube jobs aren't tracked (`-st` doesn't list them): each writes the `.cube` named in
   `submitted[].output` into the folder. Check for the files rather than reading them, and copy them down only on
   the human's word (they can be large).
