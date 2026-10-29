# Ratchet planning guide · {{repo}} · run {{run}}

**Goal:** {{goal}}

Products in this repo:
{{products}}

Add tasks with `{{R}} task add --run {{run}} --from-json -`, as a JSON array of objects with:
- `title`;
- `goal` (why);
- `packages` (repo paths it will touch);
- `deps` (task ids);
- `criteria`: observable, checkable statements;
- `verify`: the exact commands or interactions that demonstrate them.

## Rules

- **Size.** One task fits one worker session (about 400 changed lines or fewer) and leaves the repo green and mergeable.
- **Criteria.** State behaviour the product shows, in that product's terms. Include at least one negative or edge case when logic changes. A behaviour change adds a test that proves it.
- **Packages.** List them truthfully. They decide what can run in parallel and which product guide the judge gets.
- **Deps.** Set them only where order really matters.
- **Imported plans.** When the source already has tasks (spec-kit `tasks.md`, superpowers plans), map them 1:1 and sharpen the criteria.
- **Subjective goals** (looks like X, feels responsive): write a rubric or point to a reference. If the repo uses a judge panel (`agents.judge_panel`), the panel scores it.

## What good criteria look like for these products

{{criteria_hints}}
