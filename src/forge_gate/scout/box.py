"""The isolation box (design v1.4.5 §4a.6, build step 4): where scout runs pool code.

RUN mode (built): bubblewrap with every namespace unshared, so network off (a fresh netns
has only its own loopback), no home folder and no secrets file visible (only /usr, /lib*,
/bin, /sbin and a few /etc files are mounted, read-only), the source mounted read-only at
/src, a throwaway copy at /work, an empty environment, uid 65534 inside the namespace, its
own process tree, killed with its parent. If FORGE_GATE_BOX_USER is set, bubblewrap itself
runs as that unprivileged user (sudo -n -u), so an escape lands as that user, not ours.

FETCH mode (built in step 7, slice 1): the same box, still in a fresh network namespace, plus
one way out: a unix socket bound at /proxy, served OUTSIDE the box by
:class:`fetchproxy.Proxy`, which opens a connection only to a member of the run's NAMED HOST
SET (V43-1; ``forge_gate/data/host_sets.json``) and logs every request. A forwarder inside the
box carries loopback traffic to that socket and runs the command with the proxy variables set.
No root, no firewall rules. Its red arm adds two attempts to the three below: a CONNECT to
hosts OUTSIDE the set must be refused (the proxy's own log is the witness), and a CONNECT to a
host INSIDE the set must succeed (otherwise the refusals prove nothing).

Every run first runs the RED ARM in its mode (V4-5): a planted probe tries to read a planted
canary secret, write a planted canary target outside its copy, and open the network. All
three must fail, judged from OUTSIDE (the secret's bytes absent from the output, the write
target unchanged), not from the probe's own word. If any succeeds, the run aborts as
BLIND(box-red-arm: ...). There is no exempt path.

Copies carry file DATA only (no modes, no ACLs; symlinks skipped and named in the result). So
nothing in the box is executable by path: callers run ``[interpreter, "/src/file"]``, never
``./file`` (6E BU-2, 2026-11-11).
"""
from __future__ import annotations

import json
import os
import resource
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .. import config
from . import fetchproxy
from .outcomes import Outcome, blind, clean, found

BOX_USER_KEY = "FORGE_GATE_BOX_USER"
# Where the box's copies live when a box user is set: a folder that user can reach (it cannot
# reach our home, by design). Deploy host: /var/lib/forge-gate-box, owner au, group fgbox, 2770.
BOX_ROOT_KEY = "FORGE_GATE_BOX_ROOT"
_ETC = ("ld.so.cache", "ld.so.conf", "ld.so.conf.d", "alternatives", "localtime", "nsswitch.conf")
_SYS = ("/usr", "/bin", "/sbin", "/lib", "/lib64", "/lib32")
MEMORY_MB = 1024   # address-space cap for the box and everything in it (same mechanism as runner.run_file)
FETCH_MEMORY_MB = 3072   # git's index-pack maps the pack; 1 GiB is too tight for a cpython-sized fetch [guess]
# TLS trust roots only, read-only (fetch mode). Never all of /etc/ssl or /etc/pki: they hold
# private/ folders (review F3).
FETCH_ETC = ("ssl/certs", "ca-certificates", "pki/tls/certs", "pki/ca-trust")
KEEP_MAX_BYTES = 1024 * 1024 * 1024   # review F11: what fetch mode may carry out of the box
# Fetch red arm: hosts that must be refused. Same organisation, another port, an IP literal, a
# look-alike suffix, an unrelated host. Members of the run's set are removed from this list.
OUTSIDE_PROBES = ("api.github.com:443", "github.com:22", "1.1.1.1:443",
                  "github.com.evil.example:443", "example.com:443")

PROBE = r'''
import json, socket, sys
res = {}
try:
    res["secret"] = "LEAKED:" + open(sys.argv[1]).read()
except Exception as e:
    res["secret"] = "blocked:" + type(e).__name__
try:
    with open(sys.argv[2], "w") as fh:
        fh.write("written-from-inside")
    res["write"] = "LEAKED"
except Exception as e:
    res["write"] = "blocked:" + type(e).__name__
hits = []
targets = [("1.1.1.1", 443), ("8.8.8.8", 53), ("127.0.0.1", 22)]
if len(sys.argv) > 3:
    targets.insert(0, ("127.0.0.1", int(sys.argv[3])))  # the parent's own listener: the control
for host, port in targets:
    s = socket.socket()
    s.settimeout(2)
    try:
        s.connect((host, port))
        hits.append(f"{host}:{port}")
    except Exception:
        pass
    finally:
        s.close()
res["network"] = ("LEAKED:" + ",".join(hits)) if hits else "blocked"
if len(sys.argv) > 4:   # fetch mode: CONNECT attempts through the forwarder (the proxy judges them)
    said = {}
    for t in json.loads(sys.argv[4]):
        s = socket.socket()
        s.settimeout(25)
        try:
            s.connect(("127.0.0.1", int(sys.argv[5])))
            s.sendall(("CONNECT %s HTTP/1.1\r\nHost: %s\r\n\r\n" % (t, t)).encode())
            said[t] = s.recv(256).split(b"\r\n", 1)[0].decode("latin-1")
        except Exception as e:
            said[t] = "error:" + type(e).__name__
        finally:
            s.close()
    res["connect"] = said
print("PROBE " + json.dumps(res))
'''


@dataclass
class BoxResult:
    outcome: Outcome                   # FOUND = the command ran; BLIND(reason) = it did not
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""
    red_arm: dict = field(default_factory=dict)
    skipped_links: list = field(default_factory=list)  # 6E BU-1: left out of the copies, named
    fetch_record: dict = field(default_factory=dict)   # fetch mode: set, its file hash, every CONNECT


def box_user() -> str | None:
    return config.configured(BOX_USER_KEY)


def box_root() -> Path:
    r = config.configured(BOX_ROOT_KEY)
    if r:
        return Path(r)
    if box_user():
        raise RuntimeError(f"{BOX_USER_KEY} is set but {BOX_ROOT_KEY} is not: the box user cannot "
                           "reach our scratch folder")
    return config.scratch_dir() / "box"


def available() -> str | None:
    """None when the box can run here, else the reason it cannot."""
    if sys.platform != "linux":
        return f"box-unavailable: {sys.platform} (bubblewrap needs Linux)"
    if shutil.which("bwrap") is None:
        return "box-unavailable: bwrap not installed"
    try:
        root = box_root()
    except RuntimeError as e:
        return f"box-unavailable: {e}"
    probe = root if root.exists() else root.parent
    if not os.access(probe, os.W_OK | os.X_OK):   # e.g. another user's box root
        return f"box-unavailable: cannot write the box root {root} as {os.environ.get('USER', '?')}"
    user = box_user()
    if user:
        r = subprocess.run(["sudo", "-n", "-u", user, "--", "true"], capture_output=True, text=True)
        if r.returncode != 0:   # e.g. a reviewer who may not act as the box user
            return f"box-unavailable: cannot run as box user {user}: {r.stderr.strip()[:200]}"
    return None


def start_failure() -> str | None:
    """None when bubblewrap can start a namespace here, else its stderr (B4-2: e.g. unprivileged
    user namespaces disabled). Used to tell 'the box cannot start' from 'the box leaked'."""
    try:
        r = subprocess.run(["bwrap", "--unshare-all", "--ro-bind", "/", "/", "true"],
                           capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"{type(e).__name__}: {e}"
    return None if r.returncode == 0 else (r.stderr.strip()[-300:] or f"exit {r.returncode}")


def _bwrap_argv(src: Path, work: Path, argv: list[str], timeout: float = 60, ref: Path | None = None,
                *, binds=(), ro_binds=(), etc_extra=(), memory_mb: int = MEMORY_MB) -> list[str]:
    a = ["bwrap", "--unshare-all", "--die-with-parent", "--new-session", "--clearenv",
         "--uid", "65534", "--gid", "65534",
         "--setenv", "PATH", "/usr/bin:/bin", "--setenv", "HOME", "/work",
         "--setenv", "LANG", "C.UTF-8", "--setenv", "PYTHONDONTWRITEBYTECODE", "1"]
    for d in _SYS:
        if Path(d).exists():
            a += ["--ro-bind", d, d]
    for e in tuple(_ETC) + tuple(etc_extra):
        if Path("/etc", e).exists():
            a += ["--ro-bind", f"/etc/{e}", f"/etc/{e}"]   # bwrap creates the parent folders empty
    a += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
          "--ro-bind", str(src), "/src"]
    if ref is not None:   # a second, read-only tree (6E 6a pre-note: what we compare against is never writable)
        a += ["--ro-bind", str(ref), "/ref"]
    for host_path, inside in ro_binds:
        a += ["--ro-bind", str(host_path), inside]
    for host_path, inside in binds:
        a += ["--bind", str(host_path), inside]
    a += ["--bind", str(work), "/work", "--chdir", "/work", "--"]
    a += argv
    user = box_user()
    if not user:
        return a   # limits are set by _exec's preexec_fn
    # sudo resets resource limits (PAM), so a parent's setrlimit does not reach the box user:
    # set them after sudo, on the command that starts bubblewrap (measured 2026-11-11).
    cap = memory_mb * 1024 * 1024
    return ["sudo", "-n", "-u", user, "--", "prlimit", f"--as={cap}", f"--cpu={int(timeout) + 1}",
            "--"] + a


def _plain_copy(src: Path, dst: Path) -> list[str]:
    """Copy file DATA only into the box's copies. Not shutil.copytree: that copies metadata
    (a source folder's ACL then overrides the box user's group access) and, by default,
    follows symlinks (a link to a host file would carry that file's bytes into the box).
    Symlinks are skipped and returned, so the caller can see what was left out."""
    if not Path(src).is_dir():
        raise FileNotFoundError(f"box source is not a folder: {src}")
    skipped = []
    dst.mkdir()
    for dirpath, dirnames, filenames in os.walk(src, followlinks=False):
        rel = Path(dirpath).relative_to(src)
        for d in list(dirnames):
            if (Path(dirpath) / d).is_symlink():
                skipped.append(str(rel / d)); dirnames.remove(d)
            else:
                (dst / rel / d).mkdir()
        for f in filenames:
            sp = Path(dirpath) / f
            if sp.is_symlink() or not sp.is_file():
                skipped.append(str(rel / f)); continue
            shutil.copyfile(sp, dst / rel / f, follow_symlinks=False)
    return skipped


def _exec(src: Path, argv: list[str], timeout: float, *, unboxed: bool = False, ref: Path | None = None,
          binds=(), ro_binds=(), etc_extra=(), memory_mb: int = MEMORY_MB, keep: Path | None = None):
    """``keep``: after a run that exits 0, the work copy's DATA is copied there (fetch mode's
    result); it must not exist yet. Symlinks are skipped, as for every copy."""
    user = None if unboxed else box_user()
    root = box_root() if not unboxed else config.scratch_dir() / "box"
    root.mkdir(parents=True, exist_ok=True)
    run_dir = Path(tempfile.mkdtemp(prefix="run-", dir=root))
    work, src_copy = run_dir / "work", run_dir / "src"
    try:
        skipped = _plain_copy(src, work)
        _plain_copy(src, src_copy)   # /src is bound from this copy: the box user can reach it
        ref_copy = None
        if ref is not None:
            ref_copy = run_dir / "ref"
            skipped += ["ref/" + x for x in _plain_copy(ref, ref_copy)]
        if user:  # group (the box user's) may read the source copy and write the work copy
            trees = [(src_copy, 0o2750, 0o640), (work, 0o2770, 0o660)]
            if ref_copy is not None:
                trees.append((ref_copy, 0o2750, 0o640))
            for top, dmode, fmode in trees:
                for dirpath, _dirs, files in os.walk(top):
                    os.chmod(dirpath, dmode)
                    for f in files:
                        os.chmod(Path(dirpath) / f, fmode)
            os.chmod(run_dir, 0o2750)
        full = argv if unboxed else _bwrap_argv(src_copy, work, argv, timeout, ref_copy, binds=binds,
                                                ro_binds=ro_binds, etc_extra=etc_extra, memory_mb=memory_mb)

        def _limits():  # inherited by bwrap and everything it starts
            cap = memory_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (cap, cap))
            cpu = int(timeout) + 1
            resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))

        r = subprocess.run(full, capture_output=True, text=True, timeout=timeout,
                           cwd=work if unboxed else run_dir, start_new_session=True,
                           preexec_fn=None if unboxed else _limits)
        if keep is not None and r.returncode == 0:
            if user:   # what the box user wrote must be readable by us before we copy it
                subprocess.run(["sudo", "-n", "-u", user, "chmod", "-R", "g+rwX", str(work)],
                               capture_output=True, timeout=60)
            size = sum(p.stat().st_size for p in work.rglob("*") if p.is_file() and not p.is_symlink())
            if size > KEEP_MAX_BYTES:
                return r.returncode, r.stdout, r.stderr + f"\nkeep-too-large: {size} bytes", skipped + ["KEEP-REFUSED"]
            tmp_keep = Path(str(keep) + ".tmp")   # review F11: atomic, no half-filled keep
            try:
                skipped += ["kept/" + x for x in _plain_copy(work, tmp_keep)]
                os.replace(tmp_keep, keep)
            finally:
                shutil.rmtree(tmp_keep, ignore_errors=True)
        return r.returncode, r.stdout, r.stderr, skipped
    except subprocess.TimeoutExpired:
        return None, "", f"timeout after {timeout}s", skipped
    finally:
        if user:  # files the box user wrote: let us remove them (throwaway copy, created above)
            subprocess.run(["sudo", "-n", "-u", user, "chmod", "-R", "g+rwX", str(work)],
                           capture_output=True, timeout=30)
        shutil.rmtree(run_dir, ignore_errors=True)  # throwaway copies, created above


def red_arm(mode: str = "run", *, unboxed: bool = False, fetch: dict | None = None) -> dict:
    """Plant a canary secret and a canary write target, run the probe, judge from outside.
    Returns {"secret","write","network": "blocked"|"LEAKED…", "ok": bool}. ``unboxed=True``
    runs the same probe with no box: every attempt must then LEAK, which proves the targets
    are real (a red arm whose targets cannot be reached passes vacuously).

    Fetch mode (``fetch`` = the context :func:`run` builds: proxy, its folders, the set): the
    probe runs behind the in-box forwarder and also sends CONNECTs. "proxy_outside" is blocked
    only if the PROXY'S LOG shows every outside target refused and none dialled; "proxy_inside"
    is reached only if the log shows the inside target dialled with upstream ok."""
    if mode not in ("run", "fetch") or (mode == "fetch") != (fetch is not None) or (fetch and unboxed):
        raise ValueError(f"red_arm: bad mode/context: {mode!r}, fetch={fetch is not None}, unboxed={unboxed}")
    canary_dir = config.scratch_dir() / "box-canary"
    canary_dir.mkdir(parents=True, exist_ok=True)
    token = "FG-CANARY-" + secrets.token_hex(16)
    secret = canary_dir / "secret.txt"
    secret.write_text(token)
    target = canary_dir / f"write-target-{secrets.token_hex(4)}"
    # B4-1: the network control must not depend on the host having internet. A listener on our
    # loopback is reachable unboxed and unreachable boxed (the box's loopback is a fresh netns).
    lsock = socket.socket()
    lsock.bind(("127.0.0.1", 0))
    lsock.listen(8)
    port = lsock.getsockname()[1]
    connects: list[str] = []
    try:
        if port == fetchproxy.FORWARD_PORT:   # inside the box that port is the forwarder's
            raise RuntimeError("control listener landed on the forwarder's port; retry")
        with tempfile.TemporaryDirectory(dir=canary_dir) as src:
            (Path(src) / "probe.py").write_text(PROBE)
            argv = ["python3", "/src/probe.py" if not unboxed else str(Path(src) / "probe.py"),
                    str(secret), str(target), str(port)]
            kw = {}
            if fetch is not None:
                outside = [t for t in OUTSIDE_PROBES if t not in fetch["members"]]
                inside = sorted(fetch["members"])[0]
                connects = outside + [inside]
                argv = (["python3", "/fwd/fwd.py", "/proxy/p.sock", str(fetchproxy.FORWARD_PORT), "--"]
                        + argv + [json.dumps(connects), str(fetchproxy.FORWARD_PORT)])
                kw = dict(ro_binds=[(fetch["proxy_dir"], "/proxy"), (fetch["fwd_dir"], "/fwd")],
                          etc_extra=FETCH_ETC)
                fetch["proxy"].phase = "red-arm"
                n0 = len(fetch["proxy"].log)
                fetch["dials_n0"] = len(fetch["dials"])
            rc, out, err, _ = _exec(Path(src), argv, 60 if fetch else 30, unboxed=unboxed, **kw)
        lsock.setblocking(False)
        try:                       # review F13: our own listener is the outside witness
            c, _ = lsock.accept()
            c.close()
            witnessed = True
        except (BlockingIOError, OSError):
            witnessed = False
    finally:
        lsock.close()
    line = next((l for l in out.splitlines() if l.startswith("PROBE ")), None)
    said = json.loads(line[6:]) if line else {}
    res = {
        "secret": "LEAKED" if token in out or token in err else "blocked",
        "write": "LEAKED" if target.exists() else "blocked",
        # network has no outside witness; the probe's word is used, and a probe that did not
        # report counts as LEAKED (fail closed).
        "network": ("LEAKED:witnessed:" + f"127.0.0.1:{port}") if witnessed and not unboxed
                   else said.get("network", "LEAKED:no-probe-report"),
        "probe": said, "rc": rc, "stderr": err[-500:], "control_target": f"127.0.0.1:{port}",
    }
    if target.exists():
        target.unlink()  # our own planted canary target, recreated per run
    keys = ["secret", "write", "network"]
    if fetch is not None:
        log = fetch["proxy"].log[n0:]
        res["proxy_log"] = log
        inside, outside = connects[-1], connects[:-1]
        bad = []
        for t in outside:
            seen = [e for e in log if e.get("target") == t]
            if not seen:
                bad.append(f"unattempted:{t}")          # fail closed: an untried refusal proves nothing
            elif any(e["decision"] == "allowed" for e in seen):
                bad.append(t)
        res["proxy_outside"] = "blocked" if not bad else "LEAKED:" + ",".join(bad)
        dialled_out = [e["target"] for e in log if e["decision"] == "allowed" and e["target"] != inside]
        # review F5: box.py's OWN count of dials (the proxy's resolver is wrapped below), so a proxy
        # that dials and logs "denied" is still caught
        dials = fetch["dials"][fetch.get("dials_n0", 0):]
        dialled_out += [t for t in dials if t != inside]
        if dialled_out:   # anything dialled that is not the inside target is a leak, whatever it was
            res["proxy_outside"] = "LEAKED:dialled:" + ",".join(dialled_out)
        res["dials"] = list(dials)
        ins = [e for e in log if e.get("target") == inside]
        res["proxy_inside"] = ("reached" if any(e["decision"] == "allowed" and e["upstream"] == "ok" for e in ins)
                               and dials.count(inside) == 1
                               else "UNREACHED:" + (",".join(str(e.get("upstream")) for e in ins) or "unattempted"))
        res["inside_target"] = inside
        keys.append("proxy_outside")
    res["ok"] = (all(res[k] == "blocked" for k in keys) and line is not None
                 and (fetch is None or res["proxy_inside"] == "reached"))
    return res


def _fetch_context(host_set: str, sets: dict, resolve=None) -> dict:
    """Start the proxy and write the forwarder, in folders the box user can reach. The proxy's
    resolver is wrapped so box.py itself counts every dial (review F5)."""
    root = box_root()
    root.mkdir(parents=True, exist_ok=True)
    proxy_dir = Path(tempfile.mkdtemp(prefix="proxy-", dir=root))
    fwd_dir = Path(tempfile.mkdtemp(prefix="fwd-", dir=root))
    try:
        # The box root is setgid to the box user's group, so what is created in these folders
        # inherits that group. A chmod by a non-member silently CLEARS a folder's setgid bit
        # (Linux), so every file, the socket included, is created BEFORE its folder's chmod.
        # Found by the live reach check on the deploy host, 2026-11-14: the socket came out au:au and the
        # box user got EACCES (failed closed: zero dials).
        (fwd_dir / "fwd.py").write_text(fetchproxy.FORWARDER)
        os.chmod(fwd_dir / "fwd.py", 0o640)
        members = sets[host_set]
        dials: list[str] = []
        base = resolve or (lambda t: (t.rpartition(":")[0], int(t.rpartition(":")[2])))

        def counted(target):
            dials.append(target)
            return base(target)
        proxy = fetchproxy.Proxy(proxy_dir / "p.sock", members, resolve=counted).start()
        try:
            os.chmod(proxy_dir, 0o750)   # read + traverse for the box user's group; the socket is 0660
            os.chmod(fwd_dir, 0o750)
            if box_user():
                want = root.stat().st_gid
                for f in (proxy_dir / "p.sock", fwd_dir / "fwd.py"):
                    if f.stat().st_gid != want:
                        raise RuntimeError(f"{f.name} is group {f.stat().st_gid}, the box root's group is {want}: "
                                           f"the box user could not reach it (is {root} setgid?)")
        except BaseException:
            proxy.stop()
            raise
    except BaseException:   # review F12: nothing left behind when the proxy cannot start
        shutil.rmtree(proxy_dir, ignore_errors=True)
        shutil.rmtree(fwd_dir, ignore_errors=True)
        raise
    return {"proxy": proxy, "proxy_dir": proxy_dir, "fwd_dir": fwd_dir, "members": members, "dials": dials}


def run(src: Path, argv: list[str], *, mode: str = "run", timeout: float = 60,
        ref: Path | None = None, host_set: str | None = None, keep: Path | None = None,
        _host_sets_file: Path | None = None, _resolve=None, _same_user_ok: bool = False) -> BoxResult:
    """Run ``argv`` in the box. Fetch mode needs ``host_set`` (a name in host_sets.json); its
    result's ``fetch_record`` names the set, the file's sha256 and every CONNECT of the run.
    ``_host_sets_file`` / ``_resolve`` exist for tests only (a fake set, a local fake upstream)."""
    sets = sha = None
    if mode == "fetch":
        if not host_set:
            return BoxResult(blind("fetch-needs-host-set"))
        try:
            sets, sha = fetchproxy.load_host_sets(_host_sets_file or fetchproxy.HOST_SETS_FILE)
        except (OSError, ValueError) as e:
            return BoxResult(blind(f"host-sets-unreadable: {e}"))
        if host_set not in sets:
            return BoxResult(blind(f"unknown-host-set: {host_set}"))
    elif mode != "run":
        return BoxResult(blind(f"unknown-mode: {mode}"))
    elif host_set or keep is not None:
        return BoxResult(blind("run-mode-takes-no-host-set-or-keep"))
    why = available()
    if why:
        return BoxResult(blind(why))
    for tree in (src, ref) if ref is not None else (src,):
        if not Path(tree).is_dir():   # os.walk would read a missing folder as an empty one
            return BoxResult(blind(f"source-missing: {tree}"))
    if keep is not None and Path(keep).exists():
        return BoxResult(blind(f"keep-exists: {keep}"))
    if box_user() and shutil.which("prlimit") is None:
        return BoxResult(blind("box-unavailable: prlimit not installed (needed to cap the box user)"))
    if mode == "fetch" and not box_user() and not _same_user_ok:
        # review F4 / §4a.6: fetch mode uses a throwaway user. Tests may waive it, recorded below.
        return BoxResult(blind(f"fetch-needs-box-user: set {BOX_USER_KEY}"))
    try:
        fx = _fetch_context(host_set, sets, _resolve) if mode == "fetch" else None
    except fetchproxy.SocketPathTooLong as e:
        return BoxResult(blind(f"box-unavailable: {e}"))
    try:
        arm = red_arm(mode) if fx is None else red_arm(mode, fetch=fx)   # run mode: the call as before
        record = {}
        if fx is not None:
            record = {"mode": "fetch", "host_set": host_set, "members": sorted(fx["members"]),
                      "host_sets_sha256": sha, "box_user": box_user(), "red_arm_connects": arm.get("proxy_log", []),
                      "overrides": [k for k, v in (("host_sets_file", _host_sets_file), ("resolver", _resolve),
                                                   ("same_user", _same_user_ok)) if v]}
        if not arm.get("probe"):
            cannot = start_failure()
            if cannot:
                return BoxResult(blind(f"box-unavailable: bwrap cannot start: {cannot}"), red_arm=arm,
                                 fetch_record=record)
        if not arm["ok"]:
            keys = ("secret", "write", "network", "proxy_outside")
            failed = [k for k in keys if k in arm and arm[k] != "blocked"]
            if fx is not None and arm.get("proxy_inside") != "reached":
                failed.append("proxy_inside")
            return BoxResult(blind("box-red-arm: " + ",".join(failed or ["no-probe-report"])), red_arm=arm,
                             fetch_record=record)
        kw = {}
        if fx is not None:
            fx["proxy"].phase = "run"
            argv = ["python3", "/fwd/fwd.py", "/proxy/p.sock", str(fetchproxy.FORWARD_PORT), "--"] + list(argv)
            kw = dict(ro_binds=[(fx["proxy_dir"], "/proxy"), (fx["fwd_dir"], "/fwd")],
                      etc_extra=FETCH_ETC, memory_mb=FETCH_MEMORY_MB, keep=keep)
        rc, out, err, skipped = _exec(Path(src), argv, timeout, ref=Path(ref) if ref is not None else None, **kw)
    finally:
        if fx is not None:
            log = fx["proxy"].stop()
            shutil.rmtree(fx["proxy_dir"], ignore_errors=True)   # our own folders, created above
            shutil.rmtree(fx["fwd_dir"], ignore_errors=True)
    if fx is not None:
        record["connects"] = [e for e in log if e["phase"] == "run"]
        record["dials"] = list(fx["dials"])
        if "KEEP-REFUSED" in skipped:
            return BoxResult(blind("keep-too-large"), rc, out, err, arm, skipped, record)
    note = f"; skipped {len(skipped)} symlink(s): {', '.join(skipped[:5])}" if skipped else ""
    if rc is None:
        return BoxResult(blind("timeout" + note), None, out, err, arm, skipped, record)
    return BoxResult(found(f"ran, exit {rc}{note}"), rc, out, err, arm, skipped, record)
