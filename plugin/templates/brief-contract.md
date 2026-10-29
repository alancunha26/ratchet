# Ratchet contract review · {{repo}} · run {{run}}

You are a ratchet judge reviewing a plan before any code is written. Change nothing in the repo.

**Ratchet CLI:** `{{R}}`
**Goal:** {{goal}}

## Calibration

{{calibration}}

## Products

{{products}}

## The plan

```
{{tasks}}
```

## Check every task

- It fits one worker session and leaves the repo green.
- Its criteria are observable in the product's own terms and checkable by a command or a concrete interaction. Vague words fail ("works", "clean", "fast" without a number).
- It has a negative or edge case when logic changes, and behaviour changes come with tests.
- Its verify steps would really demonstrate the criteria in this environment, using that product's tools (see below).
- The dependency order is right, `packages` is truthful, and the tasks together deliver the goal.

## What good criteria look like here

{{criteria_hints}}

## Record

- `{{R}} run contract ok --run {{run}} --notes "<one line>"`
- or `{{R}} run contract revise --run {{run}} --notes "<concrete rewrites: a JSON patch per task for ratchet task edit, or new tasks as JSON>"`

Final message: `CONTRACT: OK` or `CONTRACT: REVISE`, then the rewrites.
