"""Snapshot the working state as a git tree and diff it against the branch base."""
from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .util import EMPTY_TREE, git, git_ok, state_dir


def tree_id(root: Path) -> str:
    """Tree hash of the full working state (tracked + untracked, minus ignored files).

    Uses a throwaway index so the user's staging area is never touched. Identical
    working states always produce the same id, which is what makes results cacheable.
    """
    tmp = state_dir(root) / f"index-{os.getpid()}"
    index = git_ok(["rev-parse", "--git-path", "index"], root)
    if index:
        src = Path(index) if Path(index).is_absolute() else root / index
        if src.exists():
            shutil.copy2(src, tmp)  # keep mtime: git relies on it to catch same-second edits
    env = {**os.environ, "GIT_INDEX_FILE": str(tmp)}
    try:
        git(["add", "-A"], root, env=env)
        return git(["write-tree"], root, env=env).strip()
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def resolve_base(root: Path, cfg: dict) -> Optional[str]:
    base = cfg.get("base") or "auto"
    if base != "auto":
        return base if git_ok(["rev-parse", "--verify", "-q", base], root) else None
    origin_head = git_ok(["rev-parse", "--abbrev-ref", "origin/HEAD"], root)
    candidates = [origin_head] if origin_head and origin_head != "origin/HEAD" else []
    candidates += ["origin/main", "origin/master", "main", "master"]
    for ref in candidates:
        if ref and git_ok(["rev-parse", "--verify", "-q", ref], root):
            return ref
    return None


def merge_base(root: Path, cfg: dict) -> str:
    """Commit the branch forked from, or the empty tree when there is no usable base."""
    base = resolve_base(root, cfg)
    if base and git_ok(["rev-parse", "--verify", "-q", "HEAD"], root):
        mb = git_ok(["merge-base", base, "HEAD"], root)
        if mb:
            return mb
    return EMPTY_TREE


@dataclass
class Change:
    status: str  # A, M, D, T
    path: str


def changes(root: Path, since: str, tree: str) -> list[Change]:
    out = git(["diff", "--name-status", "--no-renames", "-z", since, tree], root)
    parts = [p for p in out.split("\0") if p]
    result = []
    for i in range(0, len(parts) - 1, 2):
        result.append(Change(parts[i][0], parts[i + 1]))
    return result


@dataclass
class AddedLine:
    path: str
    line: int
    text: str


_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def added_lines(root: Path, since: str, tree: str) -> list[AddedLine]:
    out = git(["diff", "-U0", "--no-renames", "--no-color", since, tree], root)
    result: list[AddedLine] = []
    path = None
    lineno = 0
    for raw in out.splitlines():
        if raw.startswith("+++ "):
            path = raw[6:] if raw.startswith("+++ b/") else None
            continue
        if raw.startswith("--- ") or raw.startswith("diff --git"):
            continue
        m = _HUNK.match(raw)
        if m:
            lineno = int(m.group(1))
            continue
        if raw.startswith("+") and path:
            result.append(AddedLine(path, lineno, raw[1:]))
            lineno += 1
    return result
