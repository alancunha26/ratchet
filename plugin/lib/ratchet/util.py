"""Small helpers shared by every ratchet module: git, paths, json, time."""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


class RatchetError(Exception):
    """An expected failure with a message meant for the user or agent."""


def git(args: list[str], cwd: Path, *, env: Optional[dict] = None, check: bool = True) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",  # diffs can contain non-UTF-8 bytes (binary-ish files git treats as text)
    )
    if check and proc.returncode != 0:
        raise RatchetError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def git_ok(args: list[str], cwd: Path) -> Optional[str]:
    """Run git and return stripped stdout, or None when it fails."""
    proc = subprocess.run(["git", *args], cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                          errors="replace")
    return proc.stdout.strip() if proc.returncode == 0 else None


def find_root(cwd: Optional[Path] = None) -> Optional[Path]:
    out = git_ok(["rev-parse", "--show-toplevel"], cwd or Path.cwd())
    return Path(out) if out else None


def require_root(cwd: Optional[Path] = None) -> Path:
    root = find_root(cwd)
    if root is None:
        raise RatchetError("not inside a git repository")
    return root


def is_enabled(root: Path) -> bool:
    return (root / ".ratchet" / "harness.json").is_file()


def state_dir(root: Path) -> Path:
    """Runtime state lives in the git common dir: shared by all worktrees, never committed."""
    common = git_ok(["rev-parse", "--git-common-dir"], root) or ".git"
    path = Path(common)
    if not path.is_absolute():
        path = (root / path).resolve()
    d = path / "ratchet"
    d.mkdir(parents=True, exist_ok=True)
    return d


def current_branch(root: Path) -> str:
    name = git_ok(["symbolic-ref", "--short", "-q", "HEAD"], root)
    if name:
        return name
    sha = git_ok(["rev-parse", "--short", "HEAD"], root)
    return f"detached-{sha or 'none'}"


def head_sha(root: Path) -> Optional[str]:
    return git_ok(["rev-parse", "HEAD"], root)


def slug(text: str) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-")
    return s[:80] or "default"


def now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def short_hash(*parts: str, n: int = 10) -> str:
    return hashlib.sha1("\x00".join(parts).encode()).hexdigest()[:n]


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return default
    except json.JSONDecodeError as exc:
        raise RatchetError(f"{path} is not valid JSON: {exc}") from exc


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=False) + "\n")
    tmp.replace(path)


def append_jsonl(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        fh.write(json.dumps(data) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def log_event(root: Path, event: str, **fields: Any) -> None:
    append_jsonl(state_dir(root) / "events.jsonl", {"at": now(), "type": event, **fields})


def eprint(*args: Any) -> None:
    print(*args, file=sys.stderr)
