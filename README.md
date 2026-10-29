# ratchet

A verification loop for coding agents. A ratchet only turns one way: each task locks in progress behind checks, and nothing gets through by weakening those checks.

- **Built on [T3 Code](https://github.com/pingdotgg/t3code), the agent IDE.** Every role is a T3 thread: the coordinator, the workers and the judges. ratchet uses T3's orchestration (`delegate_task` and async wake-ups), its built-in browser and devices, PR watching, schedules and webhooks, `t3.json` project scripts, and HTML renders.
- **Harness-agnostic.** Claude Code, Codex, Cursor, OpenCode, Pi or any ACP agent can fill any role, and each role can use a different provider. Everything an agent needs comes from the `ratchet` CLI and self-contained briefs, not from one harness's features.
- **Product-aware.** A Godot game, a web app, an API, a CLI, a library and a mobile app are verified differently. Profiles tell workers and judges how to run, exercise and collect evidence for each product in the repo.

```
ratchet start "<goal>" ──▶ t3 thread start ──▶ coordinator thread (any harness, own worktree, pinned)
                                                 │  ratchet tick → next actions
             ┌───────────────────────────────────┼──────────────────────────────┐
     delegate_task (role: implementation)   delegate_task (role: review)   watch_pull_request
     worker thread, any harness             judge thread(s), any harness   CI and review comments
     brief → code → verify → submit         verify + exercise the product   wake the coordinator
                                            (T3 browser, devices, frames) → accept / reject
             └──────────── each finished child wakes the coordinator ───────────────┘
     accepted → merge with an integration gate → final judge → PR → CI → HTML report in the thread
```

## Why it looks like this

| Principle | Source | Where it lives |
|---|---|---|
| Deterministic checks gate everything. They're scoped to the change and return only the error lines that matter. | Spotify's background agents; Huntley's "backpressure" | `ratchet verify`. `task submit`, `task accept` and `task merge` refuse red work. |
| An agent grading its own work is too kind, so a separate, skeptical judge decides. | Anthropic, harness design | judge threads, on a different model or harness if you like; `judge_panel` for subjective work |
| Agree on how success is checked before writing code. | Anthropic's sprint contracts | task `criteria` and `verify` steps, a contract-review thread, profile criteria guides |
| State lives in files, not in the conversation. | Anthropic, effective harnesses | `.git/ratchet/`, shared by every worktree. Resume anywhere with `ratchet tick`. |
| A fresh context per task, a simple structure, decisions in code. | Ralph loop; Cursor's planner/worker/judge | one worker thread per task; the `tick` state machine |
| Cap retries and escalate. | Stripe Minions | `limits.*`, blockers, `escalate` |
| Block attempts to pass by weakening checks. | Anthropic, Arize | `ratchet tamper` / `ratchet allow` |
| Turn friction into harness fixes. | OpenAI harness engineering; Theo's "ask what blocks them" | `ratchet blocker`, `ratchet stats`, the gardener brief on a T3 schedule |

## Install (once per machine)

```bash
~/.repos/github.com/cosmyk/ratchet/scripts/install.sh
```

This links `ratchet` into `~/.local/bin`, so every harness and T3 terminal can find it. If Claude Code is installed, it also installs the Claude plugin (`ratchet@cosmyk`), which adds one-line skills and a Stop hook that gives Claude faster feedback. Briefs call the CLI by absolute path, so child threads work either way.

## Use

| You | What happens |
|---|---|
| In a repo thread: "set up ratchet" (`/ratchet:init`, or follow `ratchet brief init`) | Detects packages, checks and products (web, api, cli, library, mobile, Godot); proves the baseline is green; writes `.ratchet/`, the `CLAUDE.md`/`AGENTS.md` sections and the `t3.json` scripts; offers schedules. |
| `ratchet start "<goal or issue URL>" [--provider codex] [--model ...]` | Launches a coordinator thread in T3, in its own worktree on `ratchet/<slug>`. It plans, dispatches, judges, merges, opens a PR, watches CI, and ends with a report. |
| In any existing thread: "run ratchet for X" (`/ratchet:run X`, or `ratchet brief coordinator --goal "X"`) | The same coordinator, in that thread. |
| Label an issue `ratchet` (optional T3 webhook) | A coordinator thread starts for it with no one at the keyboard. |
| Weekly T3 schedule: "run `ratchet brief garden`" | Reads `ratchet stats` and the blockers, then fixes the biggest friction through its own run. |

## The repo contract: `.ratchet/config.json`

```jsonc
{
  "base": "auto",
  "setup": "pnpm install --frozen-lockfile",       // also t3.json's runOnWorktreeCreate script (async: false)
  "checks": {
    "node":  {"fast": ["script:lint", "script:typecheck", "script:test"], "full": ["script:build", "script:test:e2e"]},
    "godot": {"fast": ["ratchet exec-check --fail-on 'SCRIPT ERROR|^ERROR:' -- godot --headless --path . --quit"]}
  },
  "repo_checks": {"fast": [{"run": "pnpm prettier --check .", "when": ["**/*.{ts,tsx}"]}]},
  "protect": [".ratchet/config.json", ".github/workflows/**", "**/__snapshots__/**", "**/*.snap"],
  "limits": {"gate_attempts": 4, "judge_rounds": 2, "worker_attempts": 3, "ci_rounds": 2},
  "run": {"pr": "draft"},
  "agents": {                                       // every role is a T3 thread; any provider T3 hosts
    "max_parallel": 1,                              // 1: one task at a time in the run checkout; >1: worktrees
    "contract_review": true,
    "judge_panel": 1,                               // >1: independent judges vote (games, design)
    "coordinator": {"provider": null, "model": null},
    "worker": {"provider": "claudeAgent", "model": null},
    "judge":  {"provider": "codex", "model": null}  // a different harness gives a second opinion
  },
  "products": [                                     // what the repo ships; each uses a profile
    {"name": "web",  "profile": "web",   "paths": ["apps/web/**"], "run": "pnpm --filter web dev",
     "port_env": "PORT", "url": "http://localhost:{port}/", "ready_text": "ready", "browser_profile": "test-user"},
    {"name": "api",  "profile": "api",   "paths": ["services/api/**"], "run": "pnpm --filter api dev", "port_env": "PORT"},
    {"name": "game", "profile": "godot", "paths": ["game/**"], "dir": "game", "godot": "godot4", "main_scene": "res://main.tscn"}
  ]
}
```

## Product profiles

Run `ratchet profiles` to list them, or `ratchet profiles godot` to show one. Each profile is a markdown guide with `## Run`, `## Exercise`, `## Evidence` and `## Good criteria` sections:

| Profile | How the judge exercises it |
|---|---|
| `web` | T3's built-in browser: background tab, act, snapshot images, mobile size, dark and light mode, recordings; console errors are a fail. Playwright elsewhere. |
| `api` | Starts the service on a free port and sends real requests, including errors and auth; checks responses against the schema and the logs. |
| `cli` | Runs commands in a scratch dir; checks exit codes, output and golden files, plus bad inputs. |
| `library` | Writes a tiny consumer of the public API, checks dependents, flags unstated breaking changes. |
| `mobile` | T3 devices (simulators and emulators through `agent-device`), plus Maestro, Detox or Flutter integration tests. |
| `godot` | Checks the headless load output for script errors, runs GUT or gdUnit4 scene tests with simulated input, captures Movie Maker frames for visuals (xvfb on Linux), replays recorded inputs, measures FPS, and uses a rubric with a judge panel for "feel". |

Add your own in `.ratchet/profiles/<name>.md`. A repo profile overrides a built-in one of the same name. Briefs include only the profiles of the products a task touches.

## How verification works

1. **Snapshot.** The working state becomes a git tree id (via a throwaway index). Results are cached per id.
2. **Affected packages.** The diff against the merge-base maps to the packages that own the changed files, plus everything that depends on them. Their checks run in parallel, with a cap on how many verify runs a machine handles at once, and only the error lines that matter are kept.
3. **Tamper scan.** It flags skipped or focused tests, deleted tests, suppressions, and changes to protected paths.
4. **Hard gates, the same for every harness.**
   - `task submit` needs committed work and green fast checks.
   - `task accept` needs green full checks.
   - `task merge` merges into the run branch and re-runs the checks. On a conflict or a red result it restores the branch and sends the task back.
   - `run final accept` needs green full checks.
5. **Claude Code adapter.** The plugin's Stop hook runs the same verification whenever Claude tries to finish. Detached checkouts (judges) skip it.

State (evidence, logs, runs, blockers, events) lives in `.git/ratchet/`, shared by every worktree and never committed.

## CLI

```
ratchet start "<goal>" [--provider] [--model] [--base] [--dry-run]
ratchet tick [--json]                              # coordinator: the next actions
ratchet brief coordinator|plan|worker <id>|judge <id>|contract|final|init|garden
ratchet release --thread <id>                      # a child ended without recording an outcome
ratchet run start|show|dispatch|contract|final|pr|pushed|ci|close
ratchet task add|edit|list|next|show|dispatch|start|submit|accept|reject|merge|rollback|block|reset
ratchet verify [--full] [--all] | affected | tamper | allow <id> --reason "..."
ratchet evidence add <task|final> <file> --note "..." | evidence list
ratchet report [--html] | profiles [name] | exec-check --fail-on RE -- cmd | port
ratchet log | blocker add|list|resolve | status | stats | init | doctor [--baseline] | setup
```

`RATCHET_GATE=off` turns the Claude Stop gate off for a session, and `RATCHET_DEBUG=1` explains its decisions.

## T3 Code facts the design relies on

- T3 hosts the harnesses and gives each one the `t3-code` MCP tools. ACP agents get them only if they support MCP. Pi finds most of them through its tool search.
- A `delegate_task` child runs in the parent thread's project and receives only its task text, so briefs are self-contained. Completion wakes the parent.
- Children can't widen their permission mode, and approvals aren't routed to the parent. Unattended runs need a mode that doesn't stop for approvals.
- `watch_pull_request` polls every 2 minutes and wakes the thread on failed checks, comments from others, or conflicts.
- `t3.json` scripts are committed and shared across machines. `runOnWorktreeCreate` with `async: false` holds the agent until setup finishes.

## Layout

```
scripts/install.sh                  ratchet on PATH + the Claude plugin
.claude-plugin/marketplace.json     marketplace "cosmyk" → ./plugin
plugin/
  bin/ratchet                       the CLI (Python 3.9+, stdlib only)
  lib/ratchet/                      gitstate, workspace, checks, tamper, tasks, orchestrate, profiles, report, ...
  templates/                        briefs: coordinator, plan, worker, judge, contract, final, init, garden
  profiles/                         web, api, cli, library, mobile, godot
  skills/                           Claude adapter: init, run, work, judge, garden (each calls a brief)
  hooks/hooks.json                  Claude adapter: SessionStart context, Stop gate, state guard
tests/                              end-to-end tests on throwaway monorepos and worktrees
```
