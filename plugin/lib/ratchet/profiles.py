"""Product profiles: how to run, exercise and judge each kind of product.

Testing a Godot game, a web app, an API and a CLI are different jobs. A profile is a
markdown guide (## Run / ## Exercise / ## Evidence / ## Good criteria) that briefs pull in
for the products a task touches. Repos can add or override profiles in .ratchet/profiles/.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Optional

from . import workspace

BUILTIN = Path(__file__).resolve().parents[2] / "profiles"


def available(root: Path) -> dict[str, Path]:
    found = {p.stem: p for p in sorted(BUILTIN.glob("*.md"))}
    local = root / ".ratchet" / "profiles"
    if local.is_dir():
        found.update({p.stem: p for p in sorted(local.glob("*.md"))})  # repo profiles win
    return found


def load(root: Path, name: str) -> dict[str, str]:
    path = available(root).get(name)
    if path is None:
        return {"intro": f"(no profile named '{name}'; add .ratchet/profiles/{name}.md)"}
    parts = re.split(r"(?m)^## +(.+?)\s*$", path.read_text())
    sections = {"intro": parts[0].split("\n", 1)[-1].strip()}
    for i in range(1, len(parts) - 1, 2):
        sections[parts[i].strip()] = parts[i + 1].strip()
    return sections


def _fill(text: str, values: dict) -> str:
    def sub(m: re.Match) -> str:
        val = values.get(m.group(1))
        return str(val) if val not in (None, "") else f"({m.group(1)} not configured)"

    return re.sub(r"\{\{(\w+)\}\}", sub, text)


def products_touching(cfg: dict, paths: list[str]) -> list[dict]:
    from .tamper import _glob_match

    out = []
    for prod in cfg.get("products") or []:
        globs = prod.get("paths") or ([f"{prod['dir']}/**"] if prod.get("dir") not in (None, "", ".") else ["**"])
        if any(_glob_match(p, g) for p in paths for g in globs):
            out.append(prod)
    return out


def render(root: Path, product: dict, sections: list[str], base: dict) -> str:
    prof = load(root, product.get("profile") or "library")
    values = {**base, **product, "dir": product.get("dir") or "."}
    lines = [f"### {product.get('name', product.get('profile'))} ({product.get('profile')})"]
    if prof.get("intro"):
        lines.append(prof["intro"])
    for name in sections:
        if prof.get(name):
            lines.append(f"**{name}**\n\n{_fill(prof[name], values)}")
    return "\n\n".join(lines)


def section_for(root: Path, cfg: dict, paths: list[str], sections: list[str], base: dict,
                fallback: str = "library") -> str:
    prods = products_touching(cfg, paths)
    if not prods:
        prods = [{"name": "this change", "profile": fallback}]
    return "\n\n".join(render(root, p, sections, base) for p in prods)


# ---------- detection (used by `ratchet init`) ----------

NODE_RULES = [
    ("mobile", {"react-native", "expo"}),
    ("web", {"next", "vite", "react-scripts", "nuxt", "astro", "@sveltejs/kit", "@angular/core", "@remix-run/react",
             "gatsby", "@vitejs/plugin-react"}),
    ("api", {"express", "fastify", "koa", "hono", "@nestjs/core", "@hapi/hapi", "@trpc/server"}),
]


def _read(p: Path) -> str:
    try:
        return p.read_text()
    except (FileNotFoundError, UnicodeDecodeError):
        return ""


def _node_run(pm: str, pkg: workspace.Package) -> Optional[str]:
    for script in ("dev", "start"):
        if script in pkg.scripts:
            return f"{pm} run {script}" if not pkg.dir else f"cd {pkg.dir} && {pm} run {script}"
    return None


def godot_binary() -> str:
    for name in ("godot4", "godot"):
        if shutil.which(name):
            return name
    return "godot"


def detect(root: Path, pkgs: list[workspace.Package], pm: str) -> list[dict]:
    products: list[dict] = []
    for pkg in pkgs:
        paths = [f"{pkg.dir}/**"] if pkg.dir else ["**"]
        base = {"name": pkg.name, "dir": pkg.dir or ".", "paths": paths}
        if pkg.kind == "node":
            data = workspace._load_json(root / pkg.dir / "package.json")
            deps = pkg.deps
            profile = next((name for name, marks in NODE_RULES if deps & marks), None)
            if profile is None and data.get("bin"):
                profile = "cli"
            if profile in ("web", "api"):
                products.append({**base, "profile": profile, "run": _node_run(pm, pkg), "port_env": "PORT",
                                 "url": "http://localhost:{port}/", "ready_text": None, "browser_profile": None})
            elif profile == "mobile":
                products.append({**base, "profile": "mobile", "run": _node_run(pm, pkg), "platform": "ios|android"})
            elif profile == "cli":
                products.append({**base, "profile": "cli", "run": f"node {pkg.dir or '.'}/{_bin_path(data)}"})
        elif pkg.kind == "python":
            text = _read(root / pkg.dir / "pyproject.toml").lower()
            if any(f in text for f in ("fastapi", "flask", "django")):
                products.append({**base, "profile": "api", "run": None, "port_env": "PORT",
                                 "url": "http://localhost:{port}/", "ready_text": None})
            elif "[project.scripts]" in text:
                products.append({**base, "profile": "cli", "run": None})
        elif pkg.kind == "rust":
            text = _read(root / pkg.dir / "Cargo.toml")
            if any(f in text for f in ("axum", "actix-web", "rocket", "warp")):
                products.append({**base, "profile": "api", "run": f"cargo run -p {pkg.name}", "port_env": "PORT",
                                 "url": "http://localhost:{port}/", "ready_text": None})
            elif (root / pkg.dir / "src" / "main.rs").exists():
                products.append({**base, "profile": "cli", "run": f"cargo run -q -p {pkg.name} --"})
        elif pkg.kind == "go" and (root / pkg.dir / "main.go").exists():
            products.append({**base, "profile": "cli", "run": f"go run ./{pkg.dir or '.'}"})
        elif pkg.kind == "godot":
            project = _read(root / pkg.dir / "project.godot")
            scene = re.search(r'run/main_scene="([^"]+)"', project)
            products.append({**base, "profile": "godot", "godot": godot_binary(),
                             "main_scene": scene.group(1) if scene else None})
    if (root / "pubspec.yaml").exists() and "flutter" in _read(root / "pubspec.yaml"):
        products.append({"name": root.name, "dir": ".", "paths": ["lib/**", "test/**", "integration_test/**"],
                         "profile": "mobile", "run": "flutter run", "platform": "ios|android"})
    return products


def _bin_path(data: dict) -> str:
    b = data.get("bin")
    if isinstance(b, str):
        return b
    if isinstance(b, dict) and b:
        return next(iter(b.values()))
    return "index.js"
