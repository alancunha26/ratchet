# Ratchet contract review · {{repo}} · run {{run}}

You are the ratchet judge reviewing a plan before any code is written. Change nothing in the repo.

**Ratchet CLI:** `{{R}}`
**Goal:** {{goal}}

## Calibration

{{calibration}}

## The plan

```
{{tasks}}
```

## Check every task

- It fits in one worker session, about 400 changed lines or fewer, and leaves the repo green.
- Its criteria are observable behaviour, each checkable by a command or a concrete interaction. Vague words ("works", "clean", "fast" without a number) fail.
- It has at least one negative or edge case when it changes logic. Behaviour changes come with a test that proves them.
- Its verify steps would really demonstrate the criteria in this environment. UI criteria say what to look at.
- Dependency order is right. `packages` lists what it will touch, so the coordinator can run tasks in parallel safely.
- Together, the tasks deliver the goal.

## Record

- `{{R}} run contract ok --run {{run}} --notes "<one line>"`
- or `{{R}} run contract revise --run {{run}} --notes "<concrete rewrites: for each task, the exact JSON patch for ratchet task edit, or new tasks as JSON>"`

Final message: `CONTRACT: OK` or `CONTRACT: REVISE`, then the rewrites.
