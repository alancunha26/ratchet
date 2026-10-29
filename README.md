# ratchet

Sets up an **opinionated, verifiable development cycle inside a repo, using that project's own tools.** A ratchet only turns one way: every task locks in progress behind proof, and the harness itself only gets stricter.

- **Kernel (ratchet, the same in every repo, never generated).** The cycle and its invariants:
  - per-test comparison against the base: nothing that passed may break;
  - new tests must fail without the change;
  - no fake passes;
  - an independent judge;
  - a merge gate;
  - tamper detection;
  - retry limits.
- **Harness (generated into the repo, owned by it).** `.ratchet/`: how *this* project is checked and exercised, wrapping its own tools (node:test, Vitest, Jest, pytest, Go, Rust, Godot...). It evolves with the code.
- **Built on [T3 Code](https://github.com/pingdotgg/t3code), harness-agnostic.** The coordinator, workers and judges are T3 threads, and any harness T3 hosts can fill any role (Claude Code, Codex, Cursor, OpenCode, Pi, ACP agents). Everything a role needs comes from self-contained briefs and the CLI.

See [DESIGN.md](DESIGN.md) for the design and [LESSONS.md](LESSONS.md) for what earlier versions taught us.

## Install (once per machine)

```bash
~/.repos/github.com/cosmyk/ratchet/scripts/install.sh
```

This links `ratchet` into `~/.local/bin`, for every harness and T3 terminal. It also installs the optional Claude Code adapter (`ratchet@cosmyk`): one-line skills plus a Stop hook for earlier feedback. Python 3.9+, standard library only.

## The three entry points

| | Command | What you get |
|---|---|---|
| **Fresh repo** | `ratchet new <stack> <dir>` (`ratchet new --list`): node-lib, node-web, node-cli, python-lib, go-lib, godot. Any other stack: an agent follows `ratchet brief new`. | A **walking skeleton**, verified from commit one: the stack's own toolchain, checks proven able to fail, and the thinnest end-to-end slice with a replayable exercise. |
| **Existing repo** | `ratchet init` (the full protocol: `ratchet brief init`) | A harness generated from the repo's manifests, matched to its CI, and **proven**. Each check passes on the base, turns red on a planted canary test, and responds to mutations. Exercise guides are written per product, and gaps are recorded honestly. |
| **Evolve** | `ratchet readiness`, `ratchet garden`, `ratchet upgrade` (protocol: `ratchet brief garden`, on a weekly T3 schedule) | Gaps and friction (flaky or slow checks, judge rejects, blockers) become **harness tasks**, which are reviewed like any task. Kernel updates arrive as migrations. |

Readiness levels:

| Level | Meaning |
|---|---|
| 1 | Checks pass |
| 2 | Every package has a per-test check that's proven able to fail |
| 3 | Tests respond to mutations, and every product has an exercise guide |
| 4 | Replayable exercises |
| 5 | Exercises compare against the base (image diffs, budgets) |

## The cycle

```
ratchet start "<goal>" [--provider codex]   →  t3 thread start → coordinator thread (own worktree, pinned)
  ratchet tick → contract review (judge) → per task: worker thread → submit gate → judge thread(s) → merge gate
              → final review → PR → CI via watch_pull_request → HTML report in the thread
```

**Gates are code in the CLI**, so every harness hits them:

| Gate | Requires |
|---|---|
| `task submit` | Committed work. Per-test green against the task's own baseline. For features and fixes, at least one new test that **fails without the change**: the source is reverted in place and the new tests are re-run. |
| `task accept` | The same at full level. |
| `task merge` | Re-verify after merging. A conflict or red result restores the branch and sends the task back. |
| `run final accept` | Full verification against the run's base. |

**Judges collect their own evidence:**
- `ratchet proof f2p` and `ratchet proof mutate` on the changed lines;
- the repo's exercise scripts, run against the base: `ratchet exercise run <name> --task <id>` compares PNGs pixel by pixel and metrics against budgets;
- T3's browser and simulators;
- `ratchet diff-image`.

**Feature tasks can't touch the harness.** Harness changes are their own task kind, and every check they add must pass `ratchet proof canary <check>`.

## A check, in the repo's harness

```jsonc
// .ratchet/harness.json (excerpt)
{"name": "web-test", "run": "bash .ratchet/verify/web-test.sh", "level": "fast",
 "scope": ["packages/core/**", "packages/web/**"],        // changes that trigger it (follows deps)
 "min_tests": 1,                                           // an empty run is not a pass
 "canary": {"path": "packages/web/__ratchet_canary.test.js", "content": "test('ratchet canary', () => { throw ... })"},
 "mutate": ["packages/web/**/*.js"]}
```

The script wraps the project's own runner and writes per-test results into `$RATCHET_OUT`. Supported formats:
- JUnit XML (pytest, node:test, Vitest, Mocha, Playwright, Hurl, Maestro, bats, GUT, gdUnit4...);
- `*.go.json`;
- `*jest.json`;
- `*.libtest.txt`;
- `result.json`.

Tools that exit 0 on errors (Godot) are wrapped in `ratchet exec-check --fail-on`.

## CLI

```
ratchet new | init | prove | baseline | doctor | readiness | garden | upgrade
ratchet verify [--full] [--task ID] | proof f2p|mutate [task] | proof canary <check>
ratchet exercise list | exercise run <name> [--base REF | --task ID] | diff-image a.png b.png
ratchet start "<goal>" | tick | brief coordinator|plan|worker|judge|contract|final|init|new|garden
ratchet run start|show|dispatch|contract|final|pr|pushed|ci|close | task add|edit|start|submit|accept|reject|merge|rollback|...
ratchet evidence | report [--html] | tamper [--task] | allow | blocker | status | stats | exec-check | junit-unittest | port
```

## Verified so far

- **Automated:** 23 end-to-end tests pass on Python 3.14 and 3.9. They use real git repos and real runners: Node workspaces, Python, Go/Rust/Godot detection, skeletons, the cycle, exercises, images, hooks, and process hygiene.
- **ratchet checks itself:** `.ratchet/`, readiness, and a canary that turns its own suite red.
- **A live run with real T3 threads** on a `ratchet new node-web` app:
  1. The contract judge rejected the plan and correctly demanded a harness task first.
  2. The harness worker added an exercise.
  3. The feature worker wrote 5 failing-first tests and took a browser screenshot in T3.
  4. Judges accepted on their own evidence.
  5. The merge gate and final gate held.
  6. Readiness reached 5/5. That run also found two kernel bugs, both fixed.
- **Not yet verified:** the Godot skeleton and kit (no Godot binary was available while building), and Unity.
