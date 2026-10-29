# Ratchet planning guide · {{repo}} · run {{run}}

**Goal:** {{goal}}

Products in this repo, and where each one's exercise guide lives:
{{products}}

Add tasks with `{{R}} task add --run {{run}} --from-json -`. The input is a JSON array of objects with:
- `title`
- `kind`: `feature`, `fix`, `refactor`, `docs` or `harness`
- `goal`: why the task exists
- `packages`: the repo paths it will touch
- `deps`: task ids it waits for
- `criteria`: observable, checkable statements
- `verify`: the exact commands or interactions that demonstrate the criteria
- `expects_test`: defaults to true for `feature` and `fix`

## Rules

- **Size.** A task fits one worker session (about 400 changed lines at most) and leaves the repo green and mergeable.
- **Criteria.** Write each criterion as behaviour the product shows, in that product's own terms. Read its exercise guide for what counts as evidence.
- **Tests.** A feature or fix brings at least one new test that fails without it. The kernel checks this.
- **Edge cases.** Include at least one negative or edge case when logic changes.
- **Harness gaps come first.** If the run needs evidence the harness can't produce yet (no check for a package, no exercise for a product, no visual baseline), plan a `harness` task for it before the feature tasks that depend on it.
- **`packages`.** Be truthful. This field decides which tasks can run in parallel and which exercise guides the judge receives.
- **`deps`.** Set them only where order really matters.
- **Imported plans.** When the source already has tasks (a spec-kit `tasks.md`, a superpowers plan), map them 1:1 and sharpen their criteria.
- **Subjective goals** ("looks like X", "feels responsive"): write a rubric or point to a reference. If `agents.judge_panel` is above 1, a panel scores it.
