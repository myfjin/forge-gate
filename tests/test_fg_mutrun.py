"""tools/fg_mutrun.py, the mutant runner (shared-tools loop, slice S3, design doc M1-M10).

Every check has a test here that goes red when the check is removed. The runner works on small
throw-away git trees under tmp_path, never on this checkout. Its suite PATH is a bin dir made
here (FORGE_GATE_GATE_PATH), so no test depends on where a host keeps GNU timeout.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
TOOL = REPO / "tools" / "fg_mutrun.py"
GIT = shutil.which("git")


def _load():
    spec = importlib.util.spec_from_file_location("fg_mutrun_under_test", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mr = _load()


# ---- fixtures and helpers ------------------------------------------------------------------

def _bin(d: Path) -> Path:
    """A suite PATH dir; `timeout` only has to RESOLVE (G4), so git stands in where needed."""
    d.mkdir(parents=True, exist_ok=True)
    if GIT is None:
        pytest.fail("git not on PATH: the mutant-runner tests need git")
    (d / "git").symlink_to(GIT)
    (d / "timeout").symlink_to(shutil.which("timeout") or GIT)
    return d


def _git(tree, *args):
    p = subprocess.run([GIT, "-C", str(tree), "-c", "user.name=fg", "-c", "user.email=fg@example.com",
                        "-c", "commit.gpgsign=false", *args], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    return p.stdout


CALC = "def add(a, b):\n    return a + b  # ADD\n"
TEST_CALC = ("from forgeproof.calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n\n\n"
             "def test_other():\n    assert True\n")
K = {"id": "K", "file": "src/forgeproof/calc.py", "old": "return a + b", "new": "return a - b",
     "tests": ["tests/test_calc.py::test_add"], "what": "kill"}
S = {"id": "S", "file": "src/forgeproof/calc.py", "old": "# ADD", "new": "# changed",
     "tests": ["tests/test_calc.py::test_add"], "what": "survive"}


def make_tree(base: Path, tests=None, files=None) -> Path:
    t = base / "tree"
    (t / "src" / "forgeproof").mkdir(parents=True)
    (t / "tests").mkdir()
    (t / "src" / "forgeproof" / "__init__.py").write_text("")
    (t / "src" / "forgeproof" / "calc.py").write_text(CALC)
    (t / "pytest.ini").write_text("[pytest]\ntestpaths = tests\n")
    (t / "README.md").write_text("readme\n")
    for name, body in (tests or {"test_calc.py": TEST_CALC}).items():
        (t / "tests" / name).write_text(body)
    for rel, body in (files or {}).items():
        (t / rel).parent.mkdir(parents=True, exist_ok=True)
        (t / rel).write_text(body)
    _git(t, "init", "-q")
    _git(t, "add", "-A")
    _git(t, "commit", "-q", "-m", "fixture")
    return t


@pytest.fixture
def mut(tmp_path, monkeypatch, capsys):
    """Run fg_mutrun.main in-process; returns (exit, stdout lines, last record or None)."""
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setenv(mr.fg.PATH_KEY, str(_bin(tmp_path / "bin")))
    monkeypatch.setattr(mr.fg, "_loadavg", lambda: 0.0)
    rec = tmp_path / "rec" / "r.jsonl"

    def run(tree, spec, *more, record=None):
        sp = tmp_path / "spec.json"
        if spec is not None:
            sp.write_text(json.dumps(spec))
        r = record or rec
        code = mr.main(["--tree", str(tree), "--python", sys.executable, "--spec", str(sp),
                        "--record", str(r), "--tag", "t", "--max-load", "1000", "--wait", "0", *more])
        out = capsys.readouterr().out.strip().splitlines()
        rows = Path(r).read_text().splitlines() if Path(r).is_file() else []
        return code, out, (json.loads(rows[-1]) if rows else None)
    run.rec = rec
    return run


def _junits(rec: Path):
    return sorted(rec.parent.glob("junit-*.xml")) if rec.parent.is_dir() else []


CALC_REL = Path("src/forgeproof/calc.py")


# ---- M1/M6/M8/M9/M10: a kill and a survivor, the exact lines, the record -----------------------

def test_kill_and_survivor_print_the_exact_lines_and_record_every_field(tmp_path, mut):
    tree = make_tree(tmp_path)
    head = _git(tree, "rev-parse", "HEAD").strip()
    index = (tree / ".git" / "index").read_bytes()
    code, out, r = mut(tree, [K, S])
    assert code == 1, out
    assert out[0] == "CONTROL-BEFORE rc=0 tests=1"
    assert out[1].startswith("K KILLED rc=1 | kill | ") and "1 failed" in out[1], out
    assert out[2].startswith("S SURVIVED rc=0 | survive | ") and "1 passed" in out[2], out
    assert out[3:] == ["CONTROL-AFTER rc=0 tests=1", "SURVIVORS: S", "ERRORS: none", "KILLED: 1/2",
                       "porcelain_after="]
    # M5/M10: restored, nothing committed, the index untouched (read before this test's own
    # `git status`, which may refresh the index).
    assert (tree / ".git" / "index").read_bytes() == index
    assert (tree / CALC_REL).read_text() == CALC
    assert _git(tree, "status", "--porcelain") == ""
    assert _git(tree, "rev-parse", "HEAD").strip() == head
    # M9: the record.
    assert r["tool_md5"] == hashlib.md5(TOOL.read_bytes()).hexdigest()
    assert r["spec_md5"] == hashlib.md5((tmp_path / "spec.json").read_bytes()).hexdigest()
    assert r["gate_file"] is None and r["gate_md5"] is None  # S3c: fg_gate.py is no longer loaded
    common = REPO / "tools" / "fg_common.py"
    assert Path(r["common_file"]) == common.resolve()
    assert r["common_md5"] == hashlib.md5(common.read_bytes()).hexdigest()
    assert Path(r["paths_file"]).resolve() == (REPO / "src" / "forgeproof" / "paths.py").resolve()
    assert r["head"] == head and r["porcelain_before"] == "" and r["porcelain_after"] == ""
    assert r["exit"] == 1 and r["control_before"]["rc"] == 0 and r["control_after"]["rc"] == 0
    assert [m["verdict"] for m in r["mutants"]] == ["KILLED", "SURVIVED"]
    junits = [r["control_before"]["junit"], *(m["run"]["junit"] for m in r["mutants"]),
              r["control_after"]["junit"]]
    assert len(set(junits)) == 4 and all(Path(j).is_file() for j in junits)
    for m in r["mutants"]:
        assert m["md5_mutated"] != m["md5_pristine"] == m["md5_restored"]
        assert m["diff_names"] == [str(CALC_REL)]
    assert len(mr.fg.__file__) and Path(mr.fg.__file__).resolve() == (REPO / "tools" / "fg_common.py").resolve()


def test_every_mutant_killed_with_green_controls_exits_0(tmp_path, mut):
    tree = make_tree(tmp_path)
    code, out, _ = mut(tree, [K])
    assert code == 0, out
    assert out[-4:] == ["SURVIVORS: none", "ERRORS: none", "KILLED: 1/1", "porcelain_after="]


def test_m1_ids_selects_mutants_and_the_control_runs_their_tests_only(tmp_path, mut):
    tree = make_tree(tmp_path)
    s2 = dict(S, tests=["tests/test_calc.py::test_other"])
    code, out, r = mut(tree, [K, s2], "--ids", "S")
    assert code == 1, out
    assert [m["id"] for m in r["mutants"]] == ["S"]
    assert out[0] == "CONTROL-BEFORE rc=0 tests=1" and "KILLED: 0/1" in out


@pytest.mark.parametrize("argv", [["-h"], ["--help"], ["--tree", "x"]])
def test_m1_usage_errors_exit_2(tmp_path, mut, capsys, argv):
    code = mr.main(argv)
    out = capsys.readouterr().out.strip().splitlines()
    assert code == 2 and out[-1].startswith("MUTRUN ERROR usage:"), out


_BAD_SPECS = [
    ("not-json", "{nope", "spec"),
    ("empty", [], "non-empty"),
    ("missing-key", [{k: v for k, v in K.items() if k != "what"}], "keys must be exactly"),
    ("extra-key", [dict(K, extra=1)], "keys must be exactly"),
    ("duplicate-id", [K, dict(S, id="K")], "duplicate id"),
    ("bad-id", [dict(K, id="../x")], "is not"),
    ("tests-empty", [dict(K, tests=[])], "non-empty list"),
    ("old-empty", [dict(K, old="")], "non-empty"),
    ("option-smuggled", [dict(K, tests=["-o", "testpaths=/"])], "allow-list"),
    ("path-outside", [dict(K, tests=["/"])], "not an existing path inside"),
    ("unknown-ids", "IDS", "--ids not in the spec"),
]


@pytest.mark.parametrize("name,spec,expect", _BAD_SPECS, ids=[b[0] for b in _BAD_SPECS])
def test_m1_a_bad_spec_is_refused_before_any_run(tmp_path, mut, name, spec, expect):
    tree = make_tree(tmp_path)
    more = []
    if spec == "IDS":
        spec, more = [K], ["--ids", "nope"]
    if isinstance(spec, str):
        (tmp_path / "spec.json").write_text(spec)
        spec = None
    code, out, r = mut(tree, spec, *more)
    assert code == 2 and out[-1].startswith("MUTRUN ERROR") and expect in out[-1], out
    assert _junits(mut.rec) == [] and r["control_before"] is None
    assert (tree / CALC_REL).read_text() == CALC


def test_m1_a_record_inside_the_tree_is_refused(tmp_path, mut):
    tree = make_tree(tmp_path)
    code, out, _ = mut(tree, [K], record=tree / "rec" / "r.jsonl")
    assert code == 2 and "inside the tested tree" in out[-1]
    assert not (tree / "rec").exists()


# ---- M2: clean before, load guard; G4/G5 preflights ------------------------------------------------

@pytest.mark.parametrize("how", ["untracked", "unstaged", "staged"])
def test_m2_a_dirty_or_staged_tree_is_refused_before_any_run(tmp_path, mut, how):
    tree = make_tree(tmp_path)
    if how == "untracked":
        (tree / "new.txt").write_text("x\n")
    else:
        (tree / "README.md").write_text("changed\n")
        if how == "staged":
            _git(tree, "add", "README.md")
    code, out, r = mut(tree, [K])
    assert code == 2 and "tree not clean before" in out[-1], out
    assert _junits(mut.rec) == [] and r["control_before"] is None


def test_m2_a_loaded_box_is_refused_after_the_wait(tmp_path, mut, monkeypatch):
    tree = make_tree(tmp_path)
    monkeypatch.setattr(mr.fg, "_loadavg", lambda: 50.0)
    code, out, _ = mut(tree, [K], "--max-load", "1", "--wait", "0")
    assert code == 2 and "load 50.00 > 1.00" in out[-1], out
    assert _junits(mut.rec) == []


def test_g4_a_missing_tool_is_refused_before_any_run(tmp_path, mut):
    tree = make_tree(tmp_path)
    (tmp_path / "bin" / "timeout").unlink()  # the test's own bin dir, made by the fixture
    code, out, _ = mut(tree, [K])
    assert code == 2 and "timeout not found on gate PATH" in out[-1], out
    assert _junits(mut.rec) == []


@pytest.mark.parametrize("how", ["no-src", "src-links-out"])
def test_g5_forgeproof_not_from_the_tree_is_refused(tmp_path, mut, how):
    tree = make_tree(tmp_path)
    (tmp_path / "moved").mkdir()
    pkg = tree / "src" / "forgeproof"
    if how == "no-src":  # nothing to import: the preflight itself fails
        shutil.move(str(tree / "src"), str(tmp_path / "moved" / "src"))  # throw-away fixture tree
    else:                # the import succeeds, but from a package that resolves outside DIR/src
        shutil.move(str(pkg), str(tmp_path / "moved" / "forgeproof"))
        pkg.symlink_to(tmp_path / "moved" / "forgeproof")
    _git(tree, "add", "-A")
    _git(tree, "commit", "-q", "-m", how)
    code, out, _ = mut(tree, [dict(K, tests=["tests/test_calc.py"])])
    assert code == 2 and "import preflight" in out[-1], out
    if how == "src-links-out":
        assert "forgeproof imported from" in out[-1], out
    assert _junits(mut.rec) == []


# ---- M3: CONTROL-BEFORE ------------------------------------------------------------------------------

def test_m3_a_red_control_runs_no_mutant(tmp_path, mut):
    tree = make_tree(tmp_path, tests={"test_calc.py": TEST_CALC,
                                      "test_red.py": "def test_red():\n    assert 0\n"})
    code, out, r = mut(tree, [K, dict(S, tests=["tests/test_red.py"])])
    assert code == 2, out
    assert out[0] == "CONTROL-BEFORE RED rc=1 tests=2", out
    assert not any(ln.startswith(("K ", "S ")) for ln in out) and r["mutants"] == []
    assert len(_junits(mut.rec)) == 1


def test_m3_a_missing_node_id_is_rc4_and_never_a_kill(tmp_path, mut):
    tree = make_tree(tmp_path)
    code, out, r = mut(tree, [dict(K, tests=["tests/test_calc.py::no_such_test"])])
    assert code == 2, out
    assert out[0] == "CONTROL-BEFORE RED rc=4 tests=0", out
    assert out[1:] == ["why=" + r["control_before"]["last_line"], "porcelain_after="], out
    assert not any(ln.startswith("K ") for ln in out) and r["mutants"] == []


def test_m3_a_control_that_writes_the_tree_is_red(tmp_path, mut):
    body = ("from pathlib import Path\n\n\ndef test_w():\n"
            "    Path(__file__).resolve().parents[1].joinpath('README.md').write_text('x\\n')\n")
    tree = make_tree(tmp_path, tests={"test_calc.py": TEST_CALC, "test_w.py": body})
    code, out, r = mut(tree, [dict(K, tests=["tests/test_w.py"])])
    assert code == 2 and out[0] == "CONTROL-BEFORE RED rc=0 tests=1", out
    assert r["mutants"] == [] and r["control_before"]["why"].startswith("tree changed:")


# ---- M4: the mutant is inside the tree, applied once, and proven applied ----------------------------

@pytest.mark.parametrize("how", ["absolute", "dotdot", "dotdot-inside", "symlink-out"])
def test_m4_a_file_outside_the_tree_is_an_error_and_untouched(tmp_path, mut, how):
    outside = tmp_path / "outside.py"
    outside.write_text("return a + b\n")
    tree = make_tree(tmp_path)
    if how == "symlink-out":
        (tree / "link.py").symlink_to(outside)
        _git(tree, "add", "link.py")
        _git(tree, "commit", "-q", "-m", "link")
    f = {"absolute": str(outside), "dotdot": "../outside.py", "symlink-out": "link.py",
         "dotdot-inside": "tests/../src/forgeproof/calc.py"}[how]
    code, out, r = mut(tree, [dict(K, id="X", file=f)])
    assert code == 2, out
    assert out[1].startswith("X ERROR rc=none | kill | file "), out
    assert outside.read_text() == "return a + b\n" and (tree / CALC_REL).read_text() == CALC
    assert "ERRORS: X" in out


@pytest.mark.parametrize("old,n", [("not there", 0), ("b", 2)])
def test_m4_old_text_not_exactly_once_is_not_applied(tmp_path, mut, old, n):
    tree = make_tree(tmp_path)
    code, out, _ = mut(tree, [dict(K, old=old)])
    assert code == 2, out
    assert out[1] == f"K NOT-APPLIED rc=none | kill | NOT-APPLIED: old text found {n}x", out
    assert "ERRORS: K" in out and (tree / CALC_REL).read_text() == CALC


@pytest.mark.parametrize("mutant,why", [(S, "new text not in the file"),
                                        (dict(K, new="a + b"), "md5 unchanged")])
def test_m4_a_write_that_did_not_land_is_not_applied(tmp_path, mut, monkeypatch, mutant, why):
    tree = make_tree(tmp_path)
    pristine = (tree / CALC_REL).read_bytes()
    real = mr._write_bytes
    monkeypatch.setattr(mr, "_write_bytes", lambda p, d: real(p, d) if d == pristine else None)
    code, out, _ = mut(tree, [mutant])
    assert code == 2, out
    assert out[1].startswith(f"{mutant['id']} NOT-APPLIED rc=none") and why in out[1], out


def test_m4_a_file_git_does_not_track_is_not_applied(tmp_path, mut):
    tree = make_tree(tmp_path, tests={"test_calc.py": TEST_CALC,
                                      "test_ign.py": "from forgeproof.ign import f\n\n\ndef test_f():\n"
                                                     "    assert f() == 1\n"},
                     files={".gitignore": "src/forgeproof/ign.py\n"})
    (tree / "src" / "forgeproof" / "ign.py").write_text("def f():\n    return 1\n")  # ignored
    m = {"id": "I", "file": "src/forgeproof/ign.py", "old": "return 1", "new": "return 2",
         "tests": ["tests/test_ign.py"], "what": "ignored file"}
    code, out, _ = mut(tree, [m])
    assert code == 2, out
    assert out[1].startswith("I NOT-APPLIED rc=none") and "git diff names []" in out[1], out


# ---- M5: run and restore -----------------------------------------------------------------------------

SLOW_START = "import time\n\ntime.sleep(3.5)  # a slow start, as on a loaded host\n"


def test_m5_a_timeout_is_an_error_and_the_file_is_restored(tmp_path, mut):
    # --timeout bounds the controls too, so it must leave room for a slow start (S3 review F8):
    # with S3's `--timeout 3`, this fixture's CONTROL-BEFORE timed out and no mutant ran.
    tree = make_tree(tmp_path, tests={"test_calc.py": TEST_CALC, "conftest.py": SLOW_START})
    slow = dict(K, new="return __import__('time').sleep(60) or a + b")
    code, out, r = mut(tree, [slow], "--timeout", "10")
    assert code == 2, out
    assert out[0] == "CONTROL-BEFORE rc=0 tests=1", out
    assert out[1].startswith("K ERROR rc=none | kill | timeout after 10 s"), out
    assert (tree / CALC_REL).read_text() == CALC and _git(tree, "status", "--porcelain") == ""
    assert r["control_after"]["rc"] == 0


def test_m5_a_test_that_writes_the_tree_aborts_and_leaves_it(tmp_path, mut):
    body = ("from pathlib import Path\nfrom forgeproof.calc import add\n\n\ndef test_add():\n"
            "    r = add(2, 3)\n    if r != 5:\n"
            "        Path(__file__).resolve().parents[1].joinpath('README.md').write_text('dirtied\\n')\n"
            "    assert r == 5\n")
    tree = make_tree(tmp_path, tests={"test_calc.py": body})
    code, out, r = mut(tree, [K, S])
    assert code == 2, out
    assert out[1].startswith("K ABORT rc=1 | kill | tree not clean after restore:"), out
    assert out[2].startswith("CONTROL-AFTER NOT-RUN"), out
    assert out[3:6] == ["SURVIVORS: none", "ERRORS: K", "KILLED: 0/2"]
    assert out[6] == "porcelain_after= M README.md"
    assert (tree / "README.md").read_text() == "dirtied\n"  # left as it is, not restored
    assert [m["id"] for m in r["mutants"]] == ["K"]           # S never ran


def test_m5_a_restore_that_does_not_match_aborts(tmp_path, mut, monkeypatch):
    tree = make_tree(tmp_path)
    pristine = (tree / CALC_REL).read_bytes()
    real = mr._write_bytes
    monkeypatch.setattr(mr, "_write_bytes", lambda p, d: real(p, b"broken\n" if d == pristine else d))
    code, out, _ = mut(tree, [K, S])
    assert code == 2, out
    assert out[1].startswith("K ABORT rc=1 | kill | restore mismatch: md5"), out
    assert (tree / CALC_REL).read_text() == "broken\n"


# ---- M6: verdicts --------------------------------------------------------------------------------------

def _c(t, f=0, e=0, s=0):
    return {"tests": t, "failures": f, "errors": e, "skipped": s, "passed": t - f - e - s}


@pytest.mark.parametrize("rc,counts,error,want", [
    (1, _c(1, f=1), None, "KILLED"),
    (1, _c(0), None, "ERROR"),          # rc 1 with no test: e.g. "No module named pytest" (P8)
    (1, _c(1, e=1), None, "ERROR"),     # an error is not a failure
    (1, _c(1, f=1), "stale junit", "ERROR"),
    (1, None, "no junit file", "ERROR"),
    (0, _c(1), None, "SURVIVED"),
    (0, _c(0), None, "ERROR"),
    (0, _c(2, f=1), None, "ERROR"),
    (2, _c(1, e=1), None, "ERROR"),
    (2, _c(2, f=1), None, "ERROR"),     # V1: rc 2 with a failure in the junit is never a kill
    (0, _c(1, s=1), None, "ERROR"),     # F4: an all-skipped run proves nothing
    (0, _c(2, s=1), None, "SURVIVED"),  # one passed test is enough
    (3, _c(1), None, "ERROR"),
    (4, _c(0), None, "ERROR"),
    (5, _c(0), None, "ERROR"),
    (None, None, "timeout", "ERROR"),
])
def test_m6_verdict_table(rc, counts, error, want):
    assert mr.mutant_verdict(rc, counts, error) == want


@pytest.mark.parametrize("tests,rc", [(["tests/test_calc.py"], 2), (["tests/test_calc.py::test_add"], 4)])
def test_m6_a_collection_error_is_an_error_not_a_kill(tmp_path, mut, tests, rc):
    tree = make_tree(tmp_path)
    code, out, _ = mut(tree, [dict(K, new="return a +", tests=tests)])
    assert code == 2, out
    assert out[1].startswith(f"K ERROR rc={rc} | kill | "), out


def test_m6_a_junit_from_before_the_run_is_never_a_kill(tmp_path, mut, monkeypatch):
    tree = make_tree(tmp_path)
    monkeypatch.setattr(mr.fg, "_run_id", lambda: "fixed")
    mut.rec.parent.mkdir(parents=True)
    (mut.rec.parent / "junit-t-m-S-fixed.xml").write_text(
        '<testsuite tests="1" failures="1" errors="0" skipped="0"><testcase classname="x" name="y">'
        '<failure/></testcase></testsuite>')
    code, out, _ = mut(tree, [S])
    assert code == 2, out
    assert out[1].startswith("S ERROR rc=none | survive | junit ") and "exists before the run" in out[1]


# ---- M7: CONTROL-AFTER ---------------------------------------------------------------------------------

def test_m7_a_red_control_after_voids_every_verdict(tmp_path, mut, monkeypatch):
    tree = make_tree(tmp_path)
    real = mr.run_pytest

    def fake(ns, tr, rdir, label, tests):
        res = real(ns, tr, rdir, label, tests)
        if label == "control-after":
            res["rc"], res["counts"] = 1, dict(res["counts"], failures=1, passed=0)
        return res
    monkeypatch.setattr(mr, "run_pytest", fake)
    code, out, r = mut(tree, [K, S])
    assert code == 2, out
    assert out[1].startswith("K VOID rc=1") and out[2].startswith("S VOID rc=0"), out
    assert out[3] == "CONTROL-AFTER RED rc=1 tests=1"
    assert out[4] == "why=" + r["control_after"]["last_line"], out
    assert out[5:8] == ["SURVIVORS: none", "ERRORS: K S", "KILLED: 0/2"]
    assert [m["verdict_before_void"] for m in r["mutants"]] == ["KILLED", "SURVIVED"]


# ---- M9: the record goes down before the summary ---------------------------------------------------

def test_m9_no_summary_without_the_record(tmp_path, mut):
    tree = make_tree(tmp_path)
    rec_as_dir = tmp_path / "rec2" / "r.jsonl"
    rec_as_dir.mkdir(parents=True)  # os.open(..., O_WRONLY) on a directory fails
    code, out, _ = mut(tree, [K], record=rec_as_dir)
    assert code == 2 and out[-1].startswith("MUTRUN ERROR record write failed"), out
    assert not any(ln.startswith(("SURVIVORS", "KILLED", "K ")) for ln in out)


# ---- exit path (S3 review F2, F3): an interrupt restores, a crash is never exit 1 -------------

def _cli(tmp_path, tree, spec, tool=TOOL):
    (tmp_path / "spec.json").write_text(json.dumps(spec))
    env = dict(os.environ, **{mr.fg.PATH_KEY: str(_bin(tmp_path / "bin"))})
    return subprocess.Popen([sys.executable, str(tool), "--tree", str(tree), "--python", sys.executable,
                             "--spec", str(tmp_path / "spec.json"), "--record", str(tmp_path / "rec" / "r.jsonl"),
                             "--tag", "t", "--max-load", "1000", "--wait", "0"], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            preexec_fn=_default_signals)  # review S3e F1: whatever launched pytest


def _default_signals():
    for s in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        signal.signal(s, signal.SIG_DFL)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT], ids=["SIGTERM", "SIGINT"])
def test_an_interrupt_during_a_mutant_restores_records_and_kills_its_pytest(tmp_path, sig):
    tree = make_tree(tmp_path)
    pidf = tmp_path / "mutant-pytest.pid"  # the mutated code writes its own pytest's pid
    new = (f"return (open({str(pidf)!r}, 'w').write(str(__import__('os').getpid())), "
           "__import__('time').sleep(600)) and a + b")
    p = _cli(tmp_path, tree, [dict(K, new=new)])
    deadline = time.monotonic() + 60
    while not (pidf.is_file() and pidf.read_text()):  # the mutant is on disk and its test runs
        assert p.poll() is None and time.monotonic() < deadline, p.communicate()[0]
        time.sleep(0.1)
    pid = int(pidf.read_text())
    try:
        t0 = time.monotonic()
        p.send_signal(sig)
        out = p.communicate(timeout=45)[0].strip().splitlines()
        # r2 F3 / S3b-1 F1: the group kill is what ends the run this fast; without it the tool
        # waits out the mutant's sleep(600) (or leaves its pytest orphaned, checked below).
        assert time.monotonic() - t0 < 20, "the interrupted run did not end promptly"
        assert p.returncode == 2, out
        assert out[-1].startswith(f"MUTRUN ERROR interrupted (signal {int(sig)}): the tree is clean"), out
        # r2 F3: the mutant's pytest group was killed with the run, not orphaned.
        assert not _alive(pid), "the mutant's pytest outlived the interrupt"
        assert (tree / CALC_REL).read_text() == CALC and _git(tree, "status", "--porcelain") == ""
        # r2 F1: the interrupted mutant is in the record, with its restore checked.
        r = json.loads((tmp_path / "rec" / "r.jsonl").read_text().splitlines()[-1])
        assert r["exit"] == 2 and r["porcelain_after"] == ""
        [k] = r["mutants"]
        assert k["id"] == "K" and k["verdict"] == "ERROR" and k["reason"].startswith("interrupted"), k
        assert k["md5_restored"] == k["md5_pristine"] != k["md5_mutated"]
    finally:
        if _alive(pid):
            os.killpg(pid, signal.SIGKILL)
        if p.poll() is None:
            p.kill()
            p.wait()


def test_r2f1_an_interrupt_with_a_failed_restore_is_recorded_and_reported(tmp_path, mut, monkeypatch):
    tree = make_tree(tmp_path)
    pristine = (tree / CALC_REL).read_bytes()
    real_write, real_run = mr._write_bytes, mr.run_pytest

    def write(p, d):
        if d == pristine:
            raise OSError("injected: the restore write fails")
        real_write(p, d)

    def run(ns, tr, rdir, label, tests):
        if label.startswith("m-"):
            raise mr._Interrupted("signal 15")  # the signal lands while the mutant runs
        return real_run(ns, tr, rdir, label, tests)
    monkeypatch.setattr(mr, "_write_bytes", write)
    monkeypatch.setattr(mr, "run_pytest", run)
    code, out, r = mut(tree, [K])
    assert code == 2, out
    assert "the tree is LEFT AS IT IS:" in out[-1] and "src/forgeproof/calc.py" in out[-1], out
    assert "clean" not in out[-1], out
    [k] = r["mutants"]
    assert k["id"] == "K" and k["verdict"] == "ABORT" and "restore write failed" in k["reason"], k
    assert r["porcelain_after"].strip() == "M src/forgeproof/calc.py"
    assert "return a - b" in (tree / CALC_REL).read_text()  # truly left mutated, as reported


def test_r2f2_the_first_signal_ignores_all_three_until_the_run_ends():
    saved = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT)}
    try:
        with pytest.raises(mr._Interrupted):
            mr._on_signal(signal.SIGTERM, None)
        assert {s: signal.getsignal(s) for s in saved} == {s: signal.SIG_IGN for s in saved}
    finally:
        for s, h in saved.items():
            signal.signal(s, h)


def test_r2f4_a_red_control_after_prints_its_reason(tmp_path, mut, monkeypatch):
    tree = make_tree(tmp_path)
    real = mr.run_pytest

    def fake(ns, tr, rdir, label, tests):
        res = real(ns, tr, rdir, label, tests)
        if label == "control-after":
            res.update(rc=None, counts=None, error="timeout after 9 s (process group killed)")
        return res
    monkeypatch.setattr(mr, "run_pytest", fake)
    code, out, _ = mut(tree, [K])
    assert code == 2, out
    i = out.index("CONTROL-AFTER RED rc=none tests=none")
    assert out[i + 1] == "why=timeout after 9 s (process group killed)", out


# ---- S3b: parent verify V1, review F4/F6/F7 --------------------------------------------------

TEST_RC2 = ("import pytest\nfrom forgeproof.calc import add\n\n\ndef test_a():\n    assert add(2, 3) == 5\n\n\n"
            "def test_b():\n    if add(2, 3) != 5:\n        pytest.exit('mutant seen', returncode=2)\n")


def test_v1_rc2_with_a_failure_in_its_own_junit_is_an_error_never_a_kill(tmp_path, mut):
    """Parent verify V1: a real pytest run that exits 2 AFTER writing a failure into its own
    junit must read ERROR (mutant K1, `rc == 1` -> `rc in (1, 2)`, survived S3's suite)."""
    tree = make_tree(tmp_path, tests={"test_calc.py": TEST_RC2})
    code, out, r = mut(tree, [dict(K, tests=["tests/test_calc.py"])])
    run = r["mutants"][0]["run"]
    assert run["rc"] == 2 and run["counts"]["failures"] >= 1, run  # the case is real
    assert code == 2 and out[1].startswith("K ERROR rc=2 | kill | "), out
    assert r["mutants"][0]["verdict"] == "ERROR" and "ERRORS: K" in out


def test_f4_an_all_skipped_control_is_red_and_runs_no_mutant(tmp_path, mut):
    skip = "import pytest\n\n\n" "@pytest.mark.skip(reason='nothing runs')\ndef test_s():\n    pass\n"
    tree = make_tree(tmp_path, tests={"test_calc.py": TEST_CALC, "test_skip.py": skip})
    code, out, r = mut(tree, [dict(K, tests=["tests/test_skip.py"])])
    assert code == 2 and out[0] == "CONTROL-BEFORE RED rc=0 tests=1", out
    assert r["mutants"] == [] and not any(ln.startswith("K ") for ln in out)


def test_f6_the_helper_pycache_dirs_are_fresh_for_every_run(tmp_path, mut, monkeypatch):
    tree = make_tree(tmp_path)
    used, real = [], mr.fg.build_env
    monkeypatch.setattr(mr.fg, "build_env", lambda env, tr, pyc: used.append(str(pyc)) or real(env, tr, pyc))
    _, _, r1 = mut(tree, [K])
    _, _, r2 = mut(tree, [K])  # same tag, same record dir
    pre = sorted(p.name for p in mut.rec.parent.glob("pyc-t-preflight*"))
    assert len(pre) == 2, pre
    assert r1["pycache_preflight"] != r2["pycache_preflight"]
    assert r1["pycache_git"] != r2["pycache_git"]
    for r in (r1, r2):  # the recorded dirs are the ones the git and preflight envs used
        assert r["pycache_git"] in used and r["pycache_preflight"] in used, (r, used)


def test_f7_a_tree_below_a_worktree_top_level_is_refused(tmp_path, mut):
    tree = make_tree(tmp_path)
    code, out, r = mut(tree / "src", [K])
    assert code == 2 and "is not a worktree's top level" in out[-1], out
    assert _junits(mut.rec) == [] and r["control_before"] is None


def test_a_tool_that_cannot_load_its_gate_exits_2_not_1(tmp_path):
    lone = tmp_path / "lone" / "tools"
    lone.mkdir(parents=True)
    shutil.copy2(TOOL, lone / "fg_mutrun.py")  # no fg_common.py beside it (S3c)
    tree = make_tree(tmp_path)
    p = _cli(tmp_path, tree, [K], tool=lone / "fg_mutrun.py")
    out = p.communicate(timeout=60)[0].strip().splitlines()
    assert p.returncode == 2 and out[-1].startswith("MUTRUN ERROR cannot load fg_common.py"), out


def test_s3b_f3_a_signal_during_the_restore_waits_until_it_is_done(tmp_path, mut, monkeypatch):
    tree = make_tree(tmp_path)
    pristine = (tree / CALC_REL).read_bytes()
    real = mr._write_bytes

    def write(p, d):
        if d == pristine:  # the restore: a SIGTERM lands right before its write
            os.kill(os.getpid(), signal.SIGTERM)
        real(p, d)
    monkeypatch.setattr(mr, "_write_bytes", write)
    code, out, r = mut(tree, [K, S])
    assert code == 2, out
    assert out[-1].startswith("MUTRUN ERROR interrupted (signal 15): the tree is clean"), out
    assert (tree / CALC_REL).read_text() == CALC
    [k] = r["mutants"]  # S never ran
    assert k["md5_restored"] == k["md5_pristine"] and k["porcelain_after_restore"] == ""


def test_s3b_f4_an_unexpected_exception_is_a_fault_not_an_interrupt(tmp_path, mut, monkeypatch):
    tree = make_tree(tmp_path)
    real = mr.run_pytest

    def run(ns, tr, rdir, label, tests):
        if label.startswith("m-"):
            raise ValueError("boom")
        return real(ns, tr, rdir, label, tests)
    monkeypatch.setattr(mr, "run_pytest", run)
    code, out, r = mut(tree, [K])
    assert code == 2 and out[-1] == "MUTRUN ERROR fault: ValueError: boom", out
    [k] = r["mutants"]
    assert k["reason"] == "fault: ValueError: boom", k
    assert (tree / CALC_REL).read_text() == CALC


# ---- S3c: N1 (a first signal on the step into the restore) and N2 (an unreadable tree state) --

@pytest.mark.parametrize("when", ["first-block", "block-while-mutated"])
def test_s3c_n1_a_handler_run_inside_pthread_sigmask_still_restores_and_unblocks(tmp_path, mut,
                                                                                   monkeypatch, when):
    """CPython runs a pending handler inside pthread_sigmask AFTER it changed the mask (S3c probe
    A, both hosts): the call raises with the signals blocked and its return value lost. Simulated
    exactly: the real call, then the tool's own handler, as PyErr_CheckSignals runs it."""
    tree = make_tree(tmp_path)
    pristine = (tree / CALC_REL).read_bytes()
    real = signal.pthread_sigmask
    start = real(signal.SIG_BLOCK, [])
    fired = []

    def sigmask(how, mask):
        prev = real(how, mask)
        hit = how == signal.SIG_BLOCK and set(mask) == set(mr._SIGNALS) and not fired
        if hit and (when == "first-block" or (tree / CALC_REL).read_bytes() != pristine):
            fired.append(how)
            mr._on_signal(int(signal.SIGTERM), None)
        return prev
    monkeypatch.setattr(signal, "pthread_sigmask", sigmask)
    try:
        code, out, r = mut(tree, [K, S])
        after = real(signal.SIG_BLOCK, [])
    finally:
        real(signal.SIG_SETMASK, start)
    assert fired, "the simulated signal never landed"
    assert code == 2, out
    assert out[-1].startswith("MUTRUN ERROR interrupted (signal 15): the tree is clean"), out
    assert (tree / CALC_REL).read_bytes() == pristine
    [k] = r["mutants"]  # S never ran
    assert k["reason"].startswith("interrupted") and k["md5_restored"] == k["md5_pristine"], k
    assert after == start, f"signals left blocked: {sorted(int(s) for s in set(after) - set(start))}"



def test_s3c_n1_the_signals_are_blocked_in_a_mutant_except_while_its_pytest_runs(tmp_path, mut,
                                                                                 monkeypatch):
    """N1: the step from the mutant's try body into its restore must never be unblocked, so the
    signals are blocked from before the write to the end of the restore, and only the pytest run
    (which an interrupt must reach, to kill its group) runs under the caller's own mask."""
    tree = make_tree(tmp_path)
    pristine = (tree / CALC_REL).read_bytes()
    seen = []

    def blocked():
        return set(mr._SIGNALS) <= set(signal.pthread_sigmask(signal.SIG_BLOCK, []))
    real_write, real_run, real_git = mr._write_bytes, mr.run_pytest, mr.fg._git
    real_verdict = mr.mutant_verdict

    def write(p, d):
        seen.append(("restore" if d == pristine else "mutant-write", blocked()))
        real_write(p, d)

    def run(ns, tr, rdir, label, tests):
        if label.startswith("m-"):
            seen.append(("mutant-run", blocked()))
        return real_run(ns, tr, rdir, label, tests)

    def git(env, tr, *args):
        if args[:1] == ("diff",):
            seen.append(("m4-diff", blocked()))
        return real_git(env, tr, *args)
    monkeypatch.setattr(mr, "_write_bytes", write)
    monkeypatch.setattr(mr, "run_pytest", run)

    def verdict(rc, counts, error):  # runs right after the run returns (S3c-1 review F1)
        seen.append(("after-run", blocked()))
        return real_verdict(rc, counts, error)
    monkeypatch.setattr(mr.fg, "_git", git)
    monkeypatch.setattr(mr, "mutant_verdict", verdict)
    start = signal.pthread_sigmask(signal.SIG_BLOCK, [])
    code, out, r = mut(tree, [K])
    assert code == 0, out
    assert seen == [("mutant-write", True), ("m4-diff", True), ("mutant-run", False),
                    ("after-run", True), ("restore", True)], seen
    assert signal.pthread_sigmask(signal.SIG_BLOCK, []) == start

def test_s3c_n2_an_unreadable_tree_state_after_an_interrupt_is_reported_as_unknown(tmp_path, mut,
                                                                                    monkeypatch):
    tree = make_tree(tmp_path)
    real_run, real_porcelain = mr.run_pytest, mr.fg._porcelain
    state = {"interrupted": False, "calls": 0}

    def run(ns, tr, rdir, label, tests):
        if label.startswith("m-"):
            state["interrupted"] = True
            raise mr._Interrupted("signal 15")
        return real_run(ns, tr, rdir, label, tests)

    def porcelain(env, tr):
        if state["interrupted"]:
            state["calls"] += 1
            if state["calls"] == 2:  # 1: the restore's own check; 2: main's report of the tree
                raise mr.MutrunError("git status --porcelain failed rc=128: injected")
        return real_porcelain(env, tr)
    monkeypatch.setattr(mr, "run_pytest", run)
    monkeypatch.setattr(mr.fg, "_porcelain", porcelain)
    code, out, r = mut(tree, [K])
    assert code == 2 and state["calls"] == 2, (out, state)
    assert "LEFT AS IT IS" not in out[-1] and "clean" not in out[-1], out
    assert out[-1].startswith("MUTRUN ERROR interrupted (signal 15): the tree state is UNKNOWN"), out
    assert "injected" in out[-1] and "check the tree by hand" in out[-1], out
    assert r["porcelain_after"] is None and "injected" in r["porcelain_error"], r
    assert (tree / CALC_REL).read_text() == CALC  # the restore itself ran and was checked
    [k] = r["mutants"]
    assert k["md5_restored"] == k["md5_pristine"] and k["porcelain_after_restore"] == ""


# ---- S3d: a failed restore check never replaces the interrupt; a hung git is bounded ----------

@pytest.mark.parametrize("fail", ["status", "status-and-read-back"])
def test_s3d_an_interrupt_stays_the_outcome_when_the_restore_check_fails(tmp_path, mut, monkeypatch,
                                                                         fail):
    tree = make_tree(tmp_path)
    real_run, real_porcelain, real_read = mr.run_pytest, mr.fg._porcelain, getattr(mr, "_read_back", None)
    state = {"interrupted": False, "calls": 0}

    def run(ns, tr, rdir, label, tests):
        if label.startswith("m-"):
            state["interrupted"] = True
            raise mr._Interrupted("signal 15")
        return real_run(ns, tr, rdir, label, tests)

    def porcelain(env, tr):
        if state["interrupted"]:
            state["calls"] += 1
            if state["calls"] == 1:  # the restore's own check; main's report (call 2) reads fine
                raise mr.MutrunError("git status --porcelain failed rc=128: injected")
        return real_porcelain(env, tr)

    def read_back(p):
        raise OSError("injected: read-back fails")
    monkeypatch.setattr(mr, "run_pytest", run)
    monkeypatch.setattr(mr.fg, "_porcelain", porcelain)
    if fail == "status-and-read-back":
        monkeypatch.setattr(mr, "_read_back", read_back, raising=False)
    code, out, r = mut(tree, [K])
    assert code == 2, out
    assert out[-1].startswith("MUTRUN ERROR interrupted (signal 15): the tree is clean"), out
    assert (tree / CALC_REL).read_text() == CALC
    [k] = r["mutants"]
    assert k["interrupted"] == "signal 15" and k["reason"].startswith("interrupted: signal 15"), k
    assert k["verdict"] == "ABORT" and "injected" in k["porcelain_error"], k
    assert k["porcelain_after_restore"] is None, k
    if fail == "status":
        assert k["md5_restored"] == k["md5_pristine"], k
    else:
        assert k["md5_restored"] is None and "read-back fails" in k["md5_restored_error"], k


def test_s3d_a_hung_git_diff_with_the_signals_blocked_times_out_loudly(tmp_path, mut, monkeypatch):
    tree = make_tree(tmp_path)
    b = tmp_path / "hungbin"
    b.mkdir()
    pidf = tmp_path / "git-diff.pid"
    (b / "git").write_text(  # M4's diff hangs (one process, so a kill ends it); all else is git
        "#!/bin/sh\ncase \" $* \" in\n  *' diff --name-only '*)\n"
        f"    echo $$ > '{pidf}'\n    exec '{sys.executable}' -c 'import time; time.sleep(15)';;\n"
        f"esac\nexec '{GIT}' \"$@\"\n")
    (b / "git").chmod(0o755)
    (b / "timeout").symlink_to(shutil.which("timeout") or GIT)
    monkeypatch.setenv(mr.fg.PATH_KEY, str(b))
    monkeypatch.setattr(mr.fg, "GIT_TIMEOUT_S", 3.0, raising=False)
    start = signal.pthread_sigmask(signal.SIG_BLOCK, [])
    t0 = time.monotonic()
    code, out, r = mut(tree, [K])
    assert time.monotonic() - t0 < 12, "the hung git diff was waited out, not ended"
    assert code == 2, out
    [k] = r["mutants"]
    assert k["verdict"] == "ERROR" and "git diff --name-only TIMED OUT after 3 s" in k["reason"], k
    assert any(ln.startswith("K ERROR") and "TIMED OUT" in ln for ln in out), out
    assert "ERRORS: K" in out, out
    pid = int(pidf.read_text())
    try:
        os.kill(pid, 0)
        alive = True
    except ProcessLookupError:
        alive = False
    assert not alive, "the hung git outlived its timeout"
    assert (tree / CALC_REL).read_text() == CALC and k["md5_restored"] == k["md5_pristine"], k
    assert signal.pthread_sigmask(signal.SIG_BLOCK, []) == start


def test_s3d_f1_a_signal_delivered_at_the_restores_unmask_is_still_the_outcome(tmp_path, mut, monkeypatch):
    """Review S3d-1 F1: a signal that lands while the three are blocked, after the run (here: in
    the restore write), is delivered by the restore's own unmask; the mutant's record must say so."""
    tree = make_tree(tmp_path)
    pristine = (tree / CALC_REL).read_bytes()
    real = mr._write_bytes

    def write(p, d):
        if d == pristine:
            os.kill(os.getpid(), signal.SIGTERM)  # blocked here: pending until the unmask
        real(p, d)
    monkeypatch.setattr(mr, "_write_bytes", write)
    code, out, r = mut(tree, [K])
    assert code == 2 and out[-1].startswith("MUTRUN ERROR interrupted (signal 15): the tree is clean"), out
    [k] = r["mutants"]
    assert k.get("interrupted") == "signal 15", k
    assert k["verdict"] == "ERROR" and k["reason"] == "interrupted: signal 15", k
    assert k["md5_restored"] == k["md5_pristine"] and k["porcelain_after_restore"] == "", k


# ---- S3e: the S3d leftovers, mutrun side (F4, F5, F6) -------------------------------------------

def test_s3e_f4_a_signal_inside_the_mutants_popen_still_kills_its_pytest(tmp_path, mut, monkeypatch):
    """F4 (same window as the gate's): a signal inside Popen, before run_pytest holds ``proc``,
    must still kill the mutant's pytest group; the mutant is restored and the run interrupted."""
    tree = make_tree(tmp_path)
    real, held = subprocess.Popen, []

    def popen(*a, **k):
        p = real(*a, **k)
        if "pytest" in list(a[0])[1:3] and any("-m-K-" in str(x) for x in a[0]) and not held:
            held.append(p)
            os.kill(os.getpid(), signal.SIGTERM)  # in the window: Popen has not returned yet
            time.sleep(0.05)
        return p
    old = signal.signal(signal.SIGTERM, signal.SIG_DFL)
    monkeypatch.setattr(mr.subprocess, "Popen", popen)
    try:
        code, out, r = mut(tree, [dict(K, new="return __import__('time').sleep(600) or a + b")],
                           "--timeout", "40")  # a lost signal ends as a timeout, not a hang
        assert held, "the mutant's Popen never ran"
        assert code == 2, out
        assert out[-1].startswith(f"MUTRUN ERROR interrupted (signal {int(signal.SIGTERM)}): "
                                  "the tree is clean"), out
        assert held[0].poll() is not None, "the mutant's pytest outlived a signal that landed in Popen"
        [k] = r["mutants"]
        assert k["verdict"] == "ERROR" and k["interrupted"] == f"signal {int(signal.SIGTERM)}", k
        assert (tree / CALC_REL).read_text() == CALC
    finally:
        signal.signal(signal.SIGTERM, old)
        for p in held:
            if p.poll() is None:
                os.killpg(p.pid, signal.SIGKILL)
                p.wait()


@pytest.mark.parametrize("start", ["inherited-ignored", "default"])
def test_s3e_f5_an_inherited_ignore_is_left_alone(tmp_path, mut, monkeypatch, start):
    """F5: a SIGHUP ignored when the tool starts (nohup) stays ignored for the whole run; from the
    default disposition the tool's own handler is installed. Both are put back after the run."""
    tree = make_tree(tmp_path)
    real, seen = mr.run_pytest, []

    def run(*a):
        seen.append((signal.getsignal(signal.SIGHUP), signal.getsignal(signal.SIGTERM)))
        return real(*a)
    disp = signal.SIG_IGN if start == "inherited-ignored" else signal.SIG_DFL
    old = {s: signal.signal(s, disp if s == signal.SIGHUP else signal.SIG_DFL)
           for s in (signal.SIGHUP, signal.SIGTERM)}
    monkeypatch.setattr(mr, "run_pytest", run)
    seen_by_suite = tmp_path / "suite-sighup.txt"  # review F1: what the mutant's pytest inherited
    new = (f"return (open({str(seen_by_suite)!r}, 'w').write(str(__import__('signal').getsignal("
           "__import__('signal').SIGHUP) == __import__('signal').SIG_IGN)), a - b)[1]")
    try:
        code, out, r = mut(tree, [dict(K, new=new)])
        after = (signal.getsignal(signal.SIGHUP), signal.getsignal(signal.SIGTERM))
    finally:
        for s, h in old.items():
            signal.signal(s, h)
    assert code == 0 and seen, out
    want_hup = signal.SIG_IGN if start == "inherited-ignored" else mr._on_signal
    assert all(h == want_hup and t == mr._on_signal for h, t in seen), seen
    assert after == (disp, signal.SIG_DFL), after
    assert seen_by_suite.read_text() == "False", "the mutant's pytest inherited an ignored SIGHUP"


def test_s3e_f6_a_bare_interrupt_is_named_by_its_type(tmp_path, mut, monkeypatch):
    """F6: ``e or type(e).__name__`` was dead (an exception is always truthy), so a bare
    KeyboardInterrupt read 'interrupted: ' and 'interrupted ()'."""
    tree = make_tree(tmp_path)
    real = mr.run_pytest

    def run(ns, tr, rdir, label, tests):
        if label.startswith("m-"):
            raise KeyboardInterrupt()
        return real(ns, tr, rdir, label, tests)
    monkeypatch.setattr(mr, "run_pytest", run)
    code, out, r = mut(tree, [K])
    assert code == 2 and out[-1].startswith("MUTRUN ERROR interrupted (KeyboardInterrupt): "
                                            "the tree is clean"), out
    [k] = r["mutants"]
    assert k["reason"] == "interrupted: KeyboardInterrupt" and k["interrupted"] == "KeyboardInterrupt", k
