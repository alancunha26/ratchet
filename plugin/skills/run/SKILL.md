---
name: run
description: Make this thread the ratchet coordinator for a goal. It plans verifiable tasks, runs each worker and judge as its own T3 Code thread (any harness), merges only accepted and green work, then opens a PR and sees CI through. Use for any multi-step feature, fix or migration ("/ratchet:run add SSO", an issue URL, a spec path, or "continue").
argument-hint: "<goal | issue URL | spec path | continue>"
---

Run `ratchet brief coordinator --goal "$ARGUMENTS"` and follow the printed brief exactly.
