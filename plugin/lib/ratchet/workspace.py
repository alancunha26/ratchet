"""Find the packages of a repo (monorepo or not) and which ones a change affects."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

SKIP_DIRS = {"node_modules", ".git", "target", "dist", "build", ".venv", "venv", "vendor", ".next", ".turbo"}


@dataclass
class Package:
    name: str
    dir: str  # posix path relative to the repo root; "" is the root
    kind: str  # node | rust | go | python | <custom>
    scripts: dict = field(default_factory=dict)
    deps: set = field(default_factory=set)

    @property
    def label(self) -> str:
        return self.dir or "."


def _read(path: Path) -> str:
    try:
        return path.read_text()
    except (FileNotFoundError, UnicodeDecodeError):
        return ""


def _expand(root: Path, patterns: Iterable[str], marker: str) -> list[Path]:
    include: list[Path] = []
    exclude: set[Path] = set()
    for pat in patterns:
        neg = pat.startswith("!")
        pat = pat.lstrip("!").strip().rstrip("/")
        if not pat:
            continue
        hits = [p for p in root.glob(pat) if p.is_dir() and (p / marker).is_file()]
        hits = [p for p in hits if not (set(p.relative_to(root).parts) & SKIP_DIRS)]
        if neg:
            exclude.update(hits)
        else:
            include.extend(hits)
    seen: list[Path] = []
    for p in include:
        if p not in exclude and p not in seen:
            seen.append(p)
    return sorted(seen)


def _rel(root: Path, p: Path) -> str:
    rel = p.relative_to(root).as_posix()
    return "" if rel == "." else rel


# ---------- node ----------

def _node_globs(root: Path) -> list[str]:
    globs: list[str] = []
    pkg = _load_json(root / "package.json")
    ws = pkg.get("workspaces") if pkg else None
    if isinstance(ws, list):
        globs += ws
    elif isinstance(ws, dict):
        globs += ws.get("packages") or []
    pnpm = _read(root / "pnpm-workspace.yaml")
    if pnpm:
        in_packages = False
        for line in pnpm.splitlines():
            if re.match(r"^packages\s*:", line):
                in_packages = True
                continue
            if in_packages:
                m = re.match(r"^\s*-\s*['\"]?([^'\"#]+?)['\"]?\s*(#.*)?$", line)
                if m:
                    globs.append(m.group(1).strip())
                elif line.strip() and not line.startswith(" ") and not line.startswith("\t"):
                    in_packages = False
    return globs


def _load_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError):
        return {}


def _node_package(root: Path, d: Path) -> Package:
    data = _load_json(d / "package.json")
    deps: set = set()
    for key in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
        deps.update((data.get(key) or {}).keys())
    return Package(data.get("name") or d.name, _rel(root, d), "node", data.get("scripts") or {}, deps)


def discover_node(root: Path) -> list[Package]:
    if not (root / "package.json").is_file():
        return []
    globs = _node_globs(root)
    if not globs:
        return [_node_package(root, root)]
    return [_node_package(root, d) for d in _expand(root, globs, "package.json")]


# ---------- rust ----------

def _toml_array(text: str, section: str, key: str) -> list[str]:
    m = re.search(rf"(?ms)^\[{re.escape(section)}\]\s*$(.*?)(?=^\[|\Z)", text)
    if not m:
        return []
    km = re.search(rf"(?ms)^\s*{re.escape(key)}\s*=\s*\[(.*?)\]", m.group(1))
    return re.findall(r"['\"]([^'\"]+)['\"]", km.group(1)) if km else []


def _toml_value(text: str, section: str, key: str) -> Optional[str]:
    m = re.search(rf"(?ms)^\[{re.escape(section)}\]\s*$(.*?)(?=^\[|\Z)", text)
    if not m:
        return None
    km = re.search(rf"(?m)^\s*{re.escape(key)}\s*=\s*['\"]([^'\"]+)['\"]", m.group(1))
    return km.group(1) if km else None


def _crate(root: Path, d: Path) -> Optional[Package]:
    text = _read(d / "Cargo.toml")
    name = _toml_value(text, "package", "name")
    if not name:
        return None
    deps = set()
    for path in re.findall(r"path\s*=\s*['\"]([^'\"]+)['\"]", text):
        deps.add(("path", (d / path).resolve()))
    return Package(name, _rel(root, d), "rust", {}, deps)


def discover_rust(root: Path) -> list[Package]:
    text = _read(root / "Cargo.toml")
    if not text:
        return []
    dirs = _expand(root, _toml_array(text, "workspace", "members"), "Cargo.toml")
    if root not in dirs:
        dirs.insert(0, root)
    crates = [c for c in (_crate(root, d) for d in dirs) if c]
    # Path dependencies -> crate names, so dependents can be followed.
    by_dir = {(root / c.dir).resolve() if c.dir else root.resolve(): c.name for c in crates}
    for c in crates:
        c.deps = {by_dir[p] for (_, p) in c.deps if p in by_dir}
    return crates


# ---------- go ----------

def discover_go(root: Path) -> list[Package]:
    work = _read(root / "go.work")
    dirs: list[str] = []
    if work:
        block = re.search(r"(?ms)^use\s*\((.*?)\)", work)
        if block:
            dirs += [l.strip() for l in block.group(1).splitlines() if l.strip() and not l.strip().startswith("//")]
        dirs += re.findall(r"(?m)^use\s+([^\s(]+)\s*$", work)
    elif (root / "go.mod").is_file():
        dirs = ["."]
    out = []
    for d in dirs:
        p = (root / d).resolve()
        mod = re.search(r"(?m)^module\s+(\S+)", _read(p / "go.mod"))
        if mod:
            out.append(Package(mod.group(1), _rel(root, p), "go"))
    return out


# ---------- python ----------

def discover_python(root: Path) -> list[Package]:
    text = _read(root / "pyproject.toml")
    if not text:
        return []
    members = _toml_array(text, "tool.uv.workspace", "members")
    dirs = _expand(root, members, "pyproject.toml") if members else []
    if _toml_value(text, "project", "name") or not dirs:
        dirs.insert(0, root)
    out = []
    for d in dirs:
        name = _toml_value(_read(d / "pyproject.toml"), "project", "name") or d.name
        out.append(Package(name, _rel(root, d), "python"))
    return out


# ---------- godot ----------

def discover_godot(root: Path) -> list[Package]:
    out = []
    for proj in sorted(root.glob("project.godot")) + sorted(root.glob("*/project.godot")) + sorted(root.glob("*/*/project.godot")):
        d = proj.parent
        if set(d.relative_to(root).parts) & (SKIP_DIRS | {"addons"}):
            continue
        name = re.search(r'config/name="([^"]+)"', _read(proj))
        out.append(Package(name.group(1) if name else (d.name if d != root else root.name), _rel(root, d), "godot"))
    return out


DISCOVERERS = {"node": discover_node, "rust": discover_rust, "go": discover_go, "python": discover_python,
               "godot": discover_godot}


def discover(root: Path, cfg: dict) -> list[Package]:
    explicit = cfg.get("packages")
    if isinstance(explicit, list):
        pkgs = []
        for entry in explicit:
            d = (entry.get("dir") or "").strip("/")
            kind = entry.get("kind") or "custom"
            scripts = _load_json(root / d / "package.json").get("scripts") or {} if kind == "node" else {}
            pkgs.append(Package(entry.get("name") or d or root.name, "" if d in ("", ".") else d, kind, scripts))
        return pkgs
    pkgs = []
    for fn in DISCOVERERS.values():
        pkgs.extend(fn(root))
    return pkgs


def owners(pkgs: list[Package], path: str) -> list[Package]:
    """Packages owning a file: the deepest package dir containing it (all kinds at that dir)."""
    best_len = -1
    best: list[Package] = []
    for p in pkgs:
        prefix = p.dir + "/" if p.dir else ""
        if path.startswith(prefix):
            n = len(p.dir)
            if n > best_len:
                best_len, best = n, [p]
            elif n == best_len:
                best.append(p)
    return best


def affected(pkgs: list[Package], paths: list[str], with_dependents: bool = True) -> list[Package]:
    hit: list[Package] = []
    for path in paths:
        for p in owners(pkgs, path):
            if p not in hit:
                hit.append(p)
    if not with_dependents:
        return hit
    by_kind_name = {(p.kind, p.name): p for p in pkgs}
    reverse: dict = {}
    for p in pkgs:
        for dep in p.deps:
            target = by_kind_name.get((p.kind, dep))
            if target:
                reverse.setdefault(id(target), []).append(p)
    queue = list(hit)
    while queue:
        cur = queue.pop()
        for dependent in reverse.get(id(cur), []):
            if dependent not in hit:
                hit.append(dependent)
                queue.append(dependent)
    return hit


def slug_name(pkg: Package) -> str:
    """A short, file-safe name for a package's checks: its dir's last segment, or its name."""
    base = (pkg.dir.rstrip("/").split("/")[-1] if pkg.dir else pkg.name) or "root"
    return re.sub(r"[^A-Za-z0-9_-]+", "-", base.lstrip("@")).strip("-").lower() or "root"


def scope_for(pkgs: list, pkg: Package) -> list:
    """Paths whose change should re-run this package's tests: itself plus everything it depends on."""
    by_name = {(p.kind, p.name): p for p in pkgs}
    seen, stack = {id(pkg)}, [pkg]
    dirs = []
    while stack:
        cur = stack.pop()
        dirs.append(cur.dir)
        for dep in cur.deps:
            target = by_name.get((cur.kind, dep))
            if target is not None and id(target) not in seen:
                seen.add(id(target))
                stack.append(target)
    if "" in dirs:
        return []  # a root package: any change triggers it
    return sorted({f"{d}/**" for d in dirs})


def package_manager(root: Path) -> str:
    if (root / "pnpm-lock.yaml").exists() or (root / "pnpm-workspace.yaml").exists():
        return "pnpm"
    if (root / "bun.lockb").exists() or (root / "bun.lock").exists():
        return "bun"
    if (root / "yarn.lock").exists():
        return "yarn"
    return "npm"
