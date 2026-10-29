# Lessons from v0.1–v0.3

The code is archived on branch `archive/v0.3` (tag `v0.3.0-archive`). It's worth reusing when a piece fits.

## What held up (reuse)

- **Snapshot the working state as a git tree** through a throwaway index. Cache results by tree id and merge-base. `gitstate.tree_id`.
  - Copy the index with `shutil.copy2`, not `copyfile`. Git's racy-clean detection relies on the index mtime. Without it, same-size edits made in the same second are missed (found by a flaky test).
- **Decode git output with `errors="replace"`.** Diffs can contain non-UTF-8 bytes, and they crashed the CLI.
- **Find affected packages with dependents** (npm/pnpm/yarn/bun workspaces, Cargo path deps, go.work, uv, Godot). `workspace.py`.
- **Error excerpts:** keep only the relevant lines, and drop runtime-internal stack frames and npm noise. Agents need short, actionable failures.
- **Tamper scan:** skip and focus markers, deleted tests, suppressions, protected paths, and reasoned allowances shared repo-wide (not per branch, so judges on detached checkouts can see them).
- **Run state in the git common dir** (`.git/ratchet/`), so every worktree shares it and nothing is committed.
- **`tick` as a deterministic state machine** that tells the coordinator what to do. It's testable, it resumes cleanly, and it doesn't drift.
- **A merge gate:** merge, re-verify, and on a conflict or red result restore the branch and send the task back.
- **Self-contained briefs** with the CLI's absolute path, so they work for any harness and need no installed plugin.
- **`exec-check --fail-on`** for tools that exit 0 on errors (Godot).
- **End-to-end tests** that build throwaway monorepos and worktrees and drive the real CLI. They caught the real bugs.

## Process hygiene (learned the hard way, 2026-10-29)

- **Never** run a check with `subprocess.run(shell=True, timeout=...)`. On timeout it kills only the shell. The test runner's children were reparented to `systemd --user` and spun at 100% CPU: mutants with infinite loops leaked a process every 20–30 s until the machine reached about 96°C.
- Every command now goes through `runner.run_group`:
  - it runs in a new session;
  - on timeout, the whole group gets SIGTERM, then SIGKILL, and so does the full process tree, which catches descendants that started their own session, such as nested ratchet runs;
  - after a normal exit, leftovers in the group are reaped.
- A mutant that hangs counts as killed. `ProcessHygieneTest` guards all of this.
- Don't run several mutation or probe runs at the same time on a dev machine.

## What didn't (avoid)

- **A universal framework.** Built-in check kinds and product profiles became runtime rules ratchet imposed, and every new stack needed more of them.
- **Judging by exit codes.** It needs per-test baselines, tests that fail before the change, and fake-pass guards.
- **Treating T3 Code as a harness.** T3 is the agent IDE. Harnesses run inside it.
- **Native subagents as the dispatch mechanism.** They differ per harness. `delegate_task` is the same everywhere.
- **Claude-only enforcement.** The Stop hook is a nice extra; the real gates have to live in the CLI.
- **Worktree per task by default in monorepos.** Each new worktree reinstalls dependencies. Default to the run's checkout and one task at a time; parallel worktrees are opt-in.

## T3 Code facts

- **The `t3-code` tools** reach every harness T3 hosts. ACP agents get them only if they support MCP; Pi finds most through its tool search.
- **`delegate_task`:**
  - children run in the parent thread's project (passing `target.projectId` is rejected);
  - children get only their task text;
  - `mode: async` wakes the parent on completion;
  - children can't widen their permission mode;
  - approvals aren't routed to the parent, but questions are.
- **`t3 thread start <project> - --worktree <base> --branch <name> --provider --model --json`** starts a thread from any shell. The binary is at `~/.t3/bin/t3`.
- **`t3.json`** at the repo root holds scripts with `runOnWorktreeCreate` (with `async: false` the agent waits for it), `runOnSettle`, `previewUrl` and `autoOpenPreview`.
- **`watch_pull_request`** polls every 2 minutes and wakes on failed checks, comments from others, or conflicts.
- **Scheduled tasks** can be webhooks with signatures, filters, dedupe and `{{body.x}}` templates. They need T3 Connect for a public URL.
- **Browser:** `preview_snapshot` should only be used when browser work was asked for. A judge's brief must ask for it explicitly. Agent tabs need `reuseExistingTab: false` when running in parallel.

## Research takeaways (2026-10-29)

- **How others judge "done":** tests that should fail before the change and pass after, plus no regressions in tests that passed before (SWE-bench and related benchmarks). An exit code is not a verdict.
- **Setup agents** (Repo2Run, SetUpAgent, ExecutionAgent, RepoLaunch) infer the setup, validate it by checking that tests actually ran and were collected, roll back between attempts, and freeze the result.
- **Prose verification instructions get skipped.** Enforce them with a gate (Spotify, OpenHands, Claude Code hooks).
- **Visual checks can be deterministic:** render, reference image, pixel diff. Use a VLM only afterwards, comparing pairs against a reference, with binary rubric items, swapped order, and a majority vote.
- **Mutation on the changed lines** (Meta ACH) shows whether new tests actually catch broken code.
