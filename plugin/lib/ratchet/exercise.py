"""Exercise scripts: replayable product checks that produce a reward and evidence.

A product in the harness can declare exercises:
  {"name": "invoice-page", "run": "bash .ratchet/exercise/web/invoice.sh",
   "compare": {"images": 0.01},                       # max differing-pixel ratio vs the base
   "budgets": {"fps": {"min_ratio": 0.9}, "ms": {"max_ratio": 1.2}}}
The script runs from the repo root with RATCHET_OUT (where to write), PORT (a free port)
and RATCHET_PHASE (head|base). It writes $RATCHET_OUT/reward.json:
  {"pass": true, "metrics": {"fps": 58.2}, "notes": "..."}
plus any artifacts (PNG frames, logs, transcripts). With --base, ratchet also runs it on
the change's base (sources reverted in place) and compares images and metrics.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

from . import imagediff
from .basestate import at_base
from .report import free_port
from .runner import BIN_DIR, env_for
from .util import RatchetError, log_event, read_json, slug, state_dir, write_json


def all_exercises(h: dict) -> list[dict]:
    out = []
    for prod in h.get("products") or []:
        for ex in prod.get("exercises") or []:
            out.append({**ex, "product": prod["name"]})
    return out


def find(h: dict, name: str) -> dict:
    for ex in all_exercises(h):
        if ex["name"] == name:
            return ex
    raise RatchetError(f"no exercise named {name} (see `ratchet exercise list`)")


def _run_once(root: Path, ex: dict, phase: str, tag: str) -> dict:
    out = state_dir(root) / "exercise" / slug(tag) / slug(ex["name"]) / phase
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    env = env_for(root, out, {"PORT": str(free_port()), "RATCHET_PHASE": phase})
    start = time.time()
    try:
        proc = subprocess.run(ex["run"], shell=True, cwd=str(root), env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True, errors="replace", timeout=int(ex.get("timeout", 600)))
        code, log = proc.returncode, proc.stdout
    except subprocess.TimeoutExpired:
        code, log = 124, "[ratchet] exercise timed out"
    (out / "exercise.log").write_text(log)
    reward = read_json(out / "reward.json", None) or {}
    return {"phase": phase, "code": code, "seconds": round(time.time() - start, 2), "dir": str(out),
            "pass": bool(reward.get("pass", code == 0)) and code == 0, "metrics": reward.get("metrics") or {},
            "notes": reward.get("notes", ""),
            "artifacts": sorted(str(p.relative_to(out)) for p in out.rglob("*") if p.is_file()
                                and p.name not in ("reward.json", "exercise.log"))}


def run(root: Path, h: dict, name: str, start: Optional[str] = None, tag: str = "adhoc") -> dict:
    ex = find(h, name)
    head = _run_once(root, ex, "head", tag)
    result = {"exercise": name, "product": ex["product"], "head": head, "base": None, "images": [], "budgets": [],
              "ok": head["pass"]}
    if start:
        with at_base(root, start, keep_tests=False):
            base = _run_once(root, ex, "base", tag)
        result["base"] = base
        limit = (ex.get("compare") or {}).get("images")
        for rel in head["artifacts"]:
            if not rel.lower().endswith(".png") or rel not in base["artifacts"]:
                continue
            d = imagediff.diff(Path(base["dir"]) / rel, Path(head["dir"]) / rel,
                               Path(head["dir"]) / (rel[:-4] + ".diff.png"))
            d["image"] = rel
            d["within_limit"] = None if limit is None else (d["same_size"] and d["ratio"] <= float(limit))
            result["images"].append(d)
            if d["within_limit"] is False:
                result["ok"] = False
        for metric, rule in (ex.get("budgets") or {}).items():
            hv, bv = head["metrics"].get(metric), base["metrics"].get(metric)
            row = {"metric": metric, "head": hv, "base": bv, "rule": rule, "ok": None}
            if isinstance(hv, (int, float)) and isinstance(bv, (int, float)) and bv:
                ratio = hv / bv
                row["ratio"] = round(ratio, 3)
                row["ok"] = (ratio >= rule.get("min_ratio", 0)) and (ratio <= rule.get("max_ratio", float("inf")))
            if isinstance(hv, (int, float)):
                if "min" in rule:
                    row["ok"] = (row["ok"] is not False) and hv >= rule["min"]
                if "max" in rule:
                    row["ok"] = (row["ok"] is not False) and hv <= rule["max"]
            if row["ok"] is False:
                result["ok"] = False
            result["budgets"].append(row)
    write_json(state_dir(root) / "exercise" / slug(tag) / slug(name) / "result.json", result)
    log_event(root, "exercise", name=name, ok=result["ok"])
    return result


def format_result(r: dict) -> str:
    h = r["head"]
    lines = [f"exercise {r['exercise']} ({r['product']}): {'PASS' if r['ok'] else 'FAIL'} — head "
             f"{'passed' if h['pass'] else 'failed'} in {h['seconds']}s, artifacts in {h['dir']}"]
    if h["metrics"]:
        lines.append("  metrics: " + ", ".join(f"{k}={v}" for k, v in h["metrics"].items()))
    for d in r["images"]:
        if not d["same_size"]:
            lines.append(f"  image {d['image']}: size changed {d['size_a']} → {d['size_b']}")
        else:
            lim = "" if d["within_limit"] is None else (" (within limit)" if d["within_limit"] else " (OVER LIMIT)")
            lines.append(f"  image {d['image']}: {d['ratio'] * 100:.2f}% pixels differ from the base{lim}; diff: {d['diff_image']}")
    for b in r["budgets"]:
        lines.append(f"  budget {b['metric']}: head {b['head']} vs base {b['base']} (ratio {b.get('ratio')}) "
                     f"{'ok' if b['ok'] else 'FAIL' if b['ok'] is False else 'n/a'}")
    if h["notes"]:
        lines.append(f"  notes: {h['notes']}")
    return "\n".join(lines)
