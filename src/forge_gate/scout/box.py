"""The isolation box (design v1.4.5 §4a.6, build step 4): where scout runs pool code.

RUN mode (built): bubblewrap with every namespace unshared, so network off (a fresh netns
has only its own loopback), no home folder and no secrets file visible (only /usr, /lib*,
/bin, /sbin and a few /etc files are mounted, read-only), the source mounted read-only at
/src, a throwaway copy at /work, an empty environment, uid 65534 inside the namespace, its
own process tree, killed with its parent. If FORGE_GATE_BOX_USER is set, bubblewrap itself
runs as that unprivileged user (sudo -n -u), so an escape lands as that user, not ours.

FETCH mode (network open only to an index's named hosts) is NOT built: it needs root-level
network rules. ``run(mode="fetch")`` refuses with BLIND(fetch-mode-not-built).

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
from .outcomes import Outcome, blind, clean, found

BOX_USER_KEY = "FORGE_GATE_BOX_USER"
# Where the box's copies live when a box user is set: a folder that user can reach (it cannot
# reach our home, by design). Deploy host: /var/lib/forge-gate-box, owner au, group fgbox, 2770.
BOX_ROOT_KEY = "FORGE_GATE_BOX_ROOT"
_ETC = ("ld.so.cache", "ld.so.conf", "ld.so.conf.d", "alternatives", "localtime", "nsswitch.conf")
_SYS = ("/usr", "/bin", "/sbin", "/lib", "/lib64", "/lib32")
MEMORY_MB = 1024   # address-space cap for the box and everything in it (same mechanism as runner.run_file)

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


def _bwrap_argv(src: Path, work: Path, argv: list[str], timeout: float = 60, ref: Path | None = None) -> list[str]:
    a = ["bwrap", "--unshare-all", "--die-with-parent", "--new-session", "--clearenv",
         "--uid", "65534", "--gid", "65534",
         "--setenv", "PATH", "/usr/bin:/bin", "--setenv", "HOME", "/work",
         "--setenv", "LANG", "C.UTF-8", "--setenv", "PYTHONDONTWRITEBYTECODE", "1"]
    for d in _SYS:
        if Path(d).exists():
            a += ["--ro-bind", d, d]
    for e in _ETC:
        if Path("/etc", e).exists():
            a += ["--ro-bind", f"/etc/{e}", f"/etc/{e}"]
    a += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
          "--ro-bind", str(src), "/src"]
    if ref is not None:   # a second, read-only tree (6E 6a pre-note: what we compare against is never writable)
        a += ["--ro-bind", str(ref), "/ref"]
    a += ["--bind", str(work), "/work", "--chdir", "/work", "--"]
    a += argv
    user = box_user()
    if not user:
        return a   # limits are set by _exec's preexec_fn
    # sudo resets resource limits (PAM), so a parent's setrlimit does not reach the box user:
    # set them after sudo, on the command that starts bubblewrap (measured 2026-11-11).
    cap = MEMORY_MB * 1024 * 1024
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


def _exec(src: Path, argv: list[str], timeout: float, *, unboxed: bool = False, ref: Path | None = None):
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
        full = argv if unboxed else _bwrap_argv(src_copy, work, argv, timeout, ref_copy)

        def _limits():  # inherited by bwrap and everything it starts
            cap = MEMORY_MB * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (cap, cap))
            cpu = int(timeout) + 1
            resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))

        r = subprocess.run(full, capture_output=True, text=True, timeout=timeout,
                           cwd=work if unboxed else run_dir, start_new_session=True,
                           preexec_fn=None if unboxed else _limits)
        return r.returncode, r.stdout, r.stderr, skipped
    except subprocess.TimeoutExpired:
        return None, "", f"timeout after {timeout}s", skipped
    finally:
        if user:  # files the box user wrote: let us remove them (throwaway copy, created above)
            subprocess.run(["sudo", "-n", "-u", user, "chmod", "-R", "g+rwX", str(work)],
                           capture_output=True, timeout=30)
        shutil.rmtree(run_dir, ignore_errors=True)  # throwaway copies, created above


def red_arm(mode: str = "run", *, unboxed: bool = False) -> dict:
    """Plant a canary secret and a canary write target, run the probe, judge from outside.
    Returns {"secret","write","network": "blocked"|"LEAKED…", "ok": bool}. ``unboxed=True``
    runs the same probe with no box: every attempt must then LEAK, which proves the targets
    are real (a red arm whose targets cannot be reached passes vacuously)."""
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
    try:
        with tempfile.TemporaryDirectory(dir=canary_dir) as src:
            (Path(src) / "probe.py").write_text(PROBE)
            argv = ["python3", "/src/probe.py" if not unboxed else str(Path(src) / "probe.py"),
                    str(secret), str(target), str(port)]
            rc, out, err, _ = _exec(Path(src), argv, 30, unboxed=unboxed)
    finally:
        lsock.close()
    line = next((l for l in out.splitlines() if l.startswith("PROBE ")), None)
    said = json.loads(line[6:]) if line else {}
    res = {
        "secret": "LEAKED" if token in out or token in err else "blocked",
        "write": "LEAKED" if target.exists() else "blocked",
        # network has no outside witness; the probe's word is used, and a probe that did not
        # report counts as LEAKED (fail closed).
        "network": said.get("network", "LEAKED:no-probe-report"),
        "probe": said, "rc": rc, "stderr": err[-500:], "control_target": f"127.0.0.1:{port}",
    }
    if target.exists():
        target.unlink()  # our own planted canary target, recreated per run
    res["ok"] = all(res[k] == "blocked" for k in ("secret", "write", "network")) and line is not None
    return res


def run(src: Path, argv: list[str], *, mode: str = "run", timeout: float = 60,
        ref: Path | None = None) -> BoxResult:
    if mode == "fetch":
        return BoxResult(blind("fetch-mode-not-built: needs root-level network rules (design §4a.6)"))
    if mode != "run":
        return BoxResult(blind(f"unknown-mode: {mode}"))
    why = available()
    if why:
        return BoxResult(blind(why))
    for tree in (src, ref) if ref is not None else (src,):
        if not Path(tree).is_dir():   # os.walk would read a missing folder as an empty one
            return BoxResult(blind(f"source-missing: {tree}"))
    if box_user() and shutil.which("prlimit") is None:
        return BoxResult(blind("box-unavailable: prlimit not installed (needed to cap the box user)"))
    arm = red_arm(mode)
    if not arm.get("probe"):
        cannot = start_failure()
        if cannot:
            return BoxResult(blind(f"box-unavailable: bwrap cannot start: {cannot}"), red_arm=arm)
    if not arm["ok"]:
        failed = [k for k in ("secret", "write", "network") if arm[k] != "blocked"] or ["no-probe-report"]
        return BoxResult(blind("box-red-arm: " + ",".join(failed)), red_arm=arm)
    rc, out, err, skipped = _exec(Path(src), argv, timeout, ref=Path(ref) if ref is not None else None)
    note = f"; skipped {len(skipped)} symlink(s): {', '.join(skipped[:5])}" if skipped else ""
    if rc is None:
        return BoxResult(blind("timeout" + note), None, out, err, arm, skipped)
    return BoxResult(found(f"ran, exit {rc}{note}"), rc, out, err, arm, skipped)
