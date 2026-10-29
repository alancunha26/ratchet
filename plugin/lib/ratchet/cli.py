"""ratchet: set up and run a verifiable development cycle inside a repo. Run `ratchet -h`."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from . import (blockers, cycle, evolve, exercise, generate, gitstate, harness, hooks, imagediff, newproject, proof,
               report, state as tasks, store, tamper, verify, workspace)
from .harness import VERSION
from .util import RatchetError, git, read_jsonl, require_root, state_dir


def _out(args, data, text: str) -> None:
    print(json.dumps(data, indent=2, default=str) if getattr(args, "json", False) else text)


def _task_base(root: Path, rid: str, tid: str) -> tuple[dict, dict]:
    run = tasks.require_run(root, rid)
    return run, tasks.get(run, tid)


# ---------- set up: new, init, prove, baseline, doctor, readiness, garden, upgrade ----------

def cmd_new(args) -> int:
    if args.list or not args.stack:
        for name, spec in newproject.STACKS.items():
            print(f"{name:<12} {spec['describe']}")
        print("\nAny other stack: follow `ratchet brief new` (an agent builds the walking skeleton by the same rules).")
        return 0
    if not args.dir:
        raise RatchetError("usage: ratchet new <stack> <dir> [--name NAME]")
    res = newproject.create(Path(args.dir).resolve(), args.stack, args.name, prove=not args.no_prove)
    lines = [f"created {res['dir']} ({res['stack']}): {res['checks']} checks, {res['products']} products, "
             "first commit is the verified walking skeleton"]
    if res["missing_tools"]:
        lines.append(f"not proven: missing tools on PATH: {', '.join(res['missing_tools'])} (install them, then `ratchet prove`)")
    if res["proofs"]:
        lines.append(generate.format_proofs(res["proofs"]))
    for e in res.get("exercises") or []:
        lines.append(exercise.format_result(e))
    if res.get("unverified"):
        lines.append(f"note: {res['unverified']}")
    lines.append(f"next: cd {res['dir']} && ratchet start \"<your first feature>\"")
    _out(args, res, "\n".join(lines))
    return 0


def cmd_init(args) -> int:
    root = require_root()
    p = generate.plan(root)
    if args.dry_run:
        data = {"setup": p["setup"], "checks": [asdict(c) for c in p["checks"]], "products": p["products"], "ci": p["ci"]}
        _out(args, data, json.dumps(data, indent=2))
        return 0
    info = generate.write(root, p, force=args.force)
    lines = [f"harness written: {info['checks']} checks, {info['products']} products "
             f"(AGENTS.md {info['agents_md']}, CLAUDE.md {info['claude_md']}, t3.json {info['t3_json']})"]
    if p["ci"]:
        lines.append(f"CI runs {len(p['ci'])} commands; reconcile them with the harness (`ratchet brief init`):")
        lines += [f"  [{c['workflow']}] {c['command'][:110]}" for c in p["ci"][:15]]
    if not args.no_prove:
        rows = generate.prove_all(root, probe=args.probe)
        lines += ["", generate.format_proofs(rows)]
    lines += ["", evolve.format_readiness(evolve.readiness(root, harness.load(root))),
              "", "next: review .ratchet/, fill .ratchet/exercise/*.md and .ratchet/judge.md, then commit "
                  "(`ratchet brief init` is the full protocol)"]
    print("\n".join(lines))
    return 0


def cmd_prove(args) -> int:
    root = require_root()
    h = harness.load(root)
    if args.check:
        check = next((c for c in harness.checks(h, "full") if c.name == args.check), None)
        if not check:
            raise RatchetError(f"no check {args.check}")
        b = verify.compare(root, check, *([verify._run(root, h, check, gitstate.tree_id(root), True)[0], None]))
        row = {"check": check.name, "green": b.ok, "tests": b.total, "format": b.format, "problems": b.problems}
        if b.ok:
            row["canary"] = proof.canary(root, h, check)
            if args.probe and b.format != "exit" and check.mutate:
                m = proof.mutate(root, h, globs=check.mutate, limit=args.probe, level="full")
                row["mutation"] = {"killed": m["killed"], "survived": m["survived"]}
        proof.record_proof(root, check.name, row)
        rows = [row]
    else:
        rows = generate.prove_all(root, probe=args.probe)
    print(generate.format_proofs(rows))
    return 0 if all(r["green"] and (r.get("canary") or {}).get("ok") is not False for r in rows) else 1


def cmd_baseline(args) -> int:
    root = require_root()
    rows = verify.baseline(root, harness.load(root), level="fast" if args.fast else "full")
    for c in rows:
        print(f"{'ok  ' if c.ok else 'RED '} {c.name}: {c.total if c.format != 'exit' else 'exit'} "
              f"{'tests' if c.format != 'exit' else 'code ' + str(c.code)}{' (cached)' if c.cached else ''}")
    return 0 if all(c.ok for c in rows) else 1


def cmd_doctor(args) -> int:
    root = require_root()
    h = harness.load(root)
    import shutil

    problems = []
    if h.get("_needs_upgrade"):
        problems.append(f"harness targets kernel {h.get('kernel')}, this ratchet is {harness.KERNEL}: run `ratchet upgrade`")
    for c in harness.checks(h, "full"):
        script = c.run.split()[-1] if c.run.startswith("bash ") else None
        if script and not (root / script).exists():
            problems.append(f"check {c.name}: {script} is missing")
        if script and (root / script).exists():
            body = (root / script).read_text().splitlines()
            tool = next((l.split()[0] for l in body if l.strip() and not l.startswith(("#", "set ", "cd "))), None)
            if tool and "=" not in tool and shutil.which(tool) is None and tool not in ("ratchet",):
                problems.append(f"check {c.name}: `{tool}` not found on PATH")
    for prod in h.get("products") or []:
        g = root / (prod.get("exercise") or "")
        if prod.get("exercise") and not g.exists():
            problems.append(f"product {prod['name']}: exercise guide {prod['exercise']} is missing")
    print("\n".join(f"PROBLEM: {p}" for p in problems) or "doctor: OK")
    return 1 if problems else 0


def cmd_readiness(args) -> int:
    root = require_root()
    h = harness.load(root)
    r = evolve.readiness(root, h)
    if args.write:
        from .util import write_json

        write_json(root / ".ratchet" / "readiness.json", {k: v for k, v in r.items()})
    _out(args, r, evolve.format_readiness(r))
    return 0


def cmd_garden(args) -> int:
    root = require_root()
    plan = evolve.garden_plan(root, harness.load(root))
    if args.json or args.plan:
        print(json.dumps(plan, indent=2))
    else:
        print("\n".join(f"- {t['title']}" for t in plan) or "nothing to garden: no gaps or friction found")
        if plan:
            print("\nRun them as a harness run: `ratchet start \"harness improvements\"`, then "
                  "`ratchet garden --plan | ratchet task add --from-json -`")
    return 0


def cmd_upgrade(args) -> int:
    root = require_root()
    r = evolve.upgrade(root, apply=not args.check)
    if r["up_to_date"] and not r["steps"]:
        print(f"harness is on kernel {r['current']}: up to date")
    else:
        print(f"migrated: {', '.join(r['steps']) or 'nothing'}; now on {r['to']} (this ratchet: {r['current']})")
        for f in r["followups"]:
            print(f"  follow-up task: {f}")
    return 0


# ---------- verification and proofs ----------

def cmd_verify(args) -> int:
    root = require_root()
    h = harness.load(root)
    base_tree, harness_task = None, False
    if args.task:
        _, task = _task_base(root, tasks.run_id(root, args.run), args.task)
        base_tree, harness_task = task.get("start_tree"), task.get("kind") == "harness"
    v = verify.verify(root, h, level="full" if args.full else "fast", base_tree=base_tree, all_checks=args.all,
                      harness_task=harness_task, use_cache=not args.no_cache)
    _out(args, asdict(v), verify.format_verdict(v, verbose=args.verbose))
    return 0 if v.ok else 1


def _start_for(root: Path, args) -> str:
    if getattr(args, "start", None):
        return git(["rev-parse", args.start], root).strip()
    if getattr(args, "task", None):
        _, task = _task_base(root, tasks.run_id(root, args.run), args.task)
        if not task.get("start_sha"):
            raise RatchetError(f"{args.task} hasn't started")
        return task["start_sha"]
    return gitstate.merge_base(root, harness.load(root))


def cmd_proof(args) -> int:
    root = require_root()
    h = harness.load(root)
    if args.kind == "canary":
        check = next((c for c in harness.checks(h, "full") if c.name == args.target), None)
        if not check:
            raise RatchetError("usage: ratchet proof canary <check>")
        r = proof.canary(root, h, check)
        print(f"{r['check']}: {r['status']} ({r.get('how', '')})")
        return 0 if r["ok"] else 1
    args.task = args.target
    start = _start_for(root, args)
    if args.kind == "f2p":
        start_tree = verify.commit_tree(root, start)
        verify.verify(root, h, level="fast", base_tree=start_tree)  # make sure head results exist
        r = proof.new_tests_fail_on_base(root, h, start)
        _out(args, r, proof.format_f2p(r))
        return 0 if r["proving"] else 1
    r = proof.mutate(root, h, start=start, limit=args.limit)
    _out(args, r, proof.format_mutation(r))
    return 0


def cmd_exercise(args) -> int:
    root = require_root()
    h = harness.load(root)
    if args.action == "list":
        exs = exercise.all_exercises(h)
        print("\n".join(f"{e['name']:<20} {e['product']:<16} {e['run']}" for e in exs) or "no exercises declared")
        return 0
    if not args.name:
        raise RatchetError("usage: ratchet exercise run <name> [--base REF | --task ID]")
    start = None
    if args.base:
        start = git(["rev-parse", args.base], root).strip()
    elif args.task:
        args.start = None
        start = _start_for(root, args)
    r = exercise.run(root, h, args.name, start=start, tag=args.task or "adhoc")
    if args.task:
        for rel in r["head"]["artifacts"]:
            if rel.lower().endswith(".png"):
                report.add_evidence(root, tasks.run_id(root, args.run), args.task, str(Path(r["head"]["dir"]) / rel),
                                    f"exercise {args.name}: {rel}")
    _out(args, r, exercise.format_result(r))
    return 0 if r["ok"] else 1


def cmd_diff_image(args) -> int:
    r = imagediff.diff(Path(args.a), Path(args.b), Path(args.out) if args.out else None, threshold=args.threshold)
    if not r["same_size"]:
        print(f"sizes differ: {r['size_a']} vs {r['size_b']}")
        return 1
    print(f"{r['differing']} pixels differ ({r['ratio'] * 100:.3f}%)" + (f"; diff image: {r['diff_image']}" if r["diff_image"] else ""))
    return 0 if args.max_ratio is None or r["ratio"] <= args.max_ratio else 1


def cmd_affected(args) -> int:
    root = require_root()
    h = harness.load(root)
    tree, base = gitstate.tree_id(root), verify.default_base_tree(root, h)
    paths = store.changed_between(root, base, tree) or []
    fast = [c.name for c in harness.checks(h, "fast") if c.triggered_by(paths)]
    full = [c.name for c in harness.checks(h, "full") if c.triggered_by(paths)]
    prods = [p["name"] for p in harness.products_touching(h, paths)] if paths else []
    data = {"base_tree": base, "changed_files": paths, "fast_checks": fast, "full_checks": full, "products": prods}
    _out(args, data, f"{len(paths)} changed files\nfast checks: {', '.join(fast) or '-'}\nfull checks: "
                     f"{', '.join(full) or '-'}\nproducts: {', '.join(prods) or '-'}")
    return 0


def cmd_tamper(args) -> int:
    root = require_root()
    h = harness.load(root)
    tree, since = gitstate.tree_id(root), verify.default_base_tree(root, h)
    allowed = tamper.allowances(root)
    found = tamper.scan(root, h, since, tree)
    data = [dict(asdict(f), allowed=f.id in allowed, reason=(allowed.get(f.id) or {}).get("reason")) for f in found]
    text = [f"[{d['id']}] {'ALLOWED ' if d['allowed'] else ''}{d['kind']}: {d['file']}:{d['line']}  {d['text'][:120]}"
            + (f"\n    reason: {d['reason']}" if d["allowed"] else "") for d in data] or ["no tamper findings"]
    _out(args, data, "\n".join(text))
    return 1 if any(not d["allowed"] for d in data) else 0


def cmd_allow(args) -> int:
    root = require_root()
    if args.test:
        from .util import now, short_hash, write_json

        check, _, test = args.test.partition("::")
        store_ = tamper.allowances(root)
        for kind in ("removed-test", "skipped-test"):
            store_[short_hash(kind, check, test)] = {"kind": kind, "file": check, "text": test, "reason": args.reason, "at": now()}
        write_json(state_dir(root) / "allowances.json", store_)
        print(f"allowed removing/skipping {args.test}; the judge will review the reason")
        return 0
    try:
        chosen = tamper.allow(root, harness.load(root), args.ids, args.reason, allow_all=args.all)
    except ValueError as exc:
        raise RatchetError(str(exc))
    print(f"allowed {len(chosen)} finding(s); the judge will review: " + ", ".join(f.id for f in chosen))
    return 0


# ---------- the cycle: tick, briefs, start, runs, tasks ----------

def cmd_tick(args) -> int:
    root = require_root()
    h = harness.load(root)
    rid = tasks.run_id(root, args.run)
    run = tasks.load_run(root, rid)
    actions = [{"do": "start", "why": f"no run '{rid}' yet", "command": "ratchet run start --goal \"...\""}] \
        if run is None else cycle.tick(root, h, run)
    if args.json:
        print(json.dumps({"run": rid, "actions": actions}, indent=2))
        return 0
    print(f"run {rid}: {tasks.summary(run) if run else 'not started'}")
    for a in actions:
        print(f"→ {a['do']}: {a['why']}")
        for k, v in a.items():
            if k not in ("do", "why"):
                print(f"    {k}: {v}")
    return 0


def cmd_brief(args) -> int:
    kinds_without_harness = ("init", "new")
    root = require_root() if args.kind not in ("new",) else (Path.cwd())
    h = harness.load(root) if args.kind not in kinds_without_harness else dict(harness.DEFAULTS)
    rid = tasks.run_id(root, args.run) if args.kind not in ("new",) else "new"
    if args.kind in ("worker", "judge") and not args.id:
        raise RatchetError(f"`ratchet brief {args.kind}` needs a task id")
    print(cycle.brief(root, h, rid, args.kind, args.id, goal=args.goal or ""))
    return 0


def cmd_start(args) -> int:
    root = require_root()
    h = harness.load(root)
    goal = " ".join(args.goal)
    cmd, message = cycle.start_command(root, h, goal, args.provider, args.model, args.base)
    if args.dry_run:
        print(" ".join(cmd) + "\n--- stdin ---\n" + message)
        return 0
    proc = subprocess.run(cmd, input=message, text=True, capture_output=True)
    if proc.returncode != 0:
        raise RatchetError(f"t3 thread start failed: {proc.stderr.strip() or proc.stdout.strip()}")
    print(proc.stdout.strip())
    return 0


def cmd_release(args) -> int:
    root = require_root()
    print(f"released: {tasks.release_thread(root, tasks.run_id(root, args.run), args.thread, args.reason or 'thread ended')}")
    return 0


def cmd_run(args) -> int:
    root = require_root()
    rid = tasks.run_id(root, args.run)
    act, val, notes = args.action, args.value, args.text or ""
    if act == "start":
        if not args.goal:
            raise RatchetError("--goal is required")
        run = tasks.start_run(root, rid, args.goal)
        if not git(["status", "--porcelain"], root).strip():
            red = [c.name for c in verify.baseline(root, harness.load(root), level="full") if not c.ok]
            if red:
                tasks.progress(root, rid, f"baseline: red checks {', '.join(red)}")
        print(f"run {run['id']} active on {run['branch']}: {run['goal']} (baseline recorded)")
        return 0
    run = tasks.require_run(root, rid)
    if act == "show":
        text = [f"run {run['id']} [{run['status']}] on {run['branch']}", f"goal: {run['goal']}",
                f"tasks: {tasks.summary(run)}", f"contract: {run.get('contract') or '-'} · final: {run.get('final') or '-'}"
                f" · PR: {run.get('pr_url') or '-'} · CI: {(run.get('ci') or {}).get('status') or '-'}"]
        text += [f"  {t['id']} [{t['status']}] ({t.get('kind')}) {t['title']}" for t in run["tasks"]]
        tail = tasks.read_progress(root, rid, last=8)
        if tail:
            text += ["recent progress:", tail]
        _out(args, run, "\n".join(text))
    elif act == "dispatch":
        if val not in ("contract", "final") or not args.thread:
            raise RatchetError("usage: ratchet run dispatch contract|final --thread <id>")
        tasks.set_run(root, rid, **{f"{val}_thread": args.thread})
        tasks.progress(root, rid, f"{val} review dispatched ({args.thread})")
        print(f"{val} review dispatched")
    elif act == "contract":
        if val not in ("ok", "revise"):
            raise RatchetError("usage: ratchet run contract ok|revise --notes \"...\"")
        tasks.set_run(root, rid, contract=val, contract_thread=None, contract_notes=notes)
        tasks.progress(root, rid, f"contract {val}{': ' + notes[:160] if notes else ''}")
        print(f"contract: {val}")
    elif act == "final":
        if val not in ("accept", "reject", "reset"):
            raise RatchetError("usage: ratchet run final accept|reject|reset --notes \"...\"")
        if val == "accept":
            h = harness.load(root)
            base = verify.commit_tree(root, run.get("base_sha") or "HEAD")
            v = verify.verify(root, h, level="full", base_tree=base)
            if not v.ok:
                raise RatchetError("full checks are red against the run's base; the run can't be accepted:\n"
                                   + verify.format_verdict(v))
        state = {"accept": "accepted", "reject": "rejected", "reset": None}[val]
        tasks.set_run(root, rid, final=state, final_thread=None, final_notes=notes)
        tasks.progress(root, rid, f"final review {val}{': ' + notes[:160] if notes else ''}")
        print(f"final: {state}")
    elif act == "pr":
        if not val:
            raise RatchetError("usage: ratchet run pr <url>")
        tasks.set_run(root, rid, pr_url=val, pushed_sha=git(["rev-parse", "HEAD"], root).strip(),
                      ci={"status": "pending", "rounds": (run.get("ci") or {}).get("rounds", 0), "details": ""})
        tasks.progress(root, rid, f"PR opened: {val}")
        print(f"PR recorded: {val}")
    elif act == "pushed":
        ci = dict(run.get("ci") or {})
        ci["status"] = "pending"
        tasks.set_run(root, rid, pushed_sha=git(["rev-parse", "HEAD"], root).strip(), ci=ci)
        print("pushed; CI pending")
    elif act == "ci":
        if val not in ("green", "red", "pending", "fixing"):
            raise RatchetError("usage: ratchet run ci green|red|pending|fixing --details \"...\"")
        ci = dict(run.get("ci") or {"rounds": 0})
        if val == "fixing":
            ci.update(rounds=int(ci.get("rounds", 0)) + 1, status="fixing")
            tasks.set_run(root, rid, ci=ci, final="accepted")
        else:
            ci.update(status=val, details=args.details or ci.get("details", ""))
            tasks.set_run(root, rid, ci=ci)
        tasks.progress(root, rid, f"CI {val}{': ' + (args.details or '')[:160] if args.details else ''}")
        print(f"CI: {val}")
    elif act == "close":
        status = val if val in ("done", "abandoned") else "done"
        tasks.set_run(root, rid, status=status)
        tasks.progress(root, rid, f"run closed: {status}")
        print(f"run {rid} {status}")
    return 0


def _task_items(args) -> list:
    if args.json_input is not None:
        raw = sys.stdin.read() if args.json_input == "-" else args.json_input
        data = json.loads(raw)
        return data if isinstance(data, list) else [data]
    return [{"title": args.title, "goal": args.goal or "", "criteria": args.criteria or [], "kind": args.kind or "feature",
             "verify": args.verify or [], "packages": args.packages or [], "deps": args.deps or []}]


def _gate(root: Path, h: dict, task: dict, level: str) -> str:
    """The kernel's hard gate for a task's committed state. Returns a summary or raises."""
    dirty = git(["status", "--porcelain"], root).strip()
    if dirty:
        raise RatchetError("commit your work first; review happens on committed work. Uncommitted:\n" + dirty[:800])
    v = verify.verify(root, h, level=level, base_tree=task.get("start_tree"), harness_task=task.get("kind") == "harness")
    if not v.ok:
        raise RatchetError("checks are not green against the task's base:\n" + verify.format_verdict(v))
    summary = f"{level} checks green ({len(v.checks)} checks, {sum(c.total for c in v.checks)} tests)"
    if task.get("expects_test"):
        r = proof.new_tests_fail_on_base(root, h, task["start_sha"])
        if not r["proving"]:
            raise RatchetError("this task must include at least one new test that fails without the change.\n"
                               + proof.format_f2p(r) + "\nAdd one, or (refactor/docs only) ask the coordinator to set "
                               "expects_test to false.")
        summary += f"; {len(r['proving'])} new test(s) fail without the change"
    return summary


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
        _out(args, run["tasks"], "\n".join(f"{t['id']} [{t['status']}] ({t.get('kind')}) {t['title']}"
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
        print(tasks.format_task(tasks.edit_task(root, rid, args.id, items[0])))
        return 0
    if act == "merge":
        ok, msg = cycle.merge(root, harness.load(root), rid, args.id)
        print(msg)
        return 0 if ok else 1
    if act == "rollback":
        print(cycle.rollback(root, rid, args.id))
        return 0
    if act == "dispatch":
        if args.role not in ("worker", "judge"):
            raise RatchetError("usage: ratchet task dispatch <id> --role worker|judge --thread <id>")
        task = tasks.transition(root, rid, args.id, f"dispatch-{args.role}", thread=args.thread, by="coordinator")
        print(f"{task['id']} {args.role} dispatched ({args.thread or 'manual'})")
        return 0
    text = args.text or ""
    gate_note = ""
    if act in ("submit", "accept"):
        h = harness.load(root)
        task = tasks.get(run, args.id)
        if act == "submit" and task["status"] == "doing" and not task.get("start_sha"):
            raise RatchetError(f"`ratchet task start {args.id}` first (it records the task's baseline)")
        gate_note = _gate(root, h, task, "fast" if act == "submit" else "full")
    if act == "start":
        task = tasks.get(run, args.id)
        if not task.get("start_sha") and not git(["status", "--porcelain"], root).strip():
            verify.baseline(root, harness.load(root), level="full")  # cached/carried forward almost always
    if act in ("reject", "block") and not text:
        raise RatchetError(f"`ratchet task {act}` needs --notes/--reason explaining why")
    task = tasks.transition(root, rid, args.id, act, text=text + (f"\n[gate] {gate_note}" if gate_note else ""),
                            by=args.by or act, thread=args.thread)
    pending = f" (panel: {len(task['votes'])}/{tasks.panel_size(root)} votes in)" if task.get("votes") else ""
    print(f"{task['id']} → {task['status']}" + pending + (f"\n{gate_note}" if gate_note else ""))
    return 0


# ---------- evidence, reports, friction ----------

def cmd_evidence(args) -> int:
    root = require_root()
    rid = tasks.run_id(root, args.run)
    if args.action == "add":
        if not args.target or not args.path:
            raise RatchetError("usage: ratchet evidence add <task-id|final> <file> --note \"...\"")
        print(f"evidence saved: {report.add_evidence(root, rid, args.target, args.path, args.note or '')['path']}")
        return 0
    run = tasks.require_run(root, rid)
    items = [dict(e, target=n) for n, hh in [("final", run)] + [(t["id"], t) for t in run["tasks"]] for e in hh.get("evidence", [])]
    _out(args, items, "\n".join(f"{e['target']}: {e['path']}  {e.get('note', '')}" for e in items) or "no evidence")
    return 0


def cmd_report(args) -> int:
    root = require_root()
    run = tasks.require_run(root, tasks.run_id(root, args.run))
    text = report.html_report(run) if args.html else report.markdown(run)
    if args.out:
        Path(args.out).write_text(text)
        print(args.out)
    else:
        print(text)
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
        print(f"{blockers.resolve(root, args.text[0], args.note or '')['id']} resolved")
    else:
        items = [b for b in blockers.load(root) if args.all or b["status"] == "open"]
        _out(args, items, "\n".join(f"{b['id']} [{b['kind']}] {b['at'][:10]} {b['text']}" for b in items) or "no open blockers")
    return 0


def cmd_status(args) -> int:
    root = require_root()
    h = harness.load(root)
    rid = tasks.run_id(root, args.run)
    run = tasks.load_run(root, rid)
    r = evolve.readiness(root, h)
    open_b = [b for b in blockers.load(root) if b["status"] == "open"]
    text = [f"harness: kernel {h.get('kernel')}, {len(h['checks'])} checks, {len(h.get('products') or [])} products, "
            f"readiness {r['level']}/5 ({len(r['gaps'])} gaps)"]
    if run:
        text.append(f"run {run['id']} [{run['status']}]: {run['goal']} — {tasks.summary(run)}")
        for t in run["tasks"]:
            if t["status"] != "done":
                who = " (worker running)" if t["worker_thread"] else " (judge running)" if t["judge_threads"] else ""
                text.append(f"  {t['id']} [{t['status']}] {t['title']}{who}")
    else:
        text.append(f"no run for '{rid}'")
    text.append(f"open blockers: {len(open_b)}")
    _out(args, {"readiness": r["level"], "run": run and tasks.summary(run), "open_blockers": len(open_b)}, "\n".join(text))
    return 0


def cmd_stats(args) -> int:
    root = require_root()
    events = read_jsonl(state_dir(root) / "events.jsonl")
    verifies = [e for e in events if e["type"] == "verify"]
    task_ev = [e for e in events if e["type"] == "task"]
    secs = sorted(e.get("seconds", 0) for e in verifies if not e.get("cached"))
    data = {
        "gate_runs": sum(1 for e in events if e["type"] == "gate"),
        "gate_failures": sum(1 for e in events if e["type"] == "gate" and not e.get("ok")),
        "verify_runs": len(verifies), "verify_seconds_p50": secs[len(secs) // 2] if secs else 0,
        "verify_seconds_max": secs[-1] if secs else 0,
        "flaky_events": sum(1 for e in events if e["type"] == "flaky"),
        "judge_accepts": sum(1 for e in task_ev if e.get("action") == "accept"),
        "judge_rejects": sum(1 for e in task_ev if e.get("action") == "reject"),
        "merge_conflicts": sum(1 for e in task_ev if e.get("action") == "conflict"),
        "worker_releases": sum(1 for e in task_ev if e.get("action") == "release"),
        "top_failing_checks": Counter(f for e in verifies for f in e.get("failed", [])).most_common(8),
        "open_blockers": sum(1 for b in blockers.load(root) if b["status"] == "open"),
    }
    text = [f"{k}: {v}" for k, v in data.items() if k != "top_failing_checks"]
    text += ["top failing checks:"] + [f"  {n}× {name}" for name, n in data["top_failing_checks"]]
    _out(args, data, "\n".join(text))
    return 0


# ---------- helpers ----------

def cmd_setup(args) -> int:
    root = require_root()
    h = harness.load(root)
    cmd = h.get("maintenance" if args.maintenance else "setup")
    if not cmd:
        print("nothing configured")
        return 0
    print(f"$ {cmd}", flush=True)
    return subprocess.call(cmd, shell=True, cwd=str(root))


def cmd_exec_check(args) -> int:
    import re as _re

    cmd = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not cmd:
        raise RatchetError("usage: ratchet exec-check --fail-on REGEX -- <command...>")
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
    sys.stdout.write(proc.stdout)
    if proc.returncode != 0:
        return proc.returncode
    if args.fail_on and any(_re.search(args.fail_on, l) for l in proc.stdout.splitlines()):
        print(f"[ratchet exec-check] output matched /{args.fail_on}/; failing", file=sys.stderr)
        return 1
    return 0


def cmd_junit_unittest(args) -> int:
    from . import junit_unittest

    return junit_unittest.main(args.start)


def cmd_port(args) -> int:
    print(report.free_port())
    return 0


def cmd_hook(args) -> int:
    return hooks.main(args.event)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ratchet", description="Set up and run a verifiable development cycle inside a repo. "
                                "Built on T3 Code; works with any harness it hosts.")
    p.add_argument("--version", action="version", version=f"ratchet {VERSION} (kernel {harness.KERNEL})")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, fn, help_):
        sp = sub.add_parser(name, help=help_)
        sp.set_defaults(fn=fn)
        return sp

    sp = add("new", cmd_new, "fresh repo with a verified walking skeleton: ratchet new <stack> <dir>")
    sp.add_argument("stack", nargs="?")
    sp.add_argument("dir", nargs="?")
    sp.add_argument("--name")
    sp.add_argument("--list", action="store_true")
    sp.add_argument("--no-prove", action="store_true")
    sp.add_argument("--json", action="store_true")
    sp = add("init", cmd_init, "generate this repo's harness from its own tools, then prove it")
    sp.add_argument("--force", action="store_true")
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--no-prove", action="store_true")
    sp.add_argument("--probe", type=int, default=3, help="mutation probes per test check (0 to skip)")
    sp.add_argument("--json", action="store_true")
    sp = add("prove", cmd_prove, "prove checks: green on the base, red on a canary, sensitive to mutation")
    sp.add_argument("--check")
    sp.add_argument("--probe", type=int, default=3)
    sp = add("baseline", cmd_baseline, "record every check's per-test results for the current clean state")
    sp.add_argument("--fast", action="store_true")
    add("doctor", cmd_doctor, "sanity-check the harness (scripts, tools, guides, kernel version)")
    sp = add("readiness", cmd_readiness, "what the harness covers and its gaps (levels 0-5)")
    sp.add_argument("--write", action="store_true", help="also write .ratchet/readiness.json")
    sp.add_argument("--json", action="store_true")
    sp = add("garden", cmd_garden, "propose harness tasks from gaps and friction")
    sp.add_argument("--plan", action="store_true", help="print the tasks as JSON for `ratchet task add --from-json -`")
    sp.add_argument("--json", action="store_true")
    sp = add("upgrade", cmd_upgrade, "migrate .ratchet/harness.json to this kernel version")
    sp.add_argument("--check", action="store_true")

    sp = add("verify", cmd_verify, "per-test verification of this change against its base (cached)")
    sp.add_argument("--full", action="store_true")
    sp.add_argument("--all", action="store_true")
    sp.add_argument("--task")
    sp.add_argument("--run")
    sp.add_argument("--no-cache", action="store_true")
    sp.add_argument("-v", "--verbose", action="store_true")
    sp.add_argument("--json", action="store_true")
    sp = add("proof", cmd_proof, "proof f2p [task] | proof mutate [task] | proof canary <check>")
    sp.add_argument("kind", choices=["f2p", "mutate", "canary"])
    sp.add_argument("target", nargs="?")
    sp.add_argument("--start", help="base commit (default: the task's start, or the merge-base)")
    sp.add_argument("--run")
    sp.add_argument("--limit", type=int)
    sp.add_argument("--json", action="store_true")
    sp = add("exercise", cmd_exercise, "exercise list | exercise run <name> [--base REF | --task ID]")
    sp.add_argument("action", choices=["list", "run"])
    sp.add_argument("name", nargs="?")
    sp.add_argument("--base")
    sp.add_argument("--task")
    sp.add_argument("--run")
    sp.add_argument("--json", action="store_true")
    sp = add("diff-image", cmd_diff_image, "pixel diff of two PNGs")
    sp.add_argument("a")
    sp.add_argument("b")
    sp.add_argument("--out")
    sp.add_argument("--threshold", type=int, default=16)
    sp.add_argument("--max-ratio", type=float)
    sp = add("affected", cmd_affected, "changed files, triggered checks and touched products")
    sp.add_argument("--json", action="store_true")
    sp = add("tamper", cmd_tamper, "check-weakening changes on this branch")
    sp.add_argument("--json", action="store_true")
    sp = add("allow", cmd_allow, "accept a tamper finding (or a removed/skipped test) with a reason")
    sp.add_argument("ids", nargs="*")
    sp.add_argument("--all", action="store_true")
    sp.add_argument("--test", help="check::test name to allow removing or skipping")
    sp.add_argument("--reason", required=True)

    sp = add("tick", cmd_tick, "coordinator: the next actions")
    sp.add_argument("--run")
    sp.add_argument("--json", action="store_true")
    sp = add("brief", cmd_brief, "self-contained prompt: coordinator|plan|worker <id>|judge <id>|contract|final|init|new|garden")
    sp.add_argument("kind", choices=["coordinator", "plan", "worker", "judge", "contract", "final", "init", "new", "garden"])
    sp.add_argument("id", nargs="?")
    sp.add_argument("--run")
    sp.add_argument("--goal")
    sp = add("start", cmd_start, "launch a coordinator thread in T3 Code (any provider)")
    sp.add_argument("goal", nargs="+")
    sp.add_argument("--provider")
    sp.add_argument("--model")
    sp.add_argument("--base")
    sp.add_argument("--dry-run", action="store_true")
    sp = add("release", cmd_release, "free the slot held by a finished thread")
    sp.add_argument("--thread", required=True)
    sp.add_argument("--reason")
    sp.add_argument("--run")
    sp = add("run", cmd_run, "run: start|show|dispatch|contract|final|pr|pushed|ci|close")
    sp.add_argument("action", choices=["start", "show", "dispatch", "contract", "final", "pr", "pushed", "ci", "close"],
                    nargs="?", default="show")
    sp.add_argument("value", nargs="?")
    sp.add_argument("--goal")
    sp.add_argument("--thread")
    sp.add_argument("--details")
    sp.add_argument("--notes", "--reason", dest="text")
    sp.add_argument("--run")
    sp.add_argument("--json", action="store_true")
    sp = add("task", cmd_task, "task: add|edit|list|next|show|dispatch|start|submit|accept|reject|merge|rollback|block|reset")
    sp.add_argument("action", choices=["add", "edit", "list", "next", "show", "dispatch", "start", "submit", "accept",
                                       "reject", "merge", "rollback", "block", "reset"])
    sp.add_argument("id", nargs="?")
    sp.add_argument("--run")
    sp.add_argument("--role", choices=["worker", "judge"])
    sp.add_argument("--thread")
    sp.add_argument("--json", action="store_true")
    sp.add_argument("--from-json", dest="json_input")
    sp.add_argument("--title")
    sp.add_argument("--goal")
    sp.add_argument("--kind")
    sp.add_argument("--criteria", action="append")
    sp.add_argument("--verify", action="append")
    sp.add_argument("--packages", action="append")
    sp.add_argument("--deps", action="append")
    sp.add_argument("--summary", "--notes", "--reason", dest="text")
    sp.add_argument("--by")

    sp = add("evidence", cmd_evidence, "evidence add <task|final> <file> --note | evidence list")
    sp.add_argument("action", choices=["add", "list"])
    sp.add_argument("target", nargs="?")
    sp.add_argument("path", nargs="?")
    sp.add_argument("--note")
    sp.add_argument("--run")
    sp.add_argument("--json", action="store_true")
    sp = add("report", cmd_report, "run report: markdown, or --html for T3's html_render")
    sp.add_argument("--html", action="store_true")
    sp.add_argument("--out")
    sp.add_argument("--run")
    sp = add("log", cmd_log, "append to the run's progress log")
    sp.add_argument("text", nargs="+")
    sp.add_argument("--run")
    sp = add("blocker", cmd_blocker, "friction for the gardener: add|list|resolve")
    sp.add_argument("action", choices=["add", "list", "resolve"])
    sp.add_argument("text", nargs="*")
    sp.add_argument("--kind", default="other", choices=blockers.KINDS)
    sp.add_argument("--note")
    sp.add_argument("--all", action="store_true")
    sp.add_argument("--json", action="store_true")
    sp = add("status", cmd_status, "harness, run and friction at a glance")
    sp.add_argument("--run")
    sp.add_argument("--json", action="store_true")
    sp = add("stats", cmd_stats, "loop health")
    sp.add_argument("--json", action="store_true")
    sp = add("setup", cmd_setup, "run the harness setup (or --maintenance) command")
    sp.add_argument("--maintenance", action="store_true")
    sp = add("exec-check", cmd_exec_check, "run a command; also fail when its output matches --fail-on")
    sp.add_argument("--fail-on")
    sp.add_argument("command", nargs=argparse.REMAINDER)
    sp = add("junit-unittest", cmd_junit_unittest, "run stdlib unittest discovery and write JUnit to $RATCHET_OUT")
    sp.add_argument("start", nargs="?", default=".")
    add("port", cmd_port, "print a free local TCP port")
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
