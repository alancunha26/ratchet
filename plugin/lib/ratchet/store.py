"""Check results keyed by (working-state tree, check).

A tree id is the hash of a full working state, so a stored result is valid for exactly
that state. A result also stays valid for any other tree whose differences don't touch
the check's scope: that "carry-forward" makes baselines cheap after the first run.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from .harness import Check
from .runner import Run
from .util import git_ok, read_json, slug, state_dir, write_json


def _dir(root: Path) -> Path:
    return state_dir(root) / "results"


def _file(root: Path, tree: str, check: str) -> Path:
    return _dir(root) / tree / f"{slug(check)}.json"


def put(root: Path, tree: str, run: Run) -> None:
    write_json(_file(root, tree, run.check), {"tree": tree, **run.to_dict()})


def get(root: Path, tree: str, check: str) -> Optional[Run]:
    data = read_json(_file(root, tree, check), None)
    return Run.from_dict(data) if data else None


def changed_between(root: Path, a: str, b: str) -> Optional[list]:
    out = git_ok(["diff", "--name-only", "--no-renames", a, b], root)
    return None if out is None else [p for p in out.splitlines() if p]


def find(root: Path, tree: str, check: Check, search: int = 30) -> Optional[Run]:
    """A result valid for `tree`: stored for it, or carried forward from a recent equivalent tree."""
    hit = get(root, tree, check.name)
    if hit:
        return hit
    d = _dir(root)
    if not d.is_dir():
        return None
    candidates = sorted((p for p in d.iterdir() if p.is_dir() and p.name != tree and
                         (p / f"{slug(check.name)}.json").exists()),
                        key=lambda p: os.path.getmtime(p / f"{slug(check.name)}.json"), reverse=True)
    for cand in candidates[:search]:
        diff = changed_between(root, cand.name, tree)
        if diff is not None and not check.triggered_by(diff) and diff != []:
            run = get(root, cand.name, check.name)
            if run:
                put(root, tree, run)  # remember the equivalence
                return run
        elif diff == []:
            run = get(root, cand.name, check.name)
            if run:
                return run
    return None
