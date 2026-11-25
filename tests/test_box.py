"""Build step 4, the isolation box (design v1.4.5 §4a.6). On Linux with bwrap these tests run the
box for real; elsewhere they check that the box reports BLIND and refuses to run."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from forgeproof import cli
from forgeproof.scout import box

LINUX = box.available() is None


@pytest.mark.skipif(LINUX, reason="checks the non-Linux path")
def test_without_bwrap_the_box_is_blind_and_runs_nothing(tmp_path):
    r = box.run(tmp_path, ["python3", "-c", "print(1)"])
    assert r.outcome.kind == "BLIND" and r.outcome.reason.startswith("box-unavailable") and r.returncode is None




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


# ---- fetch mode (step 7 slice 1, DECISIONS-7-github G1) -------------------------------------
import json as _json
import socket as _socket
import threading as _threading

from forgeproof.scout import fetchproxy


@pytest.fixture
def fake_set(tmp_path):
    """A host-sets file with one fake member, and a local echo server standing in for it."""
    srv = _socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(16)
    stop = _threading.Event()

    def serve():
        srv.settimeout(0.2)
        while not stop.is_set():
            try:
                c, _ = srv.accept()
            except (_socket.timeout, OSError):
                continue
            with c:
                c.settimeout(5)
                try:
                    while d := c.recv(4096):
                        c.sendall(d)
                except OSError:
                    pass
    t = _threading.Thread(target=serve, daemon=True)
    t.start()
    f = tmp_path / "host_sets.json"
    f.write_text(_json.dumps({"sets": {"fake": ["fake.test:443"]}}))
    yield f, srv.getsockname()
    stop.set()
    t.join(2)
    srv.close()


TUNNEL = r"""
import os, socket, sys
try:
    open("/proxy/planted", "w"); print("PROXY-WRITABLE")
except OSError:
    print("PROXY-RO")
print("SSL-PRIVATE-VISIBLE" if os.path.exists("/etc/ssl/private") else "SSL-PRIVATE-ABSENT")
print("SSL-CERTS-VISIBLE" if os.path.isdir("/etc/ssl/certs") else "SSL-CERTS-ABSENT")
host, port = os.environ["https_proxy"].rsplit(":", 1)
out = {}
for t in sys.argv[1:]:
    s = socket.create_connection(("127.0.0.1", int(port)), timeout=10)
    s.sendall(("CONNECT %s HTTP/1.1\r\nHost: %s\r\n\r\n" % (t, t)).encode())
    st = s.recv(256).split(b"\r\n", 1)[0].decode()
    if " 200 " in st:
        s.sendall(b"hello"); st += " echo=" + s.recv(64).decode()
    out[t] = st; s.close()
open("/work/result.txt", "w").write(repr(out)); print("TUNNEL", out)
"""


@pytest.mark.skipif(not LINUX, reason="needs Linux + bwrap")
def test_fetch_red_arm_passes_with_a_working_gate(tmp_path, fake_set):
    hs, up = fake_set
    r = box.run(tmp_path, ["true"], mode="fetch", host_set="fake", _host_sets_file=hs, _resolve=lambda t: up, _same_user_ok=True)
    assert r.outcome.kind == "FOUND", (r.outcome, r.red_arm)
    arm = r.red_arm
    assert arm["ok"] and arm["proxy_outside"] == "blocked" and arm["proxy_inside"] == "reached", arm
    refused = {e["target"] for e in arm["proxy_log"] if e["decision"].startswith("denied")}
    assert refused == set(box.OUTSIDE_PROBES), arm["proxy_log"]
    assert r.fetch_record["host_set"] == "fake" and len(r.fetch_record["host_sets_sha256"]) == 64


@pytest.mark.skipif(not LINUX, reason="needs Linux + bwrap")
def test_fetch_mode_tunnels_only_members_and_keeps_the_work_copy(tmp_path, fake_set):
    hs, up = fake_set
    src = tmp_path / "src"
    src.mkdir()
    (src / "tunnel.py").write_text(TUNNEL)
    keep = tmp_path / "kept"
    r = box.run(src, ["python3", "/src/tunnel.py", "fake.test:443", "api.github.com:443"], mode="fetch",
                host_set="fake", keep=keep, _host_sets_file=hs, _resolve=lambda t: up, _same_user_ok=True)
    assert r.outcome.kind == "FOUND" and r.returncode == 0, (r.outcome, r.stderr)
    assert "echo=hello" in r.stdout and "403" in r.stdout, r.stdout
    assert "PROXY-RO" in r.stdout and "SSL-PRIVATE-ABSENT" in r.stdout, r.stdout     # review F2, F3
    if Path("/etc/ssl/certs").is_dir():
        assert "SSL-CERTS-VISIBLE" in r.stdout, r.stdout
    assert r.fetch_record["dials"].count("fake.test:443") == 2      # red arm's one + the run's one
    assert sorted(r.fetch_record["overrides"]) == ["host_sets_file", "resolver", "same_user"]
    got = {(e["target"], e["decision"]) for e in r.fetch_record["connects"]}
    assert got == {("fake.test:443", "allowed"), ("api.github.com:443", "denied:not-in-set")}, got
    assert (keep / "result.txt").read_text().count("403") == 1


@pytest.mark.skipif(not LINUX, reason="needs Linux + bwrap")
def test_fetch_box_has_no_direct_network_and_no_dns(tmp_path, fake_set):
    hs, up = fake_set
    src = tmp_path / "src"
    src.mkdir()
    (src / "d.py").write_text(
        "import socket\n"
        "for a in [('1.1.1.1', 443), ('140.82.112.3', 443)]:\n"
        "    s = socket.socket(); s.settimeout(3)\n"
        "    try: s.connect(a); print('DIRECT-OPEN', a)\n"
        "    except OSError: print('DIRECT-BLOCKED', a)\n"
        "try: socket.getaddrinfo('github.com', 443); print('DNS-OPEN')\n"
        "except OSError: print('DNS-BLOCKED')\n")
    r = box.run(src, ["python3", "/src/d.py"], mode="fetch", host_set="fake", _host_sets_file=hs,
                _resolve=lambda t: up, _same_user_ok=True)
    assert r.returncode == 0, (r.outcome, r.stderr)
    assert "DIRECT-OPEN" not in r.stdout and r.stdout.count("DIRECT-BLOCKED") == 2 and "DNS-BLOCKED" in r.stdout


@pytest.mark.skipif(not LINUX, reason="needs Linux + bwrap")
def test_a_permissive_proxy_fails_the_fetch_red_arm(tmp_path, fake_set, monkeypatch):
    """Red arm: a gate that lets everything through must abort the run before argv runs."""
    hs, up = fake_set
    monkeypatch.setattr(fetchproxy, "normalise_target", lambda text: "fake.test:443")
    marker = tmp_path / "ran"
    r = box.run(tmp_path, ["python3", "-c", f"open({str(marker)!r}, 'w')"], mode="fetch", host_set="fake",
                _host_sets_file=hs, _resolve=lambda t: up, _same_user_ok=True)
    assert r.outcome.label().startswith("BLIND(box-red-arm: proxy_outside"), (r.outcome, r.red_arm)
    assert not marker.exists()


@pytest.mark.skipif(not LINUX, reason="needs Linux + bwrap")
def test_an_unreachable_inside_host_fails_the_fetch_red_arm(tmp_path, fake_set):
    """Without a reachable member the refusals are vacuous (the channel may be dead)."""
    hs, _ = fake_set
    dead = _socket.socket()
    dead.bind(("127.0.0.1", 0))
    addr = dead.getsockname()
    dead.close()
    r = box.run(tmp_path, ["true"], mode="fetch", host_set="fake", _host_sets_file=hs, _resolve=lambda t: addr, _same_user_ok=True)
    assert r.outcome.label() == "BLIND(box-red-arm: proxy_inside)", (r.outcome, r.red_arm)


@pytest.mark.skipif(not LINUX, reason="needs Linux + bwrap")
def test_an_existing_keep_folder_is_refused(tmp_path, fake_set):
    hs, up = fake_set
    r = box.run(tmp_path, ["true"], mode="fetch", host_set="fake", keep=tmp_path,
                _host_sets_file=hs, _resolve=lambda t: up, _same_user_ok=True)
    assert r.outcome.label().startswith("BLIND(keep-exists")


@pytest.mark.network
@pytest.mark.skipif(not LINUX, reason="needs Linux + bwrap")
@pytest.mark.skipif(not __import__("os").environ.get("FG_NETWORK_GO"), reason="needs FG_NETWORK_GO=1 (review F7)")
def test_live_fetch_red_arm_against_the_real_github_git_set(tmp_path):
    """THE REACH CHECK (the maintainer's go 2026-11-14, G1): one TCP connect to github.com:443 through the
    proxy, plus five refusals. Run only with -m network, on the deploy host."""
    r = box.run(tmp_path, ["true"], mode="fetch", host_set="github-git")
    assert r.outcome.kind == "FOUND", (r.outcome, r.red_arm)
    assert r.red_arm["proxy_inside"] == "reached" and r.red_arm["proxy_outside"] == "blocked", r.red_arm
    dialled = {e["target"] for e in r.fetch_record["red_arm_connects"] if e["decision"] == "allowed"}
    assert dialled == {"github.com:443"}



@pytest.mark.skipif(not LINUX or not box.box_user(), reason="needs Linux and a configured box user (the deploy host)")
def test_the_box_user_can_reach_the_proxy_socket(monkeypatch):
    """Live reach check 2026-11-14: a chmod by a non-member cleared the folder's setgid bit, so the
    socket came out in OUR group and the box user got EACCES. Connect as the box user itself."""
    import shutil as _sh
    fx = box._fetch_context("t", {"t": ["example.invalid:443"]},
                            resolve=lambda t: (_ for _ in ()).throw(OSError("no dial in this test")))
    try:
        code = ("import socket; s = socket.socket(socket.AF_UNIX); s.connect(%r); "
                "s.sendall(b'CONNECT nope.invalid:443 HTTP/1.1\\r\\n\\r\\n'); print(s.recv(64).decode())"
                % str(fx["proxy_dir"] / "p.sock"))
        r = subprocess.run(["sudo", "-n", "-u", box.box_user(), "--", sys.executable, "-c", code],
                           capture_output=True, text=True, timeout=30)
        assert r.returncode == 0 and " 403 " in r.stdout, (r.stdout, r.stderr[-300:])
    finally:
        fx["proxy"].stop()
        _sh.rmtree(fx["proxy_dir"], ignore_errors=True)
        _sh.rmtree(fx["fwd_dir"], ignore_errors=True)


@pytest.mark.skipif(not LINUX, reason="needs Linux + bwrap")
def test_fetch_without_a_box_user_is_refused(tmp_path, fake_set, monkeypatch):
    """Review F4 / §4a.6: fetch mode uses a throwaway user; no silent fallback to ours."""
    hs, up = fake_set
    monkeypatch.delenv(box.BOX_USER_KEY, raising=False)
    monkeypatch.setattr(box, "box_user", lambda: None)
    r = box.run(tmp_path, ["true"], mode="fetch", host_set="fake", _host_sets_file=hs, _resolve=lambda t: up)
    assert r.outcome.label() == f"BLIND(fetch-needs-box-user: set {box.BOX_USER_KEY})"


@pytest.mark.skipif(not LINUX, reason="needs Linux + bwrap")
def test_a_proxy_that_dials_outside_but_logs_denied_fails_the_red_arm(tmp_path, fake_set, monkeypatch):
    """Review F5: the proxy's own log is not the only witness; box.py counts every dial."""
    hs, up = fake_set

    class Everything(frozenset):
        def __contains__(self, _x):
            return True

    real_init, real_record = fetchproxy.Proxy.__init__, fetchproxy.Proxy._record

    def init(self, *a, **k):
        real_init(self, *a, **k)
        self.allowed = Everything()

    def record(self, **e):
        if e.get("target") != "fake.test:443" and e.get("decision") == "allowed":
            e["decision"] = "denied:not-in-set"      # the lie
        return real_record(self, **e)
    monkeypatch.setattr(fetchproxy.Proxy, "__init__", init)
    monkeypatch.setattr(fetchproxy.Proxy, "_record", record)
    r = box.run(tmp_path, ["true"], mode="fetch", host_set="fake", _host_sets_file=hs,
                _resolve=lambda t: up, _same_user_ok=True)
    assert r.outcome.label().startswith("BLIND(box-red-arm: proxy_outside"), (r.outcome, r.red_arm)
    assert r.red_arm["proxy_outside"].startswith("LEAKED:dialled:"), r.red_arm


@pytest.mark.skipif(not LINUX, reason="needs Linux + bwrap")
def test_keep_has_a_size_cap_and_leaves_nothing_when_refused(tmp_path, fake_set, monkeypatch):
    hs, up = fake_set
    monkeypatch.setattr(box, "KEEP_MAX_BYTES", 10)
    keep = tmp_path / "kept"
    r = box.run(tmp_path, ["python3", "-c", "open('/work/big','w').write('x'*100)"], mode="fetch",
                host_set="fake", keep=keep, _host_sets_file=hs, _resolve=lambda t: up, _same_user_ok=True)
    assert r.outcome.label() == "BLIND(keep-too-large)" and not keep.exists()
    assert not Path(str(keep) + ".tmp").exists()


@pytest.mark.skipif(not LINUX, reason="needs Linux + bwrap")
def test_a_proxy_that_logs_reached_without_dialling_fails_the_red_arm(tmp_path, fake_set, monkeypatch):
    """Mutation M6: the inside attempt counts only if box.py's own counter saw the dial."""
    hs, up = fake_set
    real_init = fetchproxy.Proxy.__init__

    def init(self, *a, **k):
        real_init(self, *a, **k)
        self.resolve = lambda t: up        # dials WITHOUT going through box.py's counter
    monkeypatch.setattr(fetchproxy.Proxy, "__init__", init)
    r = box.run(tmp_path, ["true"], mode="fetch", host_set="fake", _host_sets_file=hs,
                _resolve=lambda t: up, _same_user_ok=True)
    assert r.outcome.label() == "BLIND(box-red-arm: proxy_inside)", (r.outcome, r.red_arm)


@pytest.mark.skipif(not LINUX, reason="needs Linux + bwrap")
def test_the_listener_witness_catches_a_probe_that_lies(monkeypatch):
    """Mutation M16 / review F13: with the network shared and a probe that reaches our listener but
    REPORTS 'blocked', the red arm must still fail: our own listener saw the connection."""
    real = box._bwrap_argv

    def shared(*a, **k):
        argv = real(*a, **k)
        argv.insert(argv.index("--unshare-all") + 1, "--share-net")   # only valid AFTER --unshare-all
        return argv
    monkeypatch.setattr(box, "_bwrap_argv", shared)
    monkeypatch.setattr(box, "PROBE", box.PROBE.replace(
        'res["network"] = ("LEAKED:" + ",".join(hits)) if hits else "blocked"', 'res["network"] = "blocked"'))
    assert 'res["network"] = "blocked"' in box.PROBE
    arm = box.red_arm("run")
    assert arm["network"].startswith("LEAKED:witnessed:") and not arm["ok"], arm
