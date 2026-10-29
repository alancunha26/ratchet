"""Run one harness check and keep only what an agent needs to act on."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional

from . import results as R
from .harness import Check
from .util import slug, state_dir

BIN_DIR = str(Path(__file__).resolve().parents[2] / "bin")


@dataclass
class Run:
    check: str
    code: int
    seconds: float
    log: str
    results: R.Results = field(default_factory=R.Results)
    excerpt: str = ""

    def to_dict(self) -> dict:
        return {"check": self.check, "code": self.code, "seconds": self.seconds, "log": self.log,
                "results": self.results.to_dict(), "excerpt": self.excerpt}

    @classmethod
    def from_dict(cls, d: dict) -> "Run":
        return cls(d["check"], d["code"], d.get("seconds", 0), d.get("log", ""),
                   R.Results.from_dict(d.get("results")), d.get("excerpt", ""))


_RELEVANT = re.compile(
    r"(?i)(error|fail|failed|failure|panic|exception|traceback|assert|expected|received|"
    r"✗|✘|×|not ok|cannot|undefined|denied|missing)|:\d+:\d+|^\s+at\s"
)
_NOISE = re.compile(r"\((node:internal|node:async_hooks)|^\s+at .*(node_modules|node:internal)|"
                    r"^npm (error|ERR!) (path|location|workspace|command|code)\b")


def excerpt(output: str, max_lines: int = 40, max_chars: int = 3000) -> str:
    lines = [l.rstrip() for l in output.splitlines() if l.strip() and not _NOISE.search(l)]
    keep: list = []
    for i, line in enumerate(lines):
        if _RELEVANT.search(line):
            for j in (i - 1, i, i + 1):
                if 0 <= j < len(lines) and j not in keep:
                    keep.append(j)
    chosen = [lines[i] for i in sorted(keep)] if keep else lines[-30:]
    if len(chosen) > max_lines:
        chosen = chosen[:max_lines] + [f"... ({len(chosen) - max_lines} more relevant lines in the log)"]
    text = "\n".join(chosen)
    return text[:max_chars] + ("\n..." if len(text) > max_chars else "")


@contextmanager
def machine_slot(h: dict) -> Iterator[None]:
    """Cap concurrent verification across every worktree on this machine."""
    try:
        import fcntl
    except ImportError:  # pragma: no cover
        yield
        return
    slots = int(os.environ.get("RATCHET_SLOTS") or h["limits"].get("machine_slots") or 2)
    d = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "ratchet" / "slots"
    d.mkdir(parents=True, exist_ok=True)
    handles = [open(d / f"{i}.lock", "w") for i in range(max(1, slots))]
    held = None
    try:
        while held is None:
            for fh in handles:
                try:
                    fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    held = fh
                    break
                except BlockingIOError:
                    continue
            if held is None:
                time.sleep(0.5)
        yield
    finally:
        if held is not None:
            fcntl.flock(held, fcntl.LOCK_UN)
        for fh in handles:
            fh.close()


def env_for(root: Path, out_dir: Path, extra: Optional[dict] = None) -> dict:
    return {**os.environ, "CI": "1", "FORCE_COLOR": "0", "NO_COLOR": "1", "RATCHET": "1",
            "RATCHET_OUT": str(out_dir), "RATCHET_ROOT": str(root),
            "PATH": BIN_DIR + os.pathsep + os.environ.get("PATH", ""), **(extra or {})}


def run_check(root: Path, h: dict, check: Check, tag: str, timeout: Optional[int] = None) -> Run:
    """Run a check. `tag` separates output dirs (a tree id, 'canary', 'f2p', ...)."""
    out_dir = state_dir(root) / "out" / slug(tag) / slug(check.name)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    log = out_dir.parent / f"{slug(check.name)}.log"
    timeout = int(timeout or check.timeout or h["limits"]["check_timeout_sec"])
    start = time.time()
    try:
        proc = subprocess.run(check.run, shell=True, cwd=str(root / check.cwd), env=env_for(root, out_dir),
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace",
                              timeout=timeout)
        out, code = proc.stdout, proc.returncode
    except subprocess.TimeoutExpired as exc:
        out = (exc.stdout if isinstance(exc.stdout, str) else "") + f"\n[ratchet] timed out after {timeout}s"
        code = 124
    log.write_text(f"$ {check.run}\n(cwd: {check.cwd})\n\n{out}")
    res = R.collect(out_dir, check.results)
    return Run(check.name, code, round(time.time() - start, 2), str(log), res, "" if code == 0 else excerpt(out))
