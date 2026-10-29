# ratchet: design

ratchet sets up, inside a repo, an **opinionated, verifiable development cycle**. It uses that project's own tools and ships a small, fixed **kernel** that enforces the cycle.

It is not a universal verification framework. There are too many kinds of application and spec for one framework to cover. Instead, ratchet generates a **harness** for each project, the project owns it, and it evolves alongside the code.

## Principles

1. **The cycle is opinionated. The checks belong to the project.** ratchet decides *when* and *how strictly* work is verified. The project decides *with what*: Godot's GUT, the web app's Playwright, the API's Hurl, the CLI's golden files.
2. **Agents don't write their own gate.** The kernel's invariants are never generated and can't be changed by tasks. Harness changes go through reviewed harness tasks.
3. **A check that can't fail proves nothing.** Every generated check is shown to go red on a deliberately broken change before it's trusted.
4. **Verifiable from commit one.** A fresh repo gets a working, verified cycle before it gets features.
5. **The harness ratchets too.** It gets stricter, faster or broader over time, and never weaker without review.
6. **Built on T3 Code, harness-agnostic.** T3 Code (the agent IDE) provides threads, delegation, worktrees, the browser, devices, PR watching and schedules. Any harness T3 hosts (Claude Code, Codex, Cursor, OpenCode, Pi, ACP agents) can play any role.

## Kernel vs harness

| | Kernel | Harness |
|---|---|---|
| What | The cycle and its invariants, run state, tamper detection, retry limits, T3 orchestration | How *this* project is set up, built, run, checked and exercised |
| Where | the `ratchet` CLI and its templates | `.ratchet/` in the repo (committed), plus `t3.json` and the `AGENTS.md` / `CLAUDE.md` sections |
| Changed by | ratchet releases, rolled out to repos as migration tasks (`ratchet upgrade`) | harness tasks, judged like any task. Protected from feature tasks. |

### Kernel invariants (never generated)

- **No regressions:** every check result that passed on the base still passes.
- **New tests fail on the base and pass on the change.** A test the worker wrote is not evidence until it fails on the base commit.
- **No fake passes:** zero tests collected, or a rise in skipped tests, is a failure. Flaky results are re-run before they count.
- **Committed and green before review.** An independent judge reviews; its verdict and green checks are both required.
- **Merges are re-checked after merging.**
- **No weakening checks:** skips, suppressions and edits to protected harness files need an allowance with a reason, and the judge reviews it.
- **Limits:** gate attempts, judge rounds and CI rounds are capped, then escalated to a human.

### The cycle (kernel)

```
plan → contract review → per task: worker → gate → judge → merge gate → final review → PR → CI → report
                                            ↑______ reject / red ______|
friction, gaps, misjudgments → gardener → harness tasks (the harness ratchets)
```

Every role is a T3 thread. The coordinator runs `ratchet tick`, carries out the actions with T3 tools, and sleeps until a child thread, CI or a webhook wakes it.

### Harness layout (generated, owned by the repo)

```
.ratchet/
  harness.json          kernel version this harness targets; products; roles; limits
  verify/<check>.sh     project-specific checks; they write JUnit XML or a result.json
  exercise/<product>.md how to run and exercise each product, and what evidence counts
  exercise/<product>/   reusable exercise scripts (replays, request suites, capture scripts)
  judge.md              calibration for this repo: what matters, hard fails, past mistakes
  readiness.json        what's covered and the known gaps, which feed the gardener
t3.json                 setup for each new worktree, dev servers, preview URLs
AGENTS.md / CLAUDE.md   the ratchet section, which every harness reads
```

## Three entry points

1. **`ratchet new`: fresh repo.** A walking skeleton with a verifiable cycle from the first commit:
   1. T0: toolchain for the chosen stack, using the stack's own conventions.
   2. T1: checks, each proven to fail on a planted break and then shown green.
   3. T2: the thinnest end-to-end slice, verified the way this product will always be verified (a Godot scene loads and a captured frame proves it; a web page renders and a browser smoke test passes).
   4. Then features, through the normal cycle.
2. **`ratchet init`: existing repo.** Discover setup and checks from CI, manifests and docs. Validate them: tests actually ran, and each check fails on a planted break. Generate the harness and open it as a reviewable PR. Products it can't cover become readiness gaps, never fake checks.
3. **Evolve: `ratchet garden` and `ratchet upgrade`.**
   - **Gardener triggers:** new packages without checks, recurring failures, slow checks, judge mistakes, readiness gaps. Each becomes a harness task.
   - **`ratchet upgrade`:** kernel updates arrive as migration tasks against `harness.json`'s version.

## Starter kits (examples, not rules)

Known stacks (web, API, CLI, library, mobile, Godot, Unity...) have **starter kits**: examples of checks, exercises and tooling that the generator reads and adapts. They are never imposed at runtime. A kit can include on-demand tools (npx, uvx, binaries) for verifying when the project doesn't have its own yet, but the goal is always to move the project onto committed tooling it owns.

## Milestones

- **M1: kernel core.** Run state, `tick`, gates with per-test baselines (JUnit), the fail-on-base check, fake-pass guards, tamper detection, merge gate, briefs, T3 orchestration. Harness loading from `.ratchet/harness.json` and `verify/`.
- **M2: `ratchet init`.** Discovery, validation with planted breaks, harness generation as a PR, readiness.
- **M3: `ratchet new`.** Walking skeleton for a fresh repo, with starter kits for a first set of stacks.
- **M4: evolution.** Gardener, harness tasks, `ratchet upgrade` migrations, readiness-driven increments.
- **M5: judging depth.** Exercise scripts with reward files, base-vs-change captures (pixel diffs, relative budgets), mutation of changed lines, the VLM pairwise protocol, judge panels.

See `LESSONS.md` for what the archived v0.1–v0.3 taught us (branch `archive/v0.3`).
