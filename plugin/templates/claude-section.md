## Ratchet (agent verification loop)

Work here is done when it's verified, not when it looks done. This works in any harness running in T3 Code.

- `ratchet verify` checks the packages you changed (it's cached). `ratchet verify --full` before handing off. If `ratchet` isn't on PATH, see the ratchet README to install it.
- Never weaken a check to pass: no skipped or deleted tests, no lint or type suppressions, no edits to `.ratchet/config.json`, CI or snapshots unless that's the task. Use `ratchet allow <id> --reason "..."` only when it's genuinely required.
- Log friction (slow checks, flaky tests, missing tools, unclear specs) with `ratchet blocker add "..."`.
- Multi-step work: `ratchet start "<goal>"` launches a coordinator thread in T3. In an existing thread, run `ratchet brief coordinator --goal "<goal>"` and follow it.
- To see how a product is run and judged: `ratchet profiles`.

Project rules learned by agents (one line each, concrete, checkable):
