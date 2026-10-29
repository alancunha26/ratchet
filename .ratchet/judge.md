# Judge calibration

The ratchet judge reads this file before every review. Keep it short, concrete and true.
Agents (the gardener) update it when the judge's call turns out wrong.

## What matters most in this repo
- The kernel's invariants are never weakened: per-test baselines, failing-first tests, fake-pass guards, the merge gate.
- Anything that reverts or mutates files must restore the exact tree (check `git status` after).
- Stdlib-only Python 3.9+, macOS and Linux. Hooks never trap a session on a ratchet bug.

## How to exercise the product
<!-- How the judge runs the real thing, e.g. "pnpm --filter web dev starts :3000; e2e/smoke.spec.ts covers login" -->

## Hard fails
- Any acceptance criterion not demonstrated by evidence the judge saw or produced itself.
- Placeholder or stubbed behaviour presented as done (TODO, NotImplemented, hardcoded results).
- Changes outside the task's scope that were not needed to meet it.
- A tamper allowance whose reason does not hold up.

## Past misjudgments
<!-- YYYY-MM-DD — what the judge got wrong — the correct call -->
