"""`ratchet init`: generate a repo-owned harness from what the repo already uses, then prove it.

Generation is deterministic (kits + discovery). Judgment (reconciling with CI, writing
exercise guides, filling gaps) is the init agent's job, guided by `ratchet brief init`.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from . import harness, kits, proof, verify, workspace
from .util import RatchetError, read_json, write_json

TEMPLATES = Path(__file__).resolve().parents[2] / "templates"
MARK_START, MARK_END = "<!-- ratchet:start -->", "<!-- ratchet:end -->"


def setup_command(root: Path, kinds: set, pm: str) -> Optional[str]:
    cmds = []
    if "node" in kinds:
        cmds.append({"pnpm": "pnpm install --frozen-lockfile", "yarn": "yarn install --immutable",
                     "bun": "bun install --frozen-lockfile",
                     "npm": "npm ci" if (root / "package-lock.json").exists() else "npm install"}[pm])
    if "python" in kinds and (root / "uv.lock").exists():
        cmds.append("uv sync")
    if "rust" in kinds:
        cmds.append("cargo fetch")
    if "go" in kinds:
        cmds.append("go mod download")
    if "godot" in kinds:
        cmds.append(f"{kits.godot_binary()} --headless --path . --import || true")
    return " && ".join(cmds) or None


def plan(root: Path) -> dict:
    """What init would generate, without writing anything."""
    pkgs = workspace.discover(root, {"packages": "auto"})
    pm = workspace.package_manager(root)
    specs, products = [], []
    for pkg in pkgs:
        scope = workspace.scope_for(pkgs, pkg)
        if pkg.kind == "node":
            specs += kits.node_checks(root, pkg, pm, scope)
        elif pkg.kind == "python":
            specs += kits.python_checks(root, pkg, scope)
        elif pkg.kind == "go":
            specs += kits.go_checks(root, pkg, scope)
        elif pkg.kind == "rust":
            specs += kits.rust_checks(root, pkg, scope)
        elif pkg.kind == "godot":
            specs += kits.godot_checks(root, pkg, scope)
        prod = kits.product_for(root, pkg, pm)
        if prod:
            products.append(prod)
    seen, unique = set(), []
    for s in specs:
        while s.name in seen:
            s.name += "-2"
        seen.add(s.name)
        unique.append(s)
    return {"packages": pkgs, "pm": pm, "checks": unique, "products": products,
            "setup": setup_command(root, {p.kind for p in pkgs}, pm), "ci": kits.ci_commands(root)}


def upsert_section(root: Path, name: str) -> str:
    path = root / name
    block = f"{MARK_START}\n{(TEMPLATES / 'agents-section.md').read_text().strip()}\n{MARK_END}"
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


def upsert_t3_json(root: Path, h: dict) -> str:
    """Merge ratchet's scripts into t3.json (committed: every machine and harness gets them)."""
    path = root / "t3.json"
    data = read_json(path, None) or {"$schema": "https://t3.codes/schema/t3.json"}
    scripts = [s for s in data.get("scripts", []) if not str(s.get("name", "")).startswith("ratchet:")]
    if h.get("setup"):
        scripts.append({"name": "ratchet: setup", "command": h["setup"], "icon": "configure",
                        "runOnWorktreeCreate": True, "async": False})
    scripts.append({"name": "ratchet: verify", "command": "ratchet verify --full", "icon": "test"})
    scripts.append({"name": "ratchet: status", "command": "ratchet status && ratchet tick", "icon": "debug"})
    for prod in h.get("products") or []:
        if prod.get("kind") in ("web", "api") and prod.get("run"):
            url = (prod.get("url") or "http://localhost:{port}/").replace("{port}", "3000")
            scripts.append({"name": f"ratchet: run {prod['name']}", "command": f"{prod.get('port_env') or 'PORT'}=3000 {prod['run']}",
                            "icon": "play", "previewUrl": url})
    data["scripts"] = scripts[:50]
    existed = path.exists()
    write_json(path, data)
    return "updated" if existed else "created"


def write(root: Path, p: dict, force: bool = False) -> dict:
    if harness.exists(root) and not force:
        raise RatchetError(f"{harness.HARNESS_FILE} already exists (use --force to regenerate, or `ratchet garden`)")
    vdir = root / ".ratchet" / "verify"
    vdir.mkdir(parents=True, exist_ok=True)
    edir = root / ".ratchet" / "exercise"
    edir.mkdir(parents=True, exist_ok=True)
    checks = []
    for spec in p["checks"]:
        script = vdir / f"{spec.name}.sh"
        note = f"# {spec.note}\n" if spec.note else ""
        script.write_text(f"#!/usr/bin/env bash\n# ratchet check '{spec.name}' (kit: {spec.kit}). This repo owns this file.\n"
                          f"# Write per-test results into $RATCHET_OUT (JUnit XML, *.go.json, *jest.json, *.libtest.txt or result.json).\n"
                          f"{note}set -euo pipefail\n{spec.script}\n")
        os.chmod(script, 0o755)
        entry = {"name": spec.name, "run": f"bash .ratchet/verify/{spec.name}.sh", "level": spec.level,
                 "scope": spec.scope, "min_tests": spec.min_tests, "kit": spec.kit}
        if spec.canary:
            entry["canary"] = spec.canary
        if spec.mutate:
            entry["mutate"] = spec.mutate
        checks.append(entry)
    products = []
    for prod in p["products"]:
        guide = edir / f"{prod['name']}.md"
        if not guide.exists() or force:
            guide.write_text(kits.render_exercise(prod))
        products.append({**prod, "exercise": f".ratchet/exercise/{prod['name']}.md", "exercises": []})
    h = dict(harness.DEFAULTS)
    h.update({"setup": p["setup"], "checks": checks, "products": products})
    harness.save(root, h)
    judge = root / ".ratchet" / "judge.md"
    if not judge.exists():
        judge.write_text((TEMPLATES / "judge.md").read_text())
    gi = root / ".ratchet" / ".gitignore"
    gi.write_text("# ratchet runtime state lives in .git/ratchet, never here\nout/\n")
    return {"checks": len(checks), "products": len(products), "agents_md": upsert_section(root, "AGENTS.md"),
            "claude_md": upsert_section(root, "CLAUDE.md"), "t3_json": upsert_t3_json(root, harness.load(root))}


def prove_all(root: Path, probe: int = 3) -> list[dict]:
    """For every check: green on the current state, red on its canary, and (tests) mutation-sensitive."""
    h = harness.load(root)
    rows = []
    base = {c.name: c for c in verify.baseline(root, h, level="full")}
    for check in harness.checks(h, "full"):
        b = base[check.name]
        row = {"check": check.name, "green": b.ok, "tests": b.total, "format": b.format, "problems": b.problems}
        if b.ok:
            row["canary"] = proof.canary(root, h, check)
            if probe and b.format != "exit" and check.mutate:
                m = proof.mutate(root, h, globs=check.mutate, limit=probe, level="full")
                row["mutation"] = {"killed": m["killed"], "survived": m["survived"]}
        proof.record_proof(root, check.name, row)
        rows.append(row)
    return rows


def format_proofs(rows: list[dict]) -> str:
    lines = ["check                     green  tests  can fail  mutation"]
    for r in rows:
        can = (r.get("canary") or {}).get("status", "-") if r["green"] else "-"
        mut = r.get("mutation")
        mtxt = (f"{mut['killed']}/{mut['killed'] + mut['survived']} killed" if mut and mut["killed"] + mut["survived"] else "no mutable lines" if mut else "-")
        tests = str(r["tests"]) if r["format"] != "exit" else "exit"
        lines.append(f"{r['check']:<25} {'yes' if r['green'] else 'NO':<6} {tests:<6} {can:<9} {mtxt}")
        if not r["green"]:
            lines += [f"    - {p}" for p in r["problems"]]
    return "\n".join(lines)
