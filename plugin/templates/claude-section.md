## Ratchet (agent verification loop)

Work here is done when it is verified, not when it looks done.

- `ratchet verify` runs the checks for the packages you changed (cached; the Stop gate runs it too). `ratchet verify --full` before handing off.
- Never weaken a check to pass: no skipped or deleted tests, no lint/type suppressions, no edits to `.ratchet/config.json`, CI or snapshots unless that is the task. `ratchet allow <id> --reason "..."` only when genuinely required.
- Log friction (slow checks, flaky tests, missing tools, unclear specs): `ratchet blocker add "..."`.
- Multi-step work: `/ratchet:run <goal>`. Status: `ratchet status`.

Project rules learned by agents (keep each one line, concrete, and checkable):
