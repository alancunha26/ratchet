"""Run and task state: the plan, who is doing what, and the judge's verdicts.

State lives under the git common dir (shared by every worktree of the repo), so a
coordinator and its workers in other worktrees all see the same run.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from .util import RatchetError, current_branch, head_sha, log_event, now, read_json, slug, state_dir, write_json

STATUSES = ("todo", "doing", "review", "done", "blocked")


def run_id(root: Path, explicit: Optional[str] = None) -> str:
    return slug(explicit or os.environ.get("RATCHET_RUN") or current_branch(root))


def run_dir(root: Path, rid: str) -> Path:
    return state_dir(root) / "runs" / rid


def load_run(root: Path, rid: str) -> Optional[dict]:
    return read_json(run_dir(root, rid) / "run.json", None)


def start_run(root: Path, rid: str, goal: str) -> dict:
    existing = load_run(root, rid)
    run = existing or {"id": rid, "created": now(), "tasks": []}
    run.update({"goal": goal, "branch": current_branch(root), "status": "active", "updated": now()})
    if not existing:
        run["base_sha"] = head_sha(root)
    _save(root, run)
    progress(root, rid, f"run started: {goal}" if not existing else f"goal updated: {goal}")
    return run


def require_run(root: Path, rid: str) -> dict:
    run = load_run(root, rid)
    if run is None:
        raise RatchetError(f"no run '{rid}'. Start one with `ratchet run start --goal \"...\"`")
    return run


def _save(root: Path, run: dict) -> None:
    run["updated"] = now()
    write_json(run_dir(root, run["id"]) / "run.json", run)


def progress(root: Path, rid: str, text: str) -> None:
    path = run_dir(root, rid) / "progress.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        fh.write(f"- {now()} {text.strip()}\n")


def read_progress(root: Path, rid: str, last: int = 0) -> str:
    path = run_dir(root, rid) / "progress.md"
    if not path.exists():
        return ""
    lines = path.read_text().splitlines()
    return "\n".join(lines[-last:] if last else lines)


def _as_list(val) -> list:
    if val is None:
        return []
    return list(val) if isinstance(val, (list, tuple)) else [val]


def add_tasks(root: Path, rid: str, items: list[dict]) -> list[dict]:
    run = require_run(root, rid)
    tasks = run["tasks"]
    added = []
    for item in items:
        if not item.get("title"):
            raise RatchetError("every task needs a title")
        criteria = _as_list(item.get("criteria"))
        if not criteria:
            raise RatchetError(f"task '{item['title']}' needs at least one acceptance criterion")
        tid = item.get("id") or f"T{len(tasks) + 1}"
        if any(t["id"] == tid for t in tasks):
            raise RatchetError(f"task id {tid} already exists")
        task = {
            "id": tid,
            "title": item["title"],
            "goal": item.get("goal", ""),
            "criteria": criteria,
            "verify": _as_list(item.get("verify")),
            "packages": _as_list(item.get("packages")),
            "deps": _as_list(item.get("deps")),
            "status": "todo",
            "rounds": 0,
            "notes": [],
        }
        tasks.append(task)
        added.append(task)
    known = {t["id"] for t in tasks}
    for t in tasks:
        missing = [d for d in t["deps"] if d not in known]
        if missing:
            raise RatchetError(f"task {t['id']} depends on unknown task(s) {', '.join(missing)}")
    _save(root, run)
    for t in added:
        progress(root, rid, f"planned {t['id']}: {t['title']}")
    return added


EDITABLE = ("title", "goal", "criteria", "verify", "packages", "deps")


def edit_task(root: Path, rid: str, tid: str, patch: dict) -> dict:
    run = require_run(root, rid)
    task = get(run, tid)
    if task["status"] not in ("todo", "blocked"):
        raise RatchetError(f"{tid} is {task['status']}; only todo or blocked tasks can be edited")
    unknown = [k for k in patch if k not in EDITABLE]
    if unknown:
        raise RatchetError(f"can't edit {', '.join(unknown)}; editable: {', '.join(EDITABLE)}")
    for key, val in patch.items():
        task[key] = val if key in ("title", "goal") else _as_list(val)
    if not task["criteria"]:
        raise RatchetError("a task needs at least one acceptance criterion")
    known = {t["id"] for t in run["tasks"]}
    if any(d not in known or d == tid for d in task["deps"]):
        raise RatchetError(f"invalid deps for {tid}: {task['deps']}")
    _save(root, run)
    progress(root, rid, f"{tid} edited: {', '.join(patch)}")
    return task


def get(run: dict, tid: str) -> dict:
    for t in run["tasks"]:
        if t["id"] == tid:
            return t
    raise RatchetError(f"no task {tid} in run {run['id']}")


def ready(run: dict) -> list[dict]:
    done = {t["id"] for t in run["tasks"] if t["status"] == "done"}
    return [t for t in run["tasks"] if t["status"] == "todo" and all(d in done for d in t["deps"])]


def _note(task: dict, by: str, text: str) -> None:
    if text:
        task["notes"].append({"at": now(), "by": by, "text": text})


def transition(root: Path, rid: str, tid: str, action: str, *, text: str = "", by: str = "") -> dict:
    run = require_run(root, rid)
    task = get(run, tid)
    status = task["status"]
    limit = None
    if action == "start":
        if status not in ("todo", "doing"):
            raise RatchetError(f"{tid} is {status}; only todo tasks can start")
        task["status"] = "doing"
        task["start_sha"] = head_sha(root)
    elif action == "submit":
        if status != "doing":
            raise RatchetError(f"{tid} is {status}; start it before submitting")
        task["status"] = "review"
        task["end_sha"] = head_sha(root)
        task["summary"] = text
    elif action == "accept":
        if status != "review":
            raise RatchetError(f"{tid} is {status}; only submitted tasks can be accepted")
        task["status"] = "done"
    elif action == "reject":
        if status != "review":
            raise RatchetError(f"{tid} is {status}; only submitted tasks can be rejected")
        task["rounds"] += 1
        from .config import load

        limit = int(load(root)["limits"]["judge_rounds"])
        task["status"] = "blocked" if task["rounds"] > limit else "doing"
    elif action == "block":
        task["status"] = "blocked"
    elif action == "reset":
        task["status"] = "todo"
        task["rounds"] = 0
    else:
        raise RatchetError(f"unknown action {action}")
    _note(task, by or action, text)
    _save(root, run)
    progress(root, rid, f"{tid} {action}{' → ' + task['status'] if action == 'reject' else ''}"
                        f"{': ' + text.splitlines()[0][:160] if text else ''}")
    log_event(root, "task", run=rid, task=tid, action=action, status=task["status"], rounds=task["rounds"])
    if action == "reject" and task["status"] == "blocked":
        from .blockers import add as add_blocker

        add_blocker(root, f"{tid} '{task['title']}' rejected {task['rounds']} times (limit {limit}); last: {text[:300]}",
                    kind="judge", run=rid)
    return task


def summary(run: dict) -> str:
    counts = {s: 0 for s in STATUSES}
    for t in run["tasks"]:
        counts[t["status"]] = counts.get(t["status"], 0) + 1
    parts = [f"{n} {s}" for s, n in counts.items() if n]
    return ", ".join(parts) or "no tasks yet"


def format_task(task: dict) -> str:
    lines = [f"{task['id']} [{task['status']}] {task['title']}"]
    if task.get("goal"):
        lines.append(f"goal: {task['goal']}")
    if task.get("packages"):
        lines.append(f"packages: {', '.join(task['packages'])}")
    if task.get("deps"):
        lines.append(f"deps: {', '.join(task['deps'])}")
    lines.append("acceptance criteria:")
    lines += [f"  {i}. {c}" for i, c in enumerate(task["criteria"], 1)]
    if task.get("verify"):
        lines.append("how to verify:")
        lines += [f"  - {v}" for v in task["verify"]]
    for key in ("start_sha", "end_sha"):
        if task.get(key):
            lines.append(f"{key}: {task[key]}")
    if task.get("summary"):
        lines.append(f"worker summary: {task['summary']}")
    if task.get("notes"):
        lines.append("notes:")
        lines += [f"  - [{n['by']}] {n['text']}" for n in task["notes"][-6:]]
    return "\n".join(lines)
