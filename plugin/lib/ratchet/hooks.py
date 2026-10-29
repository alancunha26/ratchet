"""Claude Code adapter: hooks that give Claude earlier feedback. Optional; the hard gates live in the CLI.

They must never trap a session because of a ratchet bug: any unexpected error is logged and
the session is let through."""
from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path
from typing import Optional

from . import blockers, gitstate, harness, state as tasks, verify
from .util import find_root, git_ok, is_enabled, log_event, now, read_json, slug, state_dir, write_json


def _debug(*parts) -> None:
    if os.environ.get("RATCHET_DEBUG"):
        print("[ratchet debug]", *parts, file=sys.stderr)


def _emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj))


def _root_for(payload: dict) -> Optional[Path]:
    if os.environ.get("RATCHET_GATE", "").lower() in ("0", "off", "false"):
        return None
    cwd = payload.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    root = find_root(Path(cwd))
    return root if root and is_enabled(root) else None


def _session_file(root: Path, key: str) -> Path:
    return state_dir(root) / "sessions" / f"{slug(key)}.json"


# ---------- SessionStart ----------

CONTEXT = """[ratchet] This repo has a ratchet harness (.ratchet/): a verifiable development cycle.
- Finishing a turn runs a gate: `ratchet verify` compares your change against the base per test (nothing that passed may break, new tests must pass, empty runs don't count) plus a tamper scan. Run it yourself as you work; it's cached.
- A test only proves a change if it fails without it: `ratchet proof f2p`.
- Never weaken checks: no skipped/deleted tests, no suppressions, no edits to .ratchet/, t3.json or CI outside a harness task. If one is genuinely required: `ratchet allow <id> --reason "..."` (the judge reviews it).
- Log friction: `ratchet blocker add "..." --kind slow|flaky|tooling|env|spec`.
- Multi-step work: `ratchet start "<goal>"` (a T3 coordinator thread) or `ratchet brief coordinator --goal "..."`. How each product is exercised: .ratchet/exercise/."""


def session_start(payload: dict) -> None:
    root = _root_for(payload)
    if not root:
        return
    try:
        write_json(_session_file(root, payload.get("session_id") or "unknown"),
                   {"start_tree": gitstate.tree_id(root), "attempts": 0, "at": now()})
    except Exception:  # snapshotting is best effort
        pass
    lines = [CONTEXT]
    rid = tasks.run_id(root)
    run = tasks.load_run(root, rid)
    if harness.load(root).get("_needs_upgrade"):
        lines.append("- The harness targets another kernel version: run `ratchet upgrade`.")
    if run and run.get("status") == "active":
        lines.append(f"- Active run `{rid}`: {run.get('goal', '')} — {tasks.summary(run)}.")
        doing = [t for t in run["tasks"] if t["status"] in ("doing", "review")]
        nxt = doing[0] if doing else (tasks.ready(run) or [None])[0]
        if nxt:
            lines.append(f"  Current: {nxt['id']} [{nxt['status']}] {nxt['title']} (`ratchet task show {nxt['id']}`)")
    open_blockers = [b for b in blockers.load(root) if b["status"] == "open"]
    if open_blockers:
        lines.append(f"- {len(open_blockers)} open blockers (`ratchet blocker list`).")
    _emit({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "\n".join(lines)}})


# ---------- Stop / SubagentStop gate ----------

def _gate(payload: dict, key: str, enforce_if_unchanged: bool) -> None:
    root = _root_for(payload)
    if not root:
        return
    cfg = harness.load(root)
    if git_ok(["symbolic-ref", "-q", "HEAD"], root) is None:
        _debug("detached HEAD: read-only review checkout, gate skipped")
        return  # judges review detached checkouts; they must be able to finish on red work
    sess_path = _session_file(root, key)
    sess = read_json(sess_path, {}) or {}
    tree = gitstate.tree_id(root)
    since = _task_base(root) or verify.default_base_tree(root, cfg)
    _debug("gate", key, "tree", tree[:12], "since", since[:12], "session", sess)
    if not gitstate.changes(root, since, tree):
        _debug("no changes vs base")
        return  # nothing differs from the base branch
    if not enforce_if_unchanged and sess.get("start_tree") == tree:
        _debug("session changed nothing")
        return  # this session changed nothing; don't hold a read-only session hostage
    limit = int(cfg["limits"]["gate_attempts"])
    rep = verify.verify(root, cfg, level="fast", base_tree=since)  # results are cached per tree
    sess.update({"last_tree": tree, "last_ok": rep.ok})

    if rep.ok:
        sess["attempts"] = 0
        write_json(sess_path, sess)
        cached = all(c.cached for c in rep.checks)
        log_event(root, "gate", ok=True, key=key, cached=cached, seconds=rep.seconds)
        if not cached and rep.checks:
            tests = sum(c.total for c in rep.checks)
            _emit({"systemMessage": f"ratchet gate ✓ {len(rep.checks)} checks, {tests} tests ({rep.seconds}s)"})
        return

    sess["attempts"] = int(sess.get("attempts", 0)) + 1
    write_json(sess_path, sess)
    failed = [c.name for c in rep.checks if not c.ok] + [f"tamper:{t['kind']}" for t in rep.tamper]
    log_event(root, "gate", ok=False, key=key, attempt=sess["attempts"], failed=failed)
    if sess["attempts"] > limit:
        blockers.add(root, f"gate still failing after {limit} attempts: {', '.join(failed)[:400]}", kind="gate",
                     run=tasks.run_id(root))
        sess["attempts"] = 0
        write_json(sess_path, sess)
        _emit({"systemMessage": f"ratchet gate: still failing after {limit} attempts ({', '.join(failed)[:200]}). "
                                "Letting the agent stop; a blocker was logged."})
        return
    reason = (
        f"{verify.format_verdict(rep)}\n\n"
        f"ratchet gate (attempt {sess['attempts']}/{limit}): the checks above must pass before you finish. "
        "Fix the root cause in the code, run `ratchet verify`, then finish. Do not weaken or skip checks. "
        "If you are truly blocked (broken environment, unclear requirement), log it with "
        "`ratchet blocker add \"...\"` and explain in your final message."
    )
    _emit({"decision": "block", "reason": reason})


def _task_base(root: Path) -> Optional[str]:
    """The baseline for the task being worked on in this checkout, if any."""
    try:
        run = tasks.load_run(root, tasks.run_id(root))
    except Exception:
        return None
    for t in (run or {}).get("tasks", []):
        if t.get("status") == "doing" and t.get("start_tree"):
            return t["start_tree"]
    return None


def stop(payload: dict) -> None:
    _gate(payload, payload.get("session_id") or "main", enforce_if_unchanged=False)


def subagent_stop(payload: dict) -> None:
    agent = (payload.get("agent_type") or "").lower()
    if agent.split(":")[-1] != "worker":
        return
    _gate(payload, f"agent-{payload.get('agent_id') or 'worker'}", enforce_if_unchanged=True)


# ---------- PreToolUse: keep agents out of ratchet's own state ----------

def pre_edit(payload: dict) -> None:
    tool_input = payload.get("tool_input") or {}
    target = (tool_input.get("file_path") or tool_input.get("notebook_path") or "").replace("\\", "/")
    if "/.git/" not in target or "ratchet" not in target:
        return
    root = _root_for(payload)
    if not root:
        return
    state = str(state_dir(root).resolve())
    resolved = str(Path(target).resolve())
    inside = resolved == state or resolved.startswith(state + os.sep)
    if inside:
        _emit({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": "ratchet state is managed by the `ratchet` CLI (ratchet task/run/blocker). "
                                        "Edit it through the CLI, not directly.",
        }})


HANDLERS = {"session-start": session_start, "stop": stop, "subagent-stop": subagent_stop, "pre-edit": pre_edit}


def main(event: str) -> int:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        payload = {}
    handler = HANDLERS.get(event)
    if handler is None:
        print(f"unknown hook event {event}", file=sys.stderr)
        return 0
    try:
        handler(payload)
    except Exception as exc:  # never block a session on our own bug
        try:
            root = find_root(Path(payload.get("cwd") or os.getcwd()))
            if root:
                with (state_dir(root) / "errors.log").open("a") as fh:
                    fh.write(f"{now()} {event}\n{traceback.format_exc()}\n")
        except Exception:
            pass
        if event in ("stop", "subagent-stop"):
            _emit({"systemMessage": f"ratchet gate error ({exc.__class__.__name__}: {exc}); gate skipped."})
    return 0
