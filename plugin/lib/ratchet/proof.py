"""Proofs that checks and tests mean something.

  new tests fail without the change   (F2P: a test is not evidence until it fails on the base)
  canary: a check goes red on a planted failing test   (the check can fail at all)
  mutation: changing the code breaks some test         (the tests actually exercise it)
"""
from __future__ import annotations

import random
import re
import subprocess
from pathlib import Path
from typing import Optional

from . import gitstate, harness, store
from .basestate import at_base, is_test_path
from .results import ERROR, FAIL, PASS
from .runner import machine_slot, run_check
from .util import RatchetError, git, head_sha, log_event, now, read_json, state_dir, write_json


# ---------- F2P ----------

def new_tests_fail_on_base(root: Path, h: dict, start_sha: str, level: str = "fast") -> dict:
    """Run the new tests against the base source (tests kept, sources reverted)."""
    start_tree = git(["rev-parse", f"{start_sha}^{{tree}}"], root).strip()
    tree = gitstate.tree_id(root)
    cache = state_dir(root) / "f2p" / f"{tree[:16]}-{start_tree[:12]}.json"
    hit = read_json(cache, None)
    if hit:
        return hit
    changed = store.changed_between(root, start_tree, tree) or []
    out = {"proving": [], "not_proving": [], "opaque": [], "checked": [], "at": now()}
    plan = []
    for check in harness.checks(h, level):
        if not check.triggered_by(changed):
            continue
        head = store.get(root, tree, check.name)
        base = store.find(root, start_tree, check)
        if head is None:
            raise RatchetError("run `ratchet verify` first: there are no results for this state yet")
        if head.results.opaque or base is None or base.results.opaque:
            out["opaque"].append(check.name)
            continue
        new = [n for n in head.results.tests if n not in base.results.tests and head.results.tests[n] == PASS]
        if new:
            plan.append((check, new))
    if plan:
        with at_base(root, start_sha, keep_tests=True):
            for check, new in plan:
                with machine_slot(h):
                    run = run_check(root, h, check, f"f2p-{tree[:12]}")
                out["checked"].append(check.name)
                for n in new:
                    (out["not_proving"] if run.results.tests.get(n) == PASS else out["proving"]).append(f"{check.name}::{n}")
    write_json(cache, out)
    log_event(root, "f2p", proving=len(out["proving"]), not_proving=len(out["not_proving"]))
    return out


def format_f2p(r: dict) -> str:
    lines = [f"new tests that fail without the change: {len(r['proving'])}"]
    lines += [f"  ✓ {n}" for n in r["proving"][:10]]
    if r["not_proving"]:
        lines.append(f"new tests that pass even without the change (not evidence for it): {len(r['not_proving'])}")
        lines += [f"  · {n}" for n in r["not_proving"][:10]]
    if r["opaque"]:
        lines.append(f"checks without per-test results or a baseline (couldn't check): {', '.join(r['opaque'])}")
    return "\n".join(lines)


# ---------- mutation ----------

OPERATORS = [
    (r"===", "!=="), (r"!==", "==="), (r"(?<![=!<>])==(?!=)", "!="), (r"!=(?!=)", "=="),
    (r"<=", ">"), (r">=", "<"), (r"(?<![<>=\-])<(?![<=])", ">="), (r"(?<![<>=\-])>(?![>=])", "<="),
    (r"&&", "||"), (r"\|\|", "&&"), (r"\band\b", "or"), (r"\bor\b", "and"),
    (r"\btrue\b", "false"), (r"\bfalse\b", "true"), (r"\bTrue\b", "False"), (r"\bFalse\b", "True"),
    (r"(?<=[\w)\]] )\+(?= [\w(])", "-"), (r"(?<=[\w)\]] )-(?= [\w(])", "+"),
    (r"(?<=[\w)\]] )\*(?= [\w(])", "/"), (r"\b0\b", "1"), (r"\b1\b", "0"),
]
_COMMENT = re.compile(r"^\s*(//|#|\*|/\*|--|;)")


def _mutants_for(path: str, lineno: int, text: str) -> list[tuple]:
    if _COMMENT.match(text) or not text.strip() or "import" in text or "require(" in text:
        return []
    out = []
    for pat, rep in OPERATORS:
        m = re.search(pat, text)
        if m:
            out.append((path, lineno, pat, rep, text, text[:m.start()] + rep + text[m.end():]))
    return out


NOT_CODE = (".md", ".json", ".yml", ".yaml", ".toml", ".lock", ".txt", ".sh", ".cfg", ".ini", ".csv", ".svg",
            ".html", ".css")


def _mutable(path: str) -> bool:
    """Source code only: never tests, the harness itself, config, docs or scripts."""
    return not (is_test_path(path) or path.startswith(".ratchet/") or path.endswith(NOT_CODE)
                or path in ("t3.json", "AGENTS.md", "CLAUDE.md"))


def _candidates_changed(root: Path, start: str) -> list[tuple]:
    out = []
    for a in gitstate.added_lines(root, start, "HEAD"):
        if _mutable(a.path):
            out += _mutants_for(a.path, a.line, a.text)[:2]
    return out


def _candidates_existing(root: Path, globs: list, rng: random.Random) -> list[tuple]:
    files = [f for f in git(["ls-files"], root).splitlines()
             if _mutable(f) and any(harness.glob_match(f, g) for g in globs)]
    rng.shuffle(files)
    out = []
    for f in files[:40]:
        try:
            lines = (root / f).read_text().splitlines()
        except (UnicodeDecodeError, FileNotFoundError):
            continue
        for i, text in enumerate(lines, 1):
            out += _mutants_for(f, i, text)[:1]
    rng.shuffle(out)
    return out


def _inflight(root: Path) -> Path:
    return state_dir(root) / "mutation-inflight.json"


def recover(root: Path) -> None:
    """Restore a file left mutated by an interrupted probe."""
    data = read_json(_inflight(root), None)
    if data:
        (root / data["path"]).write_text(data["original"])
        _inflight(root).unlink(missing_ok=True)


def _apply(root: Path, path: str, lineno: int, new_text: str) -> str:
    p = root / path
    original = p.read_text()
    write_json(_inflight(root), {"path": path, "original": original})
    lines = original.split("\n")
    lines[lineno - 1] = new_text
    p.write_text("\n".join(lines))
    return original


def mutate(root: Path, h: dict, *, start: Optional[str] = None, globs: Optional[list] = None,
           limit: Optional[int] = None, seed: int = 7, level: str = "fast") -> dict:
    """Mutate changed lines (start given) or existing code (globs), run triggered checks, count kills."""
    recover(root)
    rng = random.Random(seed)
    limit = int(limit or h["limits"].get("mutants", 8))
    cands = _candidates_changed(root, start) if start else _candidates_existing(root, globs or ["**"], rng)
    seen_lines, picked = set(), []
    for c in cands:
        if (c[0], c[1]) not in seen_lines:
            seen_lines.add((c[0], c[1]))
            picked.append(c)
        if len(picked) >= limit:
            break
    tree = gitstate.tree_id(root)
    report = {"mutants": [], "killed": 0, "survived": 0, "at": now()}
    for i, (path, lineno, pat, rep, before, after) in enumerate(picked):
        checks = [c for c in harness.checks(h, level) if c.triggered_by([path])]
        if not checks:
            continue
        original = _apply(root, path, lineno, after)
        killed_by = None
        try:
            for check in checks:
                head = store.get(root, tree, check.name)
                # Mutants can loop forever: allow a few times the normal duration; a timeout counts as a kill.
                budget = max(15, int((head.seconds if head else 30) * 5) + 10)
                with machine_slot(h):
                    run = run_check(root, h, check, f"mutant-{i}", timeout=budget)
                newly_failing = run.results.failing() - (head.results.failing() if head else set())
                if (run.code != 0 and (head is None or head.code == 0)) or newly_failing:
                    killed_by = check.name
                    break
        finally:
            (root / path).write_text(original)
            _inflight(root).unlink(missing_ok=True)
        report["mutants"].append({"file": path, "line": lineno, "from": before.strip()[:120],
                                  "to": after.strip()[:120], "killed_by": killed_by})
        report["killed" if killed_by else "survived"] += 1
    total = report["killed"] + report["survived"]
    report["score"] = round(report["killed"] / total, 2) if total else None
    log_event(root, "mutate", killed=report["killed"], survived=report["survived"])
    return report


def format_mutation(r: dict) -> str:
    if not r["mutants"]:
        return "mutation: no mutable lines found (nothing to probe)"
    lines = [f"mutation: {r['killed']} killed, {r['survived']} survived (score {r['score']})"]
    for m in r["mutants"]:
        mark = "killed" if m["killed_by"] else "SURVIVED"
        lines.append(f"  {mark:<8} {m['file']}:{m['line']}  `{m['from']}` → `{m['to']}`"
                     + (f"  ({m['killed_by']})" if m["killed_by"] else ""))
    if r["survived"]:
        lines.append("  A surviving mutant is behaviour no test checks.")
    return "\n".join(lines)


# ---------- canary: a check must be able to fail ----------

def canary(root: Path, h: dict, check: harness.Check) -> dict:
    if not check.canary or not check.canary.get("path"):
        return {"check": check.name, "status": "no canary", "ok": None}
    target = root / check.canary["path"]
    if target.exists():
        raise RatchetError(f"canary path {check.canary['path']} already exists")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(check.canary.get("content", ""))
    try:
        with machine_slot(h):
            run = run_check(root, h, check, "canary")
    finally:
        target.unlink(missing_ok=True)
    if run.results.opaque:
        ok = run.code != 0
        how = f"exit code {run.code}"
    else:
        hits = [n for n, s in run.results.tests.items() if "ratchet" in n.lower() and s in (FAIL, ERROR)]
        ok = bool(hits) and run.code != 0
        how = f"canary test reported failing: {bool(hits)}, exit code {run.code}"
    return {"check": check.name, "ok": ok, "status": "red as expected" if ok else "STAYED GREEN", "how": how,
            "log": run.log}


def record_proof(root: Path, name: str, data: dict) -> None:
    path = state_dir(root) / "proofs.json"
    proofs = read_json(path, {}) or {}
    proofs[name] = {**data, "at": now(), "head": head_sha(root)}
    write_json(path, proofs)


def proofs(root: Path) -> dict:
    return read_json(state_dir(root) / "proofs.json", {}) or {}
