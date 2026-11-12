"""Build step 4, the isolation box (design v1.4.5 §4a.6). On Linux with bwrap these tests run the
box for real; elsewhere they check that the box reports BLIND and refuses to run."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from forge_gate import cli
from forge_gate.scout import box

LINUX = box.available() is None


@pytest.mark.skipif(LINUX, reason="checks the non-Linux path")
def test_without_bwrap_the_box_is_blind_and_runs_nothing(tmp_path):
    r = box.run(tmp_path, ["python3", "-c", "print(1)"])
    assert r.outcome.kind == "BLIND" and r.outcome.reason.startswith("box-unavailable") and r.returncode is None


def test_fetch_mode_is_refused_until_built(tmp_path):
    r = box.run(tmp_path, ["true"], mode="fetch")
    assert r.outcome.label().startswith("BLIND(fetch-mode-not-built")


@pytest.mark.skipif(not LINUX, reason="needs Linux + bwrap")
def test_red_arm_blocks_all_three_and_the_unboxed_control_leaks_all_three():
    boxed, open_ = box.red_arm("run"), box.red_arm("run", unboxed=True)
    assert boxed["ok"], boxed
    assert all(open_[k].startswith("LEAKED") for k in ("secret", "write", "network")), open_
    assert open_["control_target"] in open_["network"], open_      # B4-1: local control reached
    assert boxed["control_target"] not in boxed["network"], boxed


@pytest.mark.skipif(not LINUX, reason="needs Linux + bwrap")
def test_the_network_control_does_not_need_internet(monkeypatch):
    """B4-1: with every public target unreachable (simulated: the probe is given only the local
    listener), the unboxed control still leaks and the boxed probe is still blocked."""
    monkeypatch.setattr(box, "PROBE", box.PROBE.replace(
        'targets = [("1.1.1.1", 443), ("8.8.8.8", 53), ("127.0.0.1", 22)]', "targets = []"))
    assert 'targets = []' in box.PROBE
    boxed, open_ = box.red_arm("run"), box.red_arm("run", unboxed=True)
    assert boxed["ok"], boxed
    assert open_["network"] == "LEAKED:" + open_["control_target"], open_


@pytest.mark.skipif(not LINUX, reason="needs Linux + bwrap")
def test_box_runs_code_read_only_source_throwaway_work_empty_env(tmp_path):
    (tmp_path / "m.py").write_text(
        "import os, pathlib\n"
        "print('ENV', sorted(os.environ))\n"
        "print('HOME-VISIBLE', pathlib.Path('/home').exists())\n"
        "try:\n    open('/src/m.py','a').write('x'); print('SRC-WRITABLE')\nexcept OSError: print('SRC-RO')\n"
        "open('/work/out.txt','w').write('ok'); print('WORK-OK')\n")
    r = box.run(tmp_path, ["python3", "/src/m.py"])
    assert r.outcome.kind == "FOUND" and r.returncode == 0, (r.outcome, r.stderr)
    assert "SRC-RO" in r.stdout and "WORK-OK" in r.stdout and "HOME-VISIBLE False" in r.stdout
    env = eval(r.stdout.split("ENV ", 1)[1].splitlines()[0])  # our own printed list literal
    assert set(env) <= {"PATH", "HOME", "LANG", "PYTHONDONTWRITEBYTECODE", "PWD", "LC_CTYPE"}, env
    assert not (tmp_path / "out.txt").exists()   # the work copy was thrown away, source untouched


@pytest.mark.skipif(not LINUX, reason="needs Linux + bwrap")
def test_a_failing_red_arm_aborts_the_run(tmp_path, monkeypatch):
    monkeypatch.setattr(box, "red_arm", lambda mode: {"secret": "blocked", "write": "LEAKED",
                                                     "network": "blocked", "ok": False})
    r = box.run(tmp_path, ["python3", "-c", "print(1)"])
    assert r.outcome.label() == "BLIND(box-red-arm: write)" and r.returncode is None


@pytest.mark.skipif(not LINUX, reason="needs Linux + bwrap")
def test_selftest_command_exits_zero_and_prints_each_attempt(capsys):
    assert cli.main(["box-selftest"]) == 0
    out = capsys.readouterr().out
    for k in ("secret", "write", "network"):
        assert f"  {k}" in out
    assert "PASS: every boxed attempt blocked" in out
    assert "namespaces only" in out or "box user" in out           # B4-3: the depth is stated


@pytest.mark.skipif(not LINUX, reason="needs Linux + bwrap")
def test_a_box_that_cannot_start_is_unavailable_not_a_red_arm_failure(tmp_path, monkeypatch):
    """B4-2."""
    monkeypatch.setattr(box, "red_arm", lambda mode: {"secret": "blocked", "write": "blocked",
                        "network": "LEAKED:no-probe-report", "probe": {}, "ok": False})
    monkeypatch.setattr(box, "start_failure", lambda: "setting up uid map: Permission denied")
    r = box.run(tmp_path, ["true"])
    assert r.outcome.label().startswith("BLIND(box-unavailable: bwrap cannot start: setting up uid map")


@pytest.mark.skipif(not LINUX, reason="needs Linux + bwrap")
def test_box_caps_memory(tmp_path):
    """G4: same address-space cap mechanism as runner.run_file; a 2 GB allocation fails inside."""
    r = box.run(tmp_path, ["python3", "-c", "b = bytearray(2 * 1024**3); print('ALLOCATED')"])
    assert r.outcome.kind == "FOUND" and r.returncode != 0 and "ALLOCATED" not in r.stdout, r
    assert "MemoryError" in r.stderr, r.stderr[-300:]


@pytest.mark.skipif(not LINUX, reason="needs Linux + bwrap")
def test_selftest_fails_when_the_unboxed_control_misses_our_listener(monkeypatch, capsys):
    """B4-1: the PASS must rest on the local control. Public hosts reached but our own listener
    not reached (control broken) -> FAIL, exit 1."""
    def fake(mode, unboxed=False):
        if not unboxed:
            return {"secret": "blocked", "write": "blocked", "network": "blocked", "probe": {"x": 1},
                    "ok": True, "control_target": "127.0.0.1:1"}
        return {"secret": "LEAKED", "write": "LEAKED", "network": "LEAKED:1.1.1.1:443",
                "probe": {"x": 1}, "ok": False, "control_target": "127.0.0.1:1"}
    monkeypatch.setattr(box, "red_arm", fake)
    assert cli.main(["box-selftest"]) == 1
    assert "FAIL: the unboxed control did not leak" in capsys.readouterr().out


def test_box_user_without_box_root_is_unavailable(monkeypatch):
    """The box user cannot reach our scratch folder: refuse rather than fail mysteriously."""
    monkeypatch.setenv("FORGE_GATE_BOX_USER", "fgbox")
    monkeypatch.delenv("FORGE_GATE_BOX_ROOT", raising=False)
    with pytest.raises(RuntimeError, match="FORGE_GATE_BOX_ROOT"):
        box.box_root()
    if sys.platform == "linux":
        why = box.available()
        assert why is not None and "FORGE_GATE_BOX_ROOT" in why and why.startswith("box-unavailable"), why


def test_copies_skip_symlinks_and_carry_data_only(tmp_path):
    """A symlink in the source must not carry a host file's bytes into the box."""
    import os
    secret = tmp_path / "host-secret.txt"; secret.write_text("HOST-SECRET")
    src = tmp_path / "src"; src.mkdir(); (src / "a.py").write_text("x = 1\n")
    (src / "sub").mkdir(); (src / "sub" / "b.py").write_text("y = 2\n")
    os.symlink(secret, src / "link.txt"); os.symlink(tmp_path, src / "dirlink")
    dst = tmp_path / "dst"
    skipped = box._plain_copy(src, dst)
    assert sorted(skipped) == ["dirlink", "link.txt"], skipped
    assert (dst / "a.py").read_text() == "x = 1\n" and (dst / "sub" / "b.py").exists()
    assert not (dst / "link.txt").exists() and not (dst / "dirlink").exists()
    assert "HOST-SECRET" not in "".join(p.read_text() for p in dst.rglob("*") if p.is_file())


@pytest.mark.skipif(not LINUX, reason="needs Linux + bwrap")
def test_a_missing_source_is_blind_not_an_empty_run(tmp_path):
    r = box.run(tmp_path / "does-not-exist", ["true"])
    assert r.outcome.label().startswith("BLIND(source-missing"), r.outcome.label()


def test_plain_copy_refuses_a_missing_source(tmp_path):
    with pytest.raises(FileNotFoundError):
        box._plain_copy(tmp_path / "nope", tmp_path / "dst")


@pytest.mark.skipif(sys.platform != "linux", reason="Linux path")
def test_a_box_user_we_cannot_become_is_unavailable(monkeypatch, tmp_path):
    monkeypatch.setenv("FORGE_GATE_BOX_USER", "no-such-user-fg")
    monkeypatch.setenv("FORGE_GATE_BOX_ROOT", str(tmp_path))
    why = box.available()
    assert why and why.startswith("box-unavailable: cannot run as box user no-such-user-fg"), why


def test_an_unwritable_box_root_is_unavailable(monkeypatch, tmp_path):
    import os, stat
    root = tmp_path / "root"; root.mkdir(); os.chmod(root, stat.S_IRUSR | stat.S_IXUSR)
    monkeypatch.setenv("FORGE_GATE_BOX_ROOT", str(root))
    monkeypatch.delenv("FORGE_GATE_BOX_USER", raising=False)
    try:
        if sys.platform == "linux" and os.geteuid() != 0:
            why = box.available()
            assert why and "cannot write the box root" in why, why
    finally:
        os.chmod(root, stat.S_IRWXU)


@pytest.mark.skipif(not LINUX, reason="needs Linux + bwrap")
def test_skipped_links_reach_the_caller(tmp_path):
    """6E BU-1: a link left out of the copies is named in the result, not dropped silently."""
    import os
    src = tmp_path / "src"; src.mkdir(); (src / "a.py").write_text("print('A')\n")
    os.symlink(src / "a.py", src / "b.py")
    r = box.run(src, ["python3", "/src/a.py"])
    assert r.skipped_links == ["b.py"], r.skipped_links
    assert any("skipped 1 symlink(s): b.py" in e for e in r.outcome.evidence), r.outcome.evidence
