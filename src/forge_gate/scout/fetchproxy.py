"""Fetch mode's network gate (design v1.4.5 §4a.6, V42-1 / V43-1; build step 7 slice 1).

The box in fetch mode keeps bubblewrap's fresh network namespace (only its own loopback, no
route out, no resolver). Its ONLY way out is a unix socket bound into the box, served by
:class:`Proxy` running OUTSIDE the box as the calling user. The proxy speaks HTTP ``CONNECT``
only and opens an upstream connection only when ``host:port`` is a member of the run's named
host set. It resolves names itself, so the box never needs DNS, and it logs every request,
allowed or denied: that log is the fetch record's witness, kept outside the box.

No root and no firewall rules are needed (this replaces KD-8 (a)'s assumption).

Named host sets live in ``forge_gate/data/host_sets.json``: tracked, so widening a set is a
diff in git (V43-1), and its sha256 goes into each fetch record. Members are lower-case DNS
names with an explicit port. IP literals and wildcards are refused when the file is loaded.

What the gate binds (step-7 crew review, 6E F8): it checks the NAME. The proxy resolves it and
logs the address it reached (``peer``) but does not check that address; what ties the name to
GitHub is the TLS certificate check made by git inside the box.

Idle tunnels (6E F7, accepted, not fixed): each direction of a tunnel has its own 300 s idle limit
and either one ending closes both, so 16 idle tunnels hold every slot and later CONNECTs get 503
until one ends. The socket is mode 0660: the box's git is the intended client, but the owner and
the box group can connect too. The effect is a stalled fetch, not a way out. The same limit also
cuts a real fetch whose client sends nothing upstream for 300 s (a long pack download); whether a
GitHub fetch on this link ever does is not measured. Any change to the limit waits for a measured
fetch.

Decided by the maintainer 2026-11-14 (DECISIONS-7-github G1): ``github-git`` = {github.com:443}; ``pypi``
is configured but no step-7 fetch uses it.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import threading
import time
from pathlib import Path

HOST_SETS_FILE = Path(__file__).resolve().parent.parent / "data" / "host_sets.json"
MAX_BYTES = 300 * 1024 * 1024     # per proxy lifetime, both directions; a runaway fetch is cut
HEADER_MAX = 8192
HEADER_DEADLINE_S = 10            # the whole request header must arrive within this (review F6)
MAX_CONNECTIONS = 16              # concurrent tunnels; more get a 503 (review F6: threads run as us)
REFUSE_READ_S = 0.5               # over the cap: read the request (at most this long) before the 503
MAX_LOG = 10000                   # log entries kept; the rest are counted, not stored
_NAME = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$")
_IPV4 = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")

# Runs INSIDE the box (python3 from the host's /usr, read-only). It listens on the box's own
# loopback and joins each connection to the proxy's unix socket, then runs the command with
# the proxy variables set. It decides nothing: every decision is the outside proxy's.
FORWARDER = r'''
import os, socket, subprocess, sys, threading
sock_path, port = sys.argv[1], int(sys.argv[2])
assert sys.argv[3] == "--", sys.argv
cmd = sys.argv[4:]
ls = socket.socket()
ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
ls.bind(("127.0.0.1", port))
ls.listen(32)
def pump(a, b):
    try:
        while True:
            d = a.recv(65536)
            if not d:
                break
            b.sendall(d)
    except OSError:
        pass
    finally:
        for s in (a, b):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
def serve():
    while True:
        c, _ = ls.accept()
        u = socket.socket(socket.AF_UNIX)
        try:
            u.connect(sock_path)
        except OSError:
            c.close(); u.close(); continue
        threading.Thread(target=pump, args=(c, u), daemon=True).start()
        threading.Thread(target=pump, args=(u, c), daemon=True).start()
threading.Thread(target=serve, daemon=True).start()
url = "http://127.0.0.1:%d" % port
env = dict(os.environ, https_proxy=url, http_proxy=url, HTTPS_PROXY=url, HTTP_PROXY=url,
           ALL_PROXY=url, all_proxy=url, no_proxy="", NO_PROXY="")
sys.exit(subprocess.call(cmd, env=env))
'''
FORWARD_PORT = 3128   # inside the box's own network namespace: nothing else listens there


SOCK_PATH_MAX = 100   # AF_UNIX sun_path: 104 bytes on macOS, 108 on Linux, minus the terminator


class SocketPathTooLong(OSError):
    """The proxy's socket path does not fit AF_UNIX; the box root is too deep."""


class BadHostSet(ValueError):
    """host_sets.json is malformed or names something that is not a plain host:port."""


def _member(text: str) -> str:
    if not isinstance(text, str) or text != text.strip().lower():
        raise BadHostSet(f"member must be lower-case without spaces: {text!r}")
    host, sep, port = text.rpartition(":")
    if not sep or not port.isdigit() or not 1 <= int(port) <= 65535 or str(int(port)) != port:
        raise BadHostSet(f"member needs an explicit port: {text!r}")
    if _IPV4.match(host) or ":" in host or "*" in host or not _NAME.match(host):
        raise BadHostSet(f"member must be a DNS name, not an IP or a wildcard: {text!r}")
    return text


def load_host_sets(path: Path = HOST_SETS_FILE) -> tuple[dict[str, frozenset[str]], str]:
    """({set name: frozenset of "host:port"}, sha256 of the file's bytes)."""
    raw = Path(path).read_bytes()
    data = json.loads(raw)
    if not isinstance(data, dict) or not isinstance(data.get("sets"), dict) or not data["sets"]:
        raise BadHostSet(f"{path}: expected {{\"sets\": {{name: [host:port, ...]}}}}")
    sets = {}
    for name, members in data["sets"].items():
        if not re.match(r"^[a-z0-9][a-z0-9-]*$", name) or not isinstance(members, list) or not members:
            raise BadHostSet(f"{path}: bad set {name!r}")
        sets[name] = frozenset(_member(m) for m in members)
        if len(sets[name]) != len(members):
            raise BadHostSet(f"{path}: duplicate member in {name!r}")
    return sets, hashlib.sha256(raw).hexdigest()


def normalise_target(text: str) -> str | None:
    """'Host.Example.:443' -> 'host.example:443'; None when it is not a host:port at all."""
    host, sep, port = text.strip().rpartition(":")
    if not sep or not (port.isascii() and port.isdigit()):   # review F8: '²' is a digit to isdigit()
        return None
    host = host.lower().rstrip(".")
    if host.startswith("[") or not host:
        return f"{host}:{port}"   # an IPv6 literal: never a member (members are DNS names)
    return f"{host}:{int(port)}"


class Proxy:
    """An allowlisting CONNECT proxy on a unix socket. ``resolve`` maps an ALLOWED "host:port" to
    the (address, port) actually dialled; tests point it at a local fake upstream. It is never
    consulted for a denied target, so a denied target is never dialled."""

    def __init__(self, sock_path: Path, allowed: frozenset[str], *, resolve=None,
                 max_bytes: int = MAX_BYTES, connect_timeout: float = 20):
        self.sock_path = Path(sock_path)
        self.allowed = frozenset(allowed)
        self.resolve = resolve or (lambda target: (target.rpartition(":")[0], int(target.rpartition(":")[2])))
        self.max_bytes = max_bytes
        self.connect_timeout = connect_timeout
        self.log: list[dict] = []
        self.dropped = 0
        self.phase = "run"
        self._slots = threading.BoundedSemaphore(MAX_CONNECTIONS)
        self._bytes = 0
        self._lock = threading.Lock()
        self._srv: socket.socket | None = None
        self._threads: list[threading.Thread] = []
        self._stop = threading.Event()

    # -- lifecycle -------------------------------------------------------------------------
    def start(self, mode: int = 0o660) -> "Proxy":
        if len(os.fsencode(str(self.sock_path))) > SOCK_PATH_MAX:
            raise SocketPathTooLong(f"{self.sock_path}: longer than {SOCK_PATH_MAX} bytes (AF_UNIX)")
        srv = socket.socket(socket.AF_UNIX)
        srv.bind(str(self.sock_path))
        os.chmod(self.sock_path, mode)   # the box user's group may connect; nobody else
        srv.listen(32)
        srv.settimeout(0.2)
        self._srv = srv
        t = threading.Thread(target=self._accept_loop, daemon=True)
        t.start()
        self._threads.append(t)
        return self

    def stop(self) -> list[dict]:
        self._stop.set()
        for t in list(self._threads):
            t.join(timeout=5)
        if self._srv is not None:
            self._srv.close()
        try:
            self.sock_path.unlink()
        except FileNotFoundError:
            pass
        return self.log

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()

    # -- serving ---------------------------------------------------------------------------
    def _record(self, **entry) -> dict:
        entry = {"t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "phase": self.phase, **entry}
        with self._lock:
            if len(self.log) < MAX_LOG:
                self.log.append(entry)
            else:
                self.dropped += 1
        return entry

    def _accept_loop(self):
        while not self._stop.is_set():
            try:
                c, _ = self._srv.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            if not self._slots.acquire(blocking=False):
                self._record(target=None, decision="denied:too-many-connections", upstream=None)
                self._refuse(c, b"HTTP/1.1 503 Service Unavailable\r\n\r\n")
                continue
            try:
                t = threading.Thread(target=self._guarded, args=(c,), daemon=True)
                t.start()
            except RuntimeError:          # cannot start a thread: refuse, logged, keep accepting
                self._slots.release()
                self._record(target=None, decision="denied:no-thread", upstream=None)
                c.close()
                continue
            self._threads = [x for x in self._threads if x.is_alive()] + [t]

    def _refuse(self, c: socket.socket, reply: bytes):
        """Reply to a client the proxy will not serve, AFTER reading its request. Replying and
        closing first let the client's own CONNECT hit a closed socket (EPIPE), so it never read
        the reply (seen 2026-11-15: 11/200 on macOS). The read is bounded by REFUSE_READ_S in
        total, so a silent or trickling client stalls the accept loop for at most that long."""
        deadline = time.monotonic() + REFUSE_READ_S
        buf = b""
        try:
            while b"\r\n\r\n" not in buf and len(buf) <= HEADER_MAX:
                left = deadline - time.monotonic()
                if left <= 0:
                    break
                c.settimeout(left)
                d = c.recv(4096)
                if not d:
                    break
                buf += d
        except OSError:
            pass
        try:
            c.sendall(reply)
        except OSError:
            pass
        c.close()

    def _guarded(self, c: socket.socket):
        try:
            self._handle(c)
        finally:
            self._slots.release()

    def _handle(self, c: socket.socket):
        deadline = time.monotonic() + HEADER_DEADLINE_S
        try:
            buf = b""
            while b"\r\n\r\n" not in buf:
                left = deadline - time.monotonic()
                if left <= 0:
                    self._record(target=None, decision="denied:header-deadline", upstream=None)
                    return
                c.settimeout(left)
                d = c.recv(4096)
                if not d:
                    self._record(target=None, decision="denied:malformed", upstream=None)
                    return
                buf += d
                if len(buf) > HEADER_MAX:
                    self._record(target=None, decision="denied:header-too-long", upstream=None)
                    c.sendall(b"HTTP/1.1 431 Request Header Fields Too Large\r\n\r\n")
                    return
            head, rest = buf.split(b"\r\n\r\n", 1)
            line = head.split(b"\r\n", 1)[0].decode("latin-1")
            parts = line.split(" ")
            if len(parts) != 3 or not parts[2].startswith("HTTP/1."):
                self._record(target=None, decision="denied:malformed", upstream=None, line=line[:200])
                c.sendall(b"HTTP/1.1 400 Bad Request\r\n\r\n")
                return
            method, raw_target = parts[0], parts[1]
            if method != "CONNECT":
                self._record(target=raw_target[:200], decision="denied:not-connect", upstream=None)
                c.sendall(b"HTTP/1.1 405 Method Not Allowed\r\n\r\n")
                return
            target = normalise_target(raw_target)
            if target is None or target not in self.allowed:
                self._record(target=target or raw_target[:200], decision="denied:not-in-set", upstream=None)
                c.sendall(b"HTTP/1.1 403 Forbidden\r\n\r\n")
                return
            try:
                up = socket.create_connection(self.resolve(target), timeout=self.connect_timeout)
            except OSError as e:
                self._record(target=target, decision="allowed", upstream=f"error:{type(e).__name__}")
                c.sendall(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
                return
            try:
                peer = "%s:%s" % up.getpeername()[:2]     # review F9: which address was reached
            except OSError:
                peer = "?"
            entry = self._record(target=target, decision="allowed", upstream="ok", peer=peer, up=0, down=0)
            c.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
            if rest:
                up.sendall(rest)
                entry["up"] += len(rest)
            self._splice(c, up, entry)
        except socket.timeout:
            self._record(target=None, decision="denied:header-deadline", upstream=None)
        except Exception as e:      # review F8: every request is logged, whatever breaks
            self._record(target=None, decision=f"denied:malformed:{type(e).__name__}", upstream=None)
        finally:
            c.close()

    def _splice(self, c: socket.socket, up: socket.socket, entry: dict):
        def pump(a, b, key):
            try:
                a.settimeout(300)
                while not self._stop.is_set():
                    d = a.recv(65536)
                    if not d:
                        break
                    with self._lock:
                        self._bytes += len(d)
                        over = self._bytes > self.max_bytes
                    if over:
                        entry["cut"] = f"max-bytes {self.max_bytes}"
                        break
                    b.sendall(d)
                    entry[key] += len(d)
            except OSError:
                pass
            finally:
                for s in (a, b):
                    try:
                        s.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
        t = threading.Thread(target=pump, args=(up, c, "down"), daemon=True)
        t.start()
        pump(c, up, "up")
        t.join(timeout=310)
        up.close()


def connect_via(sock_path: Path, target: str, timeout: float = 10) -> int | None:
    """Send one CONNECT through the proxy's unix socket; the status code, or None. Test helper and
    the shape of what the in-box probe does through its forwarder."""
    s = socket.socket(socket.AF_UNIX)
    s.settimeout(timeout)
    try:
        s.connect(str(sock_path))
        s.sendall(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n".encode())
        line = s.recv(256).split(b"\r\n", 1)[0].decode("latin-1")
        return int(line.split(" ")[1]) if line.startswith("HTTP/1.") else None
    except (OSError, ValueError, IndexError):
        return None
    finally:
        s.close()
