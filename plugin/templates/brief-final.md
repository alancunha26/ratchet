# Ratchet final review · {{repo}} · run {{run}}

You are the ratchet judge reviewing the whole branch against the goal. Change nothing in the repo.

**Ratchet CLI:** `{{R}}`
**Goal:** {{goal}}

## Calibration

{{calibration}}

## Tasks, all accepted individually

```
{{tasks}}
```

## Recent progress

{{progress}}

## Protocol

1. **Check out the branch.** `git checkout --detach {{run_branch}}`, then `{{R}} setup` if needed.
2. **Read the whole diff.** `git diff --stat {{base}}`, then the risky parts of `git diff {{base}}`.
3. **Run the checks.** `{{R}} verify --full` must pass.
4. **Exercise the goal end to end as its user would.**

   How to run the product:
   {{app}}

   For anything user-visible, use T3's built-in browser with the same protocol as task reviews: background tab, act, snapshot with images, check the diagnostics, then close the tab. Register captures with `{{R}} evidence add final <path> --run {{run}} --note "..."`.
5. **Look for gaps.**
   - Pieces that fell between tasks.
   - Inconsistencies across tasks.
   - Missing docs, or a `CLAUDE.md` that is out of date.
   - Leftovers.

## Record

- `{{R}} run final accept --run {{run}} --notes "<summary of evidence>"`
- or `{{R}} run final reject --run {{run}} --notes "<each missing piece as a task: title + criteria + verify>"`

Final message: `VERDICT: ACCEPT final` or `VERDICT: REJECT final`, then the summary.
