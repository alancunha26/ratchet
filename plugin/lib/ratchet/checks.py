"""Plan, run and cache the deterministic checks for whatever a change affects."""
from __future__ import annotations

import fnmatch
import os
import re
import shlex
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterator, Optional

from . import gitstate, tamper, workspace
from .config import VERSION
from .util import log_event, now, read_json, slug, state_dir, write_json

LEVELS = ("fast", "full")


@dataclass
class Job:
    package: str  # label, "." for root / repo checks
    name: str
    cmd: str
    cwd: str  # relative to root


@dataclass
class JobResult:
    package: str
    name: str
    cmd: str
    ok: bool
    code: int
    seconds: float
    log: str
    excerpt: str = ""


@dataclass
class Report:
    ok: bool
    tree: str
    since: str
    level: str
    scope: str
    packages: list = field(default_factory=list)
    jobs: list = field(default_factory=list)
    tamper: list = field(default_factory=list)
    seconds: float = 0.0
    at: str = ""
    cached: bool = False
    version: str = VERSION

    @classmethod
    def from_dict(cls, data: dict) -> "Report":
        r = cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        r.jobs = [JobResult(**j) for j in data.get("jobs", [])]
        return r


def _check_name(spec: str) -> str:
    try:
        tokens = shlex.split(spec)
    except ValueError:
        tokens = spec.split()
    words = [w for w in tokens if not w.startswith("-") and "{" not in w]
    if words and words[0] in ("python", "python3", "uv", "poetry", "npx", "pnpm", "yarn", "bun") and len(words) > 1:
        words = words[1:]
    if words and words[0] in ("run", "exec", "-m") and len(words) > 1:
        words = words[1:]
    return " ".join(words[:2]) or spec[:30]


def _skipped(cfg: dict, pkg: workspace.Package, name: str) -> bool:
    for entry in cfg.get("skip") or []:
        target, _, check = entry.rpartition(":")
        if fnmatch.fnmatch(name, check) and target in ("*", pkg.label, pkg.name, pkg.dir):
            return True
    return False


def _levels(level: str) -> list[str]:
    return ["fast", "full"] if level == "full" else ["fast"]


def plan(root: Path, cfg: dict, pkgs: list[workspace.Package], paths: Optional[list[str]], level: str) -> list[Job]:
    """Jobs for the given packages. `paths` are the changed files, or None for "everything"."""
    jobs: list[Job] = []
    pm = workspace.package_manager(root)
    batches: dict = {}
    for pkg in pkgs:
        kind_checks = cfg["checks"].get(pkg.kind) or {}
        for lvl in _levels(level):
            for spec in kind_checks.get(lvl) or []:
                if spec.startswith("script:"):
                    script = spec[len("script:"):]
                    if pkg.kind != "node" or script not in pkg.scripts or _skipped(cfg, pkg, script):
                        continue
                    jobs.append(Job(pkg.label, script, f"{pm} run {script}", pkg.dir))
                elif spec.startswith("batch:"):
                    cmd = spec[len("batch:"):]
                    if not _skipped(cfg, pkg, _check_name(cmd)):
                        batches.setdefault((pkg.kind, cmd), []).append(pkg)
                else:
                    name = _check_name(spec)
                    if _skipped(cfg, pkg, name):
                        continue
                    cmd = spec.replace("{name}", pkg.name).replace("{dir}", pkg.dir or ".")
                    jobs.append(Job(pkg.label, name, cmd, pkg.dir))
    for (kind, cmd), members in batches.items():
        names = " ".join(shlex.quote(p.name) for p in members)
        expanded = (
            cmd.replace("{names}", names)
            .replace("{p_flags}", " ".join(f"-p {shlex.quote(p.name)}" for p in members))
            .replace("{dirs}", " ".join(shlex.quote(p.dir or ".") for p in members))
            .replace("{filters}", " ".join(f"--filter={shlex.quote(p.name)}" for p in members))
        )
        jobs.append(Job(f"{kind}:" + ",".join(p.name for p in members), _check_name(cmd), expanded, ""))
    if paths is None or paths:
        for lvl in _levels(level):
            for entry in (cfg.get("repo_checks") or {}).get(lvl) or []:
                if isinstance(entry, str):
                    entry = {"run": entry}
                when = entry.get("when")
                if when and paths is not None and not any(tamper._glob_match(p, g) for p in paths for g in when):
                    continue
                jobs.append(Job(".", entry.get("name") or _check_name(entry["run"]), entry["run"], ""))
    return jobs


# ---------- running ----------

_RELEVANT = re.compile(
    r"(?i)(error|fail|failed|failure|panic|exception|traceback|assert|expected|received|"
    r"✗|✘|×|not ok|cannot|undefined|denied|missing)|:\d+:\d+|^\s+at\s"
)


def excerpt(output: str, max_lines: int = 40, max_chars: int = 3000) -> str:
    """Keep only the lines an agent needs to act on, so failures don't flood its context."""
    lines = [l.rstrip() for l in output.splitlines() if l.strip()]
    keep: list[int] = []
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
def machine_slot(cfg: dict) -> Iterator[None]:
    """Cap concurrent verify runs across every worktree on this machine (prevents OOM storms)."""
    try:
        import fcntl
    except ImportError:  # pragma: no cover - non-posix
        yield
        return
    slots = int(os.environ.get("RATCHET_SLOTS") or cfg["limits"].get("machine_slots") or 2)
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


def _run_job(root: Path, job: Job, log_dir: Path, timeout: int) -> JobResult:
    env = {**os.environ, "CI": "1", "FORCE_COLOR": "0", "NO_COLOR": "1", "RATCHET_VERIFY": "1"}
    log = log_dir / f"{slug(job.package)}__{slug(job.name)}.log"
    start = time.time()
    try:
        proc = subprocess.run(
            job.cmd, shell=True, cwd=str(root / job.cwd) if job.cwd else str(root), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace", timeout=timeout,
        )
        out, code = proc.stdout, proc.returncode
    except subprocess.TimeoutExpired as exc:
        out = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        out += f"\n[ratchet] timed out after {timeout}s"
        code = 124
    log.write_text(f"$ {job.cmd}\n(cwd: {job.cwd or '.'})\n\n{out}")
    ok = code == 0
    return JobResult(job.package, job.name, job.cmd, ok, code, round(time.time() - start, 2), str(log),
                     "" if ok else excerpt(out))


# ---------- evidence ----------

def _evidence_path(root: Path, tree: str, since: str, level: str, scope: str) -> Path:
    return state_dir(root) / "evidence" / f"{tree}-{since[:12]}-{scope}-{level}.json"


def cached(root: Path, tree: str, since: str, level: str, scope: str) -> Optional[Report]:
    """A passing result at this level (or a stricter one) for exactly this working state."""
    for lvl in (["full"] if level == "full" else ["fast", "full"]):
        for sc in ([scope] if scope == "all" else [scope, "all"]):
            data = read_json(_evidence_path(root, tree, since, lvl, sc), None)
            if data and data.get("ok") and data.get("version") == VERSION:
                rep = Report.from_dict(data)
                rep.cached = True
                return rep
    return None


def last_result(root: Path, tree: str, since: str, level: str, scope: str) -> Optional[Report]:
    data = read_json(_evidence_path(root, tree, since, level, scope), None)
    return Report.from_dict(data) if data and data.get("version") == VERSION else None


def verify(root: Path, cfg: dict, *, level: str = "fast", scope: str = "affected",
           use_cache: bool = True, check_tamper: bool = True) -> Report:
    tree = gitstate.tree_id(root)
    since = gitstate.merge_base(root, cfg)
    if use_cache:
        hit = cached(root, tree, since, level, scope)
        if hit:
            return hit
    started = time.time()
    all_pkgs = workspace.discover(root, cfg)
    if scope == "all":
        paths, pkgs = None, all_pkgs
    else:
        paths = [c.path for c in gitstate.changes(root, since, tree)]
        pkgs = workspace.affected(all_pkgs, paths, bool(cfg.get("dependents", True)))
    jobs = plan(root, cfg, pkgs, paths, level)
    log_dir = state_dir(root) / "logs" / tree[:12]
    log_dir.mkdir(parents=True, exist_ok=True)
    results: list[JobResult] = []
    if jobs:
        with machine_slot(cfg):
            workers = max(1, int(cfg["limits"].get("parallel_jobs") or 4))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                results = list(pool.map(lambda j: _run_job(root, j, log_dir, int(cfg["limits"]["check_timeout_sec"])), jobs))
    findings = tamper.unallowed(root, cfg, since, tree) if check_tamper and cfg["gate"].get("tamper", True) else []
    rep = Report(
        ok=all(r.ok for r in results) and not findings, tree=tree, since=since, level=level, scope=scope,
        packages=[p.label for p in pkgs], jobs=results, tamper=[asdict(f) for f in findings],
        seconds=round(time.time() - started, 2), at=now(),
    )
    write_json(_evidence_path(root, tree, since, level, scope), asdict(rep))
    log_event(root, "verify", ok=rep.ok, level=level, scope=scope, seconds=rep.seconds, tree=tree,
              failed=[f"{r.package}:{r.name}" for r in results if not r.ok], tamper=len(findings))
    return rep


def format_report(rep: Report, *, verbose: bool = False) -> str:
    lines = []
    status = "PASS" if rep.ok else "FAIL"
    cache = " (cached)" if rep.cached else ""
    lines.append(f"ratchet verify [{rep.level}/{rep.scope}] {status}{cache} — {len(rep.jobs)} checks, "
                 f"{len(rep.packages)} packages, {rep.seconds}s")
    if not rep.jobs and not rep.tamper:
        lines.append("  nothing to check (no changed packages, or no checks configured for them)")
    for r in rep.jobs:
        if r.ok and not verbose:
            continue
        mark = "ok  " if r.ok else "FAIL"
        lines.append(f"  {mark} {r.package} · {r.name} ({r.seconds}s)  $ {r.cmd}")
        if not r.ok:
            lines.append("    " + r.excerpt.replace("\n", "\n    "))
            lines.append(f"    full log: {r.log}")
    passed = sum(1 for r in rep.jobs if r.ok)
    if passed and not verbose:
        lines.append(f"  ok   {passed} passing checks")
    for f in rep.tamper:
        lines.append(f"  TAMPER [{f['id']}] {f['kind']}: {f['file']}:{f['line']}  {f['text'][:120]}")
    if rep.tamper:
        lines.append("    Undo it. If it is genuinely required, run `ratchet allow <id> --reason \"...\"`;"
                     " the judge reviews every allowance.")
    return "\n".join(lines)
