# cli

Command-line tools: package.json `bin`, Cargo binaries, Go `main`, Python `[project.scripts]`.

## Run
Invoke: `{{run}}`.

## Exercise
1. Work in a scratch dir (`{{scratch}}/cwd`) so you never touch the repo.
2. Run each command the criteria name. Check the exit code, stdout and stderr, and any files written. Include `--help` when flags changed.
3. Try at least one bad input per criterion: a missing argument, a bad file, an empty stdin.
4. Compare with golden files if the repo keeps them.

## Evidence
- The full command, its exit code and its output for every criterion, saved to `{{scratch}}/runs.txt` and registered.

## Good criteria
- "`tool convert in.csv --to json` writes valid JSON with one object per row and exits 0. A missing input file exits 2 with 'in.csv: not found'."
