"""ratchet: a verification loop for coding agents. Run `ratchet -h`."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from . import blockers, checks, config, gitstate, hooks, scaffold, tamper, tasks, workspace
from .config import VERSION
from .util import RatchetError, git, read_jsonl, require_root, state_dir


def _out(args, data, text: str) -> None:
    print(json.dumps(data, indent=2, default=str) if getattr(args, "json", False) else text)


# ---------- verification ----------

def cmd_verify(args) -> int:
    root = require_root()
    cfg = config.load(root)
    rep = checks.verify(root, cfg, level="full" if args.full else "fast", scope="all" if args.all else "affected",
                        use_cache=not args.no_cache)
    _out(args, asdict(rep), checks.format_report(rep, verbose=args.verbose))
    return 0 if rep.ok else 1


def cmd_affected(args) -> int:
    root = require_root()
    cfg = config.load(root)
    tree, since = gitstate.tree_id(root), gitstate.merge_base(root, cfg)
    paths = [c.path for c in gitstate.changes(root, since, tree)]
    pkgs = workspace.affected(workspace.discover(root, cfg), paths, bool(cfg.get("dependents", True)))
    data = {"base": gitstate.resolve_base(root, cfg), "since": since, "changed_files": paths,
            "packages": [{"name": p.name, "dir": p.label, "kind": p.kind} for p in pkgs]}
    text = [f"base {data['base'] or '(none)'} @ {since[:12]} · {len(paths)} changed files"]
    text += [f"  {p.kind:<7} {p.label} ({p.name})" for p in pkgs] or ["  no packages affected"]
    _out(args, data, "\n".join(text))
    return 0


def cmd_tamper(args) -> int:
    root = require_root()
    cfg = config.load(root)
    tree, since = gitstate.tree_id(root), gitstate.merge_base(root, cfg)
    allowed = tamper.allowances(root)
    found = tamper.scan(root, cfg, since, tree)
    data = [dict(asdict(f), allowed=f.id in allowed, reason=(allowed.get(f.id) or {}).get("reason")) for f in found]
    text = [f"[{d['id']}] {'ALLOWED ' if d['allowed'] else ''}{d['kind']}: {d['file']}:{d['line']}  {d['text'][:120]}"
            + (f"\n    reason: {d['reason']}" if d["allowed"] else "") for d in data] or ["no tamper findings"]
    _out(args, data, "\n".join(text))
    return 1 if any(not d["allowed"] for d in data) else 0


def cmd_allow(args) -> int:
    root = require_root()
    try:
        chosen = tamper.allow(root, config.load(root), args.ids, args.reason, allow_all=args.all)
    except ValueError as exc:
        raise RatchetError(str(exc))
    print(f"allowed {len(chosen)} finding(s); the judge will review: " + ", ".join(f.id for f in chosen))
    return 0


# ---------- runs and tasks ----------

def cmd_run(args) -> int:
    root = require_root()
    rid = tasks.run_id(root, args.run)
    if args.action == "start":
        if not args.goal:
            raise RatchetError("--goal is required")
        run = tasks.start_run(root, rid, args.goal)
        print(f"run {run['id']} active on {run['branch']}: {run['goal']}")
    elif args.action == "close":
        run = tasks.require_run(root, rid)
        run["status"] = args.status
        tasks._save(root, run)
        tasks.progress(root, rid, f"run closed: {args.status}")
        print(f"run {rid} {args.status}")
    else:
        run = tasks.require_run(root, rid)
        text = [f"run {run['id']} [{run['status']}] on {run['branch']}", f"goal: {run['goal']}",
                f"tasks: {tasks.summary(run)}"]
        text += [f"  {t['id']} [{t['status']}] {t['title']}" for t in run["tasks"]]
        tail = tasks.read_progress(root, rid, last=8)
        if tail:
            text += ["recent progress:", tail]
        _out(args, run, "\n".join(text))
    return 0


def _task_items(args) -> list[dict]:
    if args.json_input is not None:
        raw = sys.stdin.read() if args.json_input == "-" else args.json_input
        data = json.loads(raw)
        return data if isinstance(data, list) else [data]
    return [{"title": args.title, "goal": args.goal or "", "criteria": args.criteria or [],
             "verify": args.verify or [], "packages": args.packages or [], "deps": args.deps or []}]


def _require_clean(root: Path) -> None:
    dirty = git(["status", "--porcelain"], root).strip()
    if dirty:
        raise RatchetError("commit your work first; the judge reviews committed diffs. Uncommitted:\n" + dirty[:800])


def _require_green(root: Path, level: str) -> None:
    rep = checks.verify(root, config.load(root), level=level)
    if not rep.ok:
        raise RatchetError(f"checks are not green, so this can't move forward:\n{checks.format_report(rep)}")


def cmd_task(args) -> int:
    root = require_root()
    rid = tasks.run_id(root, args.run)
    act = args.action
    if act == "add":
        added = tasks.add_tasks(root, rid, _task_items(args))
        print("added " + ", ".join(f"{t['id']} {t['title']}" for t in added))
        return 0
    run = tasks.require_run(root, rid)
    if act == "list":
        _out(args, run["tasks"], "\n".join(f"{t['id']} [{t['status']}] {t['title']}"
                                            + (f" (deps {','.join(t['deps'])})" if t["deps"] else "")
                                            for t in run["tasks"]) or "no tasks")
        return 0
    if act == "next":
        nxt = tasks.ready(run)
        _out(args, nxt, "\n".join(f"{t['id']} {t['title']}" for t in nxt) or
             ("all tasks done" if all(t["status"] == "done" for t in run["tasks"]) else "nothing ready"))
        return 0
    if not args.id:
        raise RatchetError(f"`ratchet task {act}` needs a task id")
    if act == "show":
        task = tasks.get(run, args.id)
        _out(args, task, tasks.format_task(task))
        return 0
    if act == "edit":
        items = _task_items(args) if args.json_input is not None else []
        if len(items) != 1 or not isinstance(items[0], dict):
            raise RatchetError("usage: ratchet task edit <id> --from-json '{\"criteria\": [...]}'")
        task = tasks.edit_task(root, rid, args.id, items[0])
        print(tasks.format_task(task))
        return 0
    text = args.text or ""
    if act == "submit":
        _require_clean(root)
        _require_green(root, "fast")
    if act == "accept":
        _require_green(root, "full")
    if act in ("reject", "block") and not text:
        raise RatchetError(f"`ratchet task {act}` needs --notes/--reason explaining why")
    task = tasks.transition(root, rid, args.id, act, text=text, by=args.by or act)
    print(f"{task['id']} → {task['status']}" + (f" (round {task['rounds']})" if act == "reject" else ""))
    return 0


def cmd_log(args) -> int:
    root = require_root()
    tasks.progress(root, tasks.run_id(root, args.run), " ".join(args.text))
    return 0


def cmd_blocker(args) -> int:
    root = require_root()
    if args.action == "add":
        item = blockers.add(root, " ".join(args.text), kind=args.kind, run=tasks.run_id(root))
        print(f"logged {item['id']} [{item['kind']}]")
    elif args.action == "resolve":
        if not args.text:
            raise RatchetError("usage: ratchet blocker resolve <id> --note \"...\"")
        item = blockers.resolve(root, args.text[0], args.note or "")
        print(f"{item['id']} resolved")
    else:
        items = [b for b in blockers.load(root) if args.all or b["status"] == "open"]
        _out(args, items, "\n".join(f"{b['id']} [{b['kind']}] {b['at'][:10]} {b['text']}" for b in items)
             or "no open blockers")
    return 0


def cmd_status(args) -> int:
    root = require_root()
    cfg = config.load(root)
    rid = tasks.run_id(root, args.run)
    run = tasks.load_run(root, rid)
    tree, since = gitstate.tree_id(root), gitstate.merge_base(root, cfg)
    last = checks.cached(root, tree, since, "fast", "affected")
    open_b = [b for b in blockers.load(root) if b["status"] == "open"]
    data = {"run": run and {"id": run["id"], "goal": run["goal"], "status": run["status"], "summary": tasks.summary(run)},
            "gate": "green" if last else "unverified", "open_blockers": len(open_b)}
    text = [f"working state: {'verified green' if last else 'not verified yet (run `ratchet verify`)'}"]
    if run:
        text.append(f"run {run['id']} [{run['status']}]: {run['goal']} — {tasks.summary(run)}")
        for t in run["tasks"]:
            if t["status"] != "done":
                text.append(f"  {t['id']} [{t['status']}] {t['title']}")
    else:
        text.append(f"no run for '{rid}'")
    text.append(f"open blockers: {len(open_b)}")
    _out(args, data, "\n".join(text))
    return 0


def cmd_stats(args) -> int:
    root = require_root()
    events = read_jsonl(state_dir(root) / "events.jsonl")
    gates = [e for e in events if e["type"] == "gate"]
    verifies = [e for e in events if e["type"] == "verify"]
    task_ev = [e for e in events if e["type"] == "task"]
    failing = Counter(f for e in verifies for f in e.get("failed", []))
    secs = sorted(e.get("seconds", 0) for e in verifies)
    data = {
        "gate_runs": len(gates), "gate_failures": sum(1 for e in gates if not e.get("ok")),
        "verify_runs": len(verifies),
        "verify_seconds_p50": secs[len(secs) // 2] if secs else 0,
        "verify_seconds_max": secs[-1] if secs else 0,
        "judge_accepts": sum(1 for e in task_ev if e.get("action") == "accept"),
        "judge_rejects": sum(1 for e in task_ev if e.get("action") == "reject"),
        "top_failing_checks": failing.most_common(8),
        "open_blockers": sum(1 for b in blockers.load(root) if b["status"] == "open"),
    }
    text = [f"{k}: {v}" for k, v in data.items() if k != "top_failing_checks"]
    text += ["top failing checks:"] + [f"  {n}× {name}" for name, n in data["top_failing_checks"]]
    _out(args, data, "\n".join(text))
    return 0


# ---------- setup ----------

def cmd_init(args) -> int:
    root = require_root()
    res = scaffold.init(root, force=args.force)
    text = [res["status"], f"CLAUDE.md: {res['claude_md']}", f"package manager: {res['pm']}",
            f"kinds: {', '.join(res['kinds']) or 'none detected'}", "packages:"]
    text += [f"  {p}" for p in res["packages"]] or ["  (none)"]
    text.append("next: `ratchet doctor --baseline` to prove the checks run and are green on this branch")
    _out(args, res, "\n".join(text))
    return 0


def cmd_doctor(args) -> int:
    root = require_root()
    ok, text = scaffold.doctor(root, baseline=args.baseline)
    print(text)
    return 0 if ok else 1


def cmd_setup(args) -> int:
    root = require_root()
    cmd = config.load(root).get("setup")
    if not cmd:
        print("no setup command configured")
        return 0
    print(f"$ {cmd}", flush=True)
    return subprocess.call(cmd, shell=True, cwd=str(root))


def cmd_hook(args) -> int:
    return hooks.main(args.event)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ratchet", description="Verification loop for coding agents.")
    p.add_argument("--version", action="version", version=f"ratchet {VERSION}")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, fn, help_):
        sp = sub.add_parser(name, help=help_)
        sp.set_defaults(fn=fn)
        return sp

    sp = add("verify", cmd_verify, "run checks for changed packages (cached per working state)")
    sp.add_argument("--full", action="store_true", help="also run full-level checks (build, e2e)")
    sp.add_argument("--all", action="store_true", help="every package, not just affected ones")
    sp.add_argument("--no-cache", action="store_true")
    sp.add_argument("-v", "--verbose", action="store_true")
    sp.add_argument("--json", action="store_true")

    sp = add("affected", cmd_affected, "show changed files and affected packages")
    sp.add_argument("--json", action="store_true")
    sp = add("tamper", cmd_tamper, "list check-weakening changes on this branch")
    sp.add_argument("--json", action="store_true")
    sp = add("allow", cmd_allow, "accept a tamper finding with a reason (judge reviews it)")
    sp.add_argument("ids", nargs="*")
    sp.add_argument("--all", action="store_true")
    sp.add_argument("--reason", required=True)

    sp = add("run", cmd_run, "start/show/close the run for this branch")
    sp.add_argument("action", choices=["start", "show", "close"], nargs="?", default="show")
    sp.add_argument("--goal")
    sp.add_argument("--status", default="done", choices=["done", "abandoned"])
    sp.add_argument("--run", help="run id (default: current branch)")
    sp.add_argument("--json", action="store_true")

    sp = add("task", cmd_task, "plan and move tasks: add|edit|list|next|show|start|submit|accept|reject|block|reset")
    sp.add_argument("action", choices=["add", "edit", "list", "next", "show", "start", "submit", "accept",
                                       "reject", "block", "reset"])
    sp.add_argument("id", nargs="?")
    sp.add_argument("--run")
    sp.add_argument("--json", action="store_true", help="JSON output")
    sp.add_argument("--from-json", dest="json_input", help="task JSON (object or array), or - for stdin")
    sp.add_argument("--title")
    sp.add_argument("--goal")
    sp.add_argument("--criteria", action="append")
    sp.add_argument("--verify", action="append")
    sp.add_argument("--packages", action="append")
    sp.add_argument("--deps", action="append")
    sp.add_argument("--summary", "--notes", "--reason", dest="text")
    sp.add_argument("--by", help="who is acting (worker, judge, coordinator)")

    sp = add("log", cmd_log, "append a line to the run's progress log")
    sp.add_argument("text", nargs="+")
    sp.add_argument("--run")

    sp = add("blocker", cmd_blocker, "log friction for the gardener: add|list|resolve")
    sp.add_argument("action", choices=["add", "list", "resolve"])
    sp.add_argument("text", nargs="*")
    sp.add_argument("--kind", default="other", choices=blockers.KINDS)
    sp.add_argument("--note")
    sp.add_argument("--all", action="store_true")
    sp.add_argument("--json", action="store_true")

    sp = add("status", cmd_status, "where things stand")
    sp.add_argument("--run")
    sp.add_argument("--json", action="store_true")
    sp = add("stats", cmd_stats, "loop health: gate failures, slow checks, judge verdicts")
    sp.add_argument("--json", action="store_true")

    sp = add("init", cmd_init, "write .ratchet/ and the CLAUDE.md section for this repo")
    sp.add_argument("--force", action="store_true")
    sp.add_argument("--json", action="store_true")
    sp = add("doctor", cmd_doctor, "check the contract works; --baseline runs every check once")
    sp.add_argument("--baseline", action="store_true")
    add("setup", cmd_setup, "run the configured setup command (fresh worktrees)")
    sp = add("hook", cmd_hook, "Claude Code hook entry point")
    sp.add_argument("event", choices=sorted(hooks.HANDLERS))
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except RatchetError as exc:
        print(f"ratchet: {exc}", file=sys.stderr)
        return 2
