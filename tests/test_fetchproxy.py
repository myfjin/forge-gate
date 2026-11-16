"""Step 7 slice 1: fetch mode's network gate (design §4a.6 V42-1/V43-1, DECISIONS-7-github G1).
Pure Python (AF_UNIX + a local fake upstream): runs on every host, touches no network."""
from __future__ import annotations

import json
import socket
import threading

import pytest

from forge_gate.scout import box, fetchproxy


@pytest.fixture
def upstream():
    """A local echo server standing in for an allowed host."""
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


@pytest.fixture
def sockdir():
    """AF_UNIX paths are capped (104 bytes on macOS), and pytest's tmp_path is longer there.
    A short throwaway folder for the socket only, removed after the test."""
    import shutil
    import tempfile
    d = tempfile.mkdtemp(prefix="fgp-", dir="/tmp")
    yield __import__("pathlib").Path(d)
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def proxy(sockdir, upstream):
    calls = []

    def resolve(target):
        calls.append(target)
        return upstream
    p = fetchproxy.Proxy(sockdir / "p.sock", frozenset({"fake.test:443"}), resolve=resolve).start()
    p.calls = calls
    yield p
    p.stop()


def _tunnel(sock_path, target, payload=b"ping"):
    s = socket.socket(socket.AF_UNIX)
    s.settimeout(5)
    s.connect(str(sock_path))
    s.sendall(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n".encode())
    status = s.recv(256).split(b"\r\n", 1)[0]
    echo = b""
    if b" 200 " in status:
        s.sendall(payload)
        echo = s.recv(256)
    s.close()
    return status, echo


def test_the_tracked_host_sets_are_exactly_what_was_decided():
    """G1 (decided 2026-11-14): widening a set must be a diff here AND in this test."""
    sets, sha = fetchproxy.load_host_sets()
    assert sets == {"github-git": frozenset({"github.com:443"}),
                    "pypi": frozenset({"pypi.org:443", "files.pythonhosted.org:443"})}
    assert len(sha) == 64


@pytest.mark.parametrize("member", ["1.1.1.1:443", "*.github.com:443", "github.com", "GitHub.com:443",
                                    "github.com:0443", "github.com:70000", "[::1]:443", " github.com:443"])
def test_a_member_that_is_not_a_plain_dns_name_with_port_is_refused(tmp_path, member):
    f = tmp_path / "hs.json"
    f.write_text(json.dumps({"sets": {"x": [member]}}))
    with pytest.raises(fetchproxy.BadHostSet):
        fetchproxy.load_host_sets(f)


@pytest.mark.parametrize("body", [{"sets": {}}, {"sets": {"x": []}}, {"sets": {"x": ["a.b:1", "a.b:1"]}},
                                  {"sets": {"Bad Name": ["a.b:1"]}}, {"nosets": 1}])
def test_a_malformed_host_sets_file_is_refused(tmp_path, body):
    f = tmp_path / "hs.json"
    f.write_text(json.dumps(body))
    with pytest.raises(fetchproxy.BadHostSet):
        fetchproxy.load_host_sets(f)


def test_an_allowed_target_is_tunnelled_and_logged(proxy):
    status, echo = _tunnel(proxy.sock_path, "fake.test:443")
    assert status.startswith(b"HTTP/1.1 200") and echo == b"ping"
    proxy.stop()
    (e,) = proxy.log
    assert (e["target"], e["decision"], e["upstream"]) == ("fake.test:443", "allowed", "ok")
    assert e["up"] == 4 and e["down"] == 4 and proxy.calls == ["fake.test:443"]


@pytest.mark.parametrize("target", ["api.github.com:443", "fake.test:22", "127.0.0.1:443",
                                    "fake.test.evil.example:443", "[::1]:443", "nonsense"])
def test_a_target_outside_the_set_is_refused_and_never_dialled(proxy, target):
    status, _ = _tunnel(proxy.sock_path, target)
    assert status.startswith(b"HTTP/1.1 403")
    assert proxy.calls == []      # the resolver is never asked: nothing outside the set is dialled
    assert proxy.log[-1]["decision"] == "denied:not-in-set"


def test_case_and_a_trailing_dot_normalise_to_the_member(proxy):
    """'FAKE.TEST.:443' is the same host as 'fake.test:443' (DNS is case-blind, the dot is the root)."""
    status, echo = _tunnel(proxy.sock_path, "FAKE.TEST.:443")
    assert status.startswith(b"HTTP/1.1 200") and echo == b"ping"


def test_only_connect_is_spoken(proxy):
    s = socket.socket(socket.AF_UNIX)
    s.settimeout(5)
    s.connect(str(proxy.sock_path))
    s.sendall(b"GET http://fake.test:443/ HTTP/1.1\r\nHost: fake.test\r\n\r\n")
    assert s.recv(256).startswith(b"HTTP/1.1 405")
    s.close()
    assert proxy.calls == [] and proxy.log[-1]["decision"] == "denied:not-connect"


def test_an_unreachable_allowed_host_is_a_502_and_logged(sockdir):
    dead = socket.socket()
    dead.bind(("127.0.0.1", 0))
    addr = dead.getsockname()
    dead.close()                  # nothing listens there now
    with fetchproxy.Proxy(sockdir / "p.sock", frozenset({"fake.test:443"}), resolve=lambda t: addr) as p:
        assert fetchproxy.connect_via(p.sock_path, "fake.test:443") == 502
    assert p.log[-1]["upstream"].startswith("error:")


def test_the_byte_cap_cuts_a_runaway_tunnel(sockdir, upstream):
    with fetchproxy.Proxy(sockdir / "p.sock", frozenset({"fake.test:443"}), resolve=lambda t: upstream,
                          max_bytes=10) as p:
        status, echo = _tunnel(p.sock_path, "fake.test:443", payload=b"x" * 64)
    assert status.startswith(b"HTTP/1.1 200") and echo == b""
    assert p.log[-1].get("cut", "").startswith("max-bytes")


def test_the_socket_is_group_only(sockdir):
    import os
    import stat
    with fetchproxy.Proxy(sockdir / "p.sock", frozenset({"a.b:1"})) as p:
        assert stat.S_IMODE(os.stat(p.sock_path).st_mode) == 0o660


def test_a_socket_path_too_long_for_af_unix_is_a_clear_error(tmp_path):
    with pytest.raises(fetchproxy.SocketPathTooLong):
        fetchproxy.Proxy(tmp_path / ("x" * 120) / "p.sock", frozenset({"a.b:1"})).start()


def test_the_fetch_red_arm_tries_outside_hosts_of_every_kind():
    out = box.OUTSIDE_PROBES
    sets, _ = fetchproxy.load_host_sets()
    assert not set(out) & sets["github-git"]
    assert "api.github.com:443" in out            # same organisation, not in the set
    assert "github.com:22" in out                 # the member's host on another port
    assert any(t.split(":")[0].replace(".", "").isdigit() for t in out)   # an IP literal
    assert any(t.startswith("github.com.") for t in out)                  # a look-alike suffix


def test_fetch_mode_needs_a_known_host_set(tmp_path):
    assert box.run(tmp_path, ["true"], mode="fetch").outcome.label() == "BLIND(fetch-needs-host-set)"
    r = box.run(tmp_path, ["true"], mode="fetch", host_set="nope")
    assert r.outcome.label() == "BLIND(unknown-host-set: nope)"
    bad = tmp_path / "hs.json"
    bad.write_text('{"sets": {"x": ["1.2.3.4:80"]}}')
    r = box.run(tmp_path, ["true"], mode="fetch", host_set="x", _host_sets_file=bad)
    assert r.outcome.label().startswith("BLIND(host-sets-unreadable")


def test_run_mode_takes_no_host_set(tmp_path):
    r = box.run(tmp_path, ["true"], host_set="github-git")
    assert r.outcome.label() == "BLIND(run-mode-takes-no-host-set-or-keep)"



def test_an_oversized_header_is_refused_and_logged(proxy):
    s = socket.socket(socket.AF_UNIX)
    s.settimeout(5)
    s.connect(str(proxy.sock_path))
    s.sendall(b"CONNECT fake.test:443 HTTP/1.1\r\nX: " + b"a" * (fetchproxy.HEADER_MAX + 10))
    assert s.recv(256).startswith(b"HTTP/1.1 431")
    s.close()
    assert proxy.calls == [] and proxy.log[-1]["decision"] == "denied:header-too-long"


def test_a_slow_header_hits_the_deadline(sockdir, upstream, monkeypatch):
    monkeypatch.setattr(fetchproxy, "HEADER_DEADLINE_S", 0.5)
    with fetchproxy.Proxy(sockdir / "p.sock", frozenset({"fake.test:443"}), resolve=lambda t: upstream) as p:
        s = socket.socket(socket.AF_UNIX)
        s.settimeout(5)
        s.connect(str(p.sock_path))
        s.sendall(b"CONNECT fake.test:443 HTTP/1.1\r\n")    # never finishes the header
        assert s.recv(256) == b""                           # closed by the proxy
        s.close()
    assert p.log[-1]["decision"] == "denied:header-deadline"


def test_connections_beyond_the_cap_get_503(sockdir, upstream, monkeypatch):
    monkeypatch.setattr(fetchproxy, "MAX_CONNECTIONS", 1)
    with fetchproxy.Proxy(sockdir / "p.sock", frozenset({"fake.test:443"}), resolve=lambda t: upstream) as p:
        a = socket.socket(socket.AF_UNIX)
        a.settimeout(5)
        a.connect(str(p.sock_path))
        a.sendall(b"CONNECT fake.test:443 HTTP/1.1\r\n\r\n")
        assert a.recv(256).startswith(b"HTTP/1.1 200")      # holds the only slot
        assert fetchproxy.connect_via(p.sock_path, "fake.test:443") == 503
        a.close()
    assert any(e["decision"] == "denied:too-many-connections" for e in p.log)


def test_over_the_cap_the_503_waits_for_the_request(sockdir, upstream, monkeypatch):
    """A client that sends its CONNECT a little after connecting still reads the 503. The old
    path replied and closed at once, so this client's send failed with EPIPE (red arm)."""
    import time
    monkeypatch.setattr(fetchproxy, "MAX_CONNECTIONS", 1)
    monkeypatch.setattr(fetchproxy, "REFUSE_READ_S", 5)    # review F2: a slow host must not fake EPIPE
    with fetchproxy.Proxy(sockdir / "p.sock", frozenset({"fake.test:443"}), resolve=lambda t: upstream) as p:
        a = socket.socket(socket.AF_UNIX)
        a.settimeout(5)
        a.connect(str(p.sock_path))
        a.sendall(b"CONNECT fake.test:443 HTTP/1.1\r\n\r\n")
        assert a.recv(256).startswith(b"HTTP/1.1 200")      # holds the only slot
        for _ in range(3):
            s = socket.socket(socket.AF_UNIX)
            s.settimeout(5)
            s.connect(str(p.sock_path))
            time.sleep(0.1)                                   # < REFUSE_READ_S
            s.sendall(b"CONNECT fake.test:443 HTTP/1.1\r\n\r\n")
            assert s.recv(256).startswith(b"HTTP/1.1 503")
            s.close()
        a.close()


def test_over_the_cap_a_silent_client_still_gets_503_within_the_bound(sockdir, upstream, monkeypatch):
    import time
    monkeypatch.setattr(fetchproxy, "MAX_CONNECTIONS", 1)
    with fetchproxy.Proxy(sockdir / "p.sock", frozenset({"fake.test:443"}), resolve=lambda t: upstream) as p:
        a = socket.socket(socket.AF_UNIX)
        a.settimeout(5)
        a.connect(str(p.sock_path))
        a.sendall(b"CONNECT fake.test:443 HTTP/1.1\r\n\r\n")
        assert a.recv(256).startswith(b"HTTP/1.1 200")
        s = socket.socket(socket.AF_UNIX)
        s.settimeout(5)
        s.connect(str(p.sock_path))
        t0 = time.monotonic()
        assert s.recv(256).startswith(b"HTTP/1.1 503")         # never sent anything
        assert time.monotonic() - t0 < fetchproxy.REFUSE_READ_S + 1.0
        s.close()
        a.close()


@pytest.mark.parametrize("target", ["fake.test:4²43", "fake.test:٣٤٣"])
def test_a_non_ascii_digit_port_is_refused_and_logged(proxy, target):
    assert fetchproxy.connect_via(proxy.sock_path, target) in (403, 400)
    assert proxy.calls == [] and proxy.log and proxy.log[-1]["decision"].startswith("denied")


def test_the_dialled_address_is_logged(proxy):
    _tunnel(proxy.sock_path, "fake.test:443")
    proxy.stop()
    assert proxy.log[-1]["peer"].startswith("127.0.0.1:")


def test_a_latin1_superscript_port_is_refused_with_a_status(proxy):
    """Mutation M14 survived the UTF-8 version: the proxy decodes latin-1, so '\\xb2' arrives as a
    char that str.isdigit() accepts and int() rejects. Raw bytes reach the guard."""
    s = socket.socket(socket.AF_UNIX)
    s.settimeout(5)
    s.connect(str(proxy.sock_path))
    s.sendall(b"CONNECT fake.test:4\xb23 HTTP/1.1\r\n\r\n")
    assert s.recv(256).startswith(b"HTTP/1.1 403")
    s.close()
    assert proxy.calls == [] and proxy.log[-1]["decision"] == "denied:not-in-set"
