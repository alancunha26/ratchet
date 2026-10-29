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

    def __init__(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "mono"
        self.root.mkdir()
        self.env = {**os.environ, "XDG_CACHE_HOME": str(Path(self.tmp.name) / "cache"), "RATCHET_SLOTS": "4"}
        self.env.pop("RATCHET_RUN", None)
        self.env.pop("RATCHET_GATE", None)
        sh("git init -q -b main && git config user.email t@t && git config user.name t", self.root)
        self.write("package.json", json.dumps({"name": "mono", "private": True, "workspaces": ["packages/*"]}))
        self.pkg("core", {})
        self.pkg("app", {"core": "*"})
        self.pkg("other", {})
        self.ratchet("init")
        sh("git add -A && git commit -qm init && git switch -qc feature", self.root)

    def pkg(self, name: str, deps: dict, test_code: int = 0) -> None:
        self.write(f"packages/{name}/package.json", json.dumps({
            "name": name, "dependencies": deps,
            "scripts": {"lint": node_script(0, "lint ok"), "test": node_script(test_code, f"{name} tests")},
        }))
        self.write(f"packages/{name}/index.js", f"module.exports = '{name}';\n")

    def write(self, rel: str, text: str) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def ratchet(self, *args: str, stdin: str = "", check: bool = False) -> subprocess.CompletedProcess:
        proc = subprocess.run([sys.executable, str(BIN), *args], cwd=self.root, env=self.env, input=stdin,
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
