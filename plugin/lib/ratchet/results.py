"""Per-test results: the kernel judges tests, not exit codes.

A check writes results into $RATCHET_OUT in any of these forms, auto-detected:
  *.xml        JUnit XML (pytest, Jest/Vitest, node --test, Playwright, Hurl, Maestro, bats, gdUnit4...)
  *.go.json    `go test -json` output
  *jest.json   Jest/Vitest `--json` output
  *.libtest.txt  Rust libtest console output (`test name ... ok|FAILED|ignored`)
  result.json  {"tests": [{"name": "...", "status": "pass|fail|skip|error"}]}
With none of them the check is "opaque": only its exit code counts.
"""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

PASS, FAIL, SKIP, ERROR = "pass", "fail", "skip", "error"


@dataclass
class Results:
    tests: dict = field(default_factory=dict)  # name -> status
    format: str = "exit"

    @property
    def opaque(self) -> bool:
        return self.format == "exit"

    def count(self, status: Optional[str] = None) -> int:
        return len(self.tests) if status is None else sum(1 for s in self.tests.values() if s == status)

    def passed(self) -> set:
        return {n for n, s in self.tests.items() if s == PASS}

    def failing(self) -> set:
        return {n for n, s in self.tests.items() if s in (FAIL, ERROR)}

    def to_dict(self) -> dict:
        return {"format": self.format, "tests": self.tests}

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "Results":
        d = d or {}
        return cls(tests=dict(d.get("tests") or {}), format=d.get("format", "exit"))


def _put(tests: dict, name: str, status: str) -> None:
    # A repeated name keeps its worst status (fail/error beat skip beat pass).
    rank = {PASS: 0, SKIP: 1, FAIL: 2, ERROR: 3}
    if name not in tests or rank[status] > rank[tests[name]]:
        tests[name] = status


def parse_junit(text: str, tests: dict) -> None:
    root = ET.fromstring(text)
    for case in root.iter("testcase"):
        cls = case.get("file") or case.get("classname") or ""
        name = f"{cls}::{case.get('name', '')}" if cls else case.get("name", "")
        status = PASS
        for child in case:
            tag = child.tag.lower()
            if tag == "failure":
                status = FAIL
            elif tag == "error":
                status = ERROR
            elif tag == "skipped" and status == PASS:
                status = SKIP
        _put(tests, name, status)


def parse_go_json(text: str, tests: dict) -> None:
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not ev.get("Test"):
            continue
        action = ev.get("Action")
        status = {"pass": PASS, "fail": FAIL, "skip": SKIP}.get(action)
        if status:
            _put(tests, f"{ev.get('Package', '')}::{ev['Test']}", status)


def parse_result_json(text: str, tests: dict) -> None:
    data = json.loads(text)
    for t in data.get("tests") or []:
        status = str(t.get("status", "")).lower()
        status = {"passed": PASS, "ok": PASS, "failed": FAIL, "skipped": SKIP, "todo": SKIP}.get(status, status)
        if status in (PASS, FAIL, SKIP, ERROR) and t.get("name"):
            _put(tests, str(t["name"]), status)


def parse_jest_json(text: str, tests: dict) -> None:
    data = json.loads(text)
    for suite in data.get("testResults") or []:
        for a in suite.get("assertionResults") or []:
            status = {"passed": PASS, "failed": FAIL, "pending": SKIP, "skipped": SKIP, "todo": SKIP,
                      "disabled": SKIP}.get(a.get("status"), ERROR)
            _put(tests, f"{suite.get('name', '')}::{a.get('fullName') or a.get('title')}", status)


_LIBTEST = __import__("re").compile(r"^test (\S+) \.\.\. (ok|FAILED|ignored)\s*$", __import__("re").M)


def parse_libtest(text: str, tests: dict) -> None:
    for name, status in _LIBTEST.findall(text):
        _put(tests, name, {"ok": PASS, "FAILED": FAIL, "ignored": SKIP}[status])


def collect(out_dir: Path, fmt: str = "auto") -> Results:
    tests: dict = {}
    found = set()
    if not out_dir.is_dir():
        return Results()
    for p in sorted(out_dir.rglob("*")):
        if not p.is_file():
            continue
        try:
            text = p.read_text(errors="replace")
        except OSError:
            continue
        try:
            if p.suffix == ".xml" and fmt in ("auto", "junit"):
                parse_junit(text, tests)
                found.add("junit")
            elif p.name.endswith(".go.json") and fmt in ("auto", "go-json"):
                parse_go_json(text, tests)
                found.add("go-json")
            elif p.name.endswith("jest.json") and fmt in ("auto", "jest-json"):
                parse_jest_json(text, tests)
                found.add("jest-json")
            elif p.name.endswith(".libtest.txt") and fmt in ("auto", "libtest"):
                parse_libtest(text, tests)
                found.add("libtest")
            elif p.name == "result.json" and fmt in ("auto", "result-json"):
                parse_result_json(text, tests)
                found.add("result-json")
        except (ET.ParseError, json.JSONDecodeError, AttributeError):
            continue
    if fmt == "exit" or not found:
        return Results(tests={}, format="exit")
    return Results(tests=tests, format="+".join(sorted(found)))
