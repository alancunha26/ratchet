"""Friction log: whatever slowed an agent down. The gardener turns these into harness fixes."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from .util import RatchetError, log_event, now, read_json, short_hash, state_dir, write_json

KINDS = ("slow", "flaky", "tooling", "env", "spec", "gate", "judge", "other")


def _path(root: Path) -> Path:
    return state_dir(root) / "blockers.json"


def load(root: Path) -> list[dict]:
    return read_json(_path(root), []) or []


def add(root: Path, text: str, kind: str = "other", run: Optional[str] = None) -> dict:
    if kind not in KINDS:
        raise RatchetError(f"kind must be one of {', '.join(KINDS)}")
    items = load(root)
    item = {"id": "B" + short_hash(text, now(), n=6), "at": now(), "kind": kind, "text": text.strip(),
            "run": run, "status": "open"}
    items.append(item)
    write_json(_path(root), items)
    log_event(root, "blocker", kind=kind, id=item["id"])
    return item


def resolve(root: Path, bid: str, note: str) -> dict:
    items = load(root)
    for item in items:
        if item["id"] == bid:
            item.update({"status": "resolved", "resolved_at": now(), "resolution": note})
            write_json(_path(root), items)
            return item
    raise RatchetError(f"no blocker {bid}")
