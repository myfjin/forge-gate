"""tools/fg_common.py, the code fg_gate.py and fg_mutrun.py share (shared-tools loop, slice S3c)."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
TOOLS = REPO / "tools"
COMMON = TOOLS / "fg_common.py"
GIT = shutil.which("git")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


C = _load("fg_common_under_test", COMMON)


def _bin(d: Path) -> Path:
    """A PATH dir with git and a stand-in timeout (G4 only checks that both resolve)."""
    d.mkdir(parents=True, exist_ok=True)
    (d / "git").symlink_to(GIT)
    t = d / "timeout"
    t.write_text("#!/bin/sh\nexit 0\n")
    t.chmod(0o755)
    return d


def _git(tree, *args):
    return subprocess.run([GIT, "-C", str(tree), *args], check=True, capture_output=True,
                          text=True).stdout


CALC = "def add(a, b):\n    return a + b\n"
TEST_CALC = "from forge_gate.calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"


def make_tree(base: Path) -> Path:
    tree = base / "tree"
    (tree / "src" / "forge_gate").mkdir(parents=True, mode=0o700)
    (tree / "tests").mkdir(mode=0o700)
    (tree / "src" / "forge_gate" / "__init__.py").write_text("")
    (tree / "src" / "forge_gate" / "calc.py").write_text(CALC)
    (tree / "tests" / "test_calc.py").write_text(TEST_CALC)
    (tree / ".gitignore").write_text("__pycache__/\n")
    _git(tree, "init", "-q")
    _git(tree, "add", "-A")
    _git(tree, "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false",
         "commit", "-q", "-m", "init")
    return tree


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_both_tools_load_this_file_by_path_and_each_holds_its_own_copy():
    gate = _load("fg_gate_for_common_test", TOOLS / "fg_gate.py")
    mut = _load("fg_mutrun_for_common_test", TOOLS / "fg_mutrun.py")
    for mod in (gate.C, mut.fg):
        assert Path(mod.__file__).resolve() == COMMON.resolve()
    assert gate.C is not mut.fg  # a patch on one tool's copy never leaks into the other
    assert gate.GateError is gate.C.GateError and mut.MutrunError is mut.fg.GateError
    assert "fg_common" not in sys.modules  # never imported through sys.path


@pytest.mark.parametrize("tool", ["fg_gate.py", "fg_mutrun.py"])
def test_each_tool_runs_as_a_script_from_a_foreign_cwd(tmp_path, tool):
    tree = make_tree(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    env = dict(os.environ, **{C.PATH_KEY: str(_bin(tmp_path / "bin"))})
    env.pop("PYTHONPATH", None)
    rec = tmp_path / "rec" / "r.jsonl"
    argv = [sys.executable, str(TOOLS / tool), "--tree", str(tree), "--python", sys.executable,
            "--record", str(rec), "--tag", "cwd", "--max-load", "1000", "--wait", "0"]
    if tool == "fg_mutrun.py":
        spec = tmp_path / "spec.json"
        spec.write_text(json.dumps([{"id": "K", "file": "src/forge_gate/calc.py", "old": "a + b",
                                     "new": "a - b", "tests": ["tests/test_calc.py"], "what": "kill"}]))
        argv += ["--spec", str(spec)]
    p = subprocess.run(argv, cwd=elsewhere, env=env, capture_output=True, text=True, timeout=300)
    out = p.stdout.strip().splitlines()
    assert p.returncode == 0, p.stdout + p.stderr
    if tool == "fg_gate.py":
        assert out[-1].startswith("GATE GREEN rc=0 passed=1 failed=0 errors=0 skipped=0 tree="), out
    else:
        assert out[1].startswith("K KILLED rc=1 | kill | "), out
        assert out[-2:] == ["KILLED: 1/1", "porcelain_after="], out
    r = json.loads(rec.read_text().splitlines()[-1])
    assert Path(r["paths_file"]).resolve() == (REPO / "src" / "forge_gate" / "paths.py").resolve()
    assert list(elsewhere.iterdir()) == []  # nothing written in the cwd


def test_kill_group_reaps_when_popen_left_its_waitpid_lock_held():
    # An interrupt between Popen.wait's lock acquire and its try leaves the lock held (CI hang,
    # 2026-11-23). kill_group must still kill and reap, and must not block on that lock.
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"],
                            start_new_session=True)
    lock = getattr(proc, "_waitpid_lock", None)
    if lock is None:
        proc.kill()
        proc.wait()
        pytest.skip("this Popen has no _waitpid_lock")
    lock.acquire()  # the state the interrupt leaves behind
    t = threading.Thread(target=C.kill_group, args=(proc, 0.5), daemon=True)
    try:
        t.start()
        t.join(10)
        assert not t.is_alive(), "kill_group blocked on the held waitpid lock"
        assert proc.returncode == -signal.SIGKILL
        assert not _alive(proc.pid), "the child was not reaped"
    finally:
        lock.release()
        t.join(10)
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()


def test_kill_group_kills_the_whole_process_group(tmp_path):
    pidf = tmp_path / "grandchild.pid"
    code = ("import subprocess, sys, time\n"
            "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
            f"open({str(pidf)!r}, 'w').write(str(c.pid))\n"
            "time.sleep(120)\n")
    proc = subprocess.Popen([sys.executable, "-c", code], start_new_session=True)
    gc = None
    try:
        deadline = time.monotonic() + 30
        while not (pidf.is_file() and pidf.read_text()):
            assert time.monotonic() < deadline, "the grandchild never started"
            time.sleep(0.05)
        gc = int(pidf.read_text())
        C.kill_group(proc)
        assert proc.returncode == -signal.SIGKILL
        deadline = time.monotonic() + 10
        while _alive(gc) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not _alive(gc), "the grandchild outlived kill_group"
    finally:
        for pid in (gc, proc.pid):
            if pid is not None and _alive(pid):
                os.kill(pid, signal.SIGKILL)
        if proc.poll() is None:
            proc.wait()


def test_strict_parser_turns_every_usage_error_into_a_gate_error():
    ap = C.strict_parser("x.py")
    ap.add_argument("--tree", required=True)
    for argv in ([], ["-h"], ["--tree"], ["--tree", "a", "--nope"]):
        with pytest.raises(C.GateError, match="^usage: "):
            ap.parse_args(argv)
    assert ap.parse_args(["--tree", "a"]).tree == "a"


def test_require_tools_names_the_missing_tool_and_records_what_it_found(tmp_path):
    d = tmp_path / "bin"
    d.mkdir()
    (d / "git").symlink_to(GIT)
    rec = {}
    with pytest.raises(C.GateError, match="timeout not found on gate PATH"):
        C.require_tools({"PATH": str(d)}, rec)
    assert rec == {"which_timeout": None}
    ok, rec = _bin(tmp_path / "bin2"), {}
    C.require_tools({"PATH": str(ok)}, rec)
    assert rec == {"which_timeout": str(ok / "timeout"), "which_git": str(ok / "git")}


def test_inside_tree(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "path", list(sys.path))
    paths = C._own_paths()
    (tmp_path / "tree" / "d").mkdir(parents=True)
    (tmp_path / "out").mkdir()
    tree = paths.real(tmp_path / "tree")
    assert C.inside_tree(paths.real(tmp_path / "tree" / "d" / "r.jsonl"), tree, paths)
    assert C.inside_tree(paths.real(tmp_path / "tree" / "r.jsonl"), tree, paths)
    assert not C.inside_tree(paths.real(tmp_path / "out" / "r.jsonl"), tree, paths)


def test_append_record_appends_one_sorted_json_line_per_call(tmp_path):
    rec = tmp_path / "r.jsonl"
    C.append_record(rec, {"b": 1, "a": [2]})
    C.append_record(rec, {"z": None})
    assert rec.read_text() == '{"a": [2], "b": 1}\n{"z": null}\n'


def test_last_line_md5_and_utc(tmp_path):
    log = tmp_path / "log.txt"
    log.write_bytes(b"")
    assert C.last_line(log) == ""
    log.write_bytes(b"a\n\nb \xff\n\n\n")
    assert C.last_line(log) == "b �"
    (tmp_path / "x.bin").write_bytes(b"x")
    assert C._md5b(b"x") == hashlib.md5(b"x").hexdigest() == C._md5(tmp_path / "x.bin")
    assert C.utc(0) == "1970-01-01T00:00:00Z"


# ---- S3e: tool_head is bounded; inherited SIG_IGN; an interrupt's name ---------------------------

def test_s3e_tool_head_is_bounded_like_every_other_git_call(tmp_path, monkeypatch):
    b = tmp_path / "bin"
    b.mkdir()
    (b / "git").write_text("#!/bin/sh\nexec /bin/sleep 30\n")  # a hung git (exec: the kill reaches it)
    (b / "git").chmod(0o755)
    monkeypatch.setattr(C, "GIT_TIMEOUT_S", 0.5)
    t0 = time.monotonic()
    with pytest.raises(C.GitTimeout, match="rev-parse HEAD TIMED OUT after 0.5 s"):
        C.tool_head({"PATH": str(b)}, COMMON)
    assert time.monotonic() - t0 < 10
    assert issubclass(C.GitTimeout, C.GateError)


def test_s3e_tool_head_outside_a_checkout_is_none_and_inside_is_head(tmp_path):
    env = {"PATH": str(_bin(tmp_path / "bin"))}
    lone = tmp_path / "lone"
    lone.mkdir()
    assert C.tool_head(env, lone / "fg_gate.py") is None
    tree = make_tree(tmp_path)
    assert C.tool_head(env, tree / "src" / "x.py") == _git(tree, "rev-parse", "HEAD").strip()


def test_s3e_install_handlers_skips_an_inherited_ignore_and_returns_only_what_it_replaced():
    def h(*_a):
        pass
    saved = {s: signal.getsignal(s) for s in (signal.SIGHUP, signal.SIGTERM)}
    try:
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        old = C.install_handlers((signal.SIGHUP, signal.SIGTERM), h)
        assert old == {signal.SIGTERM: signal.SIG_DFL}
        assert signal.getsignal(signal.SIGHUP) == signal.SIG_IGN and signal.getsignal(signal.SIGTERM) is h
    finally:
        for s, v in saved.items():
            signal.signal(s, v)


def test_s3e_what_names_an_interrupt():
    assert C.what(KeyboardInterrupt()) == "KeyboardInterrupt"
    assert C.what(KeyboardInterrupt("signal 2")) == "signal 2"
