"""Catch the usual ways an agent "passes" by weakening the checks instead of fixing the code."""
from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from pathlib import Path

from . import gitstate
from .util import now, read_json, short_hash, state_dir, write_json

TEST_PATH = re.compile(
    r"(^|/)(tests?|__tests__|specs?|e2e)/|[._-](test|spec)\.[A-Za-z0-9]+$|_test\.(go|py|rs)$|(^|/)test_[^/]*\.py$"
)

SKIP_MARKERS = [
    (r"\b(it|test|describe|suite|context)\.(skip|only|todo)\b", "skip/only marker"),
    (r"\b(xit|xtest|xdescribe|fit|fdescribe)\s*\(", "skip/focus marker"),
    (r"@pytest\.mark\.(skip|skipif|xfail)\b|\bpytest\.skip\(|@unittest\.skip", "python skip"),
    (r"#\[ignore\b", "rust #[ignore]"),
    (r"\bt\.Skip(Now|f)?\(", "go t.Skip"),
    (r"@(Disabled|Ignore)\b", "junit disable"),
]

SUPPRESSIONS = [
    (r"@ts-ignore|@ts-nocheck", "ts suppression"),
    (r"eslint-disable", "eslint suppression"),
    (r"#\s*type:\s*ignore|#\s*noqa|#\s*pyright:\s*ignore", "python suppression"),
    (r"#!?\[allow\((clippy|warnings|dead_code|unused)", "rust allow"),
    (r"//\s*nolint", "go nolint"),
]


@dataclass
class Finding:
    id: str
    kind: str
    file: str
    line: int
    text: str


def _glob_match(path: str, pattern: str) -> bool:
    from .harness import glob_match

    return glob_match(path, pattern)


def scan(root: Path, cfg: dict, since: str, tree: str, harness_task: bool = False) -> list[Finding]:
    """harness_task: the change is a reviewed harness task, so protected harness files may change."""
    findings: list[Finding] = []
    protect = [] if harness_task else (cfg.get("protect") or [])
    ignore = cfg.get("tamper_ignore") or []
    for ch in gitstate.changes(root, since, tree):
        if any(_glob_match(ch.path, g) for g in ignore):
            continue
        if ch.status == "D" and TEST_PATH.search(ch.path):
            findings.append(Finding(short_hash("deleted-test", ch.path), "deleted test file", ch.path, 0, ch.path))
        if any(_glob_match(ch.path, g) for g in protect):
            findings.append(Finding(short_hash("protected", ch.path, ch.status), "protected path changed",
                                    ch.path, 0, f"{ch.status} {ch.path}"))
    for added in gitstate.added_lines(root, since, tree):
        if any(_glob_match(added.path, g) for g in ignore):
            continue
        for patterns in (SKIP_MARKERS, SUPPRESSIONS):
            for pattern, kind in patterns:
                if re.search(pattern, added.text):
                    findings.append(Finding(short_hash(kind, added.path, added.text.strip()), kind,
                                            added.path, added.line, added.text.strip()))
                    break
    return findings


def _allow_path(root: Path) -> Path:
    # Repo-wide (shared by every worktree): a worker's allowance must be visible to the judge
    # on its detached checkout and to the coordinator after the merge.
    return state_dir(root) / "allowances.json"


def allowances(root: Path) -> dict:
    return read_json(_allow_path(root), {}) or {}


def unallowed(root: Path, cfg: dict, since: str, tree: str, harness_task: bool = False) -> list[Finding]:
    allowed = allowances(root)
    return [f for f in scan(root, cfg, since, tree, harness_task) if f.id not in allowed]


def allow(root: Path, cfg: dict, ids: list[str], reason: str, allow_all: bool = False) -> list[Finding]:
    if not reason.strip():
        raise ValueError("an allowance needs a reason")
    tree = gitstate.tree_id(root)
    since = gitstate.merge_base(root, cfg)
    current = {f.id: f for f in scan(root, cfg, since, tree)}
    chosen = list(current.values()) if allow_all else [current[i] for i in ids if i in current]
    missing = [] if allow_all else [i for i in ids if i not in current]
    if missing:
        raise ValueError(f"no current finding with id {', '.join(missing)} (run `ratchet tamper`)")
    store = allowances(root)
    for f in chosen:
        store[f.id] = {"kind": f.kind, "file": f.file, "text": f.text, "reason": reason, "at": now()}
    write_json(_allow_path(root), store)
    return chosen
