# ratchet (cli)

## Run
`python3 plugin/bin/ratchet <command>` from any git repo.

## Exercise
1. **Fresh repo:** `ratchet new node-web /tmp/ratchet-x-<n>`. Every proof is green, the canary goes red, and the smoke exercise passes.
2. **Existing repo:** `ratchet init` on a throwaway copy of a real repo. Read the proofs table and the readiness gaps.
3. **The cycle:** `run start`, `task add`/`start`/`submit` (the failing-first gate), `accept`, `merge`, then `tick` at every step.
4. **The adapter:** pipe hook JSON into `ratchet hook stop` from a dirty fixture. A regression must block, and a detached HEAD must pass.

## Evidence
- The command transcripts, saved to `{{scratch}}/runs.txt`.
- The `git status` of every fixture after a proof, which must be clean: base-state reverts and mutation probes have to restore everything.

## Good criteria
- "`ratchet proof f2p` lists a new test as proving when it fails on the base source, and leaves the tree unchanged."
