"""`ratchet junit-unittest [dir]`: run stdlib unittest discovery and write JUnit XML to $RATCHET_OUT.

Lets repos without pytest still give ratchet per-test results.
"""
from __future__ import annotations

import os
import sys
import time
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


class _Result(unittest.TextTestResult):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.records = []
        self._t = 0.0

    def startTest(self, test):
        self._t = time.time()
        super().startTest(test)

    def _rec(self, test, status, detail=""):
        self.records.append((test.id(), status, detail, time.time() - self._t))

    def addSuccess(self, test):
        super().addSuccess(test)
        self._rec(test, "pass")

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self._rec(test, "failure", self._exc_info_to_string(err, test))

    def addError(self, test, err):
        super().addError(test, err)
        self._rec(test, "error", self._exc_info_to_string(err, test))

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self._rec(test, "skipped", reason)

    def addExpectedFailure(self, test, err):
        super().addExpectedFailure(test, err)
        self._rec(test, "pass")

    def addUnexpectedSuccess(self, test):
        super().addUnexpectedSuccess(test)
        self._rec(test, "failure", "unexpected success")


def main(start: str = ".") -> int:
    start_abs = os.path.abspath(start)
    # A plain test dir (no __init__.py) is its own top level; a package dir is imported from the cwd.
    top = os.getcwd() if os.path.exists(os.path.join(start_abs, "__init__.py")) else start_abs
    sys.path.insert(0, top)
    suite = unittest.defaultTestLoader.discover(start_abs, pattern="test*.py", top_level_dir=top)
    runner = unittest.TextTestRunner(resultclass=_Result, verbosity=2)
    result = runner.run(suite)
    root = ET.Element("testsuites")
    ts = ET.SubElement(root, "testsuite", name="unittest", tests=str(len(result.records)))
    for tid, status, detail, secs in result.records:
        cls, _, name = tid.rpartition(".")
        case = ET.SubElement(ts, "testcase", classname=cls, name=name, time=f"{secs:.3f}")
        if status != "pass":
            ET.SubElement(case, status, message=detail.splitlines()[-1][:200] if detail else status).text = detail
    out = Path(os.environ.get("RATCHET_OUT") or ".")
    out.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(out / "unittest.xml", encoding="utf-8", xml_declaration=True)
    return 0 if result.wasSuccessful() else 1
