# ratchet

A verification loop for coding agents. A ratchet only turns one way: each task locks in progress behind checks, and nothing gets through by weakening those checks.

You give it a goal. ratchet plans the goal into tasks you can check. A fresh worker implements each task. Deterministic gates decide whether the code is green, and a separate, skeptical judge decides whether the task is done. The friction the agents hit becomes input for fixing the harness itself.

It is built for Claude Code inside [T3 Code](https://github.com/pingdotgg/t3code). It works in single-package repos and in monorepos (npm/pnpm/yarn/bun workspaces, turbo, nx, Cargo workspaces, go.work, uv workspaces).

## Why it looks like this

ratchet follows what has worked for teams running agents at scale:

| Principle | Source | Where it lives in ratchet |
|---|---|---|
| Deterministic checks gate everything. They switch on from repo contents and return only the error lines that matter. | Spotify's background agents; Huntley's "backpressure" | `ratchet verify`, Stop/SubagentStop hooks |
| Agents grade their own work too kindly, so a separate judge tuned for skepticism decides. | Anthropic, harness design for long-running apps | `ratchet:judge` agent, `.ratchet/judge.md` |
| Agree on how success is checked before writing code. | Anthropic's sprint contracts | task `criteria` + `verify`, judge "contract" mode |
| State lives in files and git, not in the conversation. | Anthropic, effective harnesses | runs/tasks/progress under `.git/ratchet/` |
| One task per fresh context. Keep the structure simple. | Ralph loop; Cursor's planner/worker/judge | `ratchet:run` coordinator + `ratchet:worker` |
| Cap the retries, then escalate. | Stripe Minions (2 CI rounds) | `limits.*`, blockers |
| Block attempts to pass by weakening checks. | Anthropic, Arize | `ratchet tamper` / `ratchet allow` |
| Turn friction into harness fixes. | OpenAI harness engineering; Theo's "ask what blocks them" | `ratchet blocker`, `ratchet stats`, `ratchet:garden` |

## Install

The repo is a Claude Code plugin marketplace called `cosmyk`.

```bash
claude plugin marketplace add ~/.repos/github.com/cosmyk/ratchet   # or Cosmyk-Labs/ratchet once pushed
claude plugin install ratchet@cosmyk
```

The plugin puts `ratchet` on the agent's PATH and adds hooks and skills. Outside repos that have `.ratchet/config.json`, the hooks do nothing.

## Use

| You do | What happens |
|---|---|
| `/ratchet:init` in a repo | Detects packages and checks, tunes them to match CI, proves the baseline is green, writes `.ratchet/` and a `CLAUDE.md` section, and adds a T3 worktree setup script. |
| `/ratchet:run <goal, issue URL or spec>` | Coordinates the whole loop: plan, contract review, a worker per task, a judge per task, a final judge, then a draft PR. CI fixes are capped at `limits.ci_rounds`. |
| Normal work in the repo | The Stop gate checks every change you make. `ratchet verify` gives fast feedback any time. |
| `/ratchet:garden` (weekly via T3 `schedule_task`) | Reads `ratchet stats` and the blockers, then fixes the biggest friction through its own ratchet run. |

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
  "judge": {"model": "inherit"},        // e.g. "sonnet" for a second opinion from a different model
  "run": {"pr": "draft", "max_parallel": 1}
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

All runtime state (evidence, logs, runs, blockers, events) lives in the git common dir under `.git/ratchet/`. Every worktree of the repo shares it, and it's never committed.

## CLI

```
ratchet verify [--full] [--all] [--no-cache] [--json]
ratchet affected | tamper | allow <id> --reason "..."
ratchet run start --goal "..." | run show | run close
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
  lib/ratchet/                    gitstate, workspace, checks, tamper, tasks, blockers, hooks, scaffold, cli
  hooks/hooks.json                SessionStart, Stop, SubagentStop, PreToolUse
  agents/worker.md, judge.md
  skills/init, run, work, judge, garden
  templates/                      CLAUDE.md section, judge calibration
tests/                            end-to-end tests on throwaway monorepos
.ratchet/                         ratchet checks itself
```

## Develop

```bash
python3 -m unittest discover -s tests       # also runs as ratchet's own gate
claude plugin validate ./plugin
claude --plugin-dir ./plugin                # try local changes without installing
```
