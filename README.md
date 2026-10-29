# ratchet

A verification loop for coding agents, built around [T3 Code](https://github.com/pingdotgg/t3code). A ratchet only turns one way: each task locks in progress behind checks, and nothing gets through by weakening those checks.

**T3 runs the agents.** The coordinator is a pinned T3 thread. Every worker and every judge is its own T3 thread, in its own worktree, visible in your sidebar, and you can step into any of them. Judges use T3's built-in browser to collect evidence for UI changes, and T3 watches the PR's CI.

**ratchet provides the parts that must not drift:**
- the repo's contract (`.ratchet/config.json`);
- a state machine (`ratchet tick`) that tells the coordinator exactly what to do next;
- self-contained briefs for each thread (`ratchet brief`);
- the deterministic gate: checks on the changed packages, plus a tamper scan;
- merges that happen only when the result is still green.

```
you ── /ratchet:run <goal> ──▶ coordinator thread (pinned, its own worktree)
                                  │  ratchet tick → actions
         ┌────────────────────────┼─────────────────────────┐
   delegate_task (worktree)  delegate_task (worktree)   watch_pull_request
   worker thread T1 … Tn     judge thread (browser,     CI wakes the coordinator
   brief → code → gate →     screenshots, verify) →
   submit                    accept / reject
         └──── finishing wakes the coordinator ─────┘
   accepted → ratchet task merge (integration gate) → final judge → PR → CI → report
```

The coordinator is event-driven. It dispatches children asynchronously and ends its turn, and each child that finishes wakes it again. A run can last as long as it needs to, and its state lives on disk (`.git/ratchet/`, shared by every worktree), so it resumes anywhere.

**Constraint:** T3 delegates run in the parent thread's project. Run the coordinator in a thread of the repo's own project (`/ratchet:run` hands that thread off to a run worktree). Cross-repo runs (several T3 projects) aren't supported yet.

## Why it looks like this

ratchet follows what has worked for teams running agents at scale:

| Principle | Source | Where it lives in ratchet |
|---|---|---|
| Deterministic checks gate everything. They switch on from repo contents and return only the error lines that matter. | Spotify's background agents; Huntley's "backpressure" | `ratchet verify`, the Stop hook, `ratchet task merge` |
| Agents grade their own work too kindly, so a separate judge tuned for skepticism decides. | Anthropic, harness design for long-running apps | judge threads (T3 browser evidence), `.ratchet/judge.md` |
| Agree on how success is checked before writing code. | Anthropic's sprint contracts | task `criteria` + `verify`, judge "contract" mode |
| State lives in files and git, not in the conversation. | Anthropic, effective harnesses | runs/tasks/progress under `.git/ratchet/` |
| One task per fresh context. Keep the structure simple. | Ralph loop; Cursor's planner/worker/judge | a T3 coordinator thread + a worker thread per task |
| Cap the retries, then escalate. | Stripe Minions (2 CI rounds) | `limits.*`, blockers |
| Block attempts to pass by weakening checks. | Anthropic, Arize | `ratchet tamper` / `ratchet allow` |
| Turn friction into harness fixes. | OpenAI harness engineering; Theo's "ask what blocks them" | `ratchet blocker`, `ratchet stats`, `ratchet:garden` |

## Install

The repo is a Claude Code plugin marketplace called `cosmyk`.

```bash
claude plugin marketplace add ~/.repos/github.com/cosmyk/ratchet   # or Cosmyk-Labs/ratchet once pushed
claude plugin install ratchet@cosmyk
```

Install it on every machine that runs T3 threads. Worker and judge threads are full Claude sessions, so the plugin's Stop gate only protects them where it's installed. Briefs call the CLI by absolute path, so they work even without it on PATH.

The plugin puts `ratchet` on the agent's PATH and adds hooks and skills. Outside repos that have `.ratchet/config.json`, the hooks do nothing.

## Use

| You do | What happens |
|---|---|
| `/ratchet:init` in a repo | Detects packages and checks, tunes them to match CI, proves the baseline is green, writes `.ratchet/` and a `CLAUDE.md` section, and adds a T3 worktree setup script. |
| `/ratchet:run <goal, issue URL or spec>` | Runs the T3 coordinator: plan, a contract-review thread, worker threads (in parallel when their packages don't overlap), a judge thread per task, a merge gate, a final judge, a draft PR, then CI watching through T3. Ends with an HTML report in the thread. |
| Normal work in the repo | The Stop gate checks every change you make. `ratchet verify` gives fast feedback any time. |
| `/ratchet:garden` (weekly via T3 `schedule_task`) | Reads `ratchet stats` and the blockers, then fixes the biggest friction through its own ratchet run. |
| Label a GitHub issue `ratchet` (optional T3 webhook) | Starts a new coordinator thread for that issue, with nobody at the keyboard. |

Other skills: `/ratchet:work <task>` (the worker protocol, used by T3 delegates working in parallel) and `/ratchet:judge <mode>`.

## The repo contract: `.ratchet/config.json`

```jsonc
{
  "base": "auto",                       // origin/HEAD, then main/master
  "setup": "pnpm install --frozen-lockfile",
  "packages": "auto",                   // or [{"dir": "svc/api", "kind": "node"}]
  "checks": {
    "node": {
      "fast": ["script:lint", "script:typecheck", "script:test"],  // only if the package defines the script
      "full": ["script:build", "script:test:e2e"]
    },
    "rust": {"fast": ["batch:cargo clippy --all-targets {p_flags} -- -D warnings", "batch:cargo test {p_flags}"]}
  },
  "repo_checks": {"fast": [{"run": "pnpm prettier --check .", "when": ["**/*.{ts,tsx,md}"]}], "full": []},
  "protect": [".ratchet/config.json", ".github/workflows/**", "**/__snapshots__/**", "**/*.snap"],
  "skip": ["packages/legacy:test"],     // quarantine; always paired with a blocker
  "limits": {"gate_attempts": 4, "judge_rounds": 2, "ci_rounds": 2, "parallel_jobs": 4, "machine_slots": 2},
  "run": {"pr": "draft"},               // draft | ready | none
  "t3": {
    "max_parallel": 2,                  // worker threads at once (disjoint packages only)
    "contract_review": true,
    "worker": {"model": null, "provider": null, "isolation": "worktree"},
    "judge": {"model": "claude-sonnet-5-5", "provider": null}  // a different model gives a second opinion
  },
  "app": {                              // how judges start the product for browser review
    "dev": "pnpm --filter web dev", "port_env": "PORT", "url": "http://localhost:{port}/",
    "ready_text": "ready", "profile": null, "ui_paths": ["**/*.tsx", "**/*.css"]
  }
}
```

Check specs:
- `script:NAME` runs a package.json script, and only in packages that define it.
- `batch:CMD` runs once at the repo root for every affected package of that kind. Placeholders: `{names}`, `{p_flags}`, `{filters}`, `{dirs}`.
- A plain `CMD` runs inside each affected package. Placeholders: `{name}`, `{dir}`.

"Affected" means the packages that own a changed file, plus every package that depends on them (followed through workspace and path dependencies).

## How the gate works

1. ratchet snapshots the full working state as a git tree, using a throwaway index so your staging area is never touched. The same state always gives the same id, so results are cached by id and an unchanged state is never re-verified.
2. It diffs that tree against the merge-base with the base branch and maps the changed files to affected packages.
3. It runs those packages' checks in parallel. A machine-wide slot limit stops parallel worktrees from running the machine out of memory. Only the relevant error lines go back to the agent; the full logs stay in `.git/ratchet/logs/`.
4. It scans the diff for attempts to weaken checks: skipped or focused tests, deleted test files, lint and type suppressions, protected paths.
5. On Stop, a failure blocks the agent from finishing and gives it the errors. After `gate_attempts` failures it lets the agent stop and logs a blocker. A session that changed nothing is never blocked.
6. `ratchet task submit` needs a clean, committed tree with fast checks green. `ratchet task accept` needs the full checks green.
7. `ratchet task merge` merges an accepted task into the run branch, then re-runs the checks. On a conflict or a red result it restores the run branch and sends the task back with a note.
8. Detached checkouts (judges) are never gated, so a judge can always finish and reject red work.

All runtime state (evidence, logs, runs, blockers, events) lives in the git common dir under `.git/ratchet/`. Every worktree of the repo shares it, and it's never committed.

## CLI

```
ratchet verify [--full] [--all] [--no-cache] [--json]
ratchet affected | tamper | allow <id> --reason "..."
ratchet tick [--json]                          # coordinator: what to do next
ratchet brief worker|judge <id> | brief contract|final
ratchet release --thread <id>                  # a child ended without recording an outcome
ratchet run start|show|dispatch|contract|final|pr|pushed|ci|close
ratchet task merge <id> | task dispatch <id> --role worker|judge --thread <id>
ratchet evidence add <task|final> <file> --note "..." | evidence list
ratchet report [--html] | port
ratchet task add --from-json - | edit <id> --from-json '{...}' | list | next | show <id>
ratchet task start|submit|accept|reject|block|reset <id> [--summary/--notes/--reason "..."]
ratchet log "..." | blocker add "..." --kind slow|flaky|tooling|env|spec | blocker list | blocker resolve <id>
ratchet status | stats | init | doctor [--baseline] | setup
```

`RATCHET_GATE=off` turns the gate off for a session. `RATCHET_DEBUG=1` explains its decisions on stderr. `RATCHET_SLOTS=N` overrides the machine slot limit.

## Layout

```
.claude-plugin/marketplace.json   marketplace "cosmyk" → ./plugin
plugin/
  .claude-plugin/plugin.json
  bin/ratchet                     CLI (Python 3.9+, stdlib only)
  lib/ratchet/                    gitstate, workspace, checks, tamper, tasks, orchestrate, report, blockers, hooks, scaffold, cli
  hooks/hooks.json                SessionStart, Stop, PreToolUse
  skills/init, run, work, judge, garden
  templates/                      briefs (worker, judge, contract, final), CLAUDE.md section, judge calibration
tests/                            end-to-end tests on throwaway monorepos
.ratchet/                         ratchet checks itself
```

## Develop

```bash
python3 -m unittest discover -s tests       # also runs as ratchet's own gate
claude plugin validate ./plugin
claude --plugin-dir ./plugin                # try local changes without installing
```
