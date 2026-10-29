# Judge calibration

The ratchet judge reads this file before every review. Keep it short, concrete and true.
Agents (the gardener) update it when the judge's call turns out wrong.

## What matters most in this repo
- Hooks must never trap a session because of a ratchet bug: errors are logged and the session is let through.
- The CLI stays stdlib-only and runs on Python 3.9+ on Linux and macOS.
- Hook fast paths stay fast: outside ratchet repos, every hook exits without running checks.
- Skills and agents are protocols an agent follows literally: precise, short, no contradictions with the CLI.

## How to exercise the product
- `python3 -m unittest discover -s tests` builds throwaway monorepos and drives the real CLI and hooks.
- For behaviour not covered by tests, make a fixture repo in /tmp and run `plugin/bin/ratchet ...` in it.
- `claude plugin validate ./plugin` for manifest, skill, agent and hook changes.

## Hard fails
- Any acceptance criterion not demonstrated by evidence the judge saw or produced itself.
- Placeholder or stubbed behaviour presented as done (TODO, NotImplemented, hardcoded results).
- Changes outside the task's scope that were not needed to meet it.
- A tamper allowance whose reason does not hold up.

## Past misjudgments
<!-- YYYY-MM-DD — what the judge got wrong — the correct call -->
