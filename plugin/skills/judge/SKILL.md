---
name: judge
description: Run a ratchet review by hand. Prints the same brief a T3 judge thread receives (task, contract or final mode), then follows it. Use for `/ratchet:judge T3 --run <run>`, `/ratchet:judge contract`, or `/ratchet:judge final`.
argument-hint: "<task-id | contract | final> [--run <run>]"
---

- For a task id: run `ratchet brief judge <task-id> [--run <run>]`.
- For `contract` or `final`: run `ratchet brief <mode> [--run <run>]`.

Follow the printed brief exactly. Never edit, commit or push. Record the verdict with the commands the brief gives.
