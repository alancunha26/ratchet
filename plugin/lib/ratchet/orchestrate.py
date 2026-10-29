"""The coordinator's state machine and the briefs every T3 thread receives.

ratchet is built on T3 Code (the agent IDE) and is agnostic of the harness inside it
(Claude Code, Codex, Cursor, OpenCode, Pi, ACP agents...). Every role is a T3 thread:
the coordinator runs `ratchet tick`, carries out the actions with T3's tools
(delegate_task, watch_pull_request, html_render, ...), records them, and ends its turn.
Children finishing wake it. Keeping the decisions here, in code, is what stops long runs
from drifting, whichever model is driving.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from . import checks, gitstate, profiles, tasks
from .util import RatchetError, git, git_ok, head_sha, slug

BIN = Path(__file__).resolve().parents[2] / "bin" / "ratchet"
TEMPLATES = Path(__file__).resolve().parents[2] / "templates"


def ratchet_cmd() -> str:
    return f'"{BIN}"'


def _agents(cfg: dict) -> dict:
    return cfg.get("agents") or {}


def shared_checkout(cfg: dict) -> bool:
    """One task at a time in the run's own checkout (one dependency install per run)."""
    return int(_agents(cfg).get("max_parallel", 1)) <= 1


def _act(do: str, why: str, **fields) -> dict:
    return {"do": do, "why": why, **{k: v for k, v in fields.items() if v not in (None, {}, "")}}


def _target(role_cfg: dict) -> dict:
    target = {}
    if role_cfg.get("provider"):
        target["providerInstanceId"] = role_cfg["provider"]
    if role_cfg.get("model"):
        target["model"] = role_cfg["model"]
    if role_cfg.get("options"):
        target["options"] = role_cfg["options"]
    return target


def _delegate(kind: str, cfg: dict, rid: str, task: Optional[dict] = None, mode: Optional[str] = None) -> dict:
    """A ready-to-send delegate_task call for a worker or a judge."""
    R = ratchet_cmd()
    agents = _agents(cfg)
    isolation = "shared" if shared_checkout(cfg) else "worktree"
    if kind == "worker":
        rnd = task["rounds"] + 1
        n = task["attempts"] + 1
        return _act(
            "dispatch_worker", f"{task['id']} is ready" + (f" (round {rnd})" if rnd > 1 else ""), task=task["id"],
            brief=f"{R} brief worker {task['id']} --run {rid}",
            delegate={"title": f"ratchet {task['id']}: {task['title']}"[:120], "role": "implementation",
                      "isolation": isolation, "mode": "async", "target": _target(agents.get("worker") or {})},
            record=f"{R} task dispatch {task['id']} --role worker --run {rid} --thread <childThreadId>",
            precondition="the checkout must be clean: if `git status --porcelain` shows leftovers, "
                         f"`git stash push -u -m ratchet-{task['id']}-leftover` first" if isolation == "shared" else None,
            attempt=n,
        )
    delegate = {"role": "review", "isolation": isolation, "mode": "async", "target": _target(agents.get("judge") or {})}
    if mode == "task":
        open_seats = tasks.panel_size_cfg(cfg) - len(task["judge_threads"]) - len(task["votes"])
        return _act("dispatch_judge", f"{task['id']} was submitted", task=task["id"], mode="task", count=open_seats,
                    brief=f"{R} brief judge {task['id']} --run {rid}",
                    delegate=dict(delegate, title=f"ratchet judge {task['id']}: {task['title']}"[:120]),
                    record=f"{R} task dispatch {task['id']} --role judge --run {rid} --thread <childThreadId>")
    return _act("dispatch_judge", f"{mode} review is due", mode=mode, brief=f"{R} brief {mode} --run {rid}",
                delegate=dict(delegate, title=f"ratchet {mode} review: {rid}"[:120]),
                record=f"{R} run dispatch {mode} --run {rid} --thread <childThreadId>")


def _overlaps(task: dict, busy: list) -> bool:
    if not busy:
        return False
    if not task["packages"] or any(not b for b in busy):
        return True  # undeclared scope conflicts with everything
    return any(set(task["packages"]) & set(b) for b in busy)


def _has_commits_since(root: Path, sha: Optional[str]) -> bool:
    head = head_sha(root)
    return bool(sha and head and head != sha and
                subprocess.run(["git", "merge-base", "--is-ancestor", sha, head], cwd=str(root)).returncode == 0)


def tick(root: Path, cfg: dict, run: dict) -> list[dict]:
    R = ratchet_cmd()
    rid = run["id"]
    agents = _agents(cfg)
    shared = shared_checkout(cfg)
    if run.get("status") in ("done", "abandoned"):
        return [_act("done", f"run is {run['status']}")]
    all_tasks = run["tasks"]
    if not all_tasks:
        return [_act("plan", "no tasks yet: plan the goal with `ratchet task add --from-json -`",
                     guide=f"{R} brief plan --run {rid}")]

    # 1. Contract review, once, before any work.
    if agents.get("contract_review", True) and run.get("contract") != "ok":
        if run.get("contract_thread"):
            return [_act("wait", "contract review is running")]
        if run.get("contract") == "revise":
            return [_act("revise_plan", "apply the judge's rewrites (`ratchet task edit` / `task add`), then "
                         f"`{R} run contract ok --run {rid} --notes \"applied\"`", notes=run.get("contract_notes"))]
        return [_delegate("judge", cfg, rid, mode="contract")]

    actions: list[dict] = []
    # 2. In the shared checkout, drop a blocked task's commits before anything else builds on them.
    if shared:
        for t in all_tasks:
            if t["status"] == "blocked" and not t["rolled_back"] and _has_commits_since(root, t["start_sha"]):
                return [_act("rollback", f"{t['id']} is blocked and its commits are on the run branch", task=t["id"],
                             command=f"{R} task rollback {t['id']} --run {rid}")]
    # 3. Merge accepted work, so dependents start from it.
    for t in all_tasks:
        if t["status"] == "accepted":
            actions.append(_act("merge", f"{t['id']} was accepted", task=t["id"],
                                command=f"{R} task merge {t['id']} --run {rid}"))
    # 4. Judge anything submitted (a panel when judge_panel > 1).
    for t in all_tasks:
        if t["status"] == "review" and len(t["judge_threads"]) + len(t["votes"]) < tasks.panel_size_cfg(cfg):
            actions.append(_delegate("judge", cfg, rid, t, mode="task"))
    # 5. Workers. Shared checkout: strictly one task in flight. Worktrees: up to max_parallel, disjoint packages.
    in_progress = [t for t in all_tasks if t["status"] in ("doing", "review", "accepted") and
                   (t["worker_thread"] or t["status"] != "doing")]
    running = [t for t in all_tasks if t["status"] == "doing" and t["worker_thread"]]
    if shared:
        capacity = 0 if in_progress or actions else 1
    else:
        capacity = int(agents.get("max_parallel", 1)) - len(running)
    busy = [t["packages"] for t in running]
    rework = [t for t in all_tasks if t["status"] == "doing" and not t["worker_thread"]]
    for t in rework + tasks.ready(run):
        if capacity <= 0:
            break
        if not shared and _overlaps(t, busy):
            continue
        actions.append(_delegate("worker", cfg, rid, t))
        busy.append(t["packages"])
        capacity -= 1
    # In the shared checkout a rework is the in-flight task itself: let it through.
    if shared and not actions and rework and not running and not any(t["judge_threads"] for t in all_tasks):
        actions.append(_delegate("worker", cfg, rid, rework[0]))

    in_flight = any(t["worker_thread"] or t["judge_threads"] for t in all_tasks)
    if actions:
        if in_flight:
            actions.append(_act("wait", "other threads are still running: end the turn after dispatching"))
        return actions
    if in_flight:
        return [_act("wait", "workers or judges are running: end the turn; their completion wakes you")]

    unfinished = [t for t in all_tasks if t["status"] != "done"]
    if unfinished:
        blocked = [t["id"] for t in unfinished if t["status"] == "blocked"]
        return [_act("escalate", "no task can move: " + (f"blocked {', '.join(blocked)}" if blocked else
                                                          "dependencies are blocked"),
                     tasks=[t["id"] for t in unfinished],
                     hint="re-plan (split or edit, then `ratchet task reset <id>`), or ask the human; then tick again")]

    # 6. Final review of the whole branch.
    if run.get("final") != "accepted":
        if run.get("final_thread"):
            return [_act("wait", "final review is running")]
        if run.get("final") == "rejected":
            return [_act("add_missing_tasks", f"the final judge found gaps: add them as tasks, then "
                         f"`{R} run final reset --run {rid}`", notes=run.get("final_notes"))]
        return [_delegate("judge", cfg, rid, mode="final")]

    # 7. Deliver: PR, CI through watch_pull_request, report.
    pr_mode = (cfg.get("run") or {}).get("pr", "draft")
    if pr_mode == "none":
        return [_act("report", "done: the branch is green and accepted (run.pr is none)",
                     command=f"{R} report --html --run {rid}", close=f"{R} run close --run {rid}")]
    if not run.get("pr_url"):
        return [_act("open_pr", "final review accepted", draft=pr_mode == "draft",
                     body=f"{R} report --run {rid}", record=f"{R} run pr <url> --run {rid}")]
    if run.get("pushed_sha") != head_sha(root):
        return [_act("push", "the run branch has commits the PR doesn't", command="git push",
                     record=f"{R} run pushed --run {rid}")]
    ci = run.get("ci") or {}
    if ci.get("status") == "fixing":
        return [_act("fix_ci", "a CI fix round is open but nothing is merged yet: add one task per failing job",
                     details=ci.get("details"))]
    if ci.get("status") in (None, "pending"):
        return [_act("watch_ci", "watch the PR (watch_pull_request) and end the turn; on wake record "
                     f"`{R} run ci green|red --details \"...\" --run {rid}`", pr=run["pr_url"])]
    if ci["status"] == "red":
        limit = int(cfg["limits"]["ci_rounds"])
        if ci.get("rounds", 0) >= limit:
            return [_act("escalate", f"CI still red after {limit} fix rounds", details=ci.get("details"))]
        return [_act("fix_ci", "CI is red: add one task per failing job (criterion: that job passes), then "
                     f"`{R} run ci fixing --run {rid}`", details=ci.get("details"))]
    return [_act("report", "CI is green: render the report, stop watching, settle",
                 command=f"{R} report --html --run {rid}", close=f"{R} run close --run {rid}")]


# ---------- merge (integration gate) and rollback ----------

def merge(root: Path, cfg: dict, rid: str, tid: str) -> tuple[bool, str]:
    run = tasks.require_run(root, rid)
    task = tasks.get(run, tid)
    if task["status"] != "accepted":
        raise RatchetError(f"{tid} is {task['status']}; only accepted tasks merge")
    if git(["status", "--porcelain"], root).strip():
        raise RatchetError("the run checkout has uncommitted changes; commit or stash them before merging")
    before = head_sha(root)
    proc = subprocess.run(["git", "merge", "--no-ff", "--no-edit", "-m", f"ratchet: merge {tid} {task['title']}",
                           task["end_sha"]], cwd=str(root), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
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


def rollback(root: Path, rid: str, tid: str) -> str:
    """Drop a blocked task's commits from the shared run checkout."""
    run = tasks.require_run(root, rid)
    task = tasks.get(run, tid)
    if task["status"] != "blocked":
        raise RatchetError(f"{tid} is {task['status']}; only blocked tasks roll back")
    start = task.get("start_sha")
    if not start:
        tasks.transition(root, rid, tid, "rolled-back", text="nothing to roll back", by="coordinator")
        return f"{tid}: nothing to roll back"
    for other in run["tasks"]:
        m = other.get("merged_sha")
        if other["id"] != tid and m and m != start and \
                subprocess.run(["git", "merge-base", "--is-ancestor", start, m], cwd=str(root)).returncode == 0:
            raise RatchetError(f"{other['id']} merged after {tid} started; roll back by hand")
    subprocess.run(["git", "reset", "--hard", start], cwd=str(root), check=True, stdout=subprocess.DEVNULL)
    tasks.transition(root, rid, tid, "rolled-back", text=f"run branch reset to {start[:12]}", by="coordinator")
    return f"{tid}: run branch reset to {start[:12]} (its commits dropped)"


# ---------- briefs: self-contained prompts for every thread, any harness ----------

def _fill(template: str, values: dict) -> str:
    out = template
    for key, val in values.items():
        out = out.replace("{{" + key + "}}", str(val))
    return out


def _task_paths(root: Path, task: dict, run_branch: str) -> list[str]:
    if task.get("end_sha"):
        base = git_ok(["merge-base", task["end_sha"], run_branch], root) or task.get("start_sha") or ""
        if base == task["end_sha"] and task.get("start_sha"):
            base = task["start_sha"]  # shared checkout: the work is already on the run branch
        files = (git_ok(["diff", "--name-only", base, task["end_sha"]], root) or "").splitlines()
        if files:
            return files
    return [f"{p.rstrip('/')}/x" for p in task.get("packages") or []] or ["x"]


def brief(root: Path, cfg: dict, rid: str, kind: str, tid: Optional[str] = None, goal: str = "") -> str:
    R = ratchet_cmd()
    shared = shared_checkout(cfg)
    if kind == "coordinator":
        return _fill((TEMPLATES / "brief-coordinator.md").read_text(), {
            "R": R, "goal": goal or "(given in the message that started this thread)", "repo": root.name,
            "isolation": "the run's own checkout, one task at a time" if shared else
                         f"separate worktrees, up to {_agents(cfg).get('max_parallel')} at once"})
    run = tasks.require_run(root, rid)
    calib = root / ".ratchet" / "judge.md"
    base = {"R": R, "scratch": f"/tmp/ratchet-{slug(rid)}"}
    values = {
        **base, "run": rid, "goal": run["goal"], "run_branch": run["branch"], "repo": root.name,
        "calibration": calib.read_text().strip() if calib.exists() else "(none)",
        "merge_base": gitstate.merge_base(root, cfg), "progress": tasks.read_progress(root, rid, last=10),
    }
    if kind in ("plan", "contract", "final"):
        paths = [p for t in run["tasks"] for p in _task_paths(root, t, run["branch"])] or ["x"]
        values["tasks"] = "\n\n".join(tasks.format_task(t) for t in run["tasks"]) or "(no tasks)"
        values["criteria_hints"] = profiles.section_for(root, cfg, paths, ["Good criteria"], base) \
            if kind != "final" else ""
        values["exercise"] = profiles.section_for(root, cfg, paths, ["Run", "Exercise", "Evidence"], base) \
            if kind == "final" else ""
        values["products"] = "\n".join(f"- {p.get('name')} ({p.get('profile')}): {', '.join(p.get('paths') or [])}"
                                       for p in cfg.get("products") or []) or "- (none configured: library profile)"
        values["checkout"] = ("You are in the run's checkout: do not switch branches or commit."
                              if shared else f"`git checkout --detach {run['branch']}`")
        return _fill((TEMPLATES / f"brief-{kind}.md").read_text(), values)

    task = tasks.get(run, tid)
    notes = [n for n in task["notes"] if n["by"] in ("judge", "merge", "coordinator")]
    paths = _task_paths(root, task, run["branch"])
    values.update({
        "id": task["id"], "contract": tasks.format_task(task), "round": task["rounds"] + 1,
        "end_sha": task.get("end_sha") or "", "start_sha": task.get("start_sha") or "",
        "rework": "\n".join(f"- [{n['by']}] {n['text']}" for n in notes[-3:]) or "(first round, no notes)",
        "exercise": profiles.section_for(root, cfg, paths, ["Run", "Exercise", "Evidence"], base),
        "panel": (f"You are one of {tasks.panel_size_cfg(cfg)} independent judges. Decide on your own; the majority wins."
                  if tasks.panel_size_cfg(cfg) > 1 else "You are the only judge for this task."),
    })
    if kind == "worker":
        prev = task.get("end_sha")
        if shared:
            values["setup"] = ("You are in the run's own checkout, on the run branch. Commit there. "
                               "Don't create branches or worktrees.")
        else:
            values["setup"] = (f"You are in your own worktree. Continue from the previous round: "
                               f"`git merge --no-edit {prev}`, and resolve any conflicts." if prev else
                               "You are in your own worktree, created from the run branch.")
        return _fill((TEMPLATES / "brief-worker.md").read_text(), values)
    if shared:
        values["checkout"] = (f"You are in the run's checkout, which the worker just finished. Do not switch branches, "
                              f"commit or edit tracked files. Confirm `git rev-parse HEAD` is `{values['end_sha']}`, "
                              "and stop with a REJECT note if it isn't.")
        values["diff_range"] = f"{values['start_sha']} {values['end_sha']}"
    else:
        values["checkout"] = f"`git checkout --detach {values['end_sha']}` (you are in your own worktree)."
        values["diff_range"] = f"{run['branch']}...{values['end_sha']}"
    return _fill((TEMPLATES / "brief-judge.md").read_text(), values)


# ---------- `ratchet start`: launch a coordinator thread through T3's CLI ----------

def t3_cli() -> Optional[str]:
    return shutil.which("t3") or next((str(p) for p in [Path.home() / ".t3" / "bin" / "t3"] if p.exists()), None)


def start_command(root: Path, cfg: dict, goal: str, provider: Optional[str], model: Optional[str],
                  base: Optional[str]) -> tuple[list[str], str]:
    """The `t3 thread start` call (and its stdin message) for a new coordinator thread."""
    t3 = t3_cli()
    if not t3:
        raise RatchetError("T3 Code's `t3` CLI wasn't found (looked on PATH and in ~/.t3/bin)")
    coord = _agents(cfg).get("coordinator") or {}
    provider = provider or coord.get("provider")
    model = model or coord.get("model")
    base_ref = base or (gitstate.resolve_base(root, cfg) or "HEAD").replace("origin/", "")
    branch = f"ratchet/{slug(goal.lower())[:40].strip('-')}"
    message = (f"You are the ratchet coordinator for this repo. Run `{ratchet_cmd()} brief coordinator` and "
               f"follow it exactly.\n\nGoal: {goal}")
    cmd = [t3, "thread", "start", str(root), "-", "--title", f"ratchet: {goal}"[:100], "--worktree", base_ref,
           "--branch", branch, "--json"]
    if provider:
        cmd += ["--provider", provider]
    if model:
        cmd += ["--model", model]
    return cmd, message
