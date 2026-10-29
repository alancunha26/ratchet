"""The per-repo contract: .ratchet/config.json merged over defaults."""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from .util import RatchetError, read_json

VERSION = "0.3.0"

# Check specs, per package kind:
#   "script:NAME"  run the package.json script NAME if the package defines it (node only)
#   "batch:CMD"    run CMD once at the repo root for all affected packages of the kind;
#                  {names} -> "a b", {p_flags} -> "-p a -p b", {filters} -> "--filter=a --filter=b",
#                  {dirs} -> "x/a x/b"
#   "CMD"          run CMD inside each affected package dir; {name} and {dir} expand
DEFAULT_CHECKS: dict[str, dict[str, list[str]]] = {
    "node": {"fast": ["script:lint", "script:typecheck", "script:test"], "full": ["script:build", "script:test:e2e"]},
    "rust": {
        "fast": ["batch:cargo clippy --all-targets {p_flags} -- -D warnings", "batch:cargo test {p_flags}"],
        "full": [],
    },
    "go": {"fast": ["go vet ./...", "go test ./..."], "full": []},
    "python": {"fast": ["ruff check .", "python -m pytest -q"], "full": []},
    # Godot exits 0 after script errors, so the load check scans its output (see profiles/godot.md).
    "godot": {
        "fast": ["ratchet exec-check --fail-on 'SCRIPT ERROR|Parse Error|^ERROR:' -- godot --headless --path . --quit"],
        "full": [],
    },
}

DEFAULT_PROTECT = [
    ".ratchet/config.json",
    ".github/workflows/**",
    "**/__snapshots__/**",
    "**/*.snap",
]

DEFAULTS: dict[str, Any] = {
    "version": 1,
    "base": "auto",
    "setup": None,
    "packages": "auto",
    "dependents": True,
    "checks": {},
    "repo_checks": {"fast": [], "full": []},
    "protect": DEFAULT_PROTECT,
    "skip": [],
    "tamper_ignore": [],
    "limits": {
        "gate_attempts": 4,
        "judge_rounds": 2,
        "ci_rounds": 2,
        "parallel_jobs": 4,
        "machine_slots": 2,
        "check_timeout_sec": 900,
        "worker_attempts": 3,
    },
    "gate": {"enabled": True, "level": "fast", "tamper": True},
    "run": {"pr": "draft"},
    # Every role is a T3 Code thread (delegate_task), so any harness T3 hosts can fill any role.
    # provider = T3 provider instance id (claudeAgent, codex, cursor, opencode, ...); null inherits.
    "agents": {
        "max_parallel": 1,        # >1: parallel workers in their own worktrees (disjoint packages only)
        "contract_review": True,
        "judge_panel": 1,         # >1: independent judges per task; majority decides (for subjective work)
        "coordinator": {"provider": None, "model": None},
        "worker": {"provider": None, "model": None, "options": {}},
        "judge": {"provider": None, "model": None, "options": {}},
    },
    # What this repo ships and how to exercise it; each entry uses a profile (profiles/*.md).
    "products": [],
}


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for key, val in over.items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], val)
        else:
            out[key] = copy.deepcopy(val)
    return out


def _upgrade(raw: dict) -> dict:
    """Accept v0.2 configs (t3 / app / judge keys)."""
    raw = dict(raw)
    if "t3" in raw and "agents" not in raw:
        t3 = raw.pop("t3") or {}
        raw["agents"] = {k: v for k, v in t3.items() if k != "isolation"}
    raw.pop("t3", None)
    if "judge" in raw:
        model = (raw.pop("judge") or {}).get("model")
        if model and model != "inherit":
            raw.setdefault("agents", {}).setdefault("judge", {}).setdefault("model", model)
    app = raw.pop("app", None)
    if app and app.get("dev") and not raw.get("products"):
        raw["products"] = [{"name": "app", "profile": "web", "paths": app.get("ui_paths") or ["**"],
                            "run": app["dev"], "port_env": app.get("port_env", "PORT"),
                            "url": app.get("url"), "ready_text": app.get("ready_text"),
                            "browser_profile": app.get("profile")}]
    return raw


def config_path(root: Path) -> Path:
    return root / ".ratchet" / "config.json"


def load(root: Path) -> dict:
    raw = read_json(config_path(root), None)
    if raw is None:
        raise RatchetError("no .ratchet/config.json here; run `ratchet init` first")
    if not isinstance(raw, dict):
        raise RatchetError(".ratchet/config.json must be a JSON object")
    raw = _upgrade(raw)
    cfg = _merge(DEFAULTS, {k: v for k, v in raw.items() if not k.startswith("$")})
    # Checks merge per kind so a repo can override just one kind or one level.
    checks = copy.deepcopy(DEFAULT_CHECKS)
    for kind, levels in (raw.get("checks") or {}).items():
        checks.setdefault(kind, {"fast": [], "full": []})
        for level, specs in (levels or {}).items():
            checks[kind][level] = list(specs)
    cfg["checks"] = checks
    return cfg
