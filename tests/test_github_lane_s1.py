"""GitHub-lane loop S1: KD-13 (a) the durable fetch record, KD-13 (f) the list-folder config key.

Pure Python, no box: bubblewrap, the red arm and the in-box command are replaced by fakes, but the
PROXY is the real one (a unix socket and a local echo server), so the record's CONNECT log and dial
list are produced by the code under test, not by the test.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import textwrap
import threading
from pathlib import Path

import pytest

from forgeproof import config
from forgeproof.scout import box, fetchproxy, ghpool, ratify

SRC = Path(__file__).resolve().parents[1] / "src"
TARGET = "fake.test:443"


@pytest.fixture
def short_root():
    """AF_UNIX paths are capped (104 bytes on macOS) and pytest's tmp_path is longer there: the
    box root (where the proxy socket lives) gets a short throwaway folder, as test_fetchproxy does."""
    d = tempfile.mkdtemp(prefix="fgs1-", dir="/tmp")
    yield Path(d)
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def echo():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    stop = threading.Event()

    def serve():
        srv.settimeout(0.2)
        while not stop.is_set():
            try:
                c, _ = srv.accept()
            except (socket.timeout, OSError):
                continue
            with c:
                c.settimeout(5)
                try:
                    while d := c.recv(4096):
                        c.sendall(d)
                except OSError:
                    pass
    t = threading.Thread(target=serve, daemon=True)
    t.start()
    yield srv.getsockname()
    stop.set()
    t.join(2)
    srv.close()


def _host_sets(tmp_path: Path) -> Path:
    f = tmp_path / "host_sets.json"
    f.write_text(json.dumps({"sets": {"fake": [TARGET]}}))
    return f


def _fake_box(monkeypatch, short_root, exec_fn, arm_hook=None):
    """No bwrap: available() passes, the red arm reports clean, _exec is ``exec_fn``."""
    monkeypatch.setenv(box.BOX_ROOT_KEY, str(short_root))
    monkeypatch.delenv(box.BOX_USER_KEY, raising=False)
    monkeypatch.setattr(box, "available", lambda: None)

    def arm(mode, fetch=None, **kw):
        if arm_hook:
            arm_hook()
        return {"ok": True, "probe": {"fake": True}, "proxy_log": [], "proxy_inside": "reached",
                "proxy_outside": "blocked"}
    monkeypatch.setattr(box, "red_arm", arm)
    monkeypatch.setattr(box, "_exec", exec_fn)


def _tunnel(kw):
    """What the in-box command does: one CONNECT through the real proxy's socket."""
    proxy_dir = dict((dst, src) for src, dst in kw["ro_binds"])["/proxy"]
    return fetchproxy.connect_via(Path(proxy_dir) / "p.sock", TARGET)


def _records(d: Path) -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(d.glob("fetch-*.json"))]


CRASHER = textwrap.dedent("""
    import json, os, sys
    from pathlib import Path
    from forgeproof.scout import box, fetchproxy
    rdir, hs, host, port = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], int(sys.argv[4])
    box.available = lambda: None
    box.red_arm = lambda mode, fetch=None, **kw: {"ok": True, "probe": {"fake": True}, "proxy_log": [],
                                                  "proxy_inside": "reached", "proxy_outside": "blocked"}
    def fake_exec(src, argv, timeout, **kw):
        proxy_dir = dict((dst, s) for s, dst in kw["ro_binds"])["/proxy"]
        code = fetchproxy.connect_via(Path(proxy_dir) / "p.sock", "fake.test:443")
        return 0, f"status {code}", "", []
    box._exec = fake_exec
    r = box.run(Path(sys.argv[5]), ["git", "fetch"], mode="fetch", host_set="fake", record_dir=rdir,
                _host_sets_file=hs, _resolve=lambda t: (host, port), _same_user_ok=True)
    print("RAN", r.outcome.label(), r.stdout, flush=True)
    os._exit(9)   # the caller dies before it could write anything itself (step 7's crash)
""")


def test_the_fetch_record_survives_a_caller_that_crashes_after_the_run(tmp_path, short_root, echo):
    rdir = tmp_path / "records"
    rdir.mkdir()
    src = tmp_path / "src"
    src.mkdir()
    env = {k: v for k, v in os.environ.items() if k != box.BOX_USER_KEY}
    env.update(PYTHONPATH=str(SRC), **{box.BOX_ROOT_KEY: str(short_root)})
    p = subprocess.run([sys.executable, "-c", CRASHER, str(rdir), str(_host_sets(tmp_path)), echo[0],
                        str(echo[1]), str(src)], capture_output=True, text=True, env=env, timeout=120)
    assert p.returncode == 9, (p.stdout, p.stderr)
    assert "RAN FOUND" in p.stdout and "status 200" in p.stdout, (p.stdout, p.stderr)
    recs = _records(rdir)
    assert len(recs) == 1, list(rdir.iterdir())
    rec = recs[0]
    assert rec["state"] == "done" and rec["host_set"] == "fake" and rec["members"] == [TARGET], rec
    assert [(e["target"], e["decision"], e["upstream"]) for e in rec["connects"]] == [(TARGET, "allowed", "ok")], rec
    assert rec["dials"] == [TARGET], rec
    assert not list(rdir.glob("*.part")), list(rdir.iterdir())


def test_the_fetch_record_is_written_when_the_run_raises(tmp_path, short_root, echo, monkeypatch):
    rdir = tmp_path / "records"
    rdir.mkdir()
    seen = {}

    def exploding(src, argv, timeout, **kw):
        seen["status"] = _tunnel(kw)
        raise RuntimeError("boom after the CONNECT")
    _fake_box(monkeypatch, short_root, exploding)
    with pytest.raises(RuntimeError, match="boom after the CONNECT"):
        box.run(tmp_path, ["git", "fetch"], mode="fetch", host_set="fake", record_dir=rdir,
                _host_sets_file=_host_sets(tmp_path), _resolve=lambda t: echo, _same_user_ok=True)
    assert seen["status"] == 200
    recs = _records(rdir)
    assert len(recs) == 1, list(rdir.iterdir())
    rec = recs[0]
    assert rec["state"] == "error" and "boom after the CONNECT" in rec["error"], rec
    assert [(e["target"], e["decision"]) for e in rec["connects"]] == [(TARGET, "allowed")], rec
    assert rec["dials"] == [TARGET], rec


def test_a_started_record_is_on_disk_before_the_red_arm_runs(tmp_path, short_root, echo, monkeypatch):
    """A kill -9 mid-run still leaves a record that a fetch began, with its set and argv."""
    rdir = tmp_path / "records"
    rdir.mkdir()
    at_arm = []
    _fake_box(monkeypatch, short_root, lambda src, argv, timeout, **kw: (0, "", "", []),
              arm_hook=lambda: at_arm.append(_records(rdir)))
    r = box.run(tmp_path, ["git", "fetch"], mode="fetch", host_set="fake", record_dir=rdir,
                _host_sets_file=_host_sets(tmp_path), _resolve=lambda t: echo, _same_user_ok=True)
    assert r.outcome.kind == "FOUND", r.outcome
    assert len(at_arm) == 1 and len(at_arm[0]) == 1, at_arm
    assert at_arm[0][0]["state"] == "started" and at_arm[0][0]["argv"] == ["git", "fetch"], at_arm
    assert r.fetch_record_file and json.loads(Path(r.fetch_record_file).read_text())["state"] == "done"


def test_a_missing_record_dir_is_blind_before_anything_runs(tmp_path, short_root, monkeypatch):
    ran = []
    _fake_box(monkeypatch, short_root, lambda *a, **k: ran.append(1) or (0, "", "", []),
              arm_hook=lambda: ran.append("arm"))
    r = box.run(tmp_path, ["true"], mode="fetch", host_set="fake", record_dir=tmp_path / "nope",
                _host_sets_file=_host_sets(tmp_path), _same_user_ok=True)
    assert r.outcome.label().startswith("BLIND(fetch-record-dir-missing"), r.outcome
    assert ran == []
    r = box.run(tmp_path, ["true"], record_dir=tmp_path)
    assert r.outcome.label() == "BLIND(record-dir-is-for-fetch-mode)"


def test_an_unwritable_record_makes_the_result_blind(tmp_path, short_root, echo, monkeypatch):
    rdir = tmp_path / "records"
    rdir.mkdir()
    _fake_box(monkeypatch, short_root, lambda src, argv, timeout, **kw: (0, "", "", []))
    real, calls = box._write_record, []

    def second_write_fails(path, record):
        calls.append(record.get("state"))
        if len(calls) > 1:
            raise OSError("disk full")
        real(path, record)
    monkeypatch.setattr(box, "_write_record", second_write_fails)
    r = box.run(tmp_path, ["true"], mode="fetch", host_set="fake", record_dir=rdir,
                _host_sets_file=_host_sets(tmp_path), _resolve=lambda t: echo, _same_user_ok=True)
    assert r.outcome.label().startswith("BLIND(fetch-record-unwritable"), r.outcome
    assert r.fetch_record_file is None


# ---- KD-13 (f): FORGEPROOF_SCOUT_LIST_DIR ------------------------------------------------------

@pytest.fixture
def no_list_key(monkeypatch, tmp_path):
    monkeypatch.delenv(config.SCOUT_LIST_DIR_KEY, raising=False)
    monkeypatch.setenv("FORGEPROOF_CONFIG", str(tmp_path / "no-config.toml"))   # no [paths] at all


def test_the_list_dir_key_wins_and_the_checkout_is_the_default(tmp_path, monkeypatch, no_list_key):
    assert config.scout_list_dir() == config.CODE / "scout"            # this suite runs from a checkout
    assert (config.CODE / "scout" / "trusted_repos.jsonl").is_file()
    d = tmp_path / "lists"
    d.mkdir()
    monkeypatch.setenv(config.SCOUT_LIST_DIR_KEY, str(d))
    assert config.scout_list_dir() == d
    assert ghpool.list_files() == (d / "trusted_repos.jsonl", d / "allowed_signers")


def test_outside_a_checkout_an_unset_key_fails_naming_it(tmp_path, monkeypatch, no_list_key):
    monkeypatch.setattr(config, "CODE", tmp_path / "site-packages")   # an install: no pyproject, no src/
    with pytest.raises(config.NotConfigured, match=f"{config.SCOUT_LIST_DIR_KEY} is not set and .* not running from a source checkout"):
        config.scout_list_dir()
    monkeypatch.setenv(config.SCOUT_LIST_DIR_KEY, str(tmp_path / "typo"))
    with pytest.raises(config.NotConfigured, match=f"{config.SCOUT_LIST_DIR_KEY}=.*not an existing directory"):
        config.scout_list_dir()


def test_admit_reads_the_list_from_the_configured_folder(tmp_path, monkeypatch, no_list_key):
    d = tmp_path / "lists"
    d.mkdir()
    monkeypatch.setenv(config.SCOUT_LIST_DIR_KEY, str(d))
    got = []

    def spy(list_file, signers_file, *a, **k):
        got.append((Path(list_file), Path(signers_file)))
        return ratify.Verdict(0, False, "spy", [], [])
    monkeypatch.setattr(ratify, "ratified", spy)
    with pytest.raises(ghpool.NotAdmitted, match="unsigned"):
        ghpool.admit("python/cpython@" + "a" * 40, "Lib/x.py")
    assert got == [(d / "trusted_repos.jsonl", d / "allowed_signers")]


def test_admit_without_a_list_folder_is_unsigned_naming_the_key(tmp_path, monkeypatch, no_list_key):
    monkeypatch.setattr(config, "CODE", tmp_path / "site-packages")
    monkeypatch.setattr(ratify, "ratified", lambda *a, **k: pytest.fail("nothing may be read"))
    with pytest.raises(ghpool.NotAdmitted, match=f"unsigned: no signed list: {config.SCOUT_LIST_DIR_KEY} is not set"):
        ghpool.admit("python/cpython@" + "a" * 40, "Lib/x.py")
