"""Starter kits: what ratchet knows about common stacks, used only to *generate* a repo's harness.

Nothing here runs at verification time. `ratchet init` reads a repo, picks matching kits,
and writes checks that wrap the project's own tools into `.ratchet/`. The repo then owns
and edits them. A kit contributes:
  - checks: commands that emit per-test results ratchet can compare (JUnit, go -json, ...)
  - canaries: a failing test in the framework's own style, to prove each check can fail
  - products: how to run and exercise what the package ships (exercise guide templates)
"""
from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import workspace

KITS_DIR = Path(__file__).resolve().parents[2] / "kits"
EXERCISE_DIR = KITS_DIR / "exercise"

JS_CANARY = "test('ratchet canary', () => { throw new Error('ratchet canary: this test must fail') })\n"


@dataclass
class CheckSpec:
    name: str
    script: str                 # body of .ratchet/verify/<name>.sh (bash), runs from the repo root
    level: str = "fast"
    scope: list = field(default_factory=list)
    min_tests: int = 1
    canary: Optional[dict] = None
    mutate: list = field(default_factory=list)
    kit: str = ""
    note: str = ""


def _read(p: Path) -> str:
    try:
        return p.read_text()
    except (FileNotFoundError, UnicodeDecodeError, IsADirectoryError):
        return ""


def _prefix(pkg: workspace.Package) -> str:
    """Check-name prefix: none for a repo-root package, the package's short name otherwise."""
    return f"{workspace.slug_name(pkg)}-" if pkg.dir else ""


def _cd(pkg: workspace.Package) -> str:
    return f'cd "{pkg.dir}"\n' if pkg.dir else ""


def _src_globs(pkg: workspace.Package, exts: str) -> list:
    base = f"{pkg.dir}/" if pkg.dir else ""
    return [f"{base}**/*.{e}" for e in exts.split(",")]


# ---------- per-language detection ----------

def node_checks(root: Path, pkg: workspace.Package, pm: str, scope: list) -> list[CheckSpec]:
    data = workspace._load_json(root / pkg.dir / "package.json")
    deps = set(pkg.deps)
    scripts = pkg.scripts
    test_script = scripts.get("test", "")
    base = f"{pkg.dir}/" if pkg.dir else ""
    pre = _prefix(pkg)
    is_esm = data.get("type") == "module"
    out = []
    mutate = _src_globs(pkg, "js,mjs,cjs,ts,tsx,jsx")
    if "vitest" in deps or "vitest" in test_script:
        out.append(CheckSpec(f"{pre}test", _cd(pkg) + 'npx vitest run --reporter=junit --outputFile="$RATCHET_OUT/junit.xml"',
                             scope=scope, kit="vitest", mutate=mutate,
                             canary={"path": f"{base}__ratchet_canary.test.js",
                                     "content": "import { test } from 'vitest'\n" + JS_CANARY}))
    elif "jest" in deps or re.search(r"\bjest\b", test_script):
        out.append(CheckSpec(f"{pre}test", _cd(pkg) + 'npx jest --ci --json --outputFile="$RATCHET_OUT/jest.json"',
                             scope=scope, kit="jest", mutate=mutate,
                             canary={"path": f"{base}__ratchet_canary.test.js", "content": JS_CANARY}))
    elif "mocha" in deps or "mocha" in test_script:
        out.append(CheckSpec(f"{pre}test", _cd(pkg) + 'npx mocha --reporter xunit --reporter-option output="$RATCHET_OUT/junit.xml"',
                             scope=scope, kit="mocha", mutate=mutate,
                             canary={"path": f"{base}test/__ratchet_canary.spec.js",
                                     "content": "it('ratchet canary', () => { throw new Error('ratchet canary') })\n"}))
    elif "node --test" in test_script:
        out.append(CheckSpec(f"{pre}test", _cd(pkg) + 'node --test --test-reporter=junit --test-reporter-destination="$RATCHET_OUT/junit.xml"',
                             scope=scope, kit="node-test", mutate=mutate,
                             canary={"path": f"{base}__ratchet_canary.test.mjs",
                                     "content": "import test from 'node:test'\n" + JS_CANARY}))
    elif test_script and "no test specified" not in test_script:
        out.append(CheckSpec(f"{pre}test", _cd(pkg) + f"{pm} run test", scope=scope, kit="node-opaque", min_tests=0,
                             note="tests run, but without per-test results: switch the runner to a JUnit reporter"))
    if "@playwright/test" in deps:
        out.append(CheckSpec(f"{pre}e2e", _cd(pkg) + 'PLAYWRIGHT_JUNIT_OUTPUT_NAME="$RATCHET_OUT/junit.xml" npx playwright test --reporter=junit',
                             level="full", scope=scope, kit="playwright"))
    for script, kind in (("lint", "lint"), ("typecheck", "types"), ("type-check", "types"), ("check-types", "types"),
                         ("tsc", "types")):
        if script in scripts and not any(c.name == f"{pre}{kind}" for c in out):
            out.append(CheckSpec(f"{pre}{kind}", _cd(pkg) + f"{pm} run {script}", scope=[f"{base}**"] if base else [],
                                 min_tests=0, kit="node-script"))
    if "build" in scripts:
        out.append(CheckSpec(f"{pre}build", _cd(pkg) + f"{pm} run build", level="full", scope=scope, min_tests=0,
                             kit="node-script"))
    return out


def python_checks(root: Path, pkg: workspace.Package, scope: list) -> list[CheckSpec]:
    text = (_read(root / pkg.dir / "pyproject.toml") + _read(root / pkg.dir / "requirements.txt") +
            _read(root / pkg.dir / "requirements-dev.txt") + _read(root / pkg.dir / "setup.cfg")).lower()
    prefix = "uv run " if (root / "uv.lock").exists() else ""
    pre = _prefix(pkg)
    base = f"{pkg.dir}/" if pkg.dir else ""
    tests_dir = "tests/" if (root / pkg.dir / "tests").is_dir() else ""
    mutate = _src_globs(pkg, "py")
    out = []
    if "pytest" in text or (root / pkg.dir / "pytest.ini").exists() or (root / pkg.dir / "conftest.py").exists():
        out.append(CheckSpec(f"{pre}test", _cd(pkg) + f'{prefix}python -m pytest -q --junitxml="$RATCHET_OUT/junit.xml"',
                             scope=scope, kit="pytest", mutate=mutate,
                             canary={"path": f"{base}{tests_dir}test_ratchet_canary.py",
                                     "content": "def test_ratchet_canary():\n    assert False, 'ratchet canary'\n"}))
    elif list((root / pkg.dir).glob("**/test_*.py")) or (root / pkg.dir / "tests").is_dir():
        out.append(CheckSpec(f"{pre}test", _cd(pkg) + f"{prefix}ratchet junit-unittest {tests_dir or '.'}",
                             scope=scope, kit="unittest", mutate=mutate,
                             canary={"path": f"{base}{tests_dir}test_ratchet_canary.py",
                                     "content": "import unittest\n\n\nclass RatchetCanary(unittest.TestCase):\n"
                                                "    def test_ratchet_canary(self):\n        self.fail('ratchet canary')\n"}))
    if "ruff" in text:
        out.append(CheckSpec(f"{pre}lint", _cd(pkg) + f"{prefix}ruff check .", scope=[f"{base}**"] if base else [],
                             min_tests=0, kit="ruff"))
    if "mypy" in text:
        out.append(CheckSpec(f"{pre}types", _cd(pkg) + f"{prefix}mypy .", scope=[f"{base}**"] if base else [],
                             min_tests=0, kit="mypy"))
    return out


def go_checks(root: Path, pkg: workspace.Package, scope: list) -> list[CheckSpec]:
    pre = _prefix(pkg)
    base = f"{pkg.dir}/" if pkg.dir else ""
    package = "main"
    for f in sorted((root / pkg.dir).glob("*.go")):
        m = re.search(r"(?m)^package\s+(\w+)", _read(f))
        if m:
            package = m.group(1)[:-5] if m.group(1).endswith("_test") else m.group(1)
            break
    return [
        CheckSpec(f"{pre}test", _cd(pkg) + 'go test -json ./... > "$RATCHET_OUT/test.go.json"', scope=scope, kit="go",
                  mutate=_src_globs(pkg, "go"),
                  canary={"path": f"{base}ratchet_canary_test.go",
                          "content": f'package {package}\n\nimport "testing"\n\nfunc TestRatchetCanary(t *testing.T) {{\n\tt.Fatal("ratchet canary")\n}}\n'}),
        CheckSpec(f"{pre}vet", _cd(pkg) + "go vet ./...", scope=[f"{base}**"] if base else [], min_tests=0, kit="go"),
    ]


def rust_checks(root: Path, pkg: workspace.Package, scope: list) -> list[CheckSpec]:
    pre = _prefix(pkg)
    base = f"{pkg.dir}/" if pkg.dir else ""
    return [
        CheckSpec(f"{pre}test", f'set -o pipefail\ncargo test -p {pkg.name} 2>&1 | tee "$RATCHET_OUT/test.libtest.txt"',
                  scope=scope, kit="cargo", mutate=_src_globs(pkg, "rs"),
                  canary={"path": f"{base}tests/ratchet_canary.rs",
                          "content": '#[test]\nfn ratchet_canary() {\n    panic!("ratchet canary");\n}\n'}),
        CheckSpec(f"{pre}clippy", f"cargo clippy -p {pkg.name} --all-targets -- -D warnings",
                  scope=[f"{base}**"] if base else [], min_tests=0, kit="cargo"),
    ]


def godot_binary() -> str:
    for name in ("godot4", "godot"):
        if shutil.which(name):
            return name
    return "godot"


def godot_checks(root: Path, pkg: workspace.Package, scope: list) -> list[CheckSpec]:
    pre = _prefix(pkg)
    base = f"{pkg.dir}/" if pkg.dir else ""
    gd = godot_binary()
    path = pkg.dir or "."
    out = [CheckSpec(f"{pre}load", f"ratchet exec-check --fail-on 'SCRIPT ERROR|Parse Error|^ERROR:' -- {gd} --headless --path {path} --quit",
                     scope=[f"{base}**"] if base else [], min_tests=0, kit="godot",
                     note="Godot exits 0 after script errors, so the load check scans its output")]
    if (root / pkg.dir / "addons" / "gut").is_dir():
        tdir = "test" if (root / pkg.dir / "test").is_dir() else "tests"
        out.append(CheckSpec(f"{pre}test", f'{gd} --headless --path {path} -s addons/gut/gut_cmdln.gd -gdir=res://{tdir} '
                             f'-ginclude_subdirs -gexit -gjunit_xml_file="$RATCHET_OUT/gut.xml"', scope=scope, kit="gut",
                             mutate=_src_globs(pkg, "gd"),
                             canary={"path": f"{base}{tdir}/test_ratchet_canary.gd",
                                     "content": "extends GutTest\n\nfunc test_ratchet_canary():\n\tassert_true(false, \"ratchet canary\")\n"}))
    elif (root / pkg.dir / "tests" / "ratchet_runner.gd").exists():
        out.append(CheckSpec(f"{pre}test", f"{gd} --headless --path {path} -s res://tests/ratchet_runner.gd",
                             scope=scope, kit="godot-ratchet-runner", mutate=_src_globs(pkg, "gd"),
                             canary={"path": f"{base}tests/test_ratchet_canary.gd",
                                     "content": "extends \"res://tests/ratchet_test.gd\"\n\nfunc test_ratchet_canary():\n\tcheck(false, \"ratchet canary\")\n"}))
    return out


# ---------- products: what each package ships and how to exercise it ----------

NODE_PRODUCT_RULES = [
    ("mobile", {"react-native", "expo"}),
    ("web", {"next", "vite", "react-scripts", "nuxt", "astro", "@sveltejs/kit", "@angular/core", "@remix-run/react",
             "gatsby", "@vitejs/plugin-react"}),
    ("api", {"express", "fastify", "koa", "hono", "@nestjs/core", "@hapi/hapi", "@trpc/server"}),
]


def product_for(root: Path, pkg: workspace.Package, pm: str) -> Optional[dict]:
    paths = [f"{pkg.dir}/**"] if pkg.dir else ["**"]
    base = {"name": workspace.slug_name(pkg), "dir": pkg.dir or ".", "paths": paths}
    run = None
    for script in ("dev", "start"):
        if script in pkg.scripts:
            run = f"{pm} run {script}" if not pkg.dir else f"cd {pkg.dir} && {pm} run {script}"
            break
    if pkg.kind == "node":
        data = workspace._load_json(root / pkg.dir / "package.json")
        kind = next((k for k, marks in NODE_PRODUCT_RULES if set(pkg.deps) & marks), None)
        if kind is None and data.get("bin"):
            kind = "cli"
        if kind in ("web", "api"):
            return {**base, "kind": kind, "run": run, "port_env": "PORT", "url": "http://localhost:{port}/",
                    "ready_text": None, "browser_profile": None}
        if kind == "mobile":
            return {**base, "kind": "mobile", "run": run, "platform": "ios|android"}
        if kind == "cli":
            return {**base, "kind": "cli", "run": run or "node ."}
        return {**base, "kind": "library"}
    if pkg.kind == "python":
        text = _read(root / pkg.dir / "pyproject.toml").lower()
        if any(f in text for f in ("fastapi", "flask", "django")):
            return {**base, "kind": "api", "run": None, "port_env": "PORT", "url": "http://localhost:{port}/", "ready_text": None}
        return {**base, "kind": "cli" if "[project.scripts]" in text else "library", "run": None}
    if pkg.kind == "rust":
        text = _read(root / pkg.dir / "Cargo.toml")
        if any(f in text for f in ("axum", "actix-web", "rocket", "warp")):
            return {**base, "kind": "api", "run": f"cargo run -p {pkg.name}", "port_env": "PORT",
                    "url": "http://localhost:{port}/", "ready_text": None}
        if (root / pkg.dir / "src" / "main.rs").exists():
            return {**base, "kind": "cli", "run": f"cargo run -q -p {pkg.name} --"}
        return {**base, "kind": "library"}
    if pkg.kind == "go":
        return {**base, "kind": "cli" if (root / pkg.dir / "main.go").exists() else "library",
                "run": f"go run ./{pkg.dir or '.'}"}
    if pkg.kind == "godot":
        project = _read(root / pkg.dir / "project.godot")
        scene = re.search(r'run/main_scene="([^"]+)"', project)
        return {**base, "kind": "godot", "godot": godot_binary(), "main_scene": scene.group(1) if scene else None}
    return None


def exercise_template(kind: str) -> str:
    p = EXERCISE_DIR / f"{kind}.md"
    return p.read_text() if p.exists() else (EXERCISE_DIR / "library.md").read_text()


def render_exercise(product: dict) -> str:
    """Fill product fields now; leave runtime placeholders ({{R}}, {{scratch}}) for briefs."""
    text = exercise_template(product.get("kind", "library"))
    for key, val in product.items():
        if isinstance(val, (str, int)) and val is not None:
            text = text.replace("{{" + key + "}}", str(val))
    text = re.sub(r"\{\{(port_env|run|url|ready_text|browser_profile|platform|godot|main_scene|dir)\}\}",
                  lambda m: f"({m.group(1)} not set: fill it in)", text)
    header = (f"<!-- ratchet exercise guide for product '{product['name']}' ({product.get('kind')}). "
              "Generated from a starter kit; this repo owns it. Edit freely; keep the ## sections. -->\n")
    return header + text


# ---------- CI: the best source of what "green" means ----------

def ci_commands(root: Path) -> list[dict]:
    out = []
    for wf in sorted((root / ".github" / "workflows").glob("*.y*ml")):
        lines = _read(wf).splitlines()
        i = 0
        while i < len(lines):
            m = re.match(r"^(\s*)(?:-\s*)?run:\s*(.*)$", lines[i])
            if m:
                indent, val = len(m.group(1)), m.group(2).strip()
                if val in ("|", ">", "|-", ">-"):
                    block = []
                    i += 1
                    while i < len(lines) and (not lines[i].strip() or len(lines[i]) - len(lines[i].lstrip()) > indent):
                        if lines[i].strip():
                            block.append(lines[i].strip())
                        i += 1
                    out += [{"workflow": wf.name, "command": b} for b in block]
                    continue
                if val:
                    out.append({"workflow": wf.name, "command": val.strip("'\"")})
            i += 1
    return out
