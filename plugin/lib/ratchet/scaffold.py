"""`ratchet init` and `ratchet doctor`: write the repo contract and check it actually works."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from . import checks, config, workspace
from .util import write_json

TEMPLATES = Path(__file__).resolve().parents[2] / "templates"

NODE_FAST = [["lint"], ["typecheck", "type-check", "check-types", "types", "tsc"], ["test", "test:unit"]]
NODE_FULL = [["build"], ["test:e2e", "e2e"]]
EXEC = {"pnpm": "pnpm exec", "yarn": "yarn", "bun": "bunx", "npm": "npx"}
MARK_START, MARK_END = "<!-- ratchet:start -->", "<!-- ratchet:end -->"


def _pick(scripts: set, groups: list[list[str]]) -> list[str]:
    out = []
    for group in groups:
        for name in group:
            if name in scripts:
                out.append(name)
                break
    return out


def detect(root: Path) -> dict:
    """A starting config from what the repo already has. The init skill refines it."""
    probe = {"checks": {}, "packages": "auto"}
    pkgs = workspace.discover(root, config._merge(config.DEFAULTS, probe))
    kinds = sorted({p.kind for p in pkgs})
    pm = workspace.package_manager(root)
    checks_cfg: dict = {}
    setup: list[str] = []

    if "node" in kinds:
        scripts = set()
        for p in pkgs:
            if p.kind == "node":
                scripts.update(p.scripts)
        fast, full = _pick(scripts, NODE_FAST), _pick(scripts, NODE_FULL)
        if (root / "turbo.json").exists():
            run = f"{EXEC[pm]} turbo run"
            checks_cfg["node"] = {
                "fast": [f"batch:{run} {' '.join(fast)} {{filters}} --output-logs=errors-only"] if fast else [],
                "full": [f"batch:{run} {' '.join(full)} {{filters}} --output-logs=errors-only"] if full else [],
            }
        elif (root / "nx.json").exists():
            run = f"{EXEC[pm]} nx run-many"
            checks_cfg["node"] = {
                "fast": [f"batch:{run} -t {' '.join(fast)} -p {{names}}"] if fast else [],
                "full": [f"batch:{run} -t {' '.join(full)} -p {{names}}"] if full else [],
            }
        else:
            checks_cfg["node"] = {"fast": [f"script:{s}" for s in fast], "full": [f"script:{s}" for s in full]}
        lock = {"pnpm": "pnpm install --frozen-lockfile", "yarn": "yarn install --immutable",
                "bun": "bun install --frozen-lockfile",
                "npm": "npm ci" if (root / "package-lock.json").exists() else "npm install"}
        setup.append(lock[pm])

    if "rust" in kinds:
        checks_cfg["rust"] = dict(config.DEFAULT_CHECKS["rust"])
        setup.append("cargo fetch")
    if "go" in kinds:
        checks_cfg["go"] = dict(config.DEFAULT_CHECKS["go"])
        setup.append("go mod download")
    if "python" in kinds:
        text = ""
        for p in pkgs:
            if p.kind == "python":
                try:
                    text += (root / p.dir / "pyproject.toml").read_text()
                except FileNotFoundError:
                    pass
        uv = (root / "uv.lock").exists()
        prefix = "uv run " if uv else ""
        fast = []
        if "ruff" in text:
            fast.append(f"{prefix}ruff check .")
        if "mypy" in text:
            fast.append(f"{prefix}mypy .")
        if "pytest" in text:
            fast.append(f"{prefix}pytest -q")
        checks_cfg["python"] = {"fast": fast, "full": []}
        if uv:
            setup.append("uv sync")

    cfg = {
        "$comment": "ratchet repo contract. See `ratchet doctor` and the ratchet README.",
        "version": 1,
        "base": "auto",
        "setup": " && ".join(setup) or None,
        "packages": "auto",
        "checks": checks_cfg,
        "repo_checks": {"fast": [], "full": []},
        "protect": config.DEFAULT_PROTECT,
        "skip": [],
        "tamper_ignore": [],
        "limits": config.DEFAULTS["limits"],
        "judge": {"model": "inherit"},
        "run": {"pr": "draft"},
        "t3": config.DEFAULTS["t3"],
        "app": dict(config.DEFAULTS["app"], dev=_dev_command(root, pkgs, pm)),
    }
    return {"config": cfg, "packages": pkgs, "kinds": kinds, "pm": pm}


def _dev_command(root: Path, pkgs: list, pm: str):
    """A starting guess for app.dev: the root package's dev/start script (init refines it)."""
    roots = [p for p in pkgs if p.kind == "node" and p.dir == ""]
    scripts = roots[0].scripts if roots else {}
    for name in ("dev", "start"):
        if name in scripts:
            return f"{pm} run {name}"
    return None


def _claude_section() -> str:
    return (TEMPLATES / "claude-section.md").read_text().strip()


def upsert_claude_md(root: Path) -> str:
    path = root / "CLAUDE.md"
    block = f"{MARK_START}\n{_claude_section()}\n{MARK_END}"
    if not path.exists():
        path.write_text(f"# {root.name}\n\n{block}\n")
        return "created"
    text = path.read_text()
    if MARK_START in text and MARK_END in text:
        head, rest = text.split(MARK_START, 1)
        _, tail = rest.split(MARK_END, 1)
        path.write_text(head + block + tail)
        return "updated"
    path.write_text(text.rstrip() + "\n\n" + block + "\n")
    return "appended"


def init(root: Path, force: bool = False) -> dict:
    cfg_path = config.config_path(root)
    found = detect(root)
    wrote = []
    if cfg_path.exists() and not force:
        status = "kept existing .ratchet/config.json (use --force to regenerate)"
    else:
        write_json(cfg_path, found["config"])
        wrote.append(".ratchet/config.json")
        status = "wrote .ratchet/config.json"
    judge = root / ".ratchet" / "judge.md"
    if not judge.exists():
        shutil.copyfile(TEMPLATES / "judge.md", judge)
        wrote.append(".ratchet/judge.md")
    claude = upsert_claude_md(root)
    return {"status": status, "wrote": wrote, "claude_md": claude, "kinds": found["kinds"], "pm": found["pm"],
            "packages": [f"{p.kind}:{p.label} ({p.name})" for p in found["packages"]]}


def doctor(root: Path, baseline: bool = False) -> tuple[bool, str]:
    cfg = config.load(root)
    pkgs = workspace.discover(root, cfg)
    lines = [f"packages ({len(pkgs)}):"]
    jobs = checks.plan(root, cfg, pkgs, None, "full")
    by_pkg: dict = {}
    for j in jobs:
        by_pkg.setdefault(j.package, []).append(j.name)
    for p in pkgs:
        names = by_pkg.get(p.label) or []
        lines.append(f"  {p.kind:<7} {p.label:<40} {', '.join(names) or '(no per-package checks)'}")
    for label, names in by_pkg.items():
        if not any(p.label == label for p in pkgs):
            lines.append(f"  {'repo' if label == '.' else 'batch':<7} {label:<40} {', '.join(names)}")
    problems = []
    if not jobs:
        problems.append("no checks are configured: the gate has nothing to enforce. Add checks before feature work.")
    for j in jobs:
        exe = j.cmd.split()[0]
        if shutil.which(exe) is None:
            problems.append(f"`{exe}` not found on PATH (needed by {j.package}:{j.name})")
    ok = not problems
    if baseline and jobs:
        rep = checks.verify(root, cfg, level="full", scope="all", use_cache=False, check_tamper=False)
        red = [j for j in rep.jobs if not j.ok]
        lines.append(f"baseline: {len(rep.jobs) - len(red)}/{len(rep.jobs)} checks green ({rep.seconds}s)")
        for j in red:
            lines.append(f"  RED {j.package} · {j.name}  $ {j.cmd}\n    " + j.excerpt.replace("\n", "\n    ")[:800])
            lines.append(f"    log: {j.log}")
        if red:
            ok = False
            problems.append("baseline is red. Fix these first, or quarantine them in \"skip\" as "
                            + json.dumps(sorted({f'{j.package}:{j.name}' for j in red}))
                            + " with a blocker explaining why.")
    lines += [f"PROBLEM: {p}" for p in problems]
    lines.append("doctor: OK" if ok else "doctor: needs attention")
    return ok, "\n".join(lines)
