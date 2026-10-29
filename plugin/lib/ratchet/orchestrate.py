"""The coordinator's state machine. `ratchet tick` says exactly what to do next.

The coordinator is a T3 thread. On every wake (start, a child finished, CI changed) it
calls tick, carries out the actions with T3 tools, records them with the CLI, and ends
its turn. Keeping the decisions here, in code, is what stops a long run from drifting.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional

from . import checks, config, gitstate, tasks
from .util import RatchetError, git, git_ok, head_sha

BIN = Path(__file__).resolve().parents[2] / "bin" / "ratchet"
TEMPLATES = Path(__file__).resolve().parents[2] / "templates"


def ratchet_cmd() -> str:
    return f'"{BIN}"'


def _t3(cfg: dict) -> dict:
    return cfg.get("t3") or {}


def _act(do: str, why: str, **fields) -> dict:
    return {"do": do, "why": why, **{k: v for k, v in fields.items() if v is not None}}


def _dispatch(kind: str, cfg: dict, rid: str, task: Optional[dict] = None, mode: Optional[str] = None) -> dict:
    """A ready-to-run delegate_task description for a worker or judge."""
    R = ratchet_cmd()
    t3 = _t3(cfg)
    if kind == "worker":
        role_cfg = t3.get("worker") or {}
        rnd = f" (round {task['rounds'] + 1})" if task["rounds"] else ""
        return _act(
            "dispatch_worker", f"{task['id']} is ready{rnd}", task=task["id"],
            title=f"ratchet {task['id']}: {task['title']}"[:120], role="implementation",
            isolation=role_cfg.get("isolation", "worktree"), model=role_cfg.get("model"),
            provider=role_cfg.get("provider"), brief=f"{R} brief worker {task['id']} --run {rid}",
            record=f"{R} task dispatch {task['id']} --role worker --run {rid} --thread <childThreadId>",
        )
    role_cfg = t3.get("judge") or {}
    model = role_cfg.get("model") or (cfg.get("judge") or {}).get("model")
    common = dict(role="review", isolation="worktree", model=None if model == "inherit" else model,
                  provider=role_cfg.get("provider"))
    if mode == "task":
        return _act("dispatch_judge", f"{task['id']} was submitted", task=task["id"], mode="task",
                    title=f"ratchet judge {task['id']}: {task['title']}"[:120],
                    brief=f"{R} brief judge {task['id']} --run {rid}",
                    record=f"{R} task dispatch {task['id']} --role judge --run {rid} --thread <childThreadId>", **common)
    return _act("dispatch_judge", f"{mode} review is due", mode=mode, title=f"ratchet {mode} review: {rid}"[:120],
                brief=f"{R} brief {mode} --run {rid}",
                record=f"{R} run dispatch {mode} --run {rid} --thread <childThreadId>", **common)


def _overlaps(task: dict, busy: list) -> bool:
    if not busy:
        return False
    if not task["packages"] or any(not b for b in busy):
        return True  # undeclared scope conflicts with everything
    mine = set(task["packages"])
    return any(mine & set(b) for b in busy)


def tick(root: Path, cfg: dict, run: dict) -> list[dict]:
    R = ratchet_cmd()
    rid = run["id"]
    t3 = _t3(cfg)
    if run.get("status") in ("done", "abandoned"):
        return [_act("done", f"run is {run['status']}")]
    all_tasks = run["tasks"]
    if not all_tasks:
        return [_act("plan", "no tasks yet: plan the goal into tasks with `ratchet task add --from-json -`")]

    # 1. Contract review, once, before any work.
    if t3.get("contract_review", True) and run.get("contract") != "ok":
        if run.get("contract_thread"):
            return [_act("wait", "contract review is running")]
        if run.get("contract") == "revise":
            return [_act("revise_plan", "apply the judge's contract rewrites with `ratchet task edit`/`task add`, "
                         f"then `{R} run contract ok --run {rid} --notes \"applied\"`",
                         notes=run.get("contract_notes", ""))]
        return [_dispatch("judge", cfg, rid, mode="contract")]

    actions: list[dict] = []
    # 2. Merge accepted work first, so dependents start from it.
    for t in all_tasks:
        if t["status"] == "accepted":
            actions.append(_act("merge", f"{t['id']} was accepted", task=t["id"],
                                command=f"{R} task merge {t['id']} --run {rid}"))
    # 3. Judge anything submitted.
    for t in all_tasks:
        if t["status"] == "review" and not t["judge_thread"]:
            actions.append(_dispatch("judge", cfg, rid, t, mode="task"))
    # 4. Workers, up to max_parallel, never two on overlapping packages.
    running = [t for t in all_tasks if t["status"] == "doing" and t["worker_thread"]]
    busy = [t["packages"] for t in running]
    capacity = int(t3.get("max_parallel", 1)) - len(running)
    rework = [t for t in all_tasks if t["status"] == "doing" and not t["worker_thread"]]
    for t in rework + tasks.ready(run):
        if capacity <= 0:
            break
        if _overlaps(t, busy):
            continue
        actions.append(_dispatch("worker", cfg, rid, t))
        busy.append(t["packages"])
        capacity -= 1

    in_flight = any(t["worker_thread"] or t["judge_thread"] for t in all_tasks)
    if actions:
        if in_flight:
            actions.append(_act("wait", "other threads are still running; end the turn after dispatching"))
        return actions
    if in_flight:
        return [_act("wait", "workers or judges are running; end the turn, their completion wakes you")]

    unfinished = [t for t in all_tasks if t["status"] != "done"]
    if unfinished:
        blocked = [t["id"] for t in unfinished if t["status"] == "blocked"]
        return [_act("escalate", "no task can move: " + (f"blocked {', '.join(blocked)}" if blocked else
                                                          "dependencies are blocked"),
                     tasks=[t["id"] for t in unfinished],
                     hint="re-plan (split/edit + `task reset`), or ask the human; then tick again")]

    # 5. Final review of the whole branch.
    if run.get("final") != "accepted":
        if run.get("final_thread"):
            return [_act("wait", "final review is running")]
        if run.get("final") == "rejected":
            return [_act("add_missing_tasks", "the final judge found gaps: add them as tasks, then "
                         f"`{R} run final reset --run {rid}`", notes=run.get("final_notes", ""))]
        return [_dispatch("judge", cfg, rid, mode="final")]

    # 6. Deliver.
    pr_mode = (cfg.get("run") or {}).get("pr", "draft")
    if pr_mode == "none":
        return [_act("report", "done: branch is green and accepted (run.pr is none)",
                     command=f"{R} report --html --run {rid}")]
    if not run.get("pr_url"):
        return [_act("open_pr", "final review accepted", draft=pr_mode == "draft",
                     command=f"git push -u origin HEAD && gh pr create {'--draft ' if pr_mode == 'draft' else ''}"
                             f"--title ... --body-file <({R} report --run {rid})",
                     record=f"{R} run pr <url> --run {rid}")]
    if run.get("pushed_sha") != head_sha(root):
        return [_act("push", "the run branch has commits the PR doesn't", command="git push",
                     record=f"{R} run pushed --run {rid}")]
    ci = run.get("ci") or {}
    if ci.get("status") == "fixing":
        # Fix round opened but nothing new merged yet (pushes are caught above).
        return [_act("fix_ci", "a CI fix round is open but no fix is merged: add one task per failing job",
                     details=ci.get("details", ""))]
    if ci.get("status") in (None, "pending"):
        return [_act("watch_ci", "wait for CI on the PR: link_pull_request + watch_pull_request, end the turn; "
                     f"when woken, record `{R} run ci green|red --details \"...\" --run {rid}`", pr=run["pr_url"])]
    if ci["status"] == "red":
        limit = int(cfg["limits"]["ci_rounds"])
        if ci.get("rounds", 0) >= limit:
            return [_act("escalate", f"CI still red after {limit} fix rounds", details=ci.get("details", ""))]
        return [_act("fix_ci", "CI is red: add one task per failing job (criteria: that job passes), "
                     f"then `{R} run ci fixing --run {rid}`", details=ci.get("details", ""))]
    return [_act("report", "CI is green: render the report, unwatch the PR, settle the thread",
                 command=f"{R} report --html --run {rid}", close=f"{R} run close --run {rid}")]


# ---------- merge with an integration gate ----------

def merge(root: Path, cfg: dict, rid: str, tid: str) -> tuple[bool, str]:
    run = tasks.require_run(root, rid)
    task = tasks.get(run, tid)
    if task["status"] != "accepted":
        raise RatchetError(f"{tid} is {task['status']}; only accepted tasks merge")
    if git(["status", "--porcelain"], root).strip():
        raise RatchetError("the run checkout has uncommitted changes; commit or stash them before merging")
    before = head_sha(root)
    msg = f"ratchet: merge {tid} {task['title']}"
    proc = subprocess.run(["git", "merge", "--no-ff", "--no-edit", "-m", msg, task["end_sha"]], cwd=str(root),
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if proc.returncode != 0:
        files = git_ok(["diff", "--name-only", "--diff-filter=U"], root) or ""
        subprocess.run(["git", "merge", "--abort"], cwd=str(root), stderr=subprocess.DEVNULL)
        note = (f"merge conflict with the run branch at {before[:12]} in: {files.replace(chr(10), ', ') or '?'}. "
                "Rework: merge the run branch into your work and resolve.")
        tasks.transition(root, rid, tid, "conflict", text=note, by="merge")
        return False, note
    rep = checks.verify(root, cfg, level="fast")
    if not rep.ok:
        subprocess.run(["git", "reset", "--hard", before], cwd=str(root), stdout=subprocess.DEVNULL)
        note = "integration failed after merging into the run branch:\n" + checks.format_report(rep)
        tasks.transition(root, rid, tid, "conflict", text=note[:3000], by="merge")
        return False, note
    tasks.transition(root, rid, tid, "merged", text=f"merged at {head_sha(root)[:12]}", by="merge")
    return True, f"{tid} merged into {run['branch']} ({head_sha(root)[:12]}), checks green"


# ---------- briefs: self-contained prompts for worker and judge threads ----------

def _fill(template: str, values: dict) -> str:
    out = template
    for key, val in values.items():
        out = out.replace("{{" + key + "}}", str(val))
    return out


def _ui_touched(root: Path, cfg: dict, task: dict) -> bool:
    globs = (cfg.get("app") or {}).get("ui_paths") or []
    if not globs or not task.get("end_sha"):
        return False
    base = git_ok(["merge-base", task["end_sha"], "HEAD"], root) or task.get("start_sha") or ""
    files = (git_ok(["diff", "--name-only", base, task["end_sha"]], root) or "").splitlines()
    from .tamper import _glob_match

    return any(_glob_match(f, g) for f in files for g in globs)


def _app_section(cfg: dict) -> str:
    app = cfg.get("app") or {}
    if not app.get("dev"):
        return ("No `app` section is configured in .ratchet/config.json, so there's no standard way to start "
                "the product. Use the task's verify steps and the repo's README.")
    lines = [f"- start: `{app['port_env']}=$PORT {app['dev']}` (pick PORT with `{ratchet_cmd()} port`)"]
    lines.append(f"- url: {app.get('url', 'http://localhost:{port}').replace('{port}', '$PORT')}")
    if app.get("ready_text"):
        lines.append(f"- ready when the log prints: {app['ready_text']}")
    if app.get("profile"):
        lines.append(f"- browser profile for logged-in flows: `{app['profile']}` (pass as profileId to preview_open)")
    return "\n".join(lines)


def brief(root: Path, cfg: dict, rid: str, kind: str, tid: Optional[str] = None) -> str:
    run = tasks.require_run(root, rid)
    task = tasks.get(run, tid) if tid else None
    calib = root / ".ratchet" / "judge.md"
    values = {
        "R": ratchet_cmd(), "run": rid, "goal": run["goal"], "run_branch": run["branch"],
        "repo": root.name, "calibration": calib.read_text().strip() if calib.exists() else "(none)",
        "app": _app_section(cfg), "base": gitstate.merge_base(root, cfg),
        "progress": tasks.read_progress(root, rid, last=10),
    }
    if task:
        notes = [n for n in task["notes"] if n["by"] in ("judge", "merge", "coordinator")]
        values.update({
            "id": task["id"], "contract": tasks.format_task(task), "round": task["rounds"] + 1,
            "end_sha": task.get("end_sha") or "", "start_sha": task.get("start_sha") or "",
            "ui": "yes" if _ui_touched(root, cfg, task) else "not detected",
            "rework": ("\n".join(f"- [{n['by']}] {n['text']}" for n in notes[-3:]) if notes else ""),
        })
        if kind == "worker":
            prev = task.get("end_sha")
            values["resume"] = (f"Continue from the previous round: `git merge --no-edit {prev}` and resolve any conflicts."
                                if prev else "This is the first round: start from the checkout you are in.")
            values["rework"] = values["rework"] or "(first round, no notes)"
    else:
        values["tasks"] = "\n\n".join(tasks.format_task(t) for t in run["tasks"]) or "(no tasks)"
    name = {"worker": "brief-worker.md", "judge": "brief-judge.md", "contract": "brief-contract.md",
            "final": "brief-final.md"}[kind]
    return _fill((TEMPLATES / name).read_text(), values)
