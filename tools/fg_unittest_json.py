"""Run a repo's unittest suite and print per-test outcomes as JSON (GitHub-lane S3, DESIGN R2, R4).

The fact tool copies this file to the root of the box's work copy (``/work``) and runs it there
as ``python3 -m fg_unittest_json --module PKG.MOD``, so ``sys.path[0]`` is ``/work``. It does
tox's ``python -m unittest`` discovery: start dir ``.``, pattern ``test*.py``, top-level dir
``.``, with the ``load_tests`` protocol (so a test module's doctests load).

stdout carries ONE JSON object and nothing else: file descriptor 1 is moved to stderr before any
test runs, so what a test prints cannot be read as our report. The object holds:
- ``tests_run``, ``ok``, ``python``;
- ``results``: test id -> ``{"outcome": pass|fail|error|skip|xfail|xpass, "exc": class name,
  "exc_mro": [class names]}`` (exc only for fail/error; a failing subtest marks its test);
- ``module``: the import proof (R4). For ``--module``, the module's ``__file__`` and the sha256
  of that file's bytes, read once right after the import (``start``) and once after every test
  ran (``end``), so a test that swaps the module for another copy is visible.

Exit code: 0 when the run was successful, 1 otherwise (failures, errors, or no report).
Standard library only; Python 3.8+.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import sys
import unittest

VERSION = "fg_unittest_json/1"


def _proof(name: str) -> dict:
    mod = sys.modules.get(name)
    f = getattr(mod, "__file__", None) if mod is not None else None
    out = {"file": f, "sha256": None}
    if f:
        try:
            with open(f, "rb") as fh:
                out["sha256"] = hashlib.sha256(fh.read()).hexdigest()
        except OSError as e:
            out["error"] = f"{type(e).__name__}: {e}"
    return out


class _Result(unittest.TextTestResult):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.records: dict = {}

    def _rec(self, test, outcome, err=None):
        tid = test.id()
        rec = {"outcome": outcome}
        if err is not None:
            rec["exc"] = err[0].__name__
            rec["exc_mro"] = [c.__name__ for c in err[0].__mro__]
        prev = self.records.get(tid)
        if prev is not None and prev["outcome"] in ("fail", "error") and outcome == "pass":
            return   # a failed subtest already marked this test
        self.records[tid] = rec

    def addSuccess(self, test):
        super().addSuccess(test)
        self._rec(test, "pass")

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self._rec(test, "fail", err)

    def addError(self, test, err):
        super().addError(test, err)
        self._rec(test, "error", err)

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self._rec(test, "skip")

    def addExpectedFailure(self, test, err):
        super().addExpectedFailure(test, err)
        self._rec(test, "xfail")

    def addUnexpectedSuccess(self, test):
        super().addUnexpectedSuccess(test)
        self._rec(test, "xpass")

    def addSubTest(self, test, subtest, err):
        super().addSubTest(test, subtest, err)
        if err is not None:
            self._rec(test, "fail" if issubclass(err[0], test.failureException) else "error", err)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="fg_unittest_json")
    ap.add_argument("--module", help="the module whose import is proven (R4)")
    args = ap.parse_args(argv)
    report = os.fdopen(os.dup(1), "w", encoding="utf-8")
    os.dup2(2, 1)   # from here on, anything written to fd 1 lands on stderr
    module = {"name": args.module}
    if args.module:
        try:
            importlib.import_module(args.module)
        except BaseException as e:   # noqa: BLE001 - recorded, the run still reports
            module["import_error"] = f"{type(e).__name__}: {e}"[:500]
        module["start"] = _proof(args.module)
    loader = unittest.TestLoader()
    suite = loader.discover(".", pattern="test*.py", top_level_dir=".")
    runner = unittest.TextTestRunner(stream=sys.stderr, verbosity=0, resultclass=_Result)
    result = runner.run(suite)
    if args.module:
        module["end"] = _proof(args.module)
    ok = result.wasSuccessful()
    out = {"runner": VERSION, "python": sys.version.split()[0], "tests_run": result.testsRun,
           "ok": ok, "results": result.records, "module": module}
    report.write(json.dumps(out, sort_keys=True))
    report.write("\n")
    report.flush()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
