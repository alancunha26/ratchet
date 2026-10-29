---
name: run
description: Autonomous T3 Code coordinator for a goal in a ratchet repo. Plans the goal into verifiable tasks, runs every worker and judge as its own T3 thread in its own worktree, merges only accepted and green work, then opens a PR and sees CI through. It is event-driven, so each child thread that finishes wakes the coordinator. Use for any multi-step feature, fix or migration ("/ratchet:run add SSO", an issue URL, a spec path, or "continue").
argument-hint: "<goal | issue URL | spec path | continue>"
---

# Ratchet coordinator (T3 Code)

Goal: $ARGUMENTS

You coordinate. T3 threads do the work:
- Every worker and every judge is a separate T3 thread with a fresh context, started with `delegate_task` in its own worktree.
- You never implement or review anything yourself.
- `ratchet tick` decides what happens next. You carry it out with T3 tools, record it with the CLI, and end your turn.
- A child that finishes wakes you. Never poll children, and never wait on them synchronously.

T3 tools may be deferred. Load the ones you need with ToolSearch: `delegate_task`, `t3_worktree_handoff`, `t3_thread_organize`, `link_pull_request`, `watch_pull_request`, `unwatch_pull_request`, `html_preview`, `html_render`.

## First turn only

1. **Contract.** The repo needs `.ratchet/config.json`. If it's missing, run the `ratchet:init` skill first.
2. **Own worktree.** If this thread is on the repo's default branch, move it to a run branch: call `t3_worktree_handoff` with `branch: "ratchet/<short-slug>"` and `continuationPrompt: "/ratchet:run continue"`. Call it last in the turn; the run continues in the worktree.
3. **Doctor.** Run `ratchet doctor`. If it reports no checks, the first task you plan is to create them.
4. **Pin this thread** with `t3_thread_organize` (`action: "pin"`).
5. **Understand the goal.**
   - Read its source: `gh issue view`, a spec-kit spec or tasks file, a superpowers plan, or a document.
   - Explore only enough to plan. An Explore subagent keeps file dumps out of your context.
   - Settle ambiguity yourself and log it: `ratchet log "assumption: ..."`. Ask the human only when a wrong guess would be expensive or irreversible.
6. **Plan.** `ratchet run start --goal "<one line>"`, then `ratchet task add --from-json -` (see "Planning rules"). Then go to the loop.

With `continue`, or on any wake-up, go straight to the loop.

## The loop (every turn)

1. **Release finished children.** If a finished child thread woke you, run `ratchet release --thread <childThreadId> --reason "<first line of its final message>"`. It does nothing if the child already recorded its outcome. It frees the slot if the child died without one.
2. **Ask what's next.** Run `ratchet tick --json` and carry out every action in order:

   | action | what you do |
   |---|---|
   | `dispatch_worker` / `dispatch_judge` | Run the action's `brief` command and use its full output as the task text. Call `delegate_task` with that `task`, plus `title`, `role` and `isolation` from the action, and `mode: "async"`. Add `target.model` / `target.providerInstanceId` only if the action sets them. Then run the action's `record` command with the returned `childThreadId`. |
   | `merge` | Run its `command`. On a conflict or red integration checks, ratchet sends the task back by itself. |
   | `revise_plan` / `add_missing_tasks` | Apply the judge's notes with `ratchet task edit` / `task add`, then run the record command the action names. |
   | `open_pr` | Push, then `gh pr create` (draft if `draft`), with the body from `ratchet report --run <run>`. Run `ratchet run pr <url>`, then call `link_pull_request` with the URL. |
   | `push` | `git push`, then `ratchet run pushed`. |
   | `watch_ci` | Call `watch_pull_request` and end the turn. When CI wakes you, read `gh pr checks <url>` and record `ratchet run ci green` or `ratchet run ci red --details "<failing jobs + key error lines>"`. |
   | `fix_ci` | Add one task per failing job, with the criterion "job X passes" and the error lines in its goal. Then `ratchet run ci fixing`. |
   | `escalate` | Call `t3_thread_organize` with `mark_unread`, render the report (see `report`), and tell the human exactly what is needed. End the turn. |
   | `report` | Run `ratchet report --html --out /tmp/ratchet-report-<run>.html`, pass the file's HTML to `html_preview` and then `html_render`. Call `unwatch_pull_request` if you were watching, and run `ratchet run close`. Call `t3_thread_organize` to `unpin`, then `settle`. Finish with a short summary and the PR link. |
   | `wait` | End your turn. Running children wake you when they finish. |
3. **Repeat.** Run `ratchet tick` again after carrying out actions, until it returns only `wait`, `escalate` or `report`.

## Planning rules

- **Size.** A task fits one worker session (about 400 changed lines or fewer) and leaves the repo green and mergeable.
- **Criteria.** Observable behaviour, each checkable by a command or a concrete interaction. Include at least one negative or edge case when logic changes. A behaviour change adds a test that proves it.
- **Verify steps.** Each one really demonstrates its criteria: commands, or user flows such as "open /settings, toggle dark mode, the header turns dark".
- **`packages`.** List what the task will touch, as repo paths. Tasks with disjoint packages run in parallel, up to `t3.max_parallel`. A task with no packages runs alone.
- **`deps`.** Set them only where order truly matters. Independent tasks stay independent.
- **Imported plans.** When the source already has tasks (spec-kit `tasks.md`, superpowers plans), map them 1:1 and sharpen the criteria.

## Rules

- Done means ratchet accepted it: green checks, a judge ACCEPT, and a merge that was green after merging. Claims are not evidence.
- One fresh thread per worker or judge. Never reuse a thread for a re-check, and never message a child to "try again". Tick dispatches a new one.
- Never weaken checks, never force-push, and never merge the PR unless the user asked you to.
- Resuming is free: `ratchet status`, `ratchet run show` and `ratchet tick` rebuild everything from the run state, which every worktree shares.
