"""GitHub-lane S3: the bug planter, the unittest JSON runner and the fact tool
(DESIGN github-lane-2026-11 §5 row S3, rules R0-R8).

The fixture repo (tests/fixtures/ghrepo_fixture/) is committed, ratified with throwaway test keys
and cached by ghcache, as in test_scout_learn_github. Pure tests run the fixture's OWN tests (our
code, not GitHub's) through the real runner without the box, with paths shown as /work; the
``box`` tests (deploy host, ``-m box``) run the same fixture in the real run-mode box."""
from __future__ import annotations

import ast
import builtins
import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from forgeproof import cli, config
from forgeproof.scout import box, ghcache, ghpool, plant, upstream_facts as uf
from forgeproof.scout.outcomes import blind, found
from test_scout_learn_github import GH

pytestmark = pytest.mark.skipif(shutil.which("ssh-keygen") is None, reason="needs ssh-keygen")

REPO = Path(__file__).resolve().parents[1]
FIX = Path(__file__).parent / "fixtures" / "ghrepo_fixture"
FILES = {p.relative_to(FIX).as_posix(): p.read_text(encoding="utf-8") for p in sorted(FIX.rglob("*")) if p.is_file()}
# Outside the box there is no /src, so the pure tests leave out the module whose test reaches for it.
PURE_FILES = {k: v for k, v in FILES.items() if k not in ("fgfix/other.py", "tests/testother.py")}
CORE = "fgfix/core.py"
CORE_SRC = (FIX / CORE).read_bytes()
RUNNER = REPO / "tools" / "fg_unittest_json.py"
LINUX_BOX = box.available() is None and bool(box.box_user())


# --- helpers ------------------------------------------------------------------------------------------

def _gh(tmp_path, files=None, **kw):
    d = tmp_path / "gh"
    d.mkdir(parents=True)
    return GH(d, files or PURE_FILES, spdx="MIT", **kw)


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A scout dir, a Brain-data folder holding registry files that must stay untouched, and (for
    the pure tests) a box user name with the box itself replaced by the unboxed runner."""
    scout, brain = tmp_path / "scout", tmp_path / "brain"
    scout.mkdir()
    brain.mkdir()
    for n in ("scout_registry.jsonl", "scout_registry_github.jsonl"):
        (brain / n).write_text('{"row": 1}\n')
    monkeypatch.setenv(config.SCOUT_DIR_KEY, str(scout))
    monkeypatch.setenv(config.BRAIN_DATA_KEY, str(brain))
    return {"scout": scout, "brain": brain, "tmp": tmp_path}


@pytest.fixture
def fake_box(env, monkeypatch):
    monkeypatch.setenv(box.BOX_USER_KEY, "fgbox-test")
    monkeypatch.setenv(box.BOX_ROOT_KEY, str(env["tmp"] / "boxroot"))
    monkeypatch.setattr(box, "available", lambda: None)
    monkeypatch.setattr(box, "run", _refuse_real_box)
    return env


def _refuse_real_box(*a, **k):
    raise AssertionError("a pure test reached box.run")


def unboxed(stage, argv, timeout, label):
    """Run the runner in the stage folder without a box; show the stage as /work, as the box does."""
    argv = [sys.executable if a == "python3" else a for a in argv]
    r = subprocess.run(argv, cwd=stage, capture_output=True, text=True, timeout=timeout,
                       env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONDONTWRITEBYTECODE": "1"})
    out = r.stdout
    for s in sorted({str(stage), os.path.realpath(stage)}, key=len, reverse=True):
        out = out.replace(s, "/work")
    return box.BoxResult(found(f"ran, exit {r.returncode}"), r.returncode, out, r.stderr)


class Recorder:
    """Wrap a run_box: count labels; optionally change what one run sees or reports."""

    def __init__(self, inner=unboxed, before=None, after=None):
        self.inner, self.before, self.after, self.labels = inner, before, after, []

    def __call__(self, stage, argv, timeout, label):
        self.labels.append(label)
        n = len(self.labels)
        if self.before:
            self.before(stage, label, n)
        try:
            r = self.inner(stage, argv, timeout, label)
        finally:
            (Path(stage) / "FG_FLAKY_FAIL").unlink(missing_ok=True)
        return self.after(r, label, n) if self.after else r


def _flag(stage):
    (Path(stage) / "FG_FLAKY_FAIL").write_text("x")


def _facts(gh, functions, run_box=unboxed, **kw):
    return uf.run(gh.spec, functions, gh_opts=gh.opts, run_box=run_box, **kw)


def _row(res, fn):
    rows = [r for r in res.rows if r.get("function") == fn]
    assert len(rows) == 1, res.rows
    return rows[0]


def _outcomes(row):
    return {m["id"]: m["outcome"] for m in row["mutants"]}


def _report(rep: dict, **changes):
    rep = json.loads(json.dumps(rep))
    rep.update(changes)
    return rep


def _rewrite(r, fn):
    rep = json.loads(r.stdout)
    return box.BoxResult(r.outcome, r.returncode, json.dumps(fn(rep)), r.stderr)


# --- plant (R5) -----------------------------------------------------------------------------------------

def test_plant_makes_one_token_changes_and_keeps_every_other_byte():
    p = plant.plant(CORE_SRC, "clamp")
    assert [m.id for m in p.mutants] == ["clamp:cmp:11:9", "clamp:return:12:15", "clamp:cmp:13:9",
                                         "clamp:return:14:15", "clamp:return:15:11"]
    assert (p.not_planted, p.version_gated, p.capped) == (0, 0, 0)
    first = p.mutants[0].source
    assert first == CORE_SRC.replace(b"    if x < lo:", b"    if x >= lo:")
    assert b">>> clamp(5, 0, 3)\n    3\n" in first                     # the doctest is untouched
    assert p.mutants[1].source == CORE_SRC.replace(b"        return lo\n", b"        return None\n")
    assert p.mutants[0].diff.count("\n-") == 1 and "+    if x >= lo:" in p.mutants[0].diff
    assert [m.id for m in plant.plant(CORE_SRC, "clamp").mutants] == [m.id for m in p.mutants]   # stable


def test_plant_splices_bytes_after_a_non_ascii_character():
    """R5: the operator sits after 'größer' (two 2-byte letters). Splicing at a character index
    would hit the wrong bytes and the self-check would drop the mutant."""
    p = plant.plant(CORE_SRC, "label")
    by_id = {m.id: m for m in p.mutants}
    assert set(by_id) == {"label:return:27:11", "label:cmp:27:25", "label:const:27:27"}
    assert p.not_planted == 0
    want = '    return "größer" if n <= 0 else "kleiner"\n'.encode()
    assert by_id["label:cmp:27:25"].source == CORE_SRC.replace(
        '    return "größer" if n > 0 else "kleiner"\n'.encode(), want)
    assert by_id["label:const:27:27"].source == CORE_SRC.replace(
        '    return "größer" if n > 0 else "kleiner"\n'.encode(), '    return "größer" if n > 1 else "kleiner"\n'.encode())


def test_plant_skips_every_candidate_inside_a_version_gated_branch():
    p = plant.plant(CORE_SRC, "gated")
    assert p.version_gated == 6          # cmp, 3, 99, return, '-', 1 inside the if
    assert [m.id for m in p.mutants] == ["gated:return:33:11", "gated:arith:33:13", "gated:const:33:15"]
    src = b"import sys\n\ndef f(x):\n    y = 1 if sys.hexversion > 5 else 2\n    if hexversion:\n        return x + 1\n    return x\n"
    q = plant.plant(src, "f")
    assert q.version_gated == 7 and [m.id for m in q.mutants] == ["f:return:7:11"]


def test_plant_self_check_drops_a_flip_that_changes_more_than_one_node():
    src = b"def f(a, b, c):\n    return a and b or c\n"
    p = plant.plant(src, "f")
    assert p.not_planted == 2 and all(why.startswith(("ast-differs", "wrong-node-class")) for _id, why in p.dropped), p.dropped
    assert sorted(i for i, _w in p.dropped) == ["f:bool:2:13", "f:bool:2:19"]
    assert [m.op for m in p.mutants] == ["return"]
    q = plant.plant(b"def g(a, b, c):\n    return a and b and c\n", "g")
    assert [m.id for m in q.mutants] == ["g:return:2:11", "g:bool:2:13"]
    assert q.mutants[1].source == b"def g(a, b, c):\n    return a or b or c\n"


def test_plant_flips_each_comparison_and_arith_operator_class():
    src = (b"def f(a, b):\n    if a is not b and a not in b and a == b and a != b:\n"
           b"        return a <= b, a - b\n    return None\n")
    p = plant.plant(src, "f")
    got = {m.source.split(b"\n")[1 if m.line == 2 else 2].strip() for m in p.mutants if m.op in ("cmp", "arith")}
    assert b"if a is b and a not in b and a == b and a != b:" in got
    assert b"if a is not b and a in b and a == b and a != b:" in got
    assert b"if a is not b and a not in b and a != b and a != b:" in got
    assert b"if a is not b and a not in b and a == b and a == b:" in got
    assert b"return a > b, a - b" in got and b"return a <= b, a + b" in got
    assert not any(m.op == "return" and m.line == 4 for m in p.mutants)   # 'return None' is not a candidate


def test_plant_caps_at_twelve_spread_evenly_and_refuses_bad_input():
    body = "".join(f"    x{i} = {i}\n" for i in range(20))
    p = plant.plant(f"def f():\n{body}".encode(), "f")
    assert len(p.mutants) == 12 and p.candidates == 20 and p.capped == 8
    assert [m.line for m in p.mutants] == [2 + i * 20 // 12 for i in range(12)]
    with pytest.raises(plant.PlantError):
        plant.plant(b"def f():\n    return 1\ndef f():\n    return 2\n", "f")
    with pytest.raises(plant.PlantError):
        plant.plant(b"\xff\xfe", "f")
    with pytest.raises(plant.PlantError):
        plant.plant(b"def f(:\n", "f")
    assert plant.functions(b"class C:\n    def m(self): pass\nasync def a(): pass\ndef top(): pass\n") == ["top"]


# --- the runner (R2, R4) ---------------------------------------------------------------------------------

def _runner_repo(tmp_path, tests: str):
    d = tmp_path / "r"
    (d / "pkg").mkdir(parents=True)
    (d / "pkg" / "__init__.py").write_text("")
    (d / "pkg" / "mod.py").write_text('def f(x):\n    """\n    >>> f(1)\n    2\n    """\n    return x + 1\n')
    (d / "tests").mkdir()
    (d / "tests" / "__init__.py").write_text("")
    (d / "tests" / "testm.py").write_text(tests)
    shutil.copyfile(RUNNER, d / "fg_unittest_json.py")
    return d


def _run_runner(d, *args):
    r = subprocess.run([sys.executable, "-m", "fg_unittest_json", *args], cwd=d, capture_output=True, text=True,
                       timeout=60, env={"PATH": os.environ.get("PATH", ""), "PYTHONHASHSEED": "0",
                                        "PYTHONDONTWRITEBYTECODE": "1"})
    return r, json.loads(r.stdout)


TESTS = '''import doctest, sys, unittest
from pkg import mod
def load_tests(loader, tests, ignore):
    tests.addTests(doctest.DocTestSuite(mod))
    return tests
class T(unittest.TestCase):
    def test_ok(self):
        print("noise on stdout"); sys.stdout.flush(); self.assertEqual(mod.f(1), 2)
    def test_fail(self):
        self.assertEqual(mod.f(1), 3)
    def test_mem(self):
        raise MemoryError("x")
    def test_rec(self):
        raise RecursionError("x")
    def test_sub(self):
        for i in (1, 2):
            with self.subTest(i=i):
                self.assertEqual(i, 1)
    @unittest.skip("s")
    def test_skip(self):
        pass
'''


def test_runner_reports_each_test_with_its_exception_class_and_only_json_on_stdout(tmp_path):
    d = _runner_repo(tmp_path, TESTS)
    r, rep = _run_runner(d, "--module", "pkg.mod")
    assert r.returncode == 1 and rep["ok"] is False
    assert "noise on stdout" in r.stderr and r.stdout.count("\n") == 1     # fd 1 belongs to the report
    res = rep["results"]
    assert res["tests.testm.T.test_ok"] == {"outcome": "pass"}
    assert res["pkg.mod.f"] == {"outcome": "pass"}                          # the doctest loaded via load_tests
    assert res["tests.testm.T.test_fail"]["outcome"] == "fail" and res["tests.testm.T.test_fail"]["exc"] == "AssertionError"
    assert res["tests.testm.T.test_mem"]["outcome"] == "error" and res["tests.testm.T.test_mem"]["exc"] == "MemoryError"
    assert "RecursionError" in res["tests.testm.T.test_rec"]["exc_mro"]
    assert res["tests.testm.T.test_sub"]["outcome"] == "fail"               # one failing subtest marks it
    assert res["tests.testm.T.test_skip"]["outcome"] == "skip"
    assert rep["tests_run"] == 7
    real = str((d / "pkg" / "mod.py").resolve())
    import hashlib
    sha = hashlib.sha256((d / "pkg" / "mod.py").read_bytes()).hexdigest()
    for k in ("start", "end"):
        assert os.path.realpath(rep["module"][k]["file"]) == real and rep["module"][k]["sha256"] == sha


def test_runner_exits_zero_only_when_the_suite_is_green_and_shows_a_swapped_module(tmp_path):
    d = _runner_repo(tmp_path, "import unittest\nfrom pkg import mod\nclass T(unittest.TestCase):\n"
                               "    def test_ok(self):\n        self.assertEqual(mod.f(1), 2)\n")
    r, rep = _run_runner(d, "--module", "pkg.mod")
    assert r.returncode == 0 and rep["ok"] is True and rep["tests_run"] == 1
    (d / "tests" / "testswap.py").write_text(
        "import sys, types, unittest\nclass S(unittest.TestCase):\n    def test_swap(self):\n"
        "        m = types.ModuleType('pkg.mod'); m.__file__ = '/usr/lib/python3/dist-packages/pkg/mod.py'\n"
        "        sys.modules['pkg.mod'] = m\n")
    r, rep = _run_runner(d, "--module", "pkg.mod")
    assert rep["module"]["start"]["file"].endswith("pkg/mod.py") and "/usr/lib" not in rep["module"]["start"]["file"]
    assert rep["module"]["end"]["file"] == "/usr/lib/python3/dist-packages/pkg/mod.py"
    r, rep = _run_runner(d, "--module", "pkg.nope")
    assert rep["module"]["import_error"].startswith("ModuleNotFoundError") and rep["module"]["start"]["file"] is None


# --- the fact tool: pure (runner without the box) --------------------------------------------------------

def test_well_tested_is_all_caught_untested_all_survived_and_only_the_facts_file_is_written(fake_box, tmp_path, monkeypatch):
    gh = _gh(tmp_path)
    brain_before = {p.name: p.read_bytes() for p in fake_box["brain"].iterdir()}
    opened = []
    real_open = builtins.open

    def spy(file, *a, **k):
        opened.append(str(file))
        return real_open(file, *a, **k)
    monkeypatch.setattr(builtins, "open", spy)
    monkeypatch.setattr(io, "open", spy)            # review F4: pathlib opens through io.open
    real_os_open = os.open
    monkeypatch.setattr(os, "open", lambda f, *a, **k: (opened.append(str(f)), real_os_open(f, *a, **k))[1])
    res = _facts(gh, ["clamp", "untested_sum"])
    monkeypatch.setattr(builtins, "open", real_open)
    monkeypatch.setattr(io, "open", real_open)
    monkeypatch.setattr(os, "open", real_os_open)
    assert any(f.endswith("github_facts.jsonl") for f in opened)   # the spy does see our own writes
    c, u = _row(res, "clamp"), _row(res, "untested_sum")
    assert c["outcome"] == "MEASURED" and (c["m"], c["k"], c["s"], c["u"]) == (5, 5, 0, 0), c
    assert u["outcome"] == "MEASURED" and (u["m"], u["k"], u["s"], u["u"]) == (2, 0, 2, 0), u
    for m in c["mutants"]:
        assert m["outcome"] == "CAUGHT" and m["rc"] == 1 and m["rerun_rc"] == 1
        assert set(m["failing_ids"]) & set(m["rerun_failing_ids"])
        assert m["module_file"] == "/work/fgfix/core.py" and m["module_sha"] == m["expected_sha"]
    assert all(m["outcome"] == "SURVIVED" and m["rerun_rc"] is None for m in u["mutants"])
    assert c["controls"][0]["pass_set_sha256"] == c["controls"][1]["pass_set_sha256"] and c["controls"][0]["tests_run"] == 8
    # Only github_facts.jsonl appears in the scout dir; the registry files are never opened.
    assert sorted(p.name for p in fake_box["scout"].iterdir()) == ["github_facts.jsonl"]
    assert {p.name: p.read_bytes() for p in fake_box["brain"].iterdir()} == brain_before
    assert not [f for f in opened if "scout_registry" in f]
    lines = (fake_box["scout"] / "github_facts.jsonl").read_text().splitlines()
    assert [json.loads(x)["function"] for x in lines] == ["clamp", "untested_sum"]
    assert not list((tmp_path / "boxroot").iterdir())                    # the staging folder is gone


R8_KEYS = {"run_id", "box_user", "controls", "mutants", "m", "k", "s", "u", "not_planted", "version_gated",
           "host_python", "box_python", "test_argv", "control_times", "tree_check", "tool_version", "outcome"}
MUTANT_KEYS = {"id", "op", "rc", "outcome", "failing_ids", "rerun_failing_ids", "error_classes", "module_file",
               "module_sha", "expected_sha"}


def test_a_row_carries_the_r8_schema_and_its_counts_recompute(fake_box, tmp_path):
    gh = _gh(tmp_path)
    res = _facts(gh, ["gated"])
    r = _row(res, "gated")
    assert R8_KEYS <= set(r) and r["box_user"] == "fgbox-test" and r["version_gated"] == 6
    assert r["test_argv"] == ["env", "PYTHONHASHSEED=0", "python3", "-m", "fg_unittest_json", "--module", "fgfix.core"]
    assert r["tree_check"]["ok"] is True and r["tree_check"]["full"] is True
    for c in r["controls"]:
        assert {"rc", "tests_run", "pass_set_sha256"} <= set(c) and c["rc"] == 0
    for m in r["mutants"]:
        assert MUTANT_KEYS <= set(m)
    assert r["k"] == sum(m["outcome"] == "CAUGHT" for m in r["mutants"]) == 3 and r["m"] == 3
    assert r["run_id"] == res.run_id and r["outcome"] == "MEASURED"


def test_box_user_unset_is_blind_for_the_whole_run_and_nothing_is_admitted_or_run(env, tmp_path, monkeypatch):
    gh = _gh(tmp_path)
    monkeypatch.delenv(box.BOX_USER_KEY, raising=False)
    monkeypatch.setattr(config, "_load", lambda: {})
    monkeypatch.setattr(ghpool, "admit", lambda *a, **k: (_ for _ in ()).throw(AssertionError("admitted")))
    rec = Recorder()
    res = _facts(gh, None, run_box=rec)
    assert res.run_blind == "box-user-unset" and rec.labels == []
    assert [r["outcome"] for r in res.rows] == ["BLIND(box-user-unset)"] and res.rows[0]["box_user"] is None
    assert json.loads((env["scout"] / "github_facts.jsonl").read_text())["outcome"] == "BLIND(box-user-unset)"


def test_a_control_that_ran_no_tests_is_blind_no_tests(fake_box, tmp_path):
    gh = _gh(tmp_path)
    rec = Recorder(after=lambda r, label, n: _rewrite(r, lambda rep: _report(rep, tests_run=0, results={}))
                   if label == "control" else r)
    res = _facts(gh, ["clamp"], run_box=rec)
    assert _row(res, "clamp")["outcome"] == "BLIND(no-tests)" and rec.labels == ["control"]


def test_a_red_or_unreadable_control_is_blind_and_no_mutant_runs(fake_box, tmp_path):
    gh = _gh(tmp_path)
    for after, want in ((lambda r, label, n: box.BoxResult(r.outcome, 1, r.stdout, r.stderr), "BLIND(control-red: rc 1)"),
                        (lambda r, label, n: box.BoxResult(r.outcome, 0, "not json", r.stderr), "BLIND(control-red: no-json")):
        rec = Recorder(after=after)
        res = _facts(gh, ["clamp"], run_box=rec)
        assert _row(res, "clamp")["outcome"].startswith(want) and rec.labels == ["control"], res.rows


def test_an_exit_code_other_than_one_is_never_caught(fake_box, tmp_path):
    gh = _gh(tmp_path)
    rec = Recorder(after=lambda r, label, n: box.BoxResult(r.outcome, 2, r.stdout, r.stderr)
                   if label.startswith(("mutant", "rerun")) else r)
    r = _row(_facts(gh, ["clamp"], run_box=rec), "clamp")
    assert r["k"] == 0 and r["u"] == 5 and set(_outcomes(r).values()) == {"UNCLEAR(rc 2)"}
    assert r["outcome"] == "BLIND(all-mutants-unclear)"           # review F1: nothing decided
    # one decided mutant is enough for MEASURED; the rest stay UNCLEAR in the row
    rec = Recorder(after=lambda r, label, n: box.BoxResult(r.outcome, 2, r.stdout, r.stderr)
                   if label.startswith(("mutant", "rerun")) and "return:15" not in label else r)
    r = _row(_facts(gh, ["clamp"], run_box=rec), "clamp")
    assert (r["k"], r["u"], r["outcome"]) == (1, 4, "MEASURED")


def test_a_flaky_failure_that_does_not_repeat_on_the_rerun_is_unclear(fake_box, tmp_path):
    gh = _gh(tmp_path)
    rec = Recorder(before=lambda stage, label, n: _flag(stage) if label.startswith("mutant") else None)
    r = _row(_facts(gh, ["flaky_fn"], run_box=rec), "flaky_fn")
    assert rec.labels == ["control", "mutant:flaky_fn:return:23:11", "rerun:flaky_fn:return:23:11", "control"]
    m = r["mutants"][0]
    assert m["failing_ids"] == ["tests.testcore.Flaky.test_flaky"] and m["rerun_failing_ids"] == []
    assert m["outcome"] == "UNCLEAR(rerun-not-failing)" and (r["k"], r["u"]) == (0, 1)


def test_a_rerun_that_fails_differently_is_not_caught(fake_box, tmp_path):
    gh = _gh(tmp_path)
    # the rerun fails only a test that was never green in control: not "the same test again"
    rec = Recorder(after=lambda r, label, n: _rewrite(r, lambda rep: _report(
        rep, results={"x.never_green": {"outcome": "fail", "exc": "AssertionError", "exc_mro": ["AssertionError"]}}))
        if label.startswith("rerun") else r)
    r = _row(_facts(gh, ["clamp"], run_box=rec), "clamp")
    assert set(_outcomes(r).values()) == {"UNCLEAR(rerun-not-failing)"} and r["k"] == 0
    rec = Recorder(after=lambda r, label, n: box.BoxResult(r.outcome, 0, r.stdout, r.stderr)
                   if label.startswith("rerun") else r)
    r = _row(_facts(gh, ["clamp"], run_box=rec), "clamp")
    assert set(_outcomes(r).values()) == {"UNCLEAR(rerun-not-failing)"}


def test_a_flaky_control_is_blind_control_unstable(fake_box, tmp_path):
    gh = _gh(tmp_path)
    seen = []
    rec = Recorder(before=lambda stage, label, n: (seen.append(label), _flag(stage) if seen.count("control") == 2 and label == "control" else None))
    res = _facts(gh, ["flaky_fn"], run_box=rec)
    # the second control fails the flaky test -> control-red (rc 1), never MEASURED
    assert _row(res, "flaky_fn")["outcome"] == "BLIND(control-red: rc 1)"
    # same pass-set size, different ids: unstable
    rec = Recorder(after=lambda r, label, n: _rewrite(r, lambda rep: _report(
        rep, results=dict(rep["results"], **{"x.extra": {"outcome": "pass"}})))
        if label == "control" and n > 1 else r)
    assert _row(_facts(gh, ["flaky_fn"], run_box=rec), "flaky_fn")["outcome"] == "BLIND(control-unstable)"


@pytest.mark.parametrize("where,proof", [
    ("start", {"file": "/usr/lib/python3/dist-packages/fgfix/core.py"}),
    ("end", {"file": "/usr/lib/python3/dist-packages/fgfix/core.py"}),
    ("end", {"sha256": "0" * 64}),
    ("start", {"file": "/work/../usr/fgfix/core.py"}),
])
def test_a_run_that_loaded_another_copy_of_the_module_is_blind_wrong_module(fake_box, tmp_path, where, proof):
    gh = _gh(tmp_path)

    def swap(rep):
        rep["module"][where] = dict(rep["module"][where], **proof)
        return rep
    rec = Recorder(after=lambda r, label, n: _rewrite(r, swap) if label == "mutant:clamp:cmp:11:9" else r)
    r = _row(_facts(gh, ["clamp"], run_box=rec), "clamp")
    assert r["outcome"] == "BLIND(wrong-module)"
    m = {x["id"]: x for x in r["mutants"]}["clamp:cmp:11:9"]
    assert m["outcome"] == "UNCLEAR(wrong-module)" and (m["module_file"] != "/work/fgfix/core.py"
                                                         or m["module_sha"] != m["expected_sha"])


def test_a_mutant_that_breaks_the_import_is_unclear_not_wrong_module(fake_box, tmp_path):
    gh = _gh(tmp_path)

    def broke(rep):
        rep["module"] = {"name": "fgfix.core", "import_error": "ValueError: boom",
                         "start": {"file": None, "sha256": None}, "end": {"file": None, "sha256": None}}
        return rep
    rec = Recorder(after=lambda r, label, n: _rewrite(r, broke) if label == "mutant:clamp:cmp:11:9" else r)
    r = _row(_facts(gh, ["clamp"], run_box=rec), "clamp")
    assert _outcomes(r)["clamp:cmp:11:9"] == "UNCLEAR(import-error)" and r["outcome"] == "MEASURED" and r["k"] == 4
    assert "rerun:clamp:cmp:11:9" not in rec.labels
    rec = Recorder(after=lambda r, label, n: _rewrite(r, broke) if label == "control" else r)
    r = _row(_facts(gh, ["clamp"], run_box=rec), "clamp")
    assert r["outcome"] == "BLIND(control-red: import-error)" and rec.labels == ["control"]


def test_a_control_that_loaded_another_copy_is_blind_wrong_module(fake_box, tmp_path):
    gh = _gh(tmp_path)
    rec = Recorder(after=lambda r, label, n: _rewrite(r, lambda rep: dict(rep, module={"name": "fgfix.core"}))
                   if label == "control" else r)
    assert _row(_facts(gh, ["clamp"], run_box=rec), "clamp")["outcome"] == "BLIND(wrong-module)"
    assert rec.labels == ["control"]                     # no mutant runs on a control that proved nothing


def test_a_memory_error_in_a_test_is_unclear_never_caught(fake_box, tmp_path):
    gh = _gh(tmp_path)
    r = _row(_facts(gh, ["guarded"]), "guarded")
    assert _outcomes(r) == {"guarded:cmp:37:9": "UNCLEAR(resource-error: MemoryError)",
                            "guarded:const:37:11": "SURVIVED", "guarded:return:39:11": "CAUGHT",
                            "guarded:const:39:15": "CAUGHT"}
    assert (r["m"], r["k"], r["s"], r["u"]) == (4, 2, 1, 1)
    m = {x["id"]: x for x in r["mutants"]}["guarded:cmp:37:9"]
    assert m["error_classes"] == {"tests.testcore.Guarded.test_guarded": "MemoryError"}


def test_a_resource_error_on_the_rerun_or_a_recursion_error_is_unclear(fake_box, tmp_path):
    gh = _gh(tmp_path)

    def rec_err(rep):
        for v in rep["results"].values():
            if v["outcome"] in ("fail", "error"):
                v.update(exc="RecursionError", exc_mro=["RecursionError", "RuntimeError", "Exception"])
        return rep
    for lab in ("mutant", "rerun"):
        rec = Recorder(after=lambda r, label, n, lab=lab: _rewrite(r, rec_err) if label.startswith(lab) else r)
        r = _row(_facts(gh, ["clamp"], run_box=rec), "clamp")
        assert r["k"] == 0 and all(v.startswith("UNCLEAR(resource-error") for v in _outcomes(r).values()), r


def test_failures_only_in_tests_red_in_control_are_unclear(fake_box, tmp_path):
    gh = _gh(tmp_path)
    rec = Recorder(after=lambda r, label, n: _rewrite(r, lambda rep: _report(rep, results=dict(
        {k: {"outcome": "pass"} for k in rep["results"]},
        **{"x.never_green": {"outcome": "error", "exc": "ValueError", "exc_mro": ["ValueError"]}})))
        if label.startswith("mutant") else r)
    r = _row(_facts(gh, ["clamp"], run_box=rec), "clamp")
    assert set(_outcomes(r).values()) == {"UNCLEAR(failed-only-outside-control)"} and "rerun:" not in " ".join(rec.labels)


def test_exit_zero_with_a_control_test_missing_is_unclear_not_survived(fake_box, tmp_path):
    gh = _gh(tmp_path)
    rec = Recorder(after=lambda r, label, n: _rewrite(r, lambda rep: _report(rep, results={
        k: v for k, v in rep["results"].items() if k != "tests.testcore.Clamp.test_inside"}))
        if label.startswith("mutant") else r)
    r = _row(_facts(gh, ["untested_sum"], run_box=rec), "untested_sum")
    assert set(_outcomes(r).values()) == {"UNCLEAR(control-tests-not-passed)"} and r["s"] == 0


def test_timeouts_box_failures_and_missing_reports_are_named(fake_box, tmp_path):
    gh = _gh(tmp_path)
    cases = [(blind("timeout after 60s"), None, "UNCLEAR(timeout)", "BLIND(all-mutants-unclear)"),
             (blind("box-red-arm: write"), None, "UNCLEAR(box: BLIND(box-red-arm: write))", "BLIND(box: BLIND(box-red-arm: write))"),
             (None, "garbage", "UNCLEAR(no-runner-report)", "BLIND(all-mutants-unclear)")]
    for oc, out, want_m, want_row in cases:
        def after(r, label, n, oc=oc, out=out):
            if not label.startswith("mutant"):
                return r
            return box.BoxResult(oc, None, "", "") if oc else box.BoxResult(r.outcome, 1, out, "")
        r = _row(_facts(gh, ["untested_sum"], run_box=Recorder(after=after)), "untested_sum")
        assert set(_outcomes(r).values()) == {want_m} and r["outcome"] == want_row, r


def test_the_timeout_is_three_medians_clamped_and_controls_get_the_maximum(fake_box, tmp_path):
    assert uf.mutant_timeout([1.0, 2.0]) == 60.0
    assert uf.mutant_timeout([40.0, 60.0, 50.0]) == 150.0
    assert uf.mutant_timeout([500.0]) == 300.0
    gh = _gh(tmp_path)
    seen = []
    rec = Recorder(inner=lambda stage, argv, timeout, label: (seen.append((label.split(":")[0], timeout)),
                                                             unboxed(stage, argv, 60, label))[1])
    clock = iter(range(0, 10_000, 30))   # every run takes 30 s on this clock
    r = _row(_facts(gh, ["untested_sum"], run_box=rec, clock=lambda: next(clock)), "untested_sum")
    assert ("control", 300.0) in seen and ("mutant", 90.0) in seen and r["timeout_s"] == 90.0
    assert r["control_times"] == [30, 30]


def test_each_run_waits_while_the_load_is_high_and_gives_up_after_the_cap(fake_box, tmp_path, monkeypatch):
    gh = _gh(tmp_path)
    loads = iter([5.0, 4.5, 1.0] + [0.5] * 100)
    slept = []
    r = _row(_facts(gh, ["untested_sum"], loadavg=lambda: (next(loads), 0, 0), sleep=slept.append), "untested_sum")
    assert slept == [uf.LOAD_POLL_S, uf.LOAD_POLL_S] and r["outcome"] == "MEASURED"
    rec = Recorder()
    res = _facts(gh, ["untested_sum"], run_box=rec, loadavg=lambda: (9.0, 0, 0), sleep=lambda s: None)
    assert rec.labels == [] and _row(res, "untested_sum")["outcome"].startswith("BLIND(control-red: box-blind")


def test_a_projection_over_four_hours_keeps_only_the_first_functions_that_fit(fake_box, tmp_path, monkeypatch):
    gh = _gh(tmp_path)
    monkeypatch.setattr(uf, "BUDGET_S", 1300.0)
    t = iter(range(0, 1_000_000, 100))   # every clock read moves 100 s
    res = _facts(gh, ["clamp", "untested_sum", "flaky_fn"], clock=lambda: next(t))
    # projection (review F3, re-runs counted): 100 + (1+2*5)*100 + (1+2*2)*100 + (1+2*1)*100 > 1300
    # -> only clamp fits (100 + 1100)
    assert _row(res, "clamp")["outcome"] == "MEASURED"
    for fn in ("untested_sum", "flaky_fn"):
        row = _row(res, fn)
        assert row["outcome"] == "BLIND(budget-cut: first 1 of 3 functions fit 1300 s)" and row["budget"]["cut"]


def test_a_run_that_overruns_its_projection_stops_at_the_budget(fake_box, tmp_path, monkeypatch):
    gh = _gh(tmp_path)
    monkeypatch.setattr(uf, "BUDGET_S", 4000.0)
    n = {"i": 0}

    def clock():   # 100 s per read; one 5000 s stall while clamp's mutants run
        n["i"] += 1
        return n["i"] * 100 + (5000 if n["i"] > 12 else 0)
    res = _facts(gh, ["clamp", "untested_sum"], clock=clock)
    assert _row(res, "clamp")["outcome"] == "MEASURED"
    row = _row(res, "untested_sum")
    assert row["outcome"].startswith("BLIND(budget-cut: elapsed ") and row["budget"]["elapsed_s"] > 4000


def test_the_full_tree_check_runs_before_a_module_and_a_tampered_tree_runs_nothing(fake_box, tmp_path, monkeypatch):
    gh = _gh(tmp_path)
    calls = []
    real = ghcache.check_cache
    monkeypatch.setattr(ghcache, "check_cache", lambda *a, **k: (calls.append(k.get("full")), real(*a, **k))[1])
    _facts(gh, ["clamp"])
    assert True in calls
    # LICENSE: admission compares its blob id from git, never its bytes on disk; only R7 reads them.
    # (A tampered .py is already refused by admit's per-file read, for the whole run.)
    f = gh.cache / "python" / f"cpython@{gh.sha}" / "LICENSE"
    os.chmod(f.parent, 0o755)
    os.chmod(f, 0o644)
    f.write_text(f.read_text() + "\n# changed\n")
    rec = Recorder()
    r = _row(_facts(gh, ["clamp"], run_box=rec), "clamp")
    assert rec.labels == [] and r["outcome"].startswith("BLIND(pin-mismatch:") and r["tree_check"]["ok"] is False


def test_a_repo_that_is_not_admitted_is_blind_for_the_whole_run(fake_box, tmp_path):
    gh = _gh(tmp_path)
    rec = Recorder()
    res = uf.run(gh.spec, None, gh_opts=dict(gh.opts, trust_pin_value="operator=0000000000000000"), run_box=rec)
    assert res.run_blind.startswith("unsigned:") and rec.labels == [] and len(res.rows) == 1
    res = uf.run("python/cpython@" + "1" * 40, None, gh_opts=gh.opts, run_box=rec)
    assert res.run_blind.startswith("pin-mismatch: cache unreadable") or res.run_blind.startswith("unsigned")
    assert uf.run("not a spec", None, gh_opts=gh.opts, run_box=rec).run_blind.startswith("unsigned:")
    assert rec.labels == []


def test_selection_names_unknown_functions_and_never_takes_test_files(fake_box, tmp_path):
    gh = _gh(tmp_path)
    rec = Recorder(inner=lambda *a: (_ for _ in ()).throw(AssertionError("ran")))
    res = uf.run(gh.spec, ["no_such_fn", "load_tests"], gh_opts=gh.opts, run_box=rec)
    assert sorted(r["outcome"] for r in res.rows) == ["BLIND(function-not-admitted)"] * 2
    assert res.rows[0]["admission"]["admitted_files"] == ["fgfix/__init__.py", "fgfix/core.py"]
    res = uf.run(gh.spec, ["fgfix/core.py::clamp", "fgfix.core:untested_sum"], gh_opts=gh.opts, run_box=unboxed)
    assert [r["function"] for r in res.rows] == ["clamp", "untested_sum"]


def test_a_function_without_mutants_or_a_runner_name_clash_or_no_package_is_blind(fake_box, tmp_path):
    files = dict(PURE_FILES, **{"fgfix/core.py": PURE_FILES["fgfix/core.py"] + "\n\ndef noop():\n    pass\n"})
    gh = _gh(tmp_path, files=files)
    assert _row(_facts(gh, ["noop"]), "noop")["outcome"] == "BLIND(no-mutants)"
    gh2 = _gh(tmp_path / "b", files=dict(PURE_FILES, **{"fg_unittest_json.py": "def x():\n    return 1\n"}))
    assert _row(_facts(gh2, ["clamp"]), "clamp")["outcome"].startswith("BLIND(runner-name-taken")
    gh3 = _gh(tmp_path / "c", files=dict(PURE_FILES, **{"loose/mod.py": "def y(a):\n    return a + 1\n"}))
    assert _row(_facts(gh3, ["y"]), "y")["outcome"] == "BLIND(not-importable: no loose/__init__.py)"


def test_the_fact_tool_never_imports_learn_or_cards():
    for f in ("src/forgeproof/scout/upstream_facts.py", "src/forgeproof/scout/plant.py", "tools/fg_unittest_json.py"):
        tree = ast.parse((REPO / f).read_text())
        names = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                names |= {a.name for a in n.names}
            elif isinstance(n, ast.ImportFrom):
                names |= {n.module or ""} | {a.name for a in n.names}
        assert not {x for x in names if x.split(".")[-1] in ("learn", "cards")}, (f, names)
    # The package's own __init__ (step 3a) loads cards for every forgeproof.scout import; the tool
    # must add no learn/cards module beyond what `import forgeproof.scout` alone loads.
    code = ("import sys; import forgeproof.scout; base = {m for m in sys.modules if m.endswith(('.learn', '.cards'))}; "
            "import forgeproof.scout.upstream_facts, forgeproof.scout.plant; "
            "print(sorted(base), sorted(m for m in sys.modules if m.endswith(('.learn', '.cards')) and m not in base))")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=dict(os.environ, PYTHONPATH=str(REPO / "src")))
    assert r.returncode == 0 and r.stdout.strip() == "['forgeproof.scout.cards'] []", (r.stdout, r.stderr)


def test_cli_github_facts_exit_codes(env, tmp_path, monkeypatch, capsys):
    monkeypatch.delenv(box.BOX_USER_KEY, raising=False)
    monkeypatch.setattr(config, "_load", lambda: {})
    assert cli.main(["github-facts", "python/cpython@" + "a" * 40]) == 3
    out = capsys.readouterr()
    assert "BLIND(box-user-unset)" in out.out and "0 MEASURED of 1 rows" in out.err
    monkeypatch.delenv(config.SCOUT_DIR_KEY)
    assert cli.main(["github-facts", "python/cpython@" + "a" * 40]) == 2


def test_cli_github_facts_exits_zero_when_a_function_was_measured(fake_box, tmp_path, monkeypatch, capsys):
    gh = _gh(tmp_path)
    real = uf.run
    monkeypatch.setattr(uf, "run", lambda spec, fns: real(spec, fns, gh_opts=gh.opts, run_box=unboxed))
    assert cli.main(["github-facts", gh.spec, "--functions", "untested_sum", "--json"]) == 0
    row = json.loads(capsys.readouterr().out.splitlines()[0])
    assert row["outcome"] == "MEASURED" and row["function"] == "untested_sum"


# --- box tests: the real run-mode box (deploy host, -m box, §9 env) ---------------------------------------

def _box_gh(tmp_path):
    return _gh(tmp_path, files=FILES)


def _boxed(tmp_path, functions, run_box=None):
    gh = _box_gh(tmp_path)
    return uf.run(gh.spec, functions, gh_opts=gh.opts, run_box=run_box)


def _box_env(tmp_path, monkeypatch):
    scout = tmp_path / "scout"
    scout.mkdir()
    monkeypatch.setenv(config.SCOUT_DIR_KEY, str(scout))
    return scout


@pytest.mark.box
@pytest.mark.timeout(900)
@pytest.mark.skipif(not LINUX_BOX, reason="needs Linux + bwrap + FORGE_GATE_BOX_USER/ROOT (run with -m box)")
def test_box_well_tested_is_all_caught_and_untested_all_survived(tmp_path, monkeypatch):
    scout = _box_env(tmp_path, monkeypatch)
    res = _boxed(tmp_path, ["clamp", "untested_sum"])
    c, u = _row(res, "clamp"), _row(res, "untested_sum")
    assert c["outcome"] == "MEASURED" and (c["m"], c["k"], c["s"], c["u"]) == (5, 5, 0, 0), c
    assert u["outcome"] == "MEASURED" and (u["m"], u["k"], u["s"], u["u"]) == (2, 0, 2, 0), u
    assert c["box_user"] == box.box_user() and c["box_python"].startswith("3.")
    for m in c["mutants"] + u["mutants"]:
        assert m["module_file"] == "/work/fgfix/core.py" and m["module_sha"] == m["expected_sha"]
    assert c["controls"][0]["tests_run"] == 9      # 8 core + 1 testother (the doctest included)
    assert sorted(p.name for p in scout.iterdir()) == ["github_facts.jsonl"]


@pytest.mark.box
@pytest.mark.timeout(900)
@pytest.mark.skipif(not LINUX_BOX, reason="needs Linux + bwrap + FORGE_GATE_BOX_USER/ROOT (run with -m box)")
def test_box_a_flaky_control_is_blind_and_a_flaky_mutant_failure_is_unclear(tmp_path, monkeypatch):
    _box_env(tmp_path, monkeypatch)
    real = uf._default_run_box
    seen = []
    rec = Recorder(inner=real, before=lambda stage, label, n: (seen.append(label),
                   _flag(stage) if label == "control" and seen.count("control") == 2 else None))
    assert _row(_boxed(tmp_path / "a", ["flaky_fn"], rec), "flaky_fn")["outcome"].startswith("BLIND(control-")
    rec = Recorder(inner=real, before=lambda stage, label, n: _flag(stage) if label.startswith("mutant") else None)
    r = _row(_boxed(tmp_path / "b", ["flaky_fn"], rec), "flaky_fn")
    assert r["mutants"][0]["outcome"] == "UNCLEAR(rerun-not-failing)" and r["k"] == 0, r


@pytest.mark.box
@pytest.mark.timeout(900)
@pytest.mark.skipif(not LINUX_BOX, reason="needs Linux + bwrap + FORGE_GATE_BOX_USER/ROOT (run with -m box)")
def test_box_a_test_that_loads_a_copy_outside_work_is_blind_wrong_module(tmp_path, monkeypatch):
    _box_env(tmp_path, monkeypatch)
    res = _boxed(tmp_path, ["elsewhere", "clamp"])
    e = _row(res, "elsewhere")
    assert e["outcome"] == "BLIND(wrong-module)", e
    assert e["controls"][0]["module_file"] == "/src/fgfix/other.py"
    assert _row(res, "clamp")["outcome"] == "MEASURED"        # the other module is not affected


@pytest.mark.box
@pytest.mark.timeout(900)
@pytest.mark.skipif(not LINUX_BOX, reason="needs Linux + bwrap + FORGE_GATE_BOX_USER/ROOT (run with -m box)")
def test_box_a_memory_error_is_unclear_and_version_gated_code_is_not_planted(tmp_path, monkeypatch):
    _box_env(tmp_path, monkeypatch)
    res = _boxed(tmp_path, ["guarded", "gated", "label"])
    g = _row(res, "guarded")
    assert _outcomes(g)["guarded:cmp:37:9"] == "UNCLEAR(resource-error: MemoryError)" and g["k"] == 2, g
    v = _row(res, "gated")
    assert v["outcome"] == "MEASURED" and v["version_gated"] == 6 and v["m"] == 3 and v["k"] == 3, v
    lab = _row(res, "label")
    assert lab["outcome"] == "MEASURED" and "label:cmp:27:25" in _outcomes(lab)


@pytest.mark.box
@pytest.mark.timeout(900)
@pytest.mark.skipif(not LINUX_BOX, reason="needs Linux + bwrap + FORGE_GATE_BOX_USER/ROOT (run with -m box)")
def test_box_user_unset_runs_nothing_in_the_box(tmp_path, monkeypatch):
    _box_env(tmp_path, monkeypatch)
    gh = _box_gh(tmp_path)
    monkeypatch.delenv(box.BOX_USER_KEY)
    monkeypatch.setattr(config, "_load", lambda: {})
    monkeypatch.setattr(box, "run", _refuse_real_box)
    res = uf.run(gh.spec, None, gh_opts=gh.opts)
    assert [r["outcome"] for r in res.rows] == ["BLIND(box-user-unset)"]
