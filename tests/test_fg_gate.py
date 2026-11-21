"""tools/fg_gate.py, the gate (shared-tools loop, slice S2, design doc G0-G12).

Every check has a test here that goes red when the check is removed. The gate runs on small
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
TOOL = REPO / "tools" / "fg_gate.py"
GIT = shutil.which("git")


def _load():
    spec = importlib.util.spec_from_file_location("fg_gate_under_test", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


fg = _load()


# ---- fixtures and helpers ------------------------------------------------------------------

def _bin(d: Path, timeout=True, git=True) -> Path:
    """A suite PATH dir. `timeout` only has to RESOLVE for the gate (G4); the throw-away trees
    never call it, so any executable stands in when the host keeps GNU timeout elsewhere."""
    d.mkdir(parents=True, exist_ok=True)
    if GIT is None:
        pytest.fail("git not on PATH: the gate tests need git")
    if git:
        (d / "git").symlink_to(GIT)
    if timeout:
        (d / "timeout").symlink_to(shutil.which("timeout") or GIT)
    return d


def _git(tree, *args):
    p = subprocess.run([GIT, "-C", str(tree), "-c", "user.name=fg", "-c", "user.email=fg@example.com",
                        "-c", "commit.gpgsign=false", *args], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    return p.stdout


def make_tree(base: Path, tests: dict, files: dict | None = None) -> Path:
    t = base / "tree"
    (t / "src" / "forge_gate").mkdir(parents=True)
    (t / "tests").mkdir()
    (t / "src" / "forge_gate" / "__init__.py").write_text("")
    (t / "pytest.ini").write_text("[pytest]\ntestpaths = tests\n")
    (t / "README.md").write_text("readme\n")
    for name, body in tests.items():
        (t / "tests" / name).write_text(body)
    for rel, body in (files or {}).items():
        (t / rel).parent.mkdir(parents=True, exist_ok=True)
        (t / rel).write_text(body)
    _git(t, "init", "-q")
    _git(t, "add", "-A")
    _git(t, "commit", "-q", "-m", "fixture")
    return t


PASS2_SKIP1 = {"test_a.py": "import pytest\n\ndef test_one():\n    assert 1\n\n"
               "@pytest.mark.parametrize('n', [1, 2])\ndef test_p(n):\n    assert n\n\n"
               "def test_s():\n    pytest.skip('by design')\n"}


@pytest.fixture
def gate(tmp_path, monkeypatch, capsys):
    """Run fg_gate.main in-process on a tree; returns (exit, last line, last record or None)."""
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setenv(fg.C.PATH_KEY, str(_bin(tmp_path / "bin")))
    monkeypatch.setattr(fg.C, "_loadavg", lambda: 0.0)
    rec = tmp_path / "rec" / "r.jsonl"

    def run(tree, *more, record=None):
        r = record or rec
        code = fg.main(["--tree", str(tree), "--python", sys.executable, "--record", str(r),
                        "--tag", "t", "--max-load", "1000", "--wait", "0", *more])
        run.out = capsys.readouterr().out
        out = run.out.strip().splitlines()
        rows = Path(r).read_text().splitlines() if Path(r).is_file() else []
        return code, (out[-1] if out else ""), (json.loads(rows[-1]) if rows else None)
    run.rec, run.out = rec, ""
    return run


def _junits(rec: Path):
    return sorted(rec.parent.glob("junit-*.xml")) if rec.parent.is_dir() else []


# ---- G1/G8/G12: a green run and its record ---------------------------------------------------

def test_green_run_prints_the_verdict_line_and_records_every_field(tmp_path, gate):
    tree = make_tree(tmp_path, PASS2_SKIP1)
    wt = _git(tree, "write-tree").strip()
    code, line, r = gate(tree, "--must-run", "tests/test_a.py::test_p", "tests/test_a.py::test_one")
    assert code == 0, line
    assert line == f"GATE GREEN rc=0 passed=3 failed=0 errors=0 skipped=1 tree={wt}"
    assert r["tree_before"] == r["tree_after"] == wt
    assert r["porcelain_before"] == r["porcelain_after"] == ""
    assert (r["tests"], r["failures"], r["errors"], r["skipped"], r["passed"]) == (4, 0, 0, 1, 3)
    assert r["must_run"] == {"tests/test_a.py::test_p": "passed", "tests/test_a.py::test_one": "passed"}
    assert r["tool_md5"] == hashlib.md5(TOOL.read_bytes()).hexdigest()
    head = subprocess.run([GIT, "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True, text=True)
    assert r["tool_head"] == (head.stdout.strip() if head.returncode == 0 else None)
    assert Path(r["paths_file"]).resolve() == (REPO / "src" / "forge_gate" / "paths.py").resolve()
    assert Path(r["forge_gate_file"]).resolve().parent == (tree / "src" / "forge_gate").resolve()
    assert r["pytest_timeout_loaded"] is (importlib.util.find_spec("pytest_timeout") is not None)
    assert r["load_before"] == r["load_after"] == 0.0 and r["max_load"] == 1000.0
    m = os.umask(0)
    os.umask(m)
    assert r["umask"] == f"{m:03o}"
    assert Path(r["junit"]).is_file() and Path(r["junit"]).parent == gate.rec.parent.resolve()
    for k in ("host", "python", "python_version", "pytest_version", "git_version", "umask", "PATH",
              "argv", "head", "staged", "rc", "last_line", "load_before", "load_after", "max_load",
              "waited_s", "duration_s", "verdict", "reason", "utc"):
        assert k in r, k
    assert r["verdict"] == "GREEN" and r["rc"] == 0 and r["staged"] == []
    assert len(gate.rec.read_text().splitlines()) == 1
    code2, line2, r2 = gate(tree)                       # O_APPEND: a second run adds a line
    assert code2 == 0 and len(gate.rec.read_text().splitlines()) == 2 and r2["run_id"] != r["run_id"]


# ---- G0: the tool's own forge_gate.paths --------------------------------------------------------

def test_g0_a_tool_that_would_load_paths_from_elsewhere_refuses(tmp_path):
    tree = make_tree(tmp_path, PASS2_SKIP1)
    fake = tmp_path / "copy"
    (fake / "tools").mkdir(parents=True)
    (fake / "src").mkdir()           # this copy's own src has no forge_gate ...
    shutil.copy(TOOL, fake / "tools" / "fg_gate.py")
    shutil.copy(TOOL.parent / "fg_common.py", fake / "tools" / "fg_common.py")  # S3c: a whole tool
    env = dict(os.environ, PYTHONPATH=str(REPO / "src"),   # ... but another one is importable
               **{fg.C.PATH_KEY: str(_bin(tmp_path / "bin"))})
    rec = tmp_path / "rec" / "r.jsonl"
    p = subprocess.run([sys.executable, str(fake / "tools" / "fg_gate.py"), "--tree", str(tree),
                        "--python", sys.executable, "--record", str(rec), "--tag", "g0",
                        "--max-load", "1000", "--wait", "0"], env=env, capture_output=True, text=True)
    last = p.stdout.strip().splitlines()[-1]
    assert p.returncode == 2, p.stdout + p.stderr
    assert last.startswith("GATE ERROR rc=none") and "G0:" in last, last
    assert not rec.exists()


# ---- G2: clean before -------------------------------------------------------------------------

@pytest.mark.parametrize("how", ["untracked", "unstaged", "staged-then-edited"])
def test_g2_a_dirty_tree_is_refused_before_pytest(tmp_path, gate, how):
    tree = make_tree(tmp_path, PASS2_SKIP1)
    if how == "untracked":
        (tree / "stray.txt").write_text("x\n")
    elif how == "unstaged":
        (tree / "README.md").write_text("changed\n")
    else:                                     # "MM": the index is not what pytest would run
        (tree / "README.md").write_text("staged\n")
        _git(tree, "add", "README.md")
        (tree / "README.md").write_text("edited after staging\n")
        assert _git(tree, "status", "--porcelain") == "MM README.md\n"
    code, line, r = gate(tree)
    assert code == 2 and line.startswith("GATE ERROR rc=none") and "tree not clean before" in line, line
    assert _junits(gate.rec) == [] and r["rc"] is None


def test_g2_staged_entries_are_allowed_and_recorded(tmp_path, gate):
    tree = make_tree(tmp_path, PASS2_SKIP1)
    (tree / "tests" / "test_b.py").write_text("def test_b():\n    assert 1\n")
    _git(tree, "add", "tests/test_b.py")
    code, line, r = gate(tree)
    assert code == 0 and "passed=4" in line, line
    assert r["staged"] == ["A  tests/test_b.py"] and r["porcelain_after"] == "A  tests/test_b.py\n"


# ---- G3: env built from nothing -----------------------------------------------------------------

def test_g3_the_suite_sees_only_the_gate_env(tmp_path, gate, monkeypatch):
    monkeypatch.setenv("FG_GATE_LEAK_PROBE", "1")
    monkeypatch.setenv("PYTHONPATH", "/nonexistent-leak")
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    monkeypatch.delenv("LC_ALL", raising=False)
    base = tmp_path / "t"
    base.mkdir()
    tree_txt = str((base / "tree").resolve())
    body = ("import os\n\ndef test_env():\n    e = os.environ\n"
            "    assert 'FG_GATE_LEAK_PROBE' not in e\n"
            f"    assert e['PYTHONPATH'] == {tree_txt + '/src'!r}\n"
            f"    assert e['FORGE_GATE_HOME'] == {tree_txt!r}\n"
            f"    assert e['PATH'] == {str(tmp_path / 'bin')!r}\n"
            f"    assert e['PYTHONPYCACHEPREFIX'].startswith({str((tmp_path / 'rec').resolve())!r})\n"
            "    assert e['LANG'] == 'en_US.UTF-8' and 'LC_ALL' not in e\n")
    tree = make_tree(base, {"test_env.py": body})
    code, line, r = gate(tree)
    assert code == 0, line
    assert r["PATH"] == str(tmp_path / "bin")


def test_g3_default_path_adds_homebrew_dirs_only_if_they_exist(tmp_path):
    home = tmp_path / "home"
    (home / "homebrew" / "bin").mkdir(parents=True)
    got = fg.C.gate_path({"HOME": str(home)}).split(":")
    assert got[:5] == ["/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"]
    assert str(home / "homebrew" / "bin") in got
    assert ("/opt/homebrew/bin" in got) == os.path.isdir("/opt/homebrew/bin")
    assert str(tmp_path / "nohome" / "homebrew" / "bin") not in fg.C.gate_path({"HOME": str(tmp_path / "nohome")})
    assert fg.C.gate_path({"HOME": str(home), fg.C.PATH_KEY: "/a:/b"}) == "/a:/b"


# ---- G4: tools on the gate PATH ----------------------------------------------------------------

@pytest.mark.parametrize("missing", ["timeout", "git"])
def test_g4_a_missing_tool_is_refused_before_pytest(tmp_path, gate, monkeypatch, missing):
    tree = make_tree(tmp_path, PASS2_SKIP1)
    b = _bin(tmp_path / "bin2", timeout=missing != "timeout", git=missing != "git")
    monkeypatch.setenv(fg.C.PATH_KEY, str(b))
    code, line, r = gate(tree)
    assert code == 2 and line.startswith("GATE ERROR rc=none"), line
    assert line.endswith(f"{missing} not found on gate PATH"), line
    assert _junits(gate.rec) == []


# ---- G5: import preflight ------------------------------------------------------------------------

def test_g5_forge_gate_resolving_outside_the_tree_is_refused(tmp_path, gate):
    other = tmp_path / "elsewhere" / "forge_gate"
    other.mkdir(parents=True)
    (other / "__init__.py").write_text("")
    site = f"import sys\nsys.path.insert(0, {str(other.parent)!r})\n"   # the egg-link trap, portably
    tree = make_tree(tmp_path, PASS2_SKIP1, {"src/sitecustomize.py": site})
    code, line, r = gate(tree)
    assert code == 2 and "import preflight: forge_gate imported from" in line, line
    assert _junits(gate.rec) == []


def test_g5_no_pytest_under_the_gate_env_is_refused(tmp_path, gate):
    tree = make_tree(tmp_path, PASS2_SKIP1, {"src/pytest.py": "raise ImportError('no pytest here')\n"})
    code, line, r = gate(tree)
    assert code == 2 and "import preflight rc=1" in line and "no pytest here" in line, line
    assert _junits(gate.rec) == []


# ---- G6: load guard -------------------------------------------------------------------------------

def test_g6_a_loaded_box_is_refused_after_the_wait(tmp_path, gate, monkeypatch):
    tree = make_tree(tmp_path, PASS2_SKIP1)
    monkeypatch.setattr(fg.C, "_loadavg", lambda: 99.0)
    slept = []
    monkeypatch.setattr(fg.C, "_sleep", slept.append)
    code, line, r = gate(tree, "--max-load", "3", "--wait", "45")
    assert code == 2 and "load 99.00 > 3.00 after waiting 45 s" in line, line
    assert slept == [30, 15] and _junits(gate.rec) == []


def test_g6_waits_until_the_load_drops(tmp_path, gate, monkeypatch):
    tree = make_tree(tmp_path, PASS2_SKIP1)
    seq = [99.0, 99.0, 99.0, 0.5]
    monkeypatch.setattr(fg.C, "_loadavg", lambda: seq.pop(0) if len(seq) > 1 else seq[0])
    slept = []
    monkeypatch.setattr(fg.C, "_sleep", slept.append)
    code, line, r = gate(tree, "--max-load", "3", "--wait", "900")
    assert code == 0, line
    assert slept == [30, 30] and r["waited_s"] == 60 and r["load_before"] == 0.5
    assert r["load_first"] == 99.0 and r["max_load"] == 3.0


def test_g6_threshold_precedence(monkeypatch):
    assert fg.C.threshold(2.5, {fg.C.LOAD_KEY: "7"}) == 2.5
    assert fg.C.threshold(None, {fg.C.LOAD_KEY: "7"}) == 7.0
    assert fg.C.threshold(None, {}) == (os.cpu_count() or 2) / 2


# ---- G7: run ---------------------------------------------------------------------------------------

def test_g7_a_junit_file_that_already_exists_is_refused(tmp_path, gate, monkeypatch):
    tree = make_tree(tmp_path, PASS2_SKIP1)
    monkeypatch.setattr(fg.C, "_run_id", lambda: "fixed")
    gate.rec.parent.mkdir(parents=True)
    old = gate.rec.parent / "junit-t-fixed.xml"
    old.write_text('<testsuites><testsuite tests="9" failures="0" errors="0" skipped="0"/></testsuites>')
    code, line, r = gate(tree)
    assert code == 2 and "exists before the run" in line and r["rc"] is None, line


def test_g7_the_wall_timeout_kills_the_whole_process_group(tmp_path, gate):
    pidfile = tmp_path / "grandchild.pid"
    body = ("import subprocess, sys, time\n\ndef test_hang():\n"
            "    c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
            f"    open({str(pidfile)!r}, 'w').write(str(c.pid))\n    time.sleep(120)\n")
    tree = make_tree(tmp_path, {"test_hang.py": body})
    t0 = time.monotonic()
    code, line, r = gate(tree, "--timeout", "6")
    assert code == 2 and "timeout after 6 s" in line, line
    assert time.monotonic() - t0 < 60
    pid = int(pidfile.read_text())
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        pytest.fail(f"grandchild {pid} outlived the gate's timeout kill")


# S2 review F8: under S2's deny-list each bypass spelling below (-o/--override-ini testpaths, a
# combined -qc<ini>, an @argfile) let pytest measure another tree. The gate tree here FAILS, so an
# arg that slipped through to the other tree would show up as a GREEN instead of a usage error.
_OFF_LIST = [
    (("--junitxml={o}/x.xml",), "--junitxml={o}/x.xml"),
    (("--rootdir={o}",), "--rootdir={o}"),
    (("-c", "{o}/bypass.ini"), "-c"),
    (("--config-file={o}/bypass.ini",), "--config-file={o}/bypass.ini"),
    (("{o}/tests",), "{o}/tests"),
    (("../other/tests/test_o.py::test_o",), "../other/tests/test_o.py::test_o"),
    (("-o", "testpaths={o}/tests"), "-o"),
    (("-otestpaths={o}/tests",), "-otestpaths={o}/tests"),
    (("--override-ini=testpaths={o}/tests",), "--override-ini=testpaths={o}/tests"),
    (("-qc{o}/bypass.ini",), "-qc{o}/bypass.ini"),
    (("@{o}/args.txt",), "@{o}/args.txt"),
    (("@in.txt",), "@in.txt"),
    (("-k", "@{o}/args2.txt"), "@{o}/args2.txt"),
    (("--deselect", "@{o}/args.txt"), "@{o}/args.txt"),
    (("-p", "no:junitxml"), "-p"),
    (("-xv",), "-xv"),
    (("--",), "--"),
    (("tests/nope.py",), "tests/nope.py"),
    (("-k",), "-k"),
    (("--maxfail=1x",), "1x"),
]


@pytest.mark.parametrize("extra,named", _OFF_LIST, ids=[" ".join(e) for e, _ in _OFF_LIST])
def test_g7_extra_args_off_the_allow_list_are_refused_before_pytest(tmp_path, gate, extra, named):
    other = make_tree(tmp_path / "o", {"test_o.py": "def test_o():\n    assert 1\n"})
    (tmp_path / "other").symlink_to(other)
    for name, body in {"args.txt": f"{other}/tests\n", "args2.txt": f"test_o\n{other}/tests\n",
                       "bypass.ini": f"[pytest]\naddopts = {other}/tests\n"}.items():
        (other / name).write_text(body)
    # in.txt is a real argfile inside the tree; "@in.txt" is also a real file, so only the
    # '@' rule (not the exists rule) stands between pytest and in.txt's -o testpaths=<other>.
    tree = make_tree(tmp_path, {"test_f.py": "def test_f():\n    assert 0\n"},
                     files={"@in.txt": "", "in.txt": f"-o\ntestpaths={other}/tests\n"})
    code, line, r = gate(tree, "--", *(e.format(o=other) for e in extra))
    assert code == 2 and line.startswith("GATE ERROR rc=none") and "usage:" in line, line
    assert repr(named.format(o=other)) in line, line
    assert _junits(gate.rec) == []


def test_g7_allow_listed_extra_args_reach_pytest(tmp_path, gate):
    tree = make_tree(tmp_path, PASS2_SKIP1)
    code, line, r = gate(tree, "--", "tests/test_a.py::test_p", "-k", "test_p")
    assert code == 0 and "passed=2 failed=0 errors=0 skipped=0" in line, line
    # every allow-listed spelling at once; the two deselects leave only test_p[1] and test_p[2]
    code, line, r = gate(tree, "--", "tests", "-x", "-v", "-q", "-m", "not slow", "--maxfail", "1",
                         "--maxfail=2", "--deselect", "tests/test_a.py::test_one",
                         "--deselect=tests/test_a.py::test_s")
    assert code == 0 and "passed=2 failed=0 errors=0 skipped=0" in line, line


# ---- G8: counts from this run's junit only ---------------------------------------------------------

_DOCTOR = ("import os\n\ndef pytest_unconfigure(config):\n    j = config.option.xmlpath\n    {}\n")


@pytest.mark.parametrize("how,expect", [
    ("os.utime(j, (1, 1))", "stale junit"),
    ("os.rename(j, j + '.moved')", "no junit file"),
])
def test_g8_a_stale_or_missing_junit_is_an_error(tmp_path, gate, how, expect):
    tree = make_tree(tmp_path, {**PASS2_SKIP1, "conftest.py": _DOCTOR.format(how)})
    code, line, r = gate(tree)
    assert code == 2 and line.startswith("GATE ERROR rc=0") and expect in line, line


def test_g8_counts_come_from_the_junit(tmp_path, gate):
    body = ("import pytest\n\n" "@pytest.fixture\ndef broken():\n    raise RuntimeError('setup')\n\n"
            "def test_ok():\n    assert 1\n\ndef test_bad():\n    assert 0\n\n"
            "def test_err(broken):\n    pass\n\ndef test_skip():\n    pytest.skip('x')\n")
    tree = make_tree(tmp_path, {"test_m.py": body})
    code, line, r = gate(tree)
    assert code == 1, line
    assert line.startswith("GATE RED rc=1 passed=1 failed=1 errors=1 skipped=1 tree="), line


# ---- G9: tree after ---------------------------------------------------------------------------------

def test_g9_a_suite_that_writes_a_tracked_file_is_an_error_and_is_not_restored(tmp_path, gate):
    tree = make_tree(tmp_path, {"test_w.py": "def test_w():\n    open('README.md', 'a').write('x\\n')\n"})
    code, line, r = gate(tree)
    assert code == 2 and line.startswith("GATE ERROR rc=0") and line.endswith("tree dirtied: README.md"), line
    assert (tree / "README.md").read_text() == "readme\nx\n"
    assert r["porcelain_after"] == " M README.md\n"


def test_g9_an_index_change_with_the_same_porcelain_is_caught_by_write_tree(tmp_path, gate):
    body = ("import subprocess\n\ndef test_i():\n    open('data.txt', 'w').write('two\\n')\n"
            "    subprocess.run(['git', 'add', 'data.txt'], check=True)\n")
    tree = make_tree(tmp_path, {"test_i.py": body}, {"data.txt": "zero\n"})
    (tree / "data.txt").write_text("one\n")
    _git(tree, "add", "data.txt")                      # before: "M  data.txt"
    code, line, r = gate(tree)
    assert r["porcelain_before"] == r["porcelain_after"] == "M  data.txt\n"
    assert code == 2 and "tree dirtied" in line and r["tree_after"] != r["tree_before"], line


# ---- G10: must-run ------------------------------------------------------------------------------------

def test_g10_a_skipped_or_missing_must_run_entry_is_red(tmp_path, gate):
    tree = make_tree(tmp_path, PASS2_SKIP1)
    code, line, r = gate(tree, "--must-run", "tests/test_a.py::test_p", "tests/test_a.py::test_s",
                         "tests/test_a.py::test_nope")
    assert code == 1 and line.startswith("GATE RED rc=0 passed=3 failed=0"), line
    assert "must-run: tests/test_a.py::test_s skipped" in line
    assert "must-run: tests/test_a.py::test_nope missing" in line
    assert r["must_run"]["tests/test_a.py::test_p"] == "passed"


def test_g10_entry_matching():
    cases = [("tests.test_a", "test_p[1]", "passed"), ("tests.test_a", "test_p[2]", "passed"),
             ("tests.test_a", "test_pp", "failed"), ("tests.test_a.TestK", "test_m", "passed"),
             ("tests.sub.test_b", "test_x", "skipped")]
    got = fg.must_run(["tests/test_a.py::test_p", "tests/test_a.py::TestK::test_m",
                       "tests/test_a.py::test_m", "tests/test_a.py", "tests/sub/test_b.py",
                       "tests/test_a.py::test_p[2]", "tests/test_c.py"], cases)
    assert got == {"tests/test_a.py::test_p": "passed", "tests/test_a.py::TestK::test_m": "passed",
                   "tests/test_a.py::test_m": "missing", "tests/test_a.py": "failed",
                   "tests/sub/test_b.py": "skipped", "tests/test_a.py::test_p[2]": "passed",
                   "tests/test_c.py": "missing"}


# ---- G11: verdict -------------------------------------------------------------------------------------

def _c(t, f=0, e=0, s=0):
    return {"tests": t, "failures": f, "errors": e, "skipped": s}


@pytest.mark.parametrize("rc,counts,tree_ok,must,want", [
    (0, _c(3), True, {}, "GREEN"),
    (0, _c(0), True, {}, "ERROR"),             # nothing ran
    (1, _c(3), True, {}, "ERROR"),             # rc 1 without a failure (P8)
    (1, _c(3, e=1), True, {}, "ERROR"),        # errors only
    (1, _c(3, f=1), True, {}, "RED"),
    (0, _c(3, f=1), True, {}, "ERROR"),        # rc 0 but the junit shows a failure
    (0, _c(3, e=1), True, {}, "ERROR"),        # rc 0 but the junit shows an error
    (0, _c(3), False, {}, "ERROR"),            # tree dirtied
    (1, _c(3, f=1), False, {}, "ERROR"),
    (0, _c(3), True, {"x.py": "skipped"}, "RED"),
    (2, _c(3), True, {"x.py": "missing"}, "ERROR"),
    (4, _c(0), True, {}, "ERROR"),
    (5, _c(0), True, {}, "ERROR"),
])
def test_g11_verdict_table(rc, counts, tree_ok, must, want):
    assert fg.verdict(rc, counts, tree_ok, must)[0] == want


def test_g11_rc1_with_no_failure_in_the_junit_is_an_error(tmp_path, gate):
    conf = "def pytest_sessionfinish(session, exitstatus):\n    session.exitstatus = 1\n"
    tree = make_tree(tmp_path, {**PASS2_SKIP1, "conftest.py": conf})
    code, line, r = gate(tree)
    assert code == 2 and line.startswith("GATE ERROR rc=1 passed=3 failed=0"), line


def test_g11_rc0_with_a_failure_in_the_junit_is_an_error(tmp_path, gate):
    conf = "def pytest_sessionfinish(session, exitstatus):\n    session.exitstatus = 0\n"
    tree = make_tree(tmp_path, {"test_f.py": "def test_f():\n    assert 0\n", "conftest.py": conf})
    code, line, r = gate(tree)
    assert code == 2 and line.startswith("GATE ERROR rc=0 passed=0 failed=1"), line


# ---- G12: record ------------------------------------------------------------------------------------

@pytest.mark.parametrize("via_link", [False, True])
def test_g12_a_record_inside_the_tree_is_refused(tmp_path, gate, via_link):
    tree = make_tree(tmp_path, PASS2_SKIP1)
    if via_link:
        (tmp_path / "link").symlink_to(tree)
        rec = tmp_path / "link" / "recs" / "r.jsonl"
    else:
        rec = tree / "recs" / "r.jsonl"
    code, line, r = gate(tree, record=rec)
    assert code == 2 and "inside the tested tree" in line, line
    assert not (tree / "recs").exists() and _git(tree, "status", "--porcelain") == ""


def test_g12_the_record_is_written_before_the_verdict_is_printed(tmp_path, gate):
    tree = make_tree(tmp_path, PASS2_SKIP1)
    rec = tmp_path / "rec" / "a-dir"
    rec.mkdir(parents=True)                # O_APPEND on a directory fails
    code, line, r = gate(tree, record=rec)
    assert code == 2 and "record write failed" in line, line
    assert "GATE GREEN" not in gate.out and gate.out.count("GATE ") == 1, gate.out


# ---- G1: usage and the tree argument ------------------------------------------------------------------

@pytest.mark.parametrize("argv", [["--python", "py"], ["-h"], ["--help"]])
def test_g1_usage_errors_are_gate_errors(tmp_path, gate, capsys, argv):
    code = fg.main(argv)
    last = capsys.readouterr().out.strip().splitlines()[-1]
    assert code == 2 and last.startswith("GATE ERROR rc=none") and "usage:" in last


def test_g1_tree_must_be_a_worktree_top_level(tmp_path, gate):
    tree = make_tree(tmp_path, PASS2_SKIP1)
    code, line, r = gate(tree / "tests")
    assert code == 2 and "is not a worktree's top level" in line, line


# ---- S3c: the shared code in fg_common.py ---------------------------------------------------------

def test_s3c_a_gate_that_cannot_load_its_common_code_exits_2_not_1(tmp_path):
    lone = tmp_path / "lone" / "tools"
    lone.mkdir(parents=True)
    shutil.copy2(TOOL, lone / "fg_gate.py")  # no fg_common.py beside it
    tree = make_tree(tmp_path, PASS2_SKIP1)
    rec = tmp_path / "rec" / "r.jsonl"
    p = subprocess.run([sys.executable, str(lone / "fg_gate.py"), "--tree", str(tree), "--python",
                        sys.executable, "--record", str(rec), "--tag", "lone", "--max-load", "1000",
                        "--wait", "0"], capture_output=True, text=True, cwd=tmp_path)
    last = (p.stdout.strip().splitlines() or [""])[-1]
    assert p.returncode == 2, p.stdout + p.stderr
    assert last.startswith("GATE ERROR rc=none") and "cannot load fg_common.py" in last, last
    assert not rec.exists()


def test_s3c_the_record_names_the_shared_code_it_ran(tmp_path, gate):
    tree = make_tree(tmp_path, PASS2_SKIP1)
    code, line, r = gate(tree)
    common = REPO / "tools" / "fg_common.py"
    assert code == 0, line
    assert Path(r["common_file"]) == common.resolve()
    assert r["common_md5"] == hashlib.md5(common.read_bytes()).hexdigest()


# ---- S3d: an interrupt kills the pytest group and is recorded, never GREEN or RED -------------

def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@pytest.mark.parametrize("sig", [signal.SIGINT, signal.SIGTERM, signal.SIGHUP],
                         ids=["SIGINT", "SIGTERM", "SIGHUP"])
def test_s3d_an_interrupt_kills_the_pytest_group_and_records_a_gate_error(tmp_path, sig):
    pidf = tmp_path / "pytest.pid"  # the suite's test writes its pytest's pid and a grandchild's
    body = ("import os, subprocess, sys, time\n\ndef test_hang():\n"
            "    c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(600)'])\n"
            f"    open({str(pidf)!r}, 'w').write(f'{{os.getpid()}} {{c.pid}}')\n    time.sleep(600)\n")
    tree = make_tree(tmp_path, {"test_hang.py": body})
    rec = tmp_path / "rec" / "r.jsonl"
    env = dict(os.environ, **{fg.C.PATH_KEY: str(_bin(tmp_path / "bin"))})
    p = subprocess.Popen([sys.executable, str(TOOL), "--tree", str(tree), "--python", sys.executable,
                          "--record", str(rec), "--tag", "intr", "--max-load", "1000", "--wait", "0"],
                         env=env, cwd=tmp_path, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                         preexec_fn=lambda: signal.signal(sig, signal.SIG_DFL))  # review S3e F1
    deadline = time.monotonic() + 60
    while not (pidf.is_file() and pidf.read_text()):
        assert p.poll() is None and time.monotonic() < deadline, p.communicate()[0]
        time.sleep(0.1)
    pid, kid = map(int, pidf.read_text().split())
    try:
        t0 = time.monotonic()
        p.send_signal(sig)
        out = p.communicate(timeout=45)[0].strip().splitlines()
        assert time.monotonic() - t0 < 20, "the interrupted gate did not end promptly"
        assert p.returncode == 2, out
        assert out[-1].startswith("GATE ERROR rc=none"), out
        assert f"interrupted (signal {int(sig)}); pytest process group killed" in out[-1], out
        assert not _alive(pid), "the suite's pytest outlived the interrupt"
        for _ in range(50):  # review F3: the whole group, not just its leader (reaped by init)
            if not _alive(kid):
                break
            time.sleep(0.1)
        else:
            pytest.fail(f"the suite's grandchild {kid} outlived the interrupt")
        r = json.loads(rec.read_text().splitlines()[-1])
        assert r["verdict"] == "ERROR" and r["rc"] is None and r["group_killed"] is True, r
        assert r["interrupted"] == f"signal {int(sig)}" and r["reason"] in out[-1], r
        assert _git(tree, "status", "--porcelain") == ""
    finally:
        for k in (pid, kid):
            if _alive(k):
                os.kill(k, signal.SIGKILL)
        if p.poll() is None:
            p.kill()
            p.wait()


# ---- S3e: the S3d leftovers, all fixed (C2) -------------- ---------------------------------------

def test_s3e_f4_a_signal_inside_popen_is_raised_once_proc_is_held_and_the_group_goes(tmp_path, gate,
                                                                                     monkeypatch):
    """F4: the signal lands inside Popen, after the fork and before the gate holds ``proc``. It
    must still kill the new pytest group. It is raised between Popen and the wait, so this is
    also the test of that kill branch on its own (parent mutant D1)."""
    tree = make_tree(tmp_path, {"test_hang.py": "import time\n\ndef test_hang():\n    time.sleep(600)\n"})
    real, held = subprocess.Popen, []

    def popen(*a, **k):
        p = real(*a, **k)
        if "pytest" in list(a[0])[1:3] and not held:  # the suite's run only, never a git call
            held.append(p)
            os.kill(os.getpid(), signal.SIGTERM)  # in the window: Popen has not returned yet
            time.sleep(0.05)
        return p
    old = signal.signal(signal.SIGTERM, signal.SIG_DFL)  # a known start, whatever ran this suite
    monkeypatch.setattr(fg.subprocess, "Popen", popen)
    try:
        code, line, r = gate(tree, "--timeout", "40")  # a lost signal ends as a timeout, not a hang
        assert held, "the suite's Popen never ran"
        assert code == 2 and line.startswith("GATE ERROR rc=none"), line
        assert f"interrupted (signal {int(signal.SIGTERM)}); pytest process group killed" in line, line
        assert r["group_killed"] is True and r["interrupted"] == f"signal {int(signal.SIGTERM)}", r
        assert held[0].poll() is not None, "the suite's pytest outlived a signal that landed in Popen"
    finally:
        signal.signal(signal.SIGTERM, old)
        for p in held:
            if p.poll() is None:
                os.killpg(p.pid, signal.SIGKILL)
                p.wait()


_WAIT_FOR_GO = ("import os, signal, time\n\ndef test_wait():\n"
                "    # review F1: the suite never inherits the gate's ignored signals\n"
                "    assert signal.SIG_IGN not in (signal.getsignal(signal.SIGHUP), signal.getsignal(signal.SIGINT))\n"
                "    open({pidf!r}, 'w').write(str(os.getpid()))\n"
                "    t = time.monotonic()\n    while not os.path.exists({go!r}):\n"
                "        assert time.monotonic() - t < 60\n        time.sleep(0.05)\n")


@pytest.mark.parametrize("sig", [signal.SIGHUP, signal.SIGINT], ids=["SIGHUP", "SIGINT"])
@pytest.mark.parametrize("start", ["inherited-ignored", "default"])
def test_s3e_f5_an_inherited_ignore_is_respected_and_a_default_still_interrupts(tmp_path, sig, start):
    """F5: under ``nohup`` (SIGHUP) or ``cmd &`` (SIGINT) the signal is ignored when the gate
    starts; the gate must leave it ignored, so the run ends GREEN. From the default disposition
    the same signal still interrupts (GATE ERROR, group killed)."""
    pidf, go = tmp_path / "pytest.pid", tmp_path / "go"
    tree = make_tree(tmp_path, {"test_wait.py": _WAIT_FOR_GO.format(pidf=str(pidf), go=str(go))})
    rec = tmp_path / "rec" / "r.jsonl"
    env = dict(os.environ, **{fg.C.PATH_KEY: str(_bin(tmp_path / "bin"))})
    disp = signal.SIG_IGN if start == "inherited-ignored" else signal.SIG_DFL
    p = subprocess.Popen([sys.executable, str(TOOL), "--tree", str(tree), "--python", sys.executable,
                          "--record", str(rec), "--tag", "f5", "--max-load", "1000", "--wait", "0"],
                         env=env, cwd=tmp_path, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, preexec_fn=lambda: signal.signal(sig, disp))
    pid = None
    try:
        deadline = time.monotonic() + 60
        while not (pidf.is_file() and pidf.read_text()):
            assert p.poll() is None and time.monotonic() < deadline, p.communicate()[0]
            time.sleep(0.1)
        pid = int(pidf.read_text())
        p.send_signal(sig)
        time.sleep(1.0)  # ample time for the gate to act on it, if it does
        go.write_text("")
        out = p.communicate(timeout=60)[0].strip().splitlines()
        r = json.loads(rec.read_text().splitlines()[-1])
        if start == "inherited-ignored":
            assert p.returncode == 0 and out[-1].startswith("GATE GREEN rc=0 passed=1"), out
            assert "interrupted" not in r and r["verdict"] == "GREEN", r
        else:
            assert p.returncode == 2, out
            assert f"interrupted (signal {int(sig)}); pytest process group killed" in out[-1], out
            assert r["group_killed"] is True and not _alive(pid), r
    finally:
        if pid is not None and _alive(pid):
            os.kill(pid, signal.SIGKILL)
        if p.poll() is None:
            p.kill()
            p.wait()


def test_s3e_f6_a_bare_interrupt_is_named_by_its_type(tmp_path, gate, monkeypatch):
    """F6: ``str(e or type(e).__name__)`` was dead (an exception is always truthy), so a bare
    KeyboardInterrupt recorded ''. The message keeps the documented ``interrupted (<what>)``."""
    tree = make_tree(tmp_path, PASS2_SKIP1)

    def boom(*_a):
        raise KeyboardInterrupt()
    monkeypatch.setattr(fg.C, "wait_for_load", boom)
    code, line, r = gate(tree)
    assert code == 2 and line.endswith(" interrupted (KeyboardInterrupt)"), line
    assert r["interrupted"] == "KeyboardInterrupt" and "group_killed" not in r, r


def test_s3e_the_record_is_written_with_the_three_signals_ignored(tmp_path, gate, monkeypatch):
    """A signal that lands while the record is written cannot break the write or the verdict."""
    tree = make_tree(tmp_path, PASS2_SKIP1)
    real, seen = fg.C.append_record, {}

    def append(record, rec):
        seen.update({s: signal.getsignal(s) for s in fg._SIGNALS})
        os.kill(os.getpid(), signal.SIGTERM)
        time.sleep(0.05)
        real(record, rec)
    old = signal.signal(signal.SIGTERM, signal.SIG_DFL)
    monkeypatch.setattr(fg.C, "append_record", append)
    try:
        code, line, r = gate(tree)
    finally:
        signal.signal(signal.SIGTERM, old)
    assert seen == {s: signal.SIG_IGN for s in fg._SIGNALS}, seen
    assert code == 0 and line.startswith("GATE GREEN rc=0 passed=3") and r["verdict"] == "GREEN", line
    assert "interrupted" not in r


@pytest.mark.parametrize("raise_", ["SystemExit(1)", "KeyboardInterrupt('late')"])
def test_s3e_the_main_wrapper_turns_an_escaped_base_exception_into_gate_error_exit_2(tmp_path, raise_):
    """``__main__``: anything that escapes main() is a GATE ERROR, exit 2 — never exit 1 (RED),
    which is what an escaped SystemExit(1) would otherwise be."""
    tools = tmp_path / "copy" / "tools"
    tools.mkdir(parents=True)
    shutil.copy2(TOOL, tools / "fg_gate.py")
    common = (REPO / "tools" / "fg_common.py").read_text()
    (tools / "fg_common.py").write_text(common + f"\n\ndef _run_id():\n    raise {raise_}\n")
    shutil.copytree(REPO / "src" / "forge_gate", tmp_path / "copy" / "src" / "forge_gate",
                    ignore=shutil.ignore_patterns("__pycache__"))
    tree = make_tree(tmp_path, PASS2_SKIP1)
    rec = tmp_path / "rec" / "r.jsonl"
    env = dict(os.environ, **{fg.C.PATH_KEY: str(_bin(tmp_path / "bin"))})
    p = subprocess.run([sys.executable, str(tools / "fg_gate.py"), "--tree", str(tree), "--python",
                        sys.executable, "--record", str(rec), "--tag", "wrap", "--max-load", "1000",
                        "--wait", "0"], capture_output=True, text=True, cwd=tmp_path, env=env)
    last = (p.stdout.strip().splitlines() or [""])[-1]
    want = raise_.split("(")[0]
    assert p.returncode == 2, (p.returncode, p.stdout, p.stderr)
    assert last.startswith("GATE ERROR rc=none") and f"gate fault: {want}" in last, last
