---
name: init
description: Set up ratchet in a repo (new or existing, monorepo or single package). Detects packages and checks, proves they run green, writes .ratchet/ and the CLAUDE.md section, and wires T3 worktree setup. Use when asked to add or enable ratchet, or when ratchet:run finds no .ratchet/config.json.
---

# Ratchet init

The deliverable is a contract the loop can rely on: checks that mirror what "green" means for this repo, run fast on changed packages, and pass on the current base.

1. **Branch.** On the default branch, run `git switch -c ratchet/init`.
2. **Scaffold.** `ratchet init` writes `.ratchet/config.json` (detected), `.ratchet/judge.md` and the ratchet section of `CLAUDE.md`. Read its output.
3. **Tune `.ratchet/config.json` against how the repo really works.** Read the package scripts, turbo/nx pipelines, Makefile/justfile and README dev docs. Above all read `.github/workflows/*`, which define what CI calls green.
   - `checks.<kind>.fast`: what must pass on every change, quickly: lint, typecheck, unit tests. Aim for a gate under about 2 minutes on a typical change.
   - `checks.<kind>.full`: build, integration and e2e. These run before a task is accepted.
   - Spec forms:
     - `script:NAME` runs a package.json script, only if the package has it.
     - `batch:CMD` runs once at the root for every affected package of that kind. Placeholders: `{names}`, `{p_flags}`, `{filters}`, `{dirs}`.
     - A plain `CMD` runs inside each affected package. Placeholders: `{name}`, `{dir}`.
   - `repo_checks`: repo-wide checks such as the formatter. Add `{"run": "...", "when": ["**/*.ts"]}` to limit when they run.
   - `setup`: what makes a fresh worktree runnable (install dependencies, codegen, copying `.env` from the main checkout).
   - `packages`: set this only if `ratchet doctor` shows detection is wrong.
   - Every required CI check needs a local equivalent here, or work will pass locally and fail in CI.
4. **Prove the baseline.** `ratchet doctor --baseline` runs every check on every package, and must end `doctor: OK`.
   - A red check: fix it if it's small and clearly broken. Otherwise quarantine it in `"skip": ["<pkg>:<check>"]` and log `ratchet blocker add "<why>" --kind flaky|env`.
   - A check that takes over 2 minutes: log `ratchet blocker add "..." --kind slow`.
5. **Calibrate the judge.** Fill in "What matters most" and "How to exercise the product" in `.ratchet/judge.md`: how to start the app, the e2e entry point, the key invariants. Keep it short.
6. **Package notes.** For a package with non-obvious build or test steps, add one or two lines to its `CLAUDE.md`. Write a map, not an encyclopedia.
7. **Allow init's own files.** `.ratchet/config.json` and anything under CI are protected paths. Run `ratchet tamper`, then `ratchet allow <ids> --reason "ratchet init"`, for init's own files only.
8. **Commit.** `git add -A && git commit -m "chore: enable ratchet verification loop"`, then confirm `ratchet verify` passes.
9. **T3 wiring.** Only if T3 tools are available: their names start with `mcp__t3-code__`, so load them with ToolSearch first.
   - Find the project with `t3_project_list` by matching `workspaceRoot` to this repo. If it isn't registered, register it with `t3_project_create`.
   - Add a worktree setup script with `t3_project_update`, keeping the existing scripts: `{"id": "ratchet-setup", "name": "Ratchet setup", "command": "<the config's setup command>", "icon": "configure", "runOnWorktreeCreate": true}`. Use the raw setup command: the `ratchet` binary is only on PATH inside Claude sessions.
   - Ask the user once whether to schedule the gardener weekly. If they agree, call `schedule_task` with `prompt: "/ratchet:garden"`, the `projectId`, `schedule: {"type": "fixed_time", "timeOfDay": "09:00", "weekdays": [1]}` and `bindToCurrentThread: false`.
10. **Report.**
    - the packages found;
    - the checks per kind and how long the baseline took;
    - anything quarantined and why;
    - the next step: `/ratchet:run <goal>`.

For a brand-new, empty repo, write a minimal config. The first `/ratchet:run` task is then to set up the toolchain and its checks.
