"""Run and task state: the plan, who is working on what, and the judge's verdicts.

State lives under the git common dir, shared by every worktree of the repo, so the
coordinator thread and the worker/judge threads in their own worktrees see one run.

Task lifecycle:
    todo -> doing (worker dispatched / started) -> review (submitted)
         -> accepted (judge) -> done (merged into the run branch)
    review -> doing on reject (a fresh worker gets the notes); -> blocked past the limits
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from .util import RatchetError, current_branch, head_sha, log_event, now, read_json, slug, state_dir, write_json

STATUSES = ("todo", "doing", "review", "accepted", "done", "blocked")
EDITABLE = ("title", "goal", "criteria", "verify", "packages", "deps")


def run_id(root: Path, explicit: Optional[str] = None) -> str:
    return slug(explicit or os.environ.get("RATCHET_RUN") or current_branch(root))


def run_dir(root: Path, rid: str) -> Path:
    return state_dir(root) / "runs" / rid


def load_run(root: Path, rid: str) -> Optional[dict]:
    return read_json(run_dir(root, rid) / "run.json", None)


def require_run(root: Path, rid: str) -> dict:
    run = load_run(root, rid)
    if run is None:
        raise RatchetError(f"no run '{rid}'. Start one with `ratchet run start --goal \"...\"`")
    return run


def save(root: Path, run: dict) -> None:
    run["updated"] = now()
    write_json(run_dir(root, run["id"]) / "run.json", run)


def start_run(root: Path, rid: str, goal: str) -> dict:
    existing = load_run(root, rid)
    run = existing or {
        "id": rid, "created": now(), "tasks": [], "base_sha": head_sha(root),
        "contract": None, "contract_thread": None, "final": None, "final_thread": None,
        "pr_url": None, "ci": {"status": None, "rounds": 0, "details": ""},
    }
    run.update({"goal": goal, "branch": current_branch(root), "status": "active"})
    save(root, run)
    progress(root, rid, f"run started: {goal}" if not existing else f"goal updated: {goal}")
    return run


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


def _new_task(tid: str, item: dict) -> dict:
    return {
        "id": tid, "title": item["title"], "goal": item.get("goal", ""),
        "criteria": _as_list(item.get("criteria")), "verify": _as_list(item.get("verify")),
        "packages": _as_list(item.get("packages")), "deps": _as_list(item.get("deps")),
        "status": "todo", "rounds": 0, "attempts": 0,
        "worker_thread": None, "judge_thread": None,
        "start_sha": None, "end_sha": None, "branch": None, "merged_sha": None,
        "summary": "", "notes": [], "evidence": [],
    }


def _check_deps(run: dict) -> None:
    known = {t["id"] for t in run["tasks"]}
    for t in run["tasks"]:
        bad = [d for d in t["deps"] if d not in known or d == t["id"]]
        if bad:
            raise RatchetError(f"task {t['id']} has invalid deps: {', '.join(bad)}")


def add_tasks(root: Path, rid: str, items: list[dict]) -> list[dict]:
    run = require_run(root, rid)
    added = []
    for item in items:
        if not item.get("title"):
            raise RatchetError("every task needs a title")
        if not _as_list(item.get("criteria")):
            raise RatchetError(f"task '{item['title']}' needs at least one acceptance criterion")
        tid = item.get("id") or f"T{len(run['tasks']) + 1}"
        if any(t["id"] == tid for t in run["tasks"]):
            raise RatchetError(f"task id {tid} already exists")
        task = _new_task(tid, item)
        run["tasks"].append(task)
        added.append(task)
    _check_deps(run)
    save(root, run)
    for t in added:
        progress(root, rid, f"planned {t['id']}: {t['title']}")
    return added


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
    _check_deps(run)
    save(root, run)
    progress(root, rid, f"{tid} edited: {', '.join(patch)}")
    return task


def get(run: dict, tid: str) -> dict:
    for t in run["tasks"]:
        if t["id"] == tid:
            return t
    raise RatchetError(f"no task {tid} in run {run['id']}")


def ready(run: dict) -> list[dict]:
    """Todo tasks whose dependencies are merged."""
    done = {t["id"] for t in run["tasks"] if t["status"] == "done"}
    return [t for t in run["tasks"] if t["status"] == "todo" and all(d in done for d in t["deps"])]


def _note(task: dict, by: str, text: str) -> None:
    if text:
        task["notes"].append({"at": now(), "by": by, "text": text})


def _limits(root: Path) -> dict:
    from .config import load

    return load(root)["limits"]


def transition(root: Path, rid: str, tid: str, action: str, *, text: str = "", by: str = "",
               thread: Optional[str] = None) -> dict:
    run = require_run(root, rid)
    task = get(run, tid)
    status = task["status"]
    blocked_reason = None

    def need(*allowed: str) -> None:
        if status not in allowed:
            raise RatchetError(f"{tid} is {status}; `{action}` needs {' or '.join(allowed)}")

    if action == "dispatch-worker":
        need("todo", "doing")
        if task["worker_thread"]:
            raise RatchetError(f"{tid} already has a worker thread ({task['worker_thread']})")
        task.update(status="doing", worker_thread=thread or "manual")
        task["attempts"] += 1
    elif action == "dispatch-judge":
        need("review")
        if task["judge_thread"]:
            raise RatchetError(f"{tid} already has a judge thread ({task['judge_thread']})")
        task["judge_thread"] = thread or "manual"
    elif action == "start":
        need("todo", "doing")
        task["status"] = "doing"
        task["start_sha"] = task["start_sha"] or head_sha(root)
        task["branch"] = current_branch(root)
    elif action == "submit":
        need("doing")
        task.update(status="review", end_sha=head_sha(root), branch=current_branch(root),
                    summary=text, worker_thread=None, judge_thread=None)
    elif action == "accept":
        need("review")
        task.update(status="accepted", judge_thread=None)
    elif action == "reject":
        need("review")
        task["rounds"] += 1
        task.update(status="doing", judge_thread=None, worker_thread=None)
        limit = int(_limits(root)["judge_rounds"])
        if task["rounds"] > limit:
            blocked_reason = f"rejected {task['rounds']} times (limit {limit}); last: {text[:300]}"
    elif action == "release":
        # The worker or judge thread ended without recording an outcome.
        if task["worker_thread"] and (not thread or task["worker_thread"] == thread):
            task["worker_thread"] = None
            limit = int(_limits(root).get("worker_attempts", 3))
            if task["attempts"] >= limit:
                blocked_reason = f"{task['attempts']} worker attempts ended without a submission; last: {text[:300]}"
        elif task["judge_thread"] and (not thread or task["judge_thread"] == thread):
            task["judge_thread"] = None
        else:
            return task  # nothing held by that thread: no-op
    elif action == "merged":
        need("accepted")
        task.update(status="done", merged_sha=head_sha(root))
    elif action == "conflict":
        need("accepted")
        task.update(status="doing", worker_thread=None)
    elif action == "block":
        task.update(status="blocked", worker_thread=None, judge_thread=None)
    elif action == "reset":
        task.update(status="todo", rounds=0, attempts=0, worker_thread=None, judge_thread=None)
    else:
        raise RatchetError(f"unknown action {action}")

    if blocked_reason:
        task.update(status="blocked", worker_thread=None, judge_thread=None)
    _note(task, by or action, text)
    save(root, run)
    first = text.splitlines()[0][:160] if text else ""
    progress(root, rid, f"{tid} {action} → {task['status']}{': ' + first if first else ''}")
    log_event(root, "task", run=rid, task=tid, action=action, status=task["status"], rounds=task["rounds"])
    if blocked_reason:
        from .blockers import add as add_blocker

        add_blocker(root, f"{tid} '{task['title']}' blocked: {blocked_reason}", kind="judge" if action == "reject" else "gate",
                    run=rid)
    return task


# ---------- run-level slots (contract review, final review, PR, CI) ----------

def set_run(root: Path, rid: str, **fields) -> dict:
    run = require_run(root, rid)
    run.update(fields)
    save(root, run)
    return run


def release_thread(root: Path, rid: str, thread: str, reason: str) -> str:
    """Free whatever slot a finished thread held. Returns what was released."""
    run = require_run(root, rid)
    for slot in ("contract_thread", "final_thread"):
        if run.get(slot) == thread:
            run[slot] = None
            save(root, run)
            progress(root, rid, f"{slot} released: {reason[:160]}")
            return slot
    for t in run["tasks"]:
        if thread in (t["worker_thread"], t["judge_thread"]):
            role = "worker" if t["worker_thread"] == thread else "judge"
            transition(root, rid, t["id"], "release", text=reason, by="coordinator", thread=thread)
            return f"{t['id']} {role}"
    return "nothing"


def summary(run: dict) -> str:
    counts: dict = {}
    for t in run["tasks"]:
        counts[t["status"]] = counts.get(t["status"], 0) + 1
    parts = [f"{counts[s]} {s}" for s in STATUSES if counts.get(s)]
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
    for key in ("branch", "start_sha", "end_sha", "merged_sha", "worker_thread", "judge_thread"):
        if task.get(key):
            lines.append(f"{key}: {task[key]}")
    if task.get("rounds"):
        lines.append(f"judge rejections: {task['rounds']}")
    if task.get("summary"):
        lines.append(f"worker summary: {task['summary']}")
    if task.get("evidence"):
        lines.append("evidence:")
        lines += [f"  - {e['path']}{' — ' + e['note'] if e.get('note') else ''}" for e in task["evidence"]]
    if task.get("notes"):
        lines.append("notes:")
        lines += [f"  - [{n['by']}] {n['text']}" for n in task["notes"][-6:]]
    return "\n".join(lines)
