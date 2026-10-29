"""Evidence files, the run report (markdown for PRs, HTML for T3's html_render), and free ports."""
from __future__ import annotations

import html
import shutil
import socket
from pathlib import Path
from typing import Optional

from . import state as tasks
from .util import RatchetError, now, slug


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def add_evidence(root: Path, rid: str, target: str, path: str, note: str = "") -> dict:
    src = Path(path).expanduser().resolve()
    if not src.is_file():
        raise RatchetError(f"no such file: {src}")
    run = tasks.require_run(root, rid)
    holder = run if target == "final" else tasks.get(run, target)
    holder.setdefault("evidence", [])
    dest_dir = tasks.run_dir(root, rid) / "evidence" / slug(target)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{len(holder['evidence']) + 1:02d}-{src.name}"
    shutil.copyfile(src, dest)
    item = {"path": str(dest), "note": note, "at": now()}
    holder["evidence"].append(item)
    tasks.save(root, run)
    return item


IMAGE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp")


def markdown(run: dict) -> str:
    lines = [f"## {run['goal']}", "", f"Run `{run['id']}` · {tasks.summary(run)}", ""]
    lines += ["| Task | Status | Rounds | Criteria |", "|---|---|---|---|"]
    for t in run["tasks"]:
        lines.append(f"| {t['id']} {t['title']} | {t['status']} | {t['rounds']} | "
                     + "<br>".join(f"{i}. {c}" for i, c in enumerate(t["criteria"], 1)) + " |")
    verdicts = [(t["id"], n["text"]) for t in run["tasks"] for n in t["notes"] if n["by"] == "judge"]
    if verdicts:
        lines += ["", "### Judge notes (latest per task)"]
        seen = {}
        for tid, text in verdicts:
            seen[tid] = text
        lines += [f"- **{tid}**: {text.splitlines()[0][:300]}" for tid, text in seen.items()]
    if run.get("final_notes"):
        lines += ["", "### Final review", run["final_notes"][:1500]]
    lines += ["", "_Verified by [ratchet](https://github.com/alancunha26/ratchet): every task passed the "
              "deterministic gate and an independent judge._"]
    return "\n".join(lines)


STATUS_VAR = {"done": "--success", "accepted": "--success", "review": "--chart-4", "doing": "--chart-2",
              "todo": "--muted-foreground", "blocked": "--destructive"}


def html_report(run: dict) -> str:
    e = html.escape
    rows = []
    for t in run["tasks"]:
        color = STATUS_VAR.get(t["status"], "--muted-foreground")
        crit = "".join(f"<li>{e(c)}</li>" for c in t["criteria"])
        judge = [n for n in t["notes"] if n["by"] == "judge"]
        verdict = f"<div class='note'>{e(judge[-1]['text'][:600])}</div>" if judge else ""
        shots = "".join(
            f"<figure><img src='{e(ev['path'])}' alt=''><figcaption>{e(ev.get('note') or Path(ev['path']).name)}</figcaption></figure>"
            for ev in t.get("evidence", []) if ev["path"].lower().endswith(IMAGE_EXT))
        files = "".join(f"<li class='mono'>{e(Path(ev['path']).name)} {e(ev.get('note', ''))}</li>"
                        for ev in t.get("evidence", []) if not ev["path"].lower().endswith(IMAGE_EXT))
        rows.append(f"""
<details class="task" {'open' if t['status'] != 'done' or shots else ''}>
  <summary><span class="pill" style="--k:var({color})">{e(t['status'])}</span>
    <b>{e(t['id'])}</b> {e(t['title'])}
    <span class="meta">{'↻' + str(t['rounds']) if t['rounds'] else ''}</span></summary>
  <ol>{crit}</ol>{verdict}
  {f"<div class='shots'>{shots}</div>" if shots else ''}{f"<ul>{files}</ul>" if files else ''}
</details>""")
    counts = {}
    for t in run["tasks"]:
        counts[t["status"]] = counts.get(t["status"], 0) + 1
    stats = "".join(f"<div class='stat'><span class='n' style='color:var({STATUS_VAR.get(s, '--foreground')})'>{n}</span>"
                    f"<span>{e(s)}</span></div>" for s, n in counts.items())
    ci = (run.get("ci") or {}).get("status")
    pr = run.get("pr_url")
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
body{{margin:0;font-family:var(--font-sans);color:var(--foreground);font-size:14px;line-height:1.45}}
.goal{{font-size:15px;font-weight:600;margin:0 0 4px}} .sub{{color:var(--muted-foreground);font-size:12.5px;margin-bottom:12px}}
.stats{{display:flex;gap:10px;margin-bottom:14px;flex-wrap:wrap}}
.stat{{border:1px solid var(--border);border-radius:var(--radius);padding:8px 14px;background:var(--card);display:flex;flex-direction:column;min-width:80px}}
.stat .n{{font-size:20px;font-weight:700}} .stat span:last-child{{color:var(--muted-foreground);font-size:12px}}
.task{{border:1px solid var(--border);border-radius:var(--radius);background:var(--card);padding:10px 14px;margin-bottom:8px}}
summary{{cursor:pointer;display:flex;gap:8px;align-items:center}} .meta{{margin-left:auto;color:var(--muted-foreground);font-size:12px}}
.pill{{font-size:11px;font-weight:600;padding:2px 8px;border-radius:999px;color:var(--k);background:color-mix(in srgb,var(--k) 15%,transparent)}}
ol{{margin:8px 0;padding-left:20px;color:var(--muted-foreground);font-size:13px}}
.note{{font-size:12.5px;border-left:3px solid var(--chart-4);padding:4px 10px;margin:6px 0;white-space:pre-wrap}}
.shots{{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:8px;margin-top:8px}}
figure{{margin:0}} img{{width:100%;border-radius:calc(var(--radius) - 2px);border:1px solid var(--border)}}
figcaption{{font-size:11.5px;color:var(--muted-foreground)}} .mono{{font-family:var(--font-mono);font-size:12px}}
</style></head><body>
<p class="goal">{e(run['goal'])}</p>
<div class="sub">run <span class="mono">{e(run['id'])}</span> on <span class="mono">{e(run['branch'])}</span>
{' · final review: ' + e(run['final']) if run.get('final') else ''}{' · CI: ' + e(ci) if ci else ''}
{f" · <a href='{e(pr)}'>PR</a>" if pr else ''}</div>
<div class="stats">{stats}</div>
{''.join(rows)}
</body></html>"""
