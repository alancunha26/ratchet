"""The harness ratchets too: readiness, gardening and kernel upgrades.

readiness  what the harness covers today and what's missing (levels 0-5)
garden     turns gaps and friction (flakes, slow checks, judge rejects, blockers) into harness tasks
upgrade    migrates .ratchet/harness.json to the kernel version this ratchet understands
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Callable

from . import blockers, harness, proof, workspace
from .util import read_jsonl, state_dir, write_json

LEVELS = {
    0: "no harness checks",
    1: "checks exist and pass on the base",
    2: "every package has a per-test check proven able to fail",
    3: "tests are mutation-sensitive and every product has an exercise guide",
    4: "every product has a replayable exercise",
    5: "exercises compare against the base (visual diffs or budgets)",
}


def readiness(root: Path, h: dict) -> dict:
    pkgs = workspace.discover(root, {"packages": "auto"})
    checks = harness.checks(h, "full")
    proofs = proof.proofs(root)
    gaps: list[dict] = []

    def per_test(c: harness.Check) -> bool:
        p = proofs.get(c.name) or {}
        return p.get("format", "exit") != "exit" if p else c.results not in ("exit",) and c.min_tests > 0

    green = all((proofs.get(c.name) or {}).get("green") for c in checks) if checks else False
    if not checks:
        gaps.append({"kind": "no-checks", "what": "the harness has no checks", "fix": "run `ratchet init` or add checks"})
    for c in checks:
        p = proofs.get(c.name)
        if not p:
            gaps.append({"kind": "unproven", "check": c.name, "what": f"check {c.name} was never proven",
                         "fix": f"`ratchet prove --check {c.name}`"})
            continue
        if not p.get("green"):
            gaps.append({"kind": "red-baseline", "check": c.name, "what": f"check {c.name} fails on the base",
                         "fix": "fix it, or quarantine the failing tests with a blocker explaining why"})
        if c.min_tests > 0 and p.get("format") == "exit":
            gaps.append({"kind": "opaque", "check": c.name, "what": f"check {c.name} reports no per-test results",
                         "fix": "make its runner write JUnit XML (or go -json / jest json / result.json) into $RATCHET_OUT"})
        can = (p.get("canary") or {})
        if p.get("green") and c.min_tests > 0 and can.get("ok") is not True:
            gaps.append({"kind": "cannot-fail", "check": c.name,
                         "what": f"check {c.name} " + ("has no canary" if can.get("ok") is None else "STAYED GREEN on its canary"),
                         "fix": "give it a canary test in the framework's style and make sure the runner picks it up"})
        mut = p.get("mutation")
        if mut and mut["killed"] == 0 and (mut["killed"] + mut["survived"]) > 0:
            gaps.append({"kind": "weak-tests", "check": c.name,
                         "what": f"no mutation probe was caught by {c.name}'s tests", "fix": "add tests that pin behaviour"})
    for pkg in pkgs:
        probe = [f"{pkg.dir}/x"] if pkg.dir else ["x"]
        if not any(c.triggered_by(probe) and c.min_tests > 0 for c in checks):
            gaps.append({"kind": "uncovered", "package": pkg.label, "what": f"package {pkg.label} has no test check",
                         "fix": "add a per-test check for it (and a canary)"})
    prods = h.get("products") or []
    for prod in prods:
        guide = root / (prod.get("exercise") or f".ratchet/exercise/{prod['name']}.md")
        if not guide.exists():
            gaps.append({"kind": "no-guide", "product": prod["name"], "what": f"product {prod['name']} has no exercise guide",
                         "fix": f"write {guide.relative_to(root)}"})
        if not prod.get("exercises") and prod.get("kind") not in ("library",):
            gaps.append({"kind": "no-exercise", "product": prod["name"],
                         "what": f"product {prod['name']} has no replayable exercise script",
                         "fix": "add one under .ratchet/exercise/ that writes reward.json (and screenshots/frames/transcripts)"})
        elif prod.get("exercises") and not any(e.get("compare") or e.get("budgets") for e in prod["exercises"]):
            gaps.append({"kind": "no-baseline-compare", "product": prod["name"],
                         "what": f"product {prod['name']}'s exercises don't compare against the base",
                         "fix": "add `compare.images` and/or `budgets` to its exercises"})
    kinds = Counter(g["kind"] for g in gaps)
    level = 0
    if checks and green:
        level = 1
        if not (kinds["unproven"] or kinds["opaque"] or kinds["cannot-fail"] or kinds["uncovered"] or kinds["red-baseline"]):
            level = 2
            if not (kinds["weak-tests"] or kinds["no-guide"]):
                level = 3
                if not kinds["no-exercise"]:
                    level = 4
                    if not kinds["no-baseline-compare"]:
                        level = 5
    return {"level": level, "meaning": LEVELS[level], "next": LEVELS.get(level + 1), "gaps": gaps,
            "checks": len(checks), "packages": len(pkgs), "products": len(prods)}


def format_readiness(r: dict) -> str:
    lines = [f"readiness level {r['level']}/5: {r['meaning']}" + (f" (next: {r['next']})" if r["next"] else "")]
    lines.append(f"  {r['checks']} checks · {r['packages']} packages · {r['products']} products")
    for g in r["gaps"]:
        lines.append(f"  gap [{g['kind']}] {g['what']} → {g['fix']}")
    if not r["gaps"]:
        lines.append("  no gaps")
    return "\n".join(lines)


def garden_plan(root: Path, h: dict) -> list[dict]:
    """Harness tasks the gardener should run, most valuable first."""
    r = readiness(root, h)
    events = read_jsonl(state_dir(root) / "events.jsonl")
    tasks: list[dict] = []
    order = ["red-baseline", "cannot-fail", "opaque", "uncovered", "unproven", "weak-tests", "no-guide",
             "no-exercise", "no-baseline-compare", "no-checks"]
    for g in sorted(r["gaps"], key=lambda g: order.index(g["kind"]) if g["kind"] in order else 99):
        target = g.get("check") or g.get("package") or g.get("product") or "harness"
        crit = {
            "red-baseline": [f"`ratchet prove --check {target}` shows {target} green on the base",
                             "every quarantined test has a blocker explaining why"],
            "cannot-fail": [f"`ratchet proof canary {target}` reports 'red as expected'"],
            "opaque": [f"check {target} reports per-test results (`ratchet prove --check {target}` shows a test count)",
                       f"`ratchet proof canary {target}` reports 'red as expected'"],
            "uncovered": [f"a per-test check covers {target} and is proven able to fail"],
            "unproven": [f"`ratchet prove --check {target}` records a proof"],
            "weak-tests": [f"a mutation probe on {target}'s code is killed (`ratchet prove --check {target}`)"],
            "no-guide": [f"`.ratchet/exercise/{target}.md` explains how to run, exercise and collect evidence for {target}"],
            "no-exercise": [f"the harness declares a replayable exercise for {target}",
                            "`ratchet exercise run <name>` passes and writes reward.json with at least one metric"],
            "no-baseline-compare": [f"{target}'s exercise compares images or budgets against the base",
                                    "`ratchet exercise run <name> --base <ref>` reports the comparison"],
            "no-checks": ["the harness has at least one proven, per-test check"],
        }.get(g["kind"], [g["fix"]])
        tasks.append({"title": f"harness: {g['what']}", "kind": "harness", "goal": g["fix"], "criteria": crit,
                      "verify": ["ratchet prove", "ratchet readiness"], "packages": [".ratchet"]})
    flaky = Counter(t for e in events if e["type"] == "flaky" for t in e.get("tests", []))
    for test, n in flaky.most_common(3):
        if n >= 2:
            tasks.append({"title": f"harness: flaky test {test} ({n} flakes)", "kind": "fix",
                          "goal": "make it deterministic, or quarantine it with a blocker", "expects_test": False,
                          "criteria": [f"{test} passes 10 consecutive runs, or is quarantined with a linked blocker"],
                          "verify": ["ratchet verify --no-cache (repeat)"]})
    slow = [e for e in events if e["type"] == "verify" and not e.get("cached") and e.get("seconds", 0) > 120]
    if len(slow) >= 3:
        tasks.append({"title": "harness: the fast gate is slow", "kind": "harness",
                      "goal": f"{len(slow)} uncached verifies took over 2 minutes",
                      "criteria": ["a typical change's `ratchet verify` finishes in under 2 minutes",
                                   "no check lost per-test results or its canary proof"],
                      "verify": ["ratchet verify --no-cache", "ratchet prove"], "packages": [".ratchet"]})
    rejects = [e for e in events if e["type"] == "task" and e.get("action") == "reject"]
    if len(rejects) >= 3:
        tasks.append({"title": "harness: recurring judge rejections", "kind": "harness",
                      "goal": "read the reject notes (`ratchet stats`, task notes) and encode the recurring reason "
                              "as a check, a lint rule or a judge.md rule",
                      "criteria": ["the recurring reason is caught mechanically or written in .ratchet/judge.md"],
                      "verify": ["ratchet stats"], "packages": [".ratchet"]})
    for b in blockers.load(root):
        if b["status"] == "open" and b["kind"] in ("slow", "flaky", "tooling", "env", "spec"):
            tasks.append({"title": f"harness: blocker {b['id']} [{b['kind']}]", "kind": "harness", "goal": b["text"],
                          "criteria": [f"blocker {b['id']} is resolved (`ratchet blocker resolve {b['id']}`) with the fix"],
                          "verify": ["ratchet blocker list"], "packages": [".ratchet"]})
    return tasks


# ---------- kernel upgrades ----------

Migration = Callable[[dict], tuple[dict, list[str]]]
MIGRATIONS: dict[str, tuple[str, Migration]] = {
    # "1": ("2", migrate_1_to_2),   # from-version -> (to-version, fn returning (harness, follow-up tasks))
}


def upgrade(root: Path, apply: bool = True) -> dict:
    raw = harness.read_raw(root)
    version = str(raw.get("kernel", "1"))
    steps, followups = [], []
    while version in MIGRATIONS:
        to, fn = MIGRATIONS[version]
        raw, todo = fn(raw)
        raw["kernel"] = to
        steps.append(f"{version} → {to}")
        followups += todo
        version = to
    if apply and steps:
        write_json(harness.path(root), raw)
    return {"from": str(harness.read_raw(root).get("kernel", "1")) if not apply else None, "to": version,
            "current": harness.KERNEL, "steps": steps, "followups": followups,
            "up_to_date": version == harness.KERNEL}
