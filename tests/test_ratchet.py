"""End-to-end tests: real git repos, real test runners, the real `ratchet` CLI."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BIN = REPO / "plugin" / "bin" / "ratchet"
sys.path.insert(0, str(REPO / "plugin" / "lib"))

from ratchet import evolve, harness, imagediff, results  # noqa: E402

HAS_NODE = shutil.which("node") is not None


def sh(cmd: str, cwd: Path) -> str:
    return subprocess.run(cmd, shell=True, cwd=cwd, check=True, text=True, capture_output=True).stdout


class Repo:
    """A throwaway git repo driven through the real CLI."""

    def __init__(self, files: dict, name: str = "repo") -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / name
        self.root.mkdir()
        self.env = {**os.environ, "XDG_CACHE_HOME": str(Path(self.tmp.name) / "cache"), "RATCHET_SLOTS": "4",
                    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        self.env.pop("RATCHET_RUN", None)
        sh("git init -q -b main", self.root)
        self.write_all(files)

    def write_all(self, files: dict) -> None:
        for rel, text in files.items():
            self.write(rel, text)

    def write(self, rel: str, text: str) -> None:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(text))

    def commit(self, msg: str = "c") -> str:
        subprocess.run("git add -A && git commit -qm " + json.dumps(msg), shell=True, cwd=self.root, env=self.env, check=True)
        return sh("git rev-parse HEAD", self.root).strip()

    def r(self, *args: str, stdin: str = "", check: bool = False, cwd: Path = None) -> subprocess.CompletedProcess:
        proc = subprocess.run([sys.executable, str(BIN), *args], cwd=cwd or self.root, env=self.env, input=stdin,
                              text=True, capture_output=True)
        if check and proc.returncode != 0:
            raise AssertionError(f"ratchet {' '.join(args)} failed ({proc.returncode}):\n{proc.stdout}\n{proc.stderr}")
        return proc

    def harness(self) -> dict:
        return json.loads((self.root / ".ratchet/harness.json").read_text())

    def close(self) -> None:
        self.tmp.cleanup()


NODE_CHECK = {"name": "core", "run": 'node --test --test-reporter=junit --test-reporter-destination="$RATCHET_OUT/junit.xml"',
              "cwd": "core", "scope": ["core/**"], "mutate": ["core/**/*.js"],
              "canary": {"path": "core/__ratchet_canary.test.mjs",
                         "content": "import test from 'node:test'\ntest('ratchet canary', () => { throw new Error('x') })\n"}}

CORE_JS = "function total(xs) {\n  let s = 0;\n  for (let i = 1; i < xs.length; i++) s = s + xs[i];\n  return s;\n}\nmodule.exports = { total };\n"
CORE_TEST = ("const test = require('node:test');\nconst assert = require('node:assert');\n"
             "const { total } = require('./index.js');\ntest('empty is zero', () => assert.strictEqual(total([]), 0));\n")


def node_core_repo(extra_checks: list = None) -> Repo:
    repo = Repo({"core/index.js": CORE_JS, "core/index.test.js": CORE_TEST,
                 ".ratchet/harness.json": json.dumps({"kernel": "1", "checks": [NODE_CHECK] + (extra_checks or [])})})
    repo.commit("init")
    sh("git switch -qc work", repo.root)
    return repo


# ---------- process hygiene ----------

class ProcessHygieneTest(unittest.TestCase):
    """A check that never finishes must not leave anything running after its timeout."""

    def _alive(self, marker: str) -> list:
        out = subprocess.run(["pgrep", "-f", marker[0] + "[" + marker[1] + "]" + marker[2:]], capture_output=True, text=True).stdout.split()
        return [p for p in out if int(p) != os.getpid()]

    def test_timeout_kills_the_whole_tree_including_new_sessions(self) -> None:
        from ratchet import runner
        marker = f"ratchet-leak-probe-{os.getpid()}"
        loop = f"{sys.executable} -c 'import sys; sys.argv; [None for _ in iter(int, 1)]' {marker}"
        detached = (f"{sys.executable} -c \"import subprocess, sys, time; "
                    f"subprocess.Popen([sys.executable, '-c', 'while True: pass', '{marker}-s'], start_new_session=True); "
                    f"time.sleep(600)\"")
        cmd = f"{loop} & {loop} & {detached}"
        try:
            start = __import__("time").time()
            code, out = runner.run_group(cmd, "/tmp", dict(os.environ), timeout=2)
            self.assertEqual(code, 124)
            self.assertLess(__import__("time").time() - start, 15)
            __import__("time").sleep(0.5)
            self.assertEqual(self._alive(marker), [], "processes survived the timeout")
        finally:
            subprocess.run(["pkill", "-9", "-f", marker])

    def test_normal_exit_reaps_background_leftovers(self) -> None:
        from ratchet import runner
        marker = f"ratchet-bg-probe-{os.getpid()}"
        try:
            code, _ = runner.run_group(f"{sys.executable} -c 'while True: pass' {marker} & echo started", "/tmp",
                                       dict(os.environ), timeout=10)
            self.assertEqual(code, 0)
            __import__("time").sleep(0.5)
            self.assertEqual(self._alive(marker), [])
        finally:
            subprocess.run(["pkill", "-9", "-f", marker])


# ---------- results ----------

class ResultsTest(unittest.TestCase):
    def test_parsers(self) -> None:
        t: dict = {}
        results.parse_junit('<testsuites><testsuite><testcase classname="a" name="ok"/><testcase classname="a" name="bad">'
                            '<failure/></testcase><testcase name="s"><skipped/></testcase></testsuite></testsuites>', t)
        self.assertEqual(t, {"a::ok": "pass", "a::bad": "fail", "s": "skip"})
        t = {}
        results.parse_go_json('{"Action":"pass","Package":"p","Test":"TestA"}\n{"Action":"fail","Package":"p","Test":"TestB"}\n'
                              '{"Action":"output","Package":"p","Test":"TestB","Output":"x"}\n', t)
        self.assertEqual(t, {"p::TestA": "pass", "p::TestB": "fail"})
        t = {}
        results.parse_jest_json(json.dumps({"testResults": [{"name": "f.test.js", "assertionResults": [
            {"fullName": "a", "status": "passed"}, {"fullName": "b", "status": "pending"}]}]}), t)
        self.assertEqual(t, {"f.test.js::a": "pass", "f.test.js::b": "skip"})
        t = {}
        results.parse_libtest("running 2 tests\ntest m::a ... ok\ntest m::b ... FAILED\n", t)
        self.assertEqual(t, {"m::a": "pass", "m::b": "fail"})

    def test_collect_without_results_is_opaque(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            self.assertTrue(results.collect(Path(d)).opaque)
            (Path(d) / "result.json").write_text('{"tests": [{"name": "x", "status": "passed"}]}')
            self.assertEqual(results.collect(Path(d)).tests, {"x": "pass"})


# ---------- kernel: verify, proofs ----------

@unittest.skipUnless(HAS_NODE, "needs node")
class KernelTest(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = node_core_repo()
        self.repo.r("baseline", check=True)

    def tearDown(self) -> None:
        self.repo.close()

    def test_regression_new_failing_removed_and_fake_pass(self) -> None:
        r = self.repo
        r.write("core/index.js", CORE_JS.replace("let s = 0", "let s = 1"))
        out = r.r("verify")
        self.assertEqual(out.returncode, 1)
        self.assertIn("regressed (passed on the base): test::empty is zero", out.stdout)
        r.write("core/index.js", CORE_JS)
        r.write("core/index.test.js", CORE_TEST.replace("test('empty is zero'", "test('renamed'"))
        out = r.r("verify")
        self.assertIn("removed (passed on the base)", out.stdout)
        r.r("allow", "--test", "core::test::empty is zero", "--reason", "renamed", check=True)
        self.assertEqual(r.r("verify").returncode, 0)
        (r.root / "core/index.test.js").unlink()  # nothing left to run
        out = r.r("verify").stdout
        self.assertIn("only 0 test(s) ran", out)
        self.assertIn("deleted test file", out)

    def test_new_tests_must_fail_without_the_change(self) -> None:
        r = self.repo
        r.write("core/index.js", CORE_JS.replace("let i = 1", "let i = 0"))
        r.write("core/index.test.js", CORE_TEST + "test('sums all', () => assert.strictEqual(total([1, 2, 3]), 6));\n"
                                                  "test('already true', () => assert.strictEqual(total([]), 0));\n")
        r.commit("fix")
        out = r.r("proof", "f2p", "--start", "main")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("✓ core::test::sums all", out.stdout)
        self.assertIn("· core::test::already true", out.stdout)
        self.assertEqual(sh("git status --porcelain", r.root), "")  # base state was restored

    def test_mutation_kills_survivors_and_infinite_loops(self) -> None:
        r = self.repo
        r.write("core/index.js", CORE_JS.replace("let i = 1", "let i = 0"))
        r.write("core/index.test.js", CORE_TEST + "test('sums all', () => assert.strictEqual(total([1, 2, 3]), 6));\n")
        r.commit("fix")
        r.r("verify", check=True)
        out = r.r("proof", "mutate", "--start", "main", "--json", check=True)
        rep = json.loads(out.stdout)
        self.assertGreaterEqual(rep["killed"], 1)  # `i >= xs.length` loops forever: the timeout counts as a kill
        self.assertEqual(sh("git status --porcelain", r.root), "")

    def test_canary_proves_a_check_can_fail_and_catches_a_blind_one(self) -> None:
        r = self.repo
        self.assertIn("red as expected", r.r("proof", "canary", "core", check=True).stdout)
        h = r.harness()
        h["checks"][0]["run"] = 'node --test --test-reporter=junit --test-reporter-destination="$RATCHET_OUT/junit.xml" index.test.js'
        r.write(".ratchet/harness.json", json.dumps(h))
        out = r.r("proof", "canary", "core")
        self.assertEqual(out.returncode, 1)
        self.assertIn("STAYED GREEN", out.stdout)

    def test_results_carry_forward_outside_scope(self) -> None:
        r = self.repo
        r.write("README.md", "docs\n")
        r.commit("docs")
        out = r.r("verify", "--json", check=True)
        self.assertEqual(json.loads(out.stdout)["checks"], [])  # nothing in scope changed
        r.write("core/index.test.js", CORE_TEST + "test('more', () => {});\n")
        v = json.loads(r.r("verify", "--json", check=True).stdout)
        self.assertEqual(v["checks"][0]["baseline"], "per-test")  # base found through carry-forward

    def test_flaky_test_is_rerun_and_logged(self) -> None:
        r = self.repo
        r.write("core/flaky.test.js", "const test = require('node:test'); const fs = require('fs');\n"
                "test('flaky', () => { const f = '/tmp/ratchet-flaky-' + process.env.RATCHET_FLAKY_ID;\n"
                "  if (!fs.existsSync(f)) { fs.writeFileSync(f, '1'); throw new Error('first run fails'); } });\n")
        r.commit("flaky test")
        r.env["RATCHET_FLAKY_ID"] = str(os.getpid())
        r.r("baseline")  # first run fails, establishing nothing about 'flaky'
        r.write("core/flaky.test.js", (r.root / "core/flaky.test.js").read_text() + "// touch\n")
        Path(f"/tmp/ratchet-flaky-{os.getpid()}").unlink(missing_ok=True)
        out = r.r("verify")
        self.assertEqual(out.returncode, 0, out.stdout)
        self.assertIn("flaky (failed, then passed on re-run)", out.stdout)
        Path(f"/tmp/ratchet-flaky-{os.getpid()}").unlink(missing_ok=True)

    def test_tamper_blocks_skips_and_harness_edits_in_feature_work(self) -> None:
        r = self.repo
        r.write("core/index.test.js", CORE_TEST.replace("test('empty", "test.skip('empty"))
        out = r.r("verify")
        self.assertIn("TAMPER", out.stdout)
        self.assertIn("newly skipped", out.stdout)


# ---------- init: discovery, generation, proofs, readiness ----------

@unittest.skipUnless(HAS_NODE, "needs node")
class InitTest(unittest.TestCase):
    def test_node_workspace_monorepo(self) -> None:
        repo = Repo({
            "package.json": json.dumps({"name": "mono", "private": True, "workspaces": ["packages/*"]}),
            "packages/core/package.json": json.dumps({"name": "core", "scripts": {"test": "node --test"}}),
            "packages/core/index.js": CORE_JS.replace("let i = 1", "let i = 0"),
            "packages/core/index.test.js": CORE_TEST + "test('sums', () => assert.strictEqual(total([1, 2]), 3));\n",
            "packages/web/package.json": json.dumps({"name": "web", "dependencies": {"core": "*", "vite": "^5"},
                                                     "scripts": {"test": "node --test", "dev": "node server.js"}}),
            "packages/web/page.js": "module.exports = () => '<main id=\"app\"></main>';\n",
            "packages/web/page.test.js": "const test = require('node:test'); const assert = require('node:assert');\n"
                                          "test('renders app', () => assert.match(require('./page.js')(), /app/));\n",
            ".github/workflows/ci.yml": "jobs:\n  t:\n    steps:\n      - run: npm ci\n      - run: |\n          npm test -ws\n          npm run lint\n",
        }, name="mono")
        try:
            repo.commit("init")
            out = repo.r("init", "--probe", "2", check=True).stdout
            h = repo.harness()
            checks = {c["name"]: c for c in h["checks"]}
            self.assertEqual(set(checks), {"core-test", "web-test"})
            self.assertEqual(checks["web-test"]["scope"], ["packages/core/**", "packages/web/**"])  # follows deps
            self.assertIn("canary", checks["core-test"])
            self.assertTrue((repo.root / ".ratchet/verify/core-test.sh").exists())
            self.assertIn("red as expected", out)
            self.assertIn("[ci.yml] npm test -ws", out)
            web = next(p for p in h["products"] if p["name"] == "web")
            self.assertEqual(web["kind"], "web")
            self.assertTrue((repo.root / ".ratchet/exercise/web.md").exists())
            self.assertIn("ratchet:start", (repo.root / "AGENTS.md").read_text())
            scripts = {s["name"]: s for s in json.loads((repo.root / "t3.json").read_text())["scripts"]}
            self.assertTrue(scripts["ratchet: setup"]["runOnWorktreeCreate"])
            r = evolve.readiness(repo.root, harness.load(repo.root))
            self.assertGreaterEqual(r["level"], 2)
            self.assertTrue(any(g["kind"] == "no-exercise" and g["product"] == "web" for g in r["gaps"]))
            plan = json.loads(repo.r("garden", "--plan", check=True).stdout)
            self.assertTrue(any("replayable exercise" in t["title"] for t in plan))
        finally:
            repo.close()

    def test_detects_python_go_rust_and_godot(self) -> None:
        repo = Repo({
            "py/pyproject.toml": "[project]\nname = 'tool'\n[tool.pytest.ini_options]\n",
            "py/tests/test_a.py": "def test_a():\n    assert True\n",
            "go.work": "go 1.22\nuse ./svc\n",
            "svc/go.mod": "module example.com/svc\n",
            "svc/main.go": "package main\nfunc main() {}\n",
            "Cargo.toml": '[workspace]\nmembers = ["crates/*"]\n',
            "crates/a/Cargo.toml": '[package]\nname = "a"\n',
            "game/project.godot": 'config/name="Skate"\nrun/main_scene="res://main.tscn"\n',
            "game/addons/gut/gut_cmdln.gd": "# gut\n",
            "pyproject.toml": "[tool.uv.workspace]\nmembers = ['py']\n",
        })
        try:
            data = json.loads(repo.r("init", "--dry-run", "--json", check=True).stdout)
            names = {c["name"]: c for c in data["checks"]}
            self.assertIn("pytest", names["py-test"]["script"])
            self.assertIn("go test -json", names["svc-test"]["script"])
            self.assertIn("ratchet_canary_test.go", names["svc-test"]["canary"]["path"])
            self.assertIn("libtest", names["a-test"]["script"])
            self.assertIn("gut_cmdln.gd", names["game-test"]["script"])
            self.assertIn("exec-check", names["game-load"]["script"])
            kinds = {p["name"]: p["kind"] for p in data["products"]}
            self.assertEqual(kinds["game"], "godot")
            self.assertEqual(kinds["svc"], "cli")
        finally:
            repo.close()


# ---------- new: walking skeletons ----------

@unittest.skipUnless(HAS_NODE, "needs node")
class NewTest(unittest.TestCase):
    def test_skeletons_are_verified_from_the_first_commit(self) -> None:
        for stack, exercise_name in (("node-lib", None), ("node-web", "smoke"), ("node-cli", "golden"), ("python-lib", None)):
            with self.subTest(stack=stack), tempfile.TemporaryDirectory() as d:
                dest = Path(d) / f"app-{stack}"
                env = {**os.environ, "XDG_CACHE_HOME": str(Path(d) / "cache")}
                proc = subprocess.run([sys.executable, str(BIN), "new", stack, str(dest), "--json"], env=env,
                                      capture_output=True, text=True)
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                res = json.loads(proc.stdout)
                self.assertTrue(all(p["green"] and p["canary"]["ok"] for p in res["proofs"]), res["proofs"])
                if exercise_name:
                    self.assertTrue(res["exercises"][0]["ok"], res["exercises"])
                log = sh("git log --oneline", dest)
                self.assertIn("walking skeleton", log)
                self.assertEqual(sh("git status --porcelain", dest), "")


# ---------- the cycle ----------

@unittest.skipUnless(HAS_NODE, "needs node")
class CycleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = node_core_repo()
        h = self.repo.harness()
        h["agents"] = {"contract_review": False}
        h["products"] = [{"name": "core", "kind": "library", "paths": ["core/**"], "exercise": ".ratchet/exercise/core.md"}]
        self.repo.write(".ratchet/harness.json", json.dumps(h))
        self.repo.write(".ratchet/exercise/core.md", "# core\n## Exercise\nCall total() from a tiny consumer.\n")
        sh("git switch -q main", self.repo.root)
        self.repo.commit("harness")
        sh("git switch -q work && git merge -q main", self.repo.root)
        self.r = self.repo.r
        self.r("run", "start", "--goal", "totals include every value", check=True)

    def tearDown(self) -> None:
        self.repo.close()

    def tick(self) -> list:
        return json.loads(self.r("tick", "--json", check=True).stdout)["actions"]

    def test_feature_needs_a_failing_first_test_then_flows_to_merge(self) -> None:
        r, root = self.r, self.repo.root
        r("task", "add", "--from-json", "-", check=True, stdin=json.dumps([
            {"title": "fix total", "kind": "fix", "packages": ["core"], "criteria": ["total([1,2,3]) is 6"]},
            {"title": "tidy", "kind": "refactor", "packages": ["core"], "criteria": ["same behaviour"], "deps": ["T1"]}]))
        acts = self.tick()
        self.assertEqual([(a["do"], a.get("task")) for a in acts], [("dispatch_worker", "T1")])
        worker = r("brief", "worker", "T1", check=True).stdout
        self.assertIn("Call total() from a tiny consumer", worker)  # the repo's own exercise guide
        self.assertIn("fails without it", worker)
        r("task", "dispatch", "T1", "--role", "worker", "--thread", "w1", check=True)
        r("task", "start", "T1", check=True)
        self.repo.write("core/index.js", CORE_JS.replace("let i = 1", "let i = 0"))
        self.repo.commit("T1 without a test")
        refused = r("task", "submit", "T1", "--summary", "done")
        self.assertEqual(refused.returncode, 2)
        self.assertIn("at least one new test that fails without the change", refused.stderr)
        self.repo.write("core/index.test.js", CORE_TEST + "test('sums all', () => assert.strictEqual(total([1, 2, 3]), 6));\n")
        self.repo.commit("T1 test")
        out = r("task", "submit", "T1", "--summary", "fixed", check=True).stdout
        self.assertIn("1 new test(s) fail without the change", out)
        judge = r("brief", "judge", "T1", check=True).stdout
        self.assertIn("proof f2p T1", judge)
        self.assertIn("proof mutate T1", judge)
        r("task", "dispatch", "T1", "--role", "judge", "--thread", "j1", check=True)
        r("task", "accept", "T1", "--notes", "1. PASS", check=True)
        self.assertEqual(self.tick()[0]["do"], "merge")
        r("task", "merge", "T1", check=True)
        # a refactor doesn't need a new test, but must not break anything
        r("task", "dispatch", "T2", "--role", "worker", "--thread", "w2", check=True)
        r("task", "start", "T2", check=True)
        self.repo.write("core/index.js", CORE_JS.replace("let i = 1", "let i = 0").replace("s = s + xs[i]", "s += xs[i]"))
        self.repo.commit("T2")
        r("task", "submit", "T2", "--summary", "tidied", check=True)

    def test_harness_tasks_may_edit_the_harness_feature_tasks_may_not(self) -> None:
        r = self.r
        r("task", "add", "--from-json", "-", check=True, stdin=json.dumps([
            {"title": "sneaky", "kind": "refactor", "packages": ["core"], "criteria": ["c"]},
            {"title": "add a lint check", "kind": "harness", "packages": [".ratchet"], "criteria": ["c"]}]))
        r("task", "start", "T1", check=True)
        h = self.repo.harness()
        h["limits"] = {"gate_attempts": 99}
        self.repo.write(".ratchet/harness.json", json.dumps(h))
        self.repo.commit("loosen limits")
        refused = r("task", "submit", "T1", "--summary", "x")
        self.assertIn("TAMPER", refused.stderr)
        r("task", "block", "T1", "--reason", "not allowed", check=True)
        r("task", "rollback", "T1", check=True)
        r("task", "start", "T2", check=True)
        h = self.repo.harness()
        h["checks"].append({"name": "lint", "run": "true", "min_tests": 0})
        self.repo.write(".ratchet/harness.json", json.dumps(h))
        self.repo.commit("add lint")
        r("task", "submit", "T2", "--summary", "lint check added", check=True)
        r("task", "accept", "T2", "--notes", "ok", check=True)
        r("task", "merge", "T2", check=True)
        # run level: the reviewed harness change is exempt; nothing else touched protected paths
        r("run", "final", "accept", "--notes", "ok", check=True)
        self.assertEqual(r("tamper").returncode, 0)
        self.repo.write("t3.json", "{}\n")  # a protected change no harness task made
        self.repo.commit("sneak")
        self.assertIn("protected path changed", r("tamper").stdout)

    def test_judge_panel_and_report(self) -> None:
        h = self.repo.harness()
        h["agents"]["judge_panel"] = 3
        sh("git switch -q main", self.repo.root)
        self.repo.write(".ratchet/harness.json", json.dumps(h))
        self.repo.commit("panel")
        sh("git switch -q work && git merge -q main", self.repo.root)
        r = self.r
        r("task", "add", "--title", "docs", "--kind", "docs", "--criteria", "README explains total", check=True)
        r("task", "start", "T1", check=True)
        self.repo.write("README.md", "total() sums values\n")
        self.repo.commit("docs")
        r("task", "submit", "T1", "--summary", "s", check=True)
        self.assertEqual(self.tick()[0]["count"], 3)
        r("task", "accept", "T1", "--notes", "ok", check=True)
        r("task", "reject", "T1", "--notes", "unclear", check=True)
        r("task", "accept", "T1", "--notes", "ok", check=True)
        self.assertEqual(json.loads(r("task", "show", "T1", "--json").stdout)["status"], "accepted")
        self.assertIn("totals include every value", r("report", "--html", check=True).stdout)

    def test_start_builds_a_t3_thread_start_call(self) -> None:
        fake = Path(self.repo.tmp.name) / "bin"
        fake.mkdir()
        (fake / "t3").write_text("#!/bin/sh\necho fake\n")
        (fake / "t3").chmod(0o755)
        self.repo.env["PATH"] = f"{fake}:{self.repo.env['PATH']}"
        out = self.r("start", "Add SSO", "--provider", "codex", "--dry-run", check=True).stdout
        self.assertIn("thread start", out)
        self.assertIn("--branch ratchet/add-sso", out)
        self.assertIn("brief coordinator", out)


# ---------- exercises and images ----------

class ImageTest(unittest.TestCase):
    def test_png_roundtrip_and_diff(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            a, b, out = Path(d) / "a.png", Path(d) / "b.png", Path(d) / "d.png"
            rows = [[(x * 10, y * 10, 0, 255) for x in range(10)] for y in range(6)]
            imagediff.encode(a, 10, 6, rows)
            rows[2][3] = (255, 255, 255, 255)
            imagediff.encode(b, 10, 6, rows)
            r = imagediff.diff(a, b, out)
            self.assertEqual(r["differing"], 1)
            self.assertEqual(imagediff.decode(out)[2][2][3], (255, 0, 0, 255))


@unittest.skipUnless(HAS_NODE, "needs node")
class ExerciseTest(unittest.TestCase):
    def test_exercise_compares_images_and_budgets_against_the_base(self) -> None:
        repo = node_core_repo()
        try:
            script = ("import json, os, sys\nsys.path.insert(0, %r)\nfrom ratchet import imagediff\n"
                      "red = 'shade = 200' in open('core/index.js').read()\nout = os.environ['RATCHET_OUT']\n"
                      "rows = [[((200 if red and x < 5 else 10), 0, 0, 255) for x in range(10)] for _ in range(10)]\n"
                      "imagediff.encode(os.path.join(out, 'shot.png'), 10, 10, rows)\n"
                      "json.dump({'pass': True, 'metrics': {'ms': 150 if red else 100}}, open(os.path.join(out, 'reward.json'), 'w'))\n"
                      % str(REPO / "plugin" / "lib"))
            repo.write(".ratchet/exercise/shot.py", script)
            h = repo.harness()
            h["products"] = [{"name": "core", "kind": "web", "paths": ["core/**"], "exercises": [
                {"name": "shot", "run": "python3 .ratchet/exercise/shot.py", "compare": {"images": 0.1},
                 "budgets": {"ms": {"max_ratio": 1.2}}}]}]
            repo.write(".ratchet/harness.json", json.dumps(h))
            sh("git switch -q main", repo.root)
            repo.commit("exercise")
            sh("git switch -q work && git merge -q main", repo.root)
            repo.write("core/index.js", CORE_JS + "// shade = 200\n")
            repo.commit("change look")
            out = repo.r("exercise", "run", "shot", "--base", "main")
            self.assertEqual(out.returncode, 1, out.stdout)
            self.assertIn("50.00% pixels differ from the base (OVER LIMIT)", out.stdout)
            self.assertIn("budget ms: head 150 vs base 100 (ratio 1.5) FAIL", out.stdout)
        finally:
            repo.close()


# ---------- evolution, hooks, helpers ----------

@unittest.skipUnless(HAS_NODE, "needs node")
class EvolveAndHooksTest(unittest.TestCase):
    def test_upgrade_runs_registered_migrations(self) -> None:
        repo = node_core_repo()
        try:
            h = repo.harness()
            h["kernel"] = "0"
            repo.write(".ratchet/harness.json", json.dumps(h))
            evolve.MIGRATIONS["0"] = ("1", lambda raw: ({**raw, "migrated": True}, ["review the new field"]))
            try:
                r = evolve.upgrade(repo.root)
            finally:
                evolve.MIGRATIONS.pop("0")
            self.assertEqual(r["steps"], ["0 → 1"])
            self.assertTrue(repo.harness()["migrated"])
        finally:
            repo.close()

    def test_stop_hook_blocks_regressions_and_skips_detached_checkouts(self) -> None:
        repo = node_core_repo()
        try:
            repo.r("baseline", check=True)
            payload = {"cwd": str(repo.root), "session_id": "s1"}
            repo.r("hook", "session-start", stdin=json.dumps(payload))
            repo.write("core/index.js", CORE_JS.replace("let s = 0", "let s = 1"))
            out = json.loads(repo.r("hook", "stop", stdin=json.dumps(payload)).stdout)
            self.assertEqual(out["decision"], "block")
            self.assertIn("regressed", out["reason"])
            sh("git stash -q && git checkout -q --detach HEAD", repo.root)
            self.assertEqual(repo.r("hook", "stop", stdin=json.dumps(payload)).stdout.strip(), "")
        finally:
            repo.close()

    def test_exec_check_and_junit_unittest(self) -> None:
        def run(*a):
            return subprocess.run([sys.executable, str(BIN), *a], capture_output=True, text=True)
        self.assertEqual(run("exec-check", "--fail-on", "SCRIPT ERROR", "--", "echo", "SCRIPT ERROR: x").returncode, 1)
        self.assertEqual(run("exec-check", "--", "true").returncode, 0)
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "test_x.py").write_text("import unittest\nclass T(unittest.TestCase):\n"
                                               "    def test_ok(self): pass\n    def test_bad(self): self.fail('no')\n")
            proc = subprocess.run([sys.executable, str(BIN), "junit-unittest", "."], cwd=d, capture_output=True, text=True,
                                  env={**os.environ, "RATCHET_OUT": d})
            self.assertEqual(proc.returncode, 1)
            t = results.collect(Path(d)).tests
            self.assertEqual(sorted(t.values()), ["fail", "pass"])
            sub = Path(d) / "plain"  # a test dir that isn't a package, run from its parent
            sub.mkdir()
            (sub / "test_y.py").write_text("import unittest\nclass T(unittest.TestCase):\n    def test_ok(self): pass\n")
            out = Path(d) / "out2"
            proc = subprocess.run([sys.executable, str(BIN), "junit-unittest", "plain"], cwd=d, capture_output=True,
                                  text=True, env={**os.environ, "RATCHET_OUT": str(out)})
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertEqual(list(results.collect(out).tests.values()), ["pass"])


if __name__ == "__main__":
    unittest.main()
