"""Put the checkout back into its "before the change" state, in place, then restore it.

Running the base in place (instead of a fresh worktree) keeps installed dependencies,
build caches and generated files, so it works in monorepos without a reinstall.
Used to prove new tests fail without the change, and to capture base-vs-change evidence.
"""
from __future__ import annotations

import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .tamper import TEST_PATH
from .util import RatchetError, git, head_sha


def is_test_path(path: str) -> bool:
    return bool(TEST_PATH.search(path))


@contextmanager
def at_base(root: Path, start: str, keep_tests: bool = True) -> Iterator[list]:
    """Revert every file the change touched to `start` (tests too unless keep_tests). Requires a clean tree."""
    if git(["status", "--porcelain"], root).strip():
        raise RatchetError("the checkout must be clean (commit first) to compare against the base")
    head = head_sha(root)
    changes = git(["diff", "--name-status", "--no-renames", "-z", start, "HEAD"], root).split("\0")
    pairs = [(changes[i][0], changes[i + 1]) for i in range(0, len(changes) - 1, 2) if changes[i]]
    reverted = []
    try:
        for status, path in pairs:
            if keep_tests and is_test_path(path):
                continue
            if status == "A":
                (root / path).unlink(missing_ok=True)
            else:
                subprocess.run(["git", "checkout", start, "--", path], cwd=str(root), check=True,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            reverted.append(path)
        yield reverted
    finally:
        subprocess.run(["git", "reset", "-q", "--hard", head], cwd=str(root), check=True)
        if head_sha(root) != head:  # pragma: no cover - defensive
            raise RatchetError(f"failed to restore HEAD {head}")
