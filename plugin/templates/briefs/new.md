# Ratchet new · a fresh repo, verifiable from its first commit

**Ratchet CLI:** `{{R}}`
**Goal:** {{goal}}

## 1. A known stack

If the stack is known (`{{R}} new --list`), run `{{R}} new <stack> <dir> --name <name>`. The first commit is a verified walking skeleton:
- the stack's own toolchain;
- checks proven able to fail;
- a thin end-to-end slice with a replayable exercise.

Then go to step 3.

## 2. Any other stack: build the walking skeleton yourself, by the same rules

Commit each step.

1. **T0, toolchain.** Set up the stack's own conventions: manifest, formatter, test runner, a minimal entry point. Pick the boring, well-supported option, and pin versions.
2. **T1, checks that can fail.**
   1. Write one real test for one real behaviour.
   2. Run `{{R}} init` to generate the harness from what now exists. If no kit matches, write `.ratchet/harness.json` and `.ratchet/verify/*.sh` by hand.
   3. Make the test runner write per-test results to `$RATCHET_OUT` (JUnit XML is the safest choice).
   4. Give the check a `canary`: a failing test in the framework's style.
   5. Run `{{R}} prove` until the check is green, red on its canary, and has per-test results.
3. **T2, the thinnest end-to-end slice, verified the way this product will always be verified.**
   1. Build the smallest path through the real product. For example: a page renders; an endpoint answers; a command prints; a scene loads, a frame shows it, and an input moves something.
   2. Write `.ratchet/exercise/<product>.md`: how to run it, exercise it, and what evidence counts.
   3. Add a replayable exercise script that writes `$RATCHET_OUT/reward.json` (plus screenshots, frames or transcripts), declared in the product's `exercises`.
   4. Run it: `{{R}} exercise run <name>`.
4. **Record readiness.** `{{R}} readiness` should reach level 4. Commit with the message "ratchet: walking skeleton".

## 3. Then

- **T3 project:** register it (`t3_project_create`).
- **Features:** everything goes through the cycle. Use `{{R}} start "<first feature>"` from a shell, or `{{R}} brief coordinator --goal "<first feature>"` in this thread.
