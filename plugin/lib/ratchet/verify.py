"""Verification against the base, per test.

A change is green when, for every check its paths trigger:
  - no test that passed on the base fails, errors, disappears or becomes skipped now;
  - every new test passes;
  - enough tests ran (no fake passes from an empty or broken run);
  - opaque checks (exit code only) exit 0, unless they were already failing on the base;
and the tamper scan finds nothing unallowed. A failing test gets re-run once to tell
flaky from real; flakes are logged instead of blocking.
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from . import gitstate, harness, store, tamper
from .results import ERROR, FAIL, PASS, SKIP
from .runner import Run, machine_slot, run_check
from .util import git_ok, log_event, now, short_hash, state_dir, write_json


@dataclass
class CheckVerdict:
    name: str
    ok: bool
    code: int
    seconds: float
    log: str
    excerpt: str
    total: int
    format: str
    baseline: str                      # "per-test" | "exit" | "none"
    regressions: list = field(default_factory=list)
    new_tests: list = field(default_factory=list)
    new_failing: list = field(default_factory=list)
    removed: list = field(default_factory=list)
    newly_skipped: list = field(default_factory=list)
    flaky: list = field(default_factory=list)
    pre_existing: list = field(default_factory=list)
    problems: list = field(default_factory=list)
    cached: bool = False


@dataclass
class Verdict:
    ok: bool
    tree: str
    base_tree: str
    level: str
    changed: Optional[list]
    checks: list = field(default_factory=list)
    tamper: list = field(default_factory=list)
    seconds: float = 0.0
    at: str = ""

    @property
    def new_tests(self) -> list:
        return [f"{c.name}::{t}" for c in self.checks for t in c.new_tests]


def commit_tree(root: Path, ref: str) -> Optional[str]:
    return git_ok(["rev-parse", f"{ref}^{{tree}}"], root)


def default_base_tree(root: Path, h: dict) -> str:
    mb = gitstate.merge_base(root, h)
    return commit_tree(root, mb) or mb


def _allowed(root: Path, kind: str, check: str, test: str) -> bool:
    return short_hash(kind, check, test) in tamper.allowances(root)


def compare(root: Path, check: harness.Check, head: Run, base: Optional[Run]) -> CheckVerdict:
    hr = head.results
    cv = CheckVerdict(check.name, True, head.code, head.seconds, head.log, head.excerpt, hr.count(), hr.format,
                      "none" if base is None else ("exit" if base.results.opaque else "per-test"))
    if hr.opaque:
        if head.code != 0:
            if base is not None and base.code != 0:
                cv.pre_existing.append(f"exit {head.code}")
                cv.problems.append(f"exit {head.code}, but it was already failing on the base (not a regression)")
            else:
                cv.ok = False
                cv.problems.append(f"exit code {head.code}")
        return cv
    if cv.total < check.min_tests:
        cv.ok = False
        cv.problems.append(f"only {cv.total} test(s) ran (this check expects at least {check.min_tests}): "
                           "an empty or broken run is not a pass")
    if base is not None and not base.results.opaque:
        bt, ht = base.results.tests, hr.tests
        cv.regressions = sorted(n for n, s in bt.items() if s == PASS and ht.get(n) in (FAIL, ERROR))
        cv.removed = sorted(n for n, s in bt.items() if s == PASS and n not in ht
                            and not _allowed(root, "removed-test", check.name, n))
        cv.newly_skipped = sorted(n for n, s in bt.items() if s == PASS and ht.get(n) == SKIP
                                  and not _allowed(root, "skipped-test", check.name, n))
        cv.new_tests = sorted(n for n in ht if n not in bt)
        cv.new_failing = sorted(n for n in cv.new_tests if ht[n] in (FAIL, ERROR))
        cv.pre_existing = sorted(n for n, s in ht.items() if s in (FAIL, ERROR) and bt.get(n) in (FAIL, ERROR))
        for label, items in (("regressed (passed on the base)", cv.regressions), ("new test failing", cv.new_failing),
                             ("removed (passed on the base)", cv.removed), ("newly skipped", cv.newly_skipped)):
            if items:
                cv.ok = False
                cv.problems.append(f"{len(items)} {label}: " + ", ".join(items[:8]) +
                                   (f" (+{len(items) - 8})" if len(items) > 8 else ""))
        if head.code != 0 and cv.ok and not cv.pre_existing:
            cv.ok = False
            cv.problems.append(f"exit code {head.code} with no failing test reported")
    else:
        failing = sorted(hr.failing())
        if failing:
            cv.ok = False
            cv.problems.append(f"{len(failing)} failing: " + ", ".join(failing[:8]))
        if head.code != 0 and not failing:
            cv.ok = False
            cv.problems.append(f"exit code {head.code}")
        if base is None:
            cv.problems.append("no baseline recorded for this check yet: judged on its own "
                               "(run `ratchet baseline` on a clean base)")
    return cv


def _run(root: Path, h: dict, check: harness.Check, tree: str, use_cache: bool) -> tuple[Run, bool]:
    if use_cache:
        hit = store.get(root, tree, check.name)
        if hit:
            return hit, True
    with machine_slot(h):
        run = run_check(root, h, check, tree[:16])
    store.put(root, tree, run)
    return run, False


def verify(root: Path, h: dict, *, level: str = "fast", base_tree: Optional[str] = None, all_checks: bool = False,
           harness_task: bool = False, use_cache: bool = True, exempt: frozenset = frozenset()) -> Verdict:
    started = time.time()
    tree = gitstate.tree_id(root)
    base_tree = base_tree or default_base_tree(root, h)
    changed = None if all_checks else (store.changed_between(root, base_tree, tree) or [])
    selected = [c for c in harness.checks(h, level) if c.triggered_by(changed)]
    verdicts = []
    for check in selected:
        head, cached = _run(root, h, check, tree, use_cache)
        base = store.find(root, base_tree, check) if base_tree != tree else None
        cv = compare(root, check, head, base)
        reruns = int(h["limits"].get("flaky_reruns", 1))
        if not cv.ok and (cv.regressions or cv.new_failing) and reruns > 0 and not cached:
            with machine_slot(h):
                again = run_check(root, h, check, tree[:16] + "-rerun")
            suspects = set(cv.regressions) | set(cv.new_failing)
            flaky = sorted(n for n in suspects if again.results.tests.get(n) == PASS)
            if flaky:
                merged = Run(head.check, head.code, head.seconds, head.log, head.results, head.excerpt)
                for n in flaky:
                    merged.results.tests[n] = PASS
                if not (suspects - set(flaky)):
                    merged.code = 0
                cv = compare(root, check, merged, base)
                cv.flaky = flaky
                cv.problems.append(f"{len(flaky)} flaky (failed, then passed on re-run): {', '.join(flaky[:5])}")
                log_event(root, "flaky", check=check.name, tests=flaky)
                store.put(root, tree, merged)
        cv.cached = cached
        verdicts.append(cv)
    since = base_tree
    findings = tamper.unallowed(root, h, since, tree, harness_task, exempt) if base_tree != tree else []
    v = Verdict(all(c.ok for c in verdicts) and not findings, tree, base_tree, level, changed, verdicts,
                [asdict(f) for f in findings], round(time.time() - started, 2), now())
    write_json(state_dir(root) / "verdicts" / f"{tree[:16]}-{base_tree[:12]}-{level}.json", asdict(v))
    log_event(root, "verify", ok=v.ok, level=level, seconds=v.seconds, tree=tree,
              failed=[c.name for c in verdicts if not c.ok], tamper=len(findings),
              cached=all(c.cached for c in verdicts) if verdicts else True)
    return v


def baseline(root: Path, h: dict, level: str = "full") -> list[CheckVerdict]:
    """Record results for every check on the current (clean) state, the reference for later changes."""
    tree = gitstate.tree_id(root)
    out = []
    for check in harness.checks(h, level):
        head, cached = _run(root, h, check, tree, use_cache=True)
        cv = compare(root, check, head, None)
        cv.cached = cached
        out.append(cv)
    log_event(root, "baseline", tree=tree, checks=len(out), red=[c.name for c in out if not c.ok])
    return out


def format_verdict(v: Verdict, verbose: bool = False) -> str:
    status = "PASS" if v.ok else "FAIL"
    lines = [f"ratchet verify [{v.level}] {status} — {len(v.checks)} checks, "
             f"{len(v.changed) if v.changed is not None else 'all'} changed files, {v.seconds}s"]
    if not v.checks and not v.tamper:
        lines.append("  no checks triggered by this change")
    for c in v.checks:
        if c.ok and not verbose and not c.flaky:
            continue
        mark = "ok  " if c.ok else "FAIL"
        tests = f"{c.total} tests" if c.format != "exit" else "exit code"
        lines.append(f"  {mark} {c.name} ({tests}, baseline: {c.baseline}, {c.seconds}s{', cached' if c.cached else ''})")
        for p in c.problems:
            lines.append(f"    - {p}")
        if not c.ok and c.excerpt:
            lines.append("    " + c.excerpt.replace("\n", "\n    "))
        if not c.ok:
            lines.append(f"    full log: {c.log}")
    passing = [c for c in v.checks if c.ok]
    if passing and not verbose:
        new = sum(len(c.new_tests) for c in passing)
        lines.append(f"  ok   {len(passing)} checks" + (f" ({new} new tests passing)" if new else ""))
    for f in v.tamper:
        lines.append(f"  TAMPER [{f['id']}] {f['kind']}: {f['file']}:{f['line']}  {f['text'][:120]}")
    if v.tamper:
        lines.append("    Undo it. If it's genuinely required: `ratchet allow <id> --reason \"...\"` (the judge reviews it).")
    return "\n".join(lines)
