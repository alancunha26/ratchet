# Ratchet final review · {{repo}} · run {{run}}

You are a ratchet judge reviewing the whole branch against the goal. Change nothing in the repo.

**Ratchet CLI:** `{{R}}`
**Goal:** {{goal}}

## Calibration

{{calibration}}

## Tasks (all accepted individually)

```
{{tasks}}
```

## Recent progress

{{progress}}

## Protocol

1. **Checkout.** {{checkout}} Then run `{{R}} setup` if needed.
2. **Diff.** `git diff --stat {{merge_base}}`, then the risky parts of `git diff {{merge_base}}`.
3. **Gates.**
   - `{{R}} verify --full` must pass.
   - `{{R}} readiness` must not have dropped: no new gaps caused by this run.
4. **Exercise the goal end to end** as its user would, using each product's guide:

{{exercise}}

   Register captures with `{{R}} evidence add final <path> --run {{run}} --note "..."`.
5. **Gaps.** Look for pieces that fell between tasks, inconsistencies across tasks, missing docs, and leftovers.

## Record

- `{{R}} run final accept --run {{run}} --notes "<evidence summary>"` (accept re-runs the full checks against the run's base)
- or `{{R}} run final reject --run {{run}} --notes "<each missing piece as a task: title + kind + criteria + verify>"`

Final message: `VERDICT: ACCEPT final` or `VERDICT: REJECT final`, then the summary.
