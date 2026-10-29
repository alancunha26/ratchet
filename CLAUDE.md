# ratchet

A verification loop built on T3 Code (the agent IDE) and agnostic of the harness inside it (Claude Code, Codex, Cursor, OpenCode...). The core is the `ratchet` CLI plus its briefs and profiles. `plugin/` also ships as a Claude Code plugin, which is just one adapter. See README.md.

## Ground rules
- `plugin/lib/ratchet` is stdlib-only Python 3.9+. It must run on macOS and Linux. No third-party imports, no `match`, no PEP 604 unions at runtime (use `from __future__ import annotations`).
- Hooks (`plugin/lib/ratchet/hooks.py`) must never trap a session because of a ratchet bug. Errors are logged to `.git/ratchet/errors.log` and the session is let through. Outside repos with `.ratchet/config.json`, hooks exit immediately.
- Protocols live in `plugin/templates/brief-*.md` and product knowledge in `plugin/profiles/*.md`. Any harness reads them, and agents follow them literally. Skills are one-line wrappers. When you change CLI behaviour, update every brief that mentions it (`grep -rn "{{R}} <cmd>" plugin/templates`).
- Never depend on a single harness's features in the core. Claude-only things (hooks, skills) are optional adapters, and the hard gates live in the CLI.
- Runtime state belongs under the git common dir (`util.state_dir`), never in the working tree.

## Verify
- `python3 -m unittest discover -s tests`: end-to-end tests that build throwaway monorepos and drive the real CLI and hooks. Add a test for every behaviour change.
- `claude plugin validate ./plugin` after manifest, skill, agent or hook changes.
- This repo runs ratchet on itself (`.ratchet/config.json`). With the plugin enabled, the Stop gate runs both checks.

<!-- ratchet:start -->
## Ratchet (agent verification loop)

Work here is done when it is verified, not when it looks done.

- `ratchet verify` runs the checks for the packages you changed (cached; the Stop gate runs it too). `ratchet verify --full` before handing off.
- Never weaken a check to pass: no skipped or deleted tests, no lint/type suppressions, no edits to `.ratchet/config.json`, CI or snapshots unless that is the task. `ratchet allow <id> --reason "..."` only when genuinely required.
- Log friction (slow checks, flaky tests, missing tools, unclear specs): `ratchet blocker add "..."`.
- Multi-step work: `/ratchet:run <goal>`. Status: `ratchet status`.

Project rules learned by agents (keep each one line, concrete, and checkable):
- When a git snapshot copies the index, use `shutil.copy2` so the mtime is kept. Otherwise git's racy-clean detection misses same-size edits made in the same second (`gitstate.tree_id`).
<!-- ratchet:end -->
