# Recipe: A Benchmark

1. The Benchmark Methods in force here (one single point per entry):

       {cu} -methods --json

2. Preview one input per method for each structure (named 8a-1-M062X_SP, ...), then submit with `next[0]`:

       {cu} -b 8a.out --dry-run --json

3. Follow them (`{cu} -st --json`), then compare energies:

       {cu} -info 8a_SP.out --json
