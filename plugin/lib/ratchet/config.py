"""The per-repo contract: .ratchet/config.json merged over defaults."""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from .util import RatchetError, read_json

VERSION = "0.2.0"

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
    "judge": {"model": "inherit"},
    "run": {"pr": "draft"},
    # How the coordinator uses T3 Code: every worker and judge is its own T3 thread.
    "t3": {
        "max_parallel": 2,
        "contract_review": True,
        "worker": {"model": None, "provider": None, "isolation": "worktree"},
        "judge": {"model": None, "provider": None},
    },
    # How agents run the product (the judge drives it in T3's built-in browser).
    "app": {
        "dev": None,
        "url": "http://localhost:{port}",
        "port_env": "PORT",
        "ready_text": None,
        "profile": None,
        "ui_paths": ["**/*.tsx", "**/*.jsx", "**/*.vue", "**/*.svelte", "**/*.css", "**/*.scss", "**/*.html"],
    },
}


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for key, val in over.items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], val)
        else:
            out[key] = copy.deepcopy(val)
    return out


def config_path(root: Path) -> Path:
    return root / ".ratchet" / "config.json"


def load(root: Path) -> dict:
    raw = read_json(config_path(root), None)
    if raw is None:
        raise RatchetError("no .ratchet/config.json here; run `ratchet init` first")
    if not isinstance(raw, dict):
        raise RatchetError(".ratchet/config.json must be a JSON object")
    cfg = _merge(DEFAULTS, {k: v for k, v in raw.items() if not k.startswith("$")})
    # Checks merge per kind so a repo can override just one kind or one level.
    checks = copy.deepcopy(DEFAULT_CHECKS)
    for kind, levels in (raw.get("checks") or {}).items():
        checks.setdefault(kind, {"fast": [], "full": []})
        for level, specs in (levels or {}).items():
            checks[kind][level] = list(specs)
    cfg["checks"] = checks
    return cfg
