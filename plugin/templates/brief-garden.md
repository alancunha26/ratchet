# Ratchet gardener · run by any harness in T3 Code

Agents get faster when someone keeps asking what slows them down, then removes it. That is this skill's job.

## 1. Gather

- `{{R}} stats` for gate failures, slow and failing checks, and judge accept/reject counts.
- `{{R}} blocker list`.
- `{{R}} status`, and `git log --since=2.weeks --oneline`.
- The judge's reject notes (`{{R}} task show` on rejected tasks) and recurring reasons in `progress.md`.

## 2. Rank

Order the friction by frequency × time lost, and take the top three to five items.

## 3. Pick the fix, mechanical first

| Friction | Fix |
|---|---|
| Slow gate (verify p50 over about 2 minutes) | Narrow the fast checks, move slow ones to `full`, add caching (turbo), raise `limits.parallel_jobs`, split test suites. |
| Flaky test | Fix the test. If you can't, quarantine it in `skip` with a blocker that links an issue. Never quarantine silently. |
| Missing tool or env | Extend `setup` and document it in `CLAUDE.md`. |
| Repeated reject reason or review comment | Encode it as a lint rule, structural test or type. Use a one-line `CLAUDE.md` rule only when it can't be made mechanical. |
| Judge misjudged (accepted broken work or rejected good work) | Add a dated line under "Past misjudgments" in `.ratchet/judge.md`. |
| Recurring scope creep or placeholder output | Sharpen the repo's `CLAUDE.md` ratchet section. |
| Frequent merge conflicts (`merge_conflicts` in stats) | Make tasks declare tighter `packages`, or lower `t3.max_parallel`. |
| Workers dying without submitting (`worker_releases`) | Read those threads with `t3_thread_read` (view `activity`: it reads the same under any harness) and fix the cause: setup, context, or tasks that are too large. |

## 4. Ship it

Ship the fixes as a normal ratchet run, one task per fix, each reviewed by a judge:
- from a shell: `{{R}} start "harness fixes: <list>"`;
- or in this thread: `{{R}} brief coordinator --goal "harness fixes: <list>"`.

Changes to protected paths need `{{R}} allow <id> --reason "garden: <why>"`.

## 5. Close the loop

For each blocker you fixed: `{{R}} blocker resolve <id> --note "<fix + commit>"`.

## 6. Report

- the top frictions found, with numbers;
- the fixes shipped, with the PR link;
- what's left;
- any decision that needs the human, such as buying compute or changing CI.
