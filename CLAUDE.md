# ratchet (developing ratchet itself)

The kernel and generator live in `plugin/lib/ratchet`. Briefs (`plugin/templates/briefs`) and starter kits (`plugin/kits`, `kits.py`, `newproject.py`) are what agents and generated harnesses see. Read DESIGN.md and LESSONS.md first.

## Ground rules

- **Code constraints:** stdlib-only Python 3.9+, macOS and Linux. No third-party imports, no `match`. Use `from __future__ import annotations`.
- **The kernel's invariants are never weakened:** per-test baselines, failing-first tests, fake-pass guards, the merge gate, tamper detection. Generated harnesses can't change them.
- **Every external command goes through `runner.run_group`.** Never call `subprocess.run(shell=True, timeout=...)` for checks or exercises; it leaks grandchildren. See LESSONS.md.
- **Anything that reverts or mutates files must restore the exact tree.** Tests assert `git status` is clean afterwards.
- **Don't run several mutation or probe runs at the same time.** After a run, check `ps -eo args | grep -cE '^node (server.js|--test|index.test.js)'` is 0.
- **Hooks never trap a session because of a ratchet bug.** Outside repos with `.ratchet/harness.json`, they exit immediately.

## Verify

- `python3 plugin/bin/ratchet verify --full` in this repo (ratchet checks itself), or directly with `python3 -m unittest discover -s tests`.
- `claude plugin validate ./plugin && claude plugin validate .`
