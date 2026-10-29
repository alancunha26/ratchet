---
name: work
description: Protocol for implementing one ratchet task end to end (orient, implement, prove each criterion, commit, submit). Use when told to work a ratchet task, for example by the ratchet:run coordinator or `/ratchet:work T3 --run my-run`.
argument-hint: "<task-id> [--run <run>]"
---

# Ratchet worker protocol

Input: a task id and a run id ($ARGUMENTS or your prompt). Pass `--run <run>` to every `ratchet` command when you were given one.

## 1. Orient (never skip)

- `ratchet task show <id> --run <run>`. This is your contract. The judge checks each acceptance criterion separately, against evidence.
- `ratchet run show --run <run>` for the goal and recent progress. Run `git log --oneline -10`.
- Read the `CLAUDE.md` of every package you will touch.
- Run `ratchet verify` once before changing anything. If it is red, you can't prove your work on a red base: fix it if it's small and clearly broken, otherwise block the task (step 9).

## 2. Start

`ratchet task start <id> --run <run>`

## 3. Implement

- Make the smallest complete change that meets every criterion. Implement it fully: no placeholders, TODO stubs, mocked results or hardcoded outputs presented as done.
- Stay in scope. Don't refactor or "improve" unrelated code. Record ideas with `ratchet log "idea: ..." --run <run>` instead.
- Add or update tests that fail without your change. Tests are how the judge believes you.
- After each meaningful step, run `ratchet verify`. It checks only the affected packages and is cached. Read the excerpt, and open the full log path when you need more.

## 4. Prove each criterion

Run every "how to verify" step for real: run the CLI, start the server and call it, run the e2e test, take a screenshot for UI work. Keep each command and its key output, because you cite them on submit.

## 5. Green and committed

`ratchet verify --full` must pass. Then commit with a descriptive message: `git add -A && git commit -m "<id>: <what and why>"`.

## 6. Submit

`ratchet task submit <id> --run <run> --summary "<for each criterion: how it is met + the evidence command>"`

Submit refuses a dirty tree or red checks. When it refuses, fix the cause. Don't work around it.

## 7. Leave the harness better

- Log anything that cost you time (slow check, flaky test, missing tool, wrong docs, ambiguous criterion): `ratchet blocker add "..." --kind slow|flaky|tooling|env|spec`.
- Learned a durable, checkable project rule? Add one line to the relevant package's `CLAUDE.md`, in the same commit.

## 8. Final message

The coordinator reads only this message, so keep it short:
- status: submitted or blocked
- commit SHA
- one line of evidence per criterion
- blockers you logged

## 9. Rework and blocking

- If the judge rejected an earlier round, its notes are in `ratchet task show`. Address every point, then repeat steps 3 to 8.
- If you're truly blocked (a criterion is impossible or contradictory, the environment is broken, a human decision is needed): run `ratchet task block <id> --run <run> --reason "..."`, log a blocker, and state exactly what is needed. Never fake progress.

## Hard rules

- Never edit `.ratchet/config.json`, CI workflows, snapshots, or test skips to get green.
- Never `git push`.
- Never change other tasks' state.
