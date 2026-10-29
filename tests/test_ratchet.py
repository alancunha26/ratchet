"""End-to-end tests: build throwaway git repos and drive the real `ratchet` CLI and hooks."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BIN = REPO / "plugin" / "bin" / "ratchet"
sys.path.insert(0, str(REPO / "plugin" / "lib"))

from ratchet import checks, config, workspace  # noqa: E402


def sh(cmd: str, cwd: Path) -> str:
    return subprocess.run(cmd, shell=True, cwd=cwd, check=True, text=True, capture_output=True).stdout


def node_script(code: int, message: str = "") -> str:
    return f"node -e \"console.log('{message}'); process.exit({code})\""


class Fixture:
    """An npm-workspaces monorepo: core <- app, plus an unrelated pkg `other`."""

    def __init__(self, agents: dict = None, web_deps: bool = False) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "mono"
        self.root.mkdir()
        self.env = {**os.environ, "XDG_CACHE_HOME": str(Path(self.tmp.name) / "cache"), "RATCHET_SLOTS": "4"}
        self.env.pop("RATCHET_RUN", None)
        self.env.pop("RATCHET_GATE", None)
        sh("git init -q -b main && git config user.email t@t && git config user.name t", self.root)
        self.write("package.json", json.dumps({"name": "mono", "private": True, "workspaces": ["packages/*"]}))
        self.pkg("core", {})
        self.pkg("app", {"core": "*", **({"vite": "^5"} if web_deps else {})}, dev=web_deps)
        self.pkg("other", {})
        self.ratchet("init")
        if agents:
            cfg = json.loads((self.root / ".ratchet/config.json").read_text())
            cfg["agents"].update(agents)
            self.write(".ratchet/config.json", json.dumps(cfg))
        sh("git add -A && git commit -qm init && git switch -qc feature", self.root)

    def pkg(self, name: str, deps: dict, test_code: int = 0, dev: bool = False) -> None:
        scripts = {"lint": node_script(0, "lint ok"), "test": node_script(test_code, f"{name} tests")}
        if dev:
            scripts["dev"] = "node server.js"
        self.write(f"packages/{name}/package.json", json.dumps({"name": name, "dependencies": deps, "scripts": scripts}))
        self.write(f"packages/{name}/index.js", f"module.exports = '{name}';\n")

    def write(self, rel: str, text: str) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def ratchet(self, *args: str, stdin: str = "", check: bool = False, cwd: Path = None) -> subprocess.CompletedProcess:
        proc = subprocess.run([sys.executable, str(BIN), *args], cwd=cwd or self.root, env=self.env, input=stdin,
                              text=True, capture_output=True)
        if check and proc.returncode != 0:
            raise AssertionError(f"ratchet {' '.join(args)} failed:\n{proc.stdout}\n{proc.stderr}")
        return proc

    def hook(self, event: str, **payload) -> dict:
        payload.setdefault("cwd", str(self.root))
        payload.setdefault("session_id", "s1")
        out = self.ratchet("hook", event, stdin=json.dumps(payload)).stdout.strip()
        return json.loads(out) if out else {}

    def close(self) -> None:
        self.tmp.cleanup()


class RatchetTest(unittest.TestCase):
    def setUp(self) -> None:
        self.f = Fixture()

    def tearDown(self) -> None:
        self.f.close()

    # ---------- detection ----------

    def test_init_detects_workspace_and_scripts(self) -> None:
        cfg = json.loads((self.f.root / ".ratchet/config.json").read_text())
        self.assertEqual(cfg["checks"]["node"]["fast"], ["script:lint", "script:test"])
        self.assertEqual(cfg["setup"], "npm install")
        self.assertIn("ratchet:start", (self.f.root / "CLAUDE.md").read_text())
        self.assertIn("ratchet:start", (self.f.root / "AGENTS.md").read_text())
        self.assertTrue((self.f.root / ".ratchet/judge.md").exists())

    def test_affected_follows_dependents(self) -> None:
        self.f.write("packages/core/index.js", "module.exports = 'core2';\n")
        data = json.loads(self.f.ratchet("affected", "--json", check=True).stdout)
        self.assertEqual(sorted(p["name"] for p in data["packages"]), ["app", "core"])
        self.assertEqual(data["changed_files"], ["packages/core/index.js"])

    # ---------- verify ----------

    def test_verify_runs_only_affected_and_caches(self) -> None:
        self.f.write("packages/other/index.js", "module.exports = 'x';\n")
        first = json.loads(self.f.ratchet("verify", "--json").stdout)
        self.assertTrue(first["ok"])
        self.assertEqual(first["packages"], ["packages/other"])
        self.assertEqual(sorted(j["name"] for j in first["jobs"]), ["lint", "test"])
        second = json.loads(self.f.ratchet("verify", "--json").stdout)
        self.assertTrue(second["cached"])

    def test_verify_failure_gives_excerpt_and_exit_code(self) -> None:
        self.f.pkg("core", {}, test_code=1)
        proc = self.f.ratchet("verify")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("FAIL packages/core · test", proc.stdout)
        self.assertIn("full log:", proc.stdout)

    def test_nothing_changed_is_trivially_green(self) -> None:
        rep = json.loads(self.f.ratchet("verify", "--json").stdout)
        self.assertTrue(rep["ok"])
        self.assertEqual(rep["jobs"], [])

    # ---------- tamper ----------

    def test_tamper_blocks_skip_until_allowed(self) -> None:
        self.f.write("packages/core/core.test.js", "it.skip('works', () => {});\n")
        proc = self.f.ratchet("verify")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("TAMPER", proc.stdout)
        findings = json.loads(self.f.ratchet("tamper", "--json").stdout)
        self.assertEqual(len(findings), 1)
        self.f.ratchet("allow", findings[0]["id"], "--reason", "upstream bug #12", check=True)
        self.assertEqual(self.f.ratchet("verify").returncode, 0)

    def test_tamper_flags_deleted_tests_and_protected_paths(self) -> None:
        self.f.write("packages/core/core.test.js", "test('a', () => {});\n")
        sh("git add -A && git commit -qm t && git switch -q main && git merge -q feature && git switch -q feature",
           self.f.root)
        (self.f.root / "packages/core/core.test.js").unlink()
        cfg = json.loads((self.f.root / ".ratchet/config.json").read_text())
        cfg["limits"]["gate_attempts"] = 9
        self.f.write(".ratchet/config.json", json.dumps(cfg))
        kinds = sorted(f["kind"] for f in json.loads(self.f.ratchet("tamper", "--json").stdout))
        self.assertEqual(kinds, ["deleted test file", "protected path changed"])

    # ---------- tasks ----------

    def test_task_lifecycle_is_gated_by_checks(self) -> None:
        r = self.f.ratchet
        r("run", "start", "--goal", "ship core v2", check=True)
        r("task", "add", "--from-json", "-", check=True, stdin=json.dumps([
            {"title": "core v2", "criteria": ["core exports core2"], "packages": ["packages/core"]},
            {"title": "app uses v2", "criteria": ["app logs core2"], "deps": ["T1"]},
        ]))
        self.assertEqual(r("task", "next").stdout.strip(), "T1 core v2")
        r("task", "start", "T1", check=True)
        self.f.pkg("core", {}, test_code=1)
        self.assertEqual(r("task", "submit", "T1", "--summary", "done").returncode, 2)  # dirty tree
        sh("git add -A && git commit -qm wip", self.f.root)
        refused = r("task", "submit", "T1", "--summary", "done")
        self.assertEqual(refused.returncode, 2)  # red checks
        self.assertIn("not green", refused.stderr)
        self.f.pkg("core", {})
        sh("git add -A && git commit -qm fix", self.f.root)
        r("task", "submit", "T1", "--summary", "core exports core2 (node -e ...)", check=True)
        r("task", "reject", "T1", "--notes", "1. missing test", "--by", "judge", check=True)
        self.assertEqual(json.loads(r("task", "show", "T1", "--json").stdout)["status"], "doing")
        r("task", "submit", "T1", "--summary", "added test", check=True)
        r("task", "accept", "T1", "--notes", "criterion 1: verified", check=True)
        self.assertEqual(r("task", "next").stdout.strip(), "nothing ready")  # T2 waits for the merge
        r("task", "merge", "T1", check=True)
        self.assertEqual(r("task", "next").stdout.strip(), "T2 app uses v2")
        r("task", "edit", "T2", "--from-json", json.dumps({"criteria": ["app prints core2", "app test asserts it"]}),
          check=True)
        self.assertEqual(len(json.loads(r("task", "show", "T2", "--json").stdout)["criteria"]), 2)

    def test_judge_rounds_limit_blocks_task_and_logs_blocker(self) -> None:
        r = self.f.ratchet
        r("run", "start", "--goal", "g", check=True)
        r("task", "add", "--title", "t", "--criteria", "c", check=True)
        r("task", "start", "T1", check=True)
        for _ in range(3):  # judge_rounds = 2, so the third reject blocks
            r("task", "submit", "T1", "--summary", "s", check=True)
            r("task", "reject", "T1", "--notes", "nope", check=True)
        self.assertEqual(json.loads(r("task", "show", "T1", "--json").stdout)["status"], "blocked")
        self.assertEqual(json.loads(r("blocker", "list", "--json").stdout)[0]["kind"], "judge")

    # ---------- hooks ----------

    def test_session_start_injects_context(self) -> None:
        out = self.f.hook("session-start", source="startup")
        self.assertIn("[ratchet]", out["hookSpecificOutput"]["additionalContext"])

    def test_stop_gate_passes_silently_when_session_changed_nothing(self) -> None:
        self.f.write("packages/core/index.js", "module.exports = 'x';\n")
        self.f.hook("session-start", source="startup")  # tree already dirty at start
        self.assertEqual(self.f.hook("stop"), {})

    def test_stop_gate_blocks_on_failure_then_gives_up_after_limit(self) -> None:
        self.f.hook("session-start", source="startup")
        self.f.pkg("core", {}, test_code=1)
        attempts = config.DEFAULTS["limits"]["gate_attempts"]
        for i in range(attempts):
            out = self.f.hook("stop", stop_hook_active=i > 0)
            self.assertEqual(out.get("decision"), "block")
            self.assertIn(f"attempt {i + 1}/{attempts}", out["reason"])
        final = self.f.hook("stop", stop_hook_active=True)
        self.assertNotIn("decision", final)
        self.assertIn("still failing", final["systemMessage"])
        self.assertEqual(json.loads(self.f.ratchet("blocker", "list", "--json").stdout)[0]["kind"], "gate")

    def test_stop_gate_passes_with_message_when_green(self) -> None:
        self.f.hook("session-start", source="startup")
        self.f.write("packages/core/index.js", "module.exports = 'v2';\n")
        out = self.f.hook("stop")
        self.assertIn("ratchet gate ✓", out["systemMessage"])

    def test_subagent_gate_only_applies_to_workers(self) -> None:
        self.f.pkg("core", {}, test_code=1)
        self.assertEqual(self.f.hook("subagent-stop", agent_type="Explore", agent_id="a1"), {})
        self.assertEqual(self.f.hook("subagent-stop", agent_type="ratchet:worker", agent_id="a2")["decision"], "block")

    def test_gate_disabled_by_env(self) -> None:
        self.f.pkg("core", {}, test_code=1)
        self.f.env["RATCHET_GATE"] = "off"
        self.assertEqual(self.f.hook("stop"), {})

    def test_pre_edit_denies_direct_state_edits(self) -> None:
        state = sh("git rev-parse --absolute-git-dir", self.f.root).strip() + "/ratchet/runs/x/run.json"
        out = self.f.hook("pre-edit", tool_name="Write", tool_input={"file_path": state})
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertEqual(self.f.hook("pre-edit", tool_name="Edit",
                                     tool_input={"file_path": str(self.f.root / "a.js")}), {})

    def test_hooks_are_inert_outside_ratchet_repos(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            sh("git init -q", Path(d))
            proc = subprocess.run([sys.executable, str(BIN), "hook", "stop"], input=json.dumps({"cwd": d}),
                                  text=True, capture_output=True)
            self.assertEqual((proc.returncode, proc.stdout), (0, ""))


class OrchestrationTest(unittest.TestCase):
    """Parallel mode (max_parallel 2): worker and judge threads in their own worktrees."""

    def setUp(self) -> None:
        self.f = Fixture(agents={"max_parallel": 2})
        self.r = self.f.ratchet
        self.r("run", "start", "--goal", "ship v2", check=True)
        self.r("task", "add", "--from-json", "-", check=True, stdin=json.dumps([
            {"title": "core v2", "criteria": ["core exports v2"], "packages": ["packages/core"]},
            {"title": "other v2", "criteria": ["other exports v2"], "packages": ["packages/other"]},
            {"title": "app v2", "criteria": ["app uses v2"], "deps": ["T1"]},
        ]))

    def tearDown(self) -> None:
        self.f.close()

    def tick(self) -> list:
        return json.loads(self.r("tick", "--json", check=True).stdout)["actions"]

    def worktree(self, name: str, ref: str, detach: bool = False) -> Path:
        path = Path(self.f.tmp.name) / name
        flag = f"--detach {path} {ref}" if detach else f"-b {name} {path} {ref}"
        sh(f"git worktree add -q {flag}", self.f.root)
        return path

    def work(self, tid: str, wt: Path, rel: str, text: str) -> str:
        self.r("task", "start", tid, "--run", "feature", check=True, cwd=wt)
        (wt / rel).write_text(text)
        sh(f"git add -A && git commit -qm '{tid}'", wt)
        self.r("task", "submit", tid, "--run", "feature", "--summary", "done", check=True, cwd=wt)
        return sh("git rev-parse HEAD", wt).strip()

    def test_full_run_through_t3_style_threads(self) -> None:
        acts = self.tick()
        self.assertEqual([(a["do"], a.get("mode")) for a in acts], [("dispatch_judge", "contract")])
        self.r("run", "dispatch", "contract", "--thread", "c1", check=True)
        self.assertEqual(self.tick()[0]["do"], "wait")
        self.r("run", "contract", "ok", "--notes", "testable", check=True)

        # Two disjoint tasks go out in parallel; T3 waits for T1 to merge.
        acts = self.tick()
        self.assertEqual([(a["do"], a.get("task")) for a in acts], [("dispatch_worker", "T1"), ("dispatch_worker", "T2")])
        self.assertEqual(acts[0]["delegate"]["isolation"], "worktree")
        self.assertEqual(acts[0]["delegate"]["mode"], "async")
        brief = self.r("brief", "worker", "T1", "--run", "feature", check=True).stdout
        self.assertIn(str(BIN), brief)
        self.assertIn("core exports v2", brief)
        self.assertIn("RATCHET T1 submitted", brief)
        self.r("task", "dispatch", "T1", "--role", "worker", "--thread", "w1", check=True)
        self.r("task", "dispatch", "T2", "--role", "worker", "--thread", "w2", check=True)
        self.assertEqual([a["do"] for a in self.tick()], ["wait"])

        # T1's worker submits from its own worktree; T2's worker dies without submitting.
        wt1 = self.worktree("t1", "feature")
        sha1 = self.work("T1", wt1, "packages/core/index.js", "module.exports = 'v2';\n")
        self.assertIn("T2 worker", self.r("release", "--thread", "w2", "--reason", "crashed", check=True).stdout)
        self.assertEqual(self.r("release", "--thread", "w1", "--reason", "done").stdout.strip(), "released: nothing")
        acts = self.tick()
        self.assertEqual([(a["do"], a.get("task")) for a in acts], [("dispatch_judge", "T1"), ("dispatch_worker", "T2")])
        judge_brief = self.r("brief", "judge", "T1", "--run", "feature", check=True).stdout
        self.assertIn(f"git checkout --detach {sha1}", judge_brief)
        self.assertIn("Judge calibration", judge_brief)
        self.assertIn("library", judge_brief)  # no products configured: library profile guide

        # The judge accepts from a detached checkout of the submitted commit.
        self.r("task", "dispatch", "T1", "--role", "judge", "--thread", "j1", check=True)
        wtj = self.worktree("j1", sha1, detach=True)
        shot = Path(self.f.tmp.name) / "shot.png"
        shot.write_bytes(b"\x89PNG fake")
        self.r("evidence", "add", "T1", str(shot), "--run", "feature", "--note", "criterion 1", check=True, cwd=wtj)
        self.r("task", "accept", "T1", "--run", "feature", "--by", "judge", "--notes", "1. PASS", check=True, cwd=wtj)
        self.assertEqual(self.f.hook("stop", cwd=str(wtj)), {})  # detached review checkout: no gate

        # Merge into the run branch, then T3 (no declared packages) waits for T2's worker to finish.
        acts = self.tick()
        self.assertEqual(acts[0]["do"], "merge")
        self.r("task", "merge", "T1", check=True)
        self.r("task", "dispatch", "T2", "--role", "worker", "--thread", "w2b", check=True)
        self.assertEqual([a["do"] for a in self.tick()], ["wait"])

        wt2 = self.worktree("t2", "feature")
        self.work("T2", wt2, "packages/other/index.js", "module.exports = 'other v2';\n")
        for tid, n in (("T2", "j2"),):
            self.r("task", "dispatch", tid, "--role", "judge", "--thread", n, check=True)
            self.r("task", "accept", tid, "--run", "feature", "--notes", "ok", check=True, cwd=wt2)
            self.r("task", "merge", tid, check=True)
        self.assertEqual([(a["do"], a.get("task")) for a in self.tick()], [("dispatch_worker", "T3")])
        self.r("task", "dispatch", "T3", "--role", "worker", "--thread", "w3", check=True)
        wt3 = self.worktree("t3", "feature")
        self.work("T3", wt3, "packages/app/index.js", "module.exports = require('../core');\n")
        self.r("task", "dispatch", "T3", "--role", "judge", "--thread", "j3", check=True)
        self.r("task", "accept", "T3", "--run", "feature", "--notes", "ok", check=True, cwd=wt3)
        self.r("task", "merge", "T3", check=True)

        # Final review, PR, CI red → fix round → green.
        self.assertEqual(self.tick()[0].get("mode"), "final")
        self.assertIn("Tasks, all accepted", self.r("brief", "final", "--run", "feature", check=True).stdout)
        self.r("run", "dispatch", "final", "--thread", "f1", check=True)
        self.r("run", "final", "accept", "--notes", "goal met", check=True)
        self.assertEqual(self.tick()[0]["do"], "open_pr")
        self.r("run", "pr", "https://github.com/x/y/pull/1", check=True)
        self.assertEqual(self.tick()[0]["do"], "watch_ci")
        self.r("run", "ci", "red", "--details", "lint job failed", check=True)
        self.assertEqual(self.tick()[0]["do"], "fix_ci")
        self.r("run", "ci", "fixing", check=True)
        self.assertEqual(self.tick()[0]["do"], "fix_ci")  # nothing added yet
        self.r("task", "add", "--title", "fix lint", "--criteria", "lint job passes", check=True)
        self.r("task", "dispatch", "T4", "--role", "worker", "--thread", "w4", check=True)
        wt4 = self.worktree("t4", "feature")
        self.work("T4", wt4, "packages/app/README.md", "lint fixed\n")
        self.r("task", "dispatch", "T4", "--role", "judge", "--thread", "j4", check=True)
        self.r("task", "accept", "T4", "--run", "feature", "--notes", "ok", check=True, cwd=wt4)
        self.assertEqual(self.tick()[0]["do"], "merge")
        self.r("task", "merge", "T4", check=True)
        self.assertEqual(self.tick()[0]["do"], "push")
        self.r("run", "pushed", check=True)
        self.r("run", "ci", "green", check=True)
        self.assertEqual(self.tick()[0]["do"], "report")

        html_out = self.r("report", "--html", check=True).stdout
        self.assertIn("ship v2", html_out)
        self.assertIn("01-shot.png", html_out)
        self.assertIn("| T1 core v2 | done |", self.r("report", check=True).stdout)

    def test_merge_conflict_sends_task_back_with_resume_point(self) -> None:
        self.r("run", "contract", "ok", check=True)
        for tid in ("T1", "T2"):
            self.r("task", "dispatch", tid, "--role", "worker", "--thread", f"w{tid}", check=True)
        a, b = self.worktree("ca", "feature"), self.worktree("cb", "feature")
        self.work("T1", a, "packages/core/index.js", "module.exports = 'A';\n")
        sha_b = self.work("T2", b, "packages/core/index.js", "module.exports = 'B';\n")
        for tid, wt in (("T1", a), ("T2", b)):
            self.r("task", "dispatch", tid, "--role", "judge", "--thread", f"j{tid}", check=True)
            self.r("task", "accept", tid, "--run", "feature", "--notes", "ok", check=True, cwd=wt)
        self.r("task", "merge", "T1", check=True)
        proc = self.r("task", "merge", "T2")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("merge conflict", proc.stdout)
        self.assertEqual(sh("git status --porcelain", self.f.root).strip(), "")  # merge aborted cleanly
        acts = self.tick()
        self.assertEqual([(x["do"], x.get("task")) for x in acts], [("dispatch_worker", "T2")])
        self.assertIn(f"git merge --no-edit {sha_b}", self.r("brief", "worker", "T2", check=True).stdout)

    def test_git_output_with_non_utf8_bytes_does_not_crash(self) -> None:
        (self.f.root / "packages/core/blob.dat").write_bytes(b"\x89PNG not really binary\n")
        self.assertEqual(self.r("tamper").returncode, 0)

    def test_worker_attempts_limit_blocks_and_escalates(self) -> None:
        self.r("run", "contract", "ok", check=True)
        for i in range(3):
            self.r("task", "dispatch", "T2", "--role", "worker", "--thread", f"w{i}", check=True)
            self.r("release", "--thread", f"w{i}", "--reason", "no submit", check=True)
        self.assertEqual(json.loads(self.r("task", "show", "T2", "--json").stdout)["status"], "blocked")
        self.r("task", "block", "T1", "--reason", "needs a human decision", check=True)
        acts = self.tick()
        self.assertEqual(acts[0]["do"], "escalate")

    def test_port_is_free_number(self) -> None:
        self.assertTrue(1024 < int(self.r("port", check=True).stdout) < 65536)


class SharedCheckoutTest(unittest.TestCase):
    """Default mode: one task at a time in the run's checkout (one dependency install per run)."""

    def setUp(self) -> None:
        self.f = Fixture(agents={"contract_review": False})
        self.r = self.f.ratchet
        self.r("run", "start", "--goal", "ship v2", check=True)
        self.r("task", "add", "--from-json", "-", check=True, stdin=json.dumps([
            {"title": "core v2", "criteria": ["core exports v2"], "packages": ["packages/core"]},
            {"title": "other v2", "criteria": ["other exports v2"], "packages": ["packages/other"]},
        ]))

    def tearDown(self) -> None:
        self.f.close()

    def tick(self) -> list:
        return json.loads(self.r("tick", "--json", check=True).stdout)["actions"]

    def commit_work(self, tid: str, rel: str, text: str) -> str:
        self.r("task", "start", tid, check=True)
        (self.f.root / rel).write_text(text)
        sh(f"git add -A && git commit -qm {tid}", self.f.root)
        return sh("git rev-parse HEAD", self.f.root).strip()

    def test_one_task_at_a_time_in_the_run_checkout(self) -> None:
        acts = self.tick()
        self.assertEqual([(a["do"], a.get("task")) for a in acts], [("dispatch_worker", "T1")])
        self.assertEqual(acts[0]["delegate"]["isolation"], "shared")
        self.assertIn("git stash", acts[0]["precondition"])
        self.assertIn("run's own checkout", self.r("brief", "worker", "T1", check=True).stdout)
        self.r("task", "dispatch", "T1", "--role", "worker", "--thread", "w1", check=True)
        start = sh("git rev-parse HEAD", self.f.root).strip()
        sha = self.commit_work("T1", "packages/core/index.js", "module.exports = 'v2';\n")
        self.r("task", "submit", "T1", "--summary", "done", check=True)
        acts = self.tick()
        self.assertEqual([(a["do"], a.get("task")) for a in acts], [("dispatch_judge", "T1")])  # T2 waits
        judge = self.r("brief", "judge", "T1", check=True).stdout
        self.assertIn(f"Confirm `git rev-parse HEAD` is `{sha}`", judge)
        self.assertIn(f"git diff --stat {start} {sha}", judge)
        self.r("task", "dispatch", "T1", "--role", "judge", "--thread", "j1", check=True)
        self.r("task", "accept", "T1", "--notes", "ok", check=True)
        self.assertEqual(self.tick()[0]["do"], "merge")
        self.r("task", "merge", "T1", check=True)
        self.assertEqual([(a["do"], a.get("task")) for a in self.tick()], [("dispatch_worker", "T2")])

    def test_blocked_task_is_rolled_back_before_the_next(self) -> None:
        self.r("task", "dispatch", "T1", "--role", "worker", "--thread", "w1", check=True)
        start = sh("git rev-parse HEAD", self.f.root).strip()
        self.commit_work("T1", "packages/core/index.js", "module.exports = 'half done';\n")
        self.r("task", "block", "T1", "--reason", "spec contradicts itself", check=True)
        acts = self.tick()
        self.assertEqual([(a["do"], a.get("task")) for a in acts], [("rollback", "T1")])
        self.r("task", "rollback", "T1", check=True)
        self.assertEqual(sh("git rev-parse HEAD", self.f.root).strip(), start)
        self.assertEqual([(a["do"], a.get("task")) for a in self.tick()], [("dispatch_worker", "T2")])

    def test_judge_panel_majority_decides(self) -> None:
        cfg = json.loads((self.f.root / ".ratchet/config.json").read_text())
        cfg["agents"]["judge_panel"] = 3
        sh("git switch -q main", self.f.root)
        self.f.write(".ratchet/config.json", json.dumps(cfg))
        sh("git commit -qam panel && git switch -q feature && git merge -q main", self.f.root)
        self.r("task", "dispatch", "T1", "--role", "worker", "--thread", "w1", check=True)
        self.commit_work("T1", "packages/core/index.js", "module.exports = 'v2';\n")
        self.r("task", "submit", "T1", "--summary", "done", check=True)
        acts = self.tick()
        self.assertEqual(acts[0]["count"], 3)
        for i in range(3):
            self.r("task", "dispatch", "T1", "--role", "judge", "--thread", f"j{i}", check=True)
        self.assertNotEqual(self.r("task", "dispatch", "T1", "--role", "judge", "--thread", "j4").returncode, 0)
        out = self.r("task", "accept", "T1", "--notes", "looks right", "--thread", "j0", check=True).stdout
        self.assertIn("1/3 votes", out)
        self.r("task", "reject", "T1", "--notes", "jump feels floaty", "--thread", "j1", check=True)
        self.r("task", "accept", "T1", "--notes", "matches the reference", "--thread", "j2", check=True)
        self.assertEqual(json.loads(self.r("task", "show", "T1", "--json").stdout)["status"], "accepted")


class ProductsTest(unittest.TestCase):
    def test_init_detects_products_writes_agents_md_and_t3_json(self) -> None:
        f = Fixture(web_deps=True)
        try:
            cfg = json.loads((f.root / ".ratchet/config.json").read_text())
            web = [p for p in cfg["products"] if p["profile"] == "web"]
            self.assertEqual(web[0]["name"], "app")
            self.assertEqual(web[0]["run"], "cd packages/app && npm run dev")
            self.assertIn("ratchet:start", (f.root / "AGENTS.md").read_text())
            scripts = {s["name"]: s for s in json.loads((f.root / "t3.json").read_text())["scripts"]}
            self.assertEqual(scripts["ratchet: setup"]["runOnWorktreeCreate"], True)
            self.assertEqual(scripts["ratchet: setup"]["async"], False)
            self.assertEqual(scripts["ratchet: run app"]["previewUrl"], "http://localhost:3000/")
            # a task touching the web package gets the web guide; one touching core gets the library guide
            r = f.ratchet
            r("run", "start", "--goal", "g", check=True)
            r("task", "add", "--from-json", "-", check=True, stdin=json.dumps([
                {"title": "ui", "criteria": ["c"], "packages": ["packages/app"]},
                {"title": "lib", "criteria": ["c"], "packages": ["packages/core"]}]))
            web_brief = r("brief", "worker", "T1", check=True).stdout
            self.assertIn("preview_open", web_brief)
            self.assertIn("cd packages/app && npm run dev", web_brief)
            self.assertIn("tiny consumer", r("brief", "worker", "T2", check=True).stdout)
            self.assertIn("Good criteria", r("brief", "plan", check=True).stdout.replace("What good criteria", "Good criteria"))
            self.assertIn("web", r("profiles", check=True).stdout)
        finally:
            f.close()

    def test_godot_project_is_a_package_with_output_scanning_checks(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "game" / "addons" / "gut").mkdir(parents=True)
            (root / "game" / "project.godot").write_text('config/name="Skate"\nrun/main_scene="res://main.tscn"\n')
            sh("git init -q -b main && git config user.email t@t && git config user.name t", root)
            subprocess.run([sys.executable, str(BIN), "init"], cwd=root, check=True, capture_output=True)
            cfg = json.loads((root / ".ratchet/config.json").read_text())
            self.assertIn("exec-check", cfg["checks"]["godot"]["fast"][0])
            self.assertIn("gut_cmdln.gd", cfg["checks"]["godot"]["fast"][1])
            prod = cfg["products"][0]
            self.assertEqual((prod["profile"], prod["name"], prod["main_scene"]), ("godot", "Skate", "res://main.tscn"))

    def test_exec_check_fails_on_matching_output_even_with_exit_zero(self) -> None:
        def run(*args):
            return subprocess.run([sys.executable, str(BIN), "exec-check", *args], capture_output=True, text=True)
        self.assertEqual(run("--fail-on", "SCRIPT ERROR", "--", "echo", "SCRIPT ERROR: bad").returncode, 1)
        self.assertEqual(run("--fail-on", "SCRIPT ERROR", "--", "echo", "all good").returncode, 0)
        self.assertEqual(run("--", "false").returncode, 1)

    def test_start_builds_a_t3_thread_start_call(self) -> None:
        f = Fixture()
        try:
            fake = Path(f.tmp.name) / "bin"
            fake.mkdir()
            (fake / "t3").write_text("#!/bin/sh\necho fake\n")
            (fake / "t3").chmod(0o755)
            f.env["PATH"] = f"{fake}:{f.env['PATH']}"
            out = f.ratchet("start", "Add SSO to the dashboard", "--provider", "codex", "--dry-run", check=True).stdout
            self.assertIn("thread start", out)
            self.assertIn("--worktree main --branch ratchet/add-sso-to-the-dashboard", out)
            self.assertIn("--provider codex", out)
            self.assertIn("brief coordinator", out)
        finally:
            f.close()

    def test_v02_config_is_upgraded(self) -> None:
        up = config._upgrade({"t3": {"max_parallel": 3}, "judge": {"model": "sonnet"},
                              "app": {"dev": "pnpm dev", "ui_paths": ["web/**"]}})
        self.assertEqual(up["agents"]["max_parallel"], 3)
        self.assertEqual(up["agents"]["judge"]["model"], "sonnet")
        self.assertEqual(up["products"][0]["run"], "pnpm dev")


class DiscoveryTest(unittest.TestCase):
    def _repo(self, files: dict) -> Path:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        for rel, text in files.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(textwrap.dedent(text))
        return root

    def tearDown(self) -> None:
        if hasattr(self, "tmp"):
            self.tmp.cleanup()

    def test_cargo_workspace_with_path_deps(self) -> None:
        root = self._repo({
            "Cargo.toml": '[workspace]\nmembers = ["crates/*"]\n',
            "crates/a/Cargo.toml": '[package]\nname = "a"\n',
            "crates/b/Cargo.toml": '[package]\nname = "b"\n[dependencies]\na = { path = "../a" }\n',
        })
        pkgs = workspace.discover_rust(root)
        self.assertEqual(sorted(p.name for p in pkgs), ["a", "b"])
        hit = workspace.affected(pkgs, ["crates/a/src/lib.rs"])
        self.assertEqual(sorted(p.name for p in hit), ["a", "b"])
        cfg = config._merge(config.DEFAULTS, {})
        cfg["checks"] = config.DEFAULT_CHECKS
        jobs = checks.plan(root, cfg, hit, ["crates/a/src/lib.rs"], "fast")
        self.assertTrue(any("-p a -p b" in j.cmd or "-p b -p a" in j.cmd for j in jobs))

    def test_pnpm_go_work_and_uv(self) -> None:
        root = self._repo({
            "package.json": '{"name": "r"}',
            "pnpm-workspace.yaml": "packages:\n  - 'apps/*'\n  - \"libs/*\"\n",
            "apps/web/package.json": '{"name": "web"}',
            "libs/ui/package.json": '{"name": "ui"}',
            "go.work": "go 1.22\nuse (\n  ./svc\n)\n",
            "svc/go.mod": "module example.com/svc\n",
            "pyproject.toml": "[tool.uv.workspace]\nmembers = [\"py/*\"]\n",
            "py/tool/pyproject.toml": "[project]\nname = \"tool\"\n",
        })
        names = sorted((p.kind, p.name) for p in workspace.discover(root, {"packages": "auto"}))
        self.assertEqual(names, [("go", "example.com/svc"), ("node", "ui"), ("node", "web"), ("python", "tool")])
        self.assertEqual(workspace.package_manager(root), "pnpm")

    def test_turbo_repo_gets_batched_turbo_checks(self) -> None:
        from ratchet import scaffold

        root = self._repo({
            "package.json": '{"name": "r", "workspaces": ["apps/*"]}',
            "turbo.json": "{}",
            "pnpm-lock.yaml": "",
            "apps/web/package.json": '{"name": "web", "scripts": {"lint": "x", "check-types": "x", "test": "x", "build": "x"}}',
        })
        node = scaffold.detect(root)["config"]["checks"]["node"]
        self.assertEqual(node["fast"], ["batch:pnpm exec turbo run lint check-types test {filters} --output-logs=errors-only"])
        self.assertEqual(node["full"], ["batch:pnpm exec turbo run build {filters} --output-logs=errors-only"])

    def test_excerpt_keeps_relevant_lines(self) -> None:
        noise = "\n".join(f"compiling module {i}" for i in range(500))
        out = checks.excerpt(noise + "\nsrc/a.ts:10:5 - error TS2322: bad\n" + noise)
        self.assertIn("error TS2322", out)
        self.assertLess(len(out.splitlines()), 10)

    def test_excerpt_drops_runtime_internal_frames(self) -> None:
        out = checks.excerpt("AssertionError: 5 !== 15\n    at TestContext.<anonymous> (/r/app/index.test.js:3:30)\n"
                             "    at Test.run (node:internal/test_runner/test:1062:25)\n"
                             "npm error path /r/app\nnpm error Lifecycle script `test` failed")
        self.assertIn("index.test.js:3:30", out)
        self.assertNotIn("node:internal", out)
        self.assertNotIn("npm error path", out)
        self.assertIn("Lifecycle script", out)


if __name__ == "__main__":
    unittest.main()
