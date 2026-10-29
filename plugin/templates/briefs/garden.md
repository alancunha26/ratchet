# Ratchet gardener · {{repo}}

The harness ratchets too: it only gets stricter, faster or broader. Your job is to find what holds the agents back, then remove it.

**Ratchet CLI:** `{{R}}`

## 1. Gather

- `{{R}} readiness`: the level and its gaps.
- `{{R}} stats`: gate failures, flaky tests, slow verifies, judge rejects, merge conflicts, stuck workers.
- `{{R}} blocker list`.
- `{{R}} garden`: harness tasks proposed from all of the above, most valuable first.
- `{{R}} upgrade --check`: whether a kernel migration is pending.
- For workers that died without submitting: `t3_thread_read` their threads with the `activity` view, which reads the same under any harness.

## 2. Choose

Take the top three to five tasks by frequency × time lost. Prefer mechanical fixes (a check, a lint rule, an exercise script, a budget) over prose rules. A judge misjudgment becomes a dated line under "Past misjudgments" in `.ratchet/judge.md`.

## 3. Ship it as a harness run

Run `{{R}} start "harness improvements: <list>"`, or in this thread `{{R}} brief coordinator --goal "harness improvements"`. Then add the tasks with `{{R}} garden --plan | {{R}} task add --from-json -`. They are `harness` tasks:
- they may change `.ratchet/` and `t3.json`;
- every new or changed check must pass `{{R}} proof canary <check>`;
- the judge reviews them like any other task.

## 4. Close the loop

- For each fixed blocker: `{{R}} blocker resolve <id> --note "<fix + commit>"`.
- Report:
  - the frictions found, with numbers;
  - the fixes shipped, with the PR;
  - the readiness level before and after;
  - what's left;
  - any decision that needs the human.
