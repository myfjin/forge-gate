"""fg_common: the code that fg_gate.py and fg_mutrun.py share (shared-tools loop, slice S3c).

Each tool loads THIS file from its own directory, by path: never an installed copy, never the
tested tree's copy, never through ``sys.path``. So ``python tools/fg_gate.py`` and
``python tools/fg_mutrun.py`` work from any cwd. The behaviour is the gate's G0, G3-G8 and G12
helpers, moved here unchanged from fg_gate.py, plus the steps both tools' ``main`` repeated.
It is not a CLI. Stdlib only, Python 3.10+.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

BASE_PATH = ("/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin")
POLL_S = 30
DEFAULT_WAIT_S = 900
DEFAULT_TIMEOUT_S = 1800
PATH_KEY = "FORGE_GATE_GATE_PATH"
LOAD_KEY = "FORGE_GATE_GATE_MAX_LOAD"
# S3d: a git call that runs while fg_mutrun blocks SIGTERM/SIGHUP/SIGINT (M4's diff, the
# restore's status) would otherwise yield only to SIGKILL; every git call either tool makes,
# tool_head's included (S3e), goes through ``_git`` and is bounded by this.
GIT_TIMEOUT_S = 120.0
PREFLIGHT = (
    "import json, sys, forge_gate, pytest\n"
    "try:\n    import pytest_timeout; t = True\nexcept Exception:\n    t = False\n"
    "print(json.dumps({'forge_gate': forge_gate.__file__, 'pytest': pytest.__version__,"
    " 'python': sys.version.split()[0], 'pytest_timeout': t}))\n")


class GateError(Exception):
    """A GATE-ERROR: the run says nothing about the code."""


class GitTimeout(GateError):
    """A git call ran past GIT_TIMEOUT_S and was killed (S3d; its own class from S3e)."""


# ---- G0: own code first -------------------------------------------------------------------

def _own_paths():
    """Import ``forge_gate.paths`` from this tool's own checkout, and prove it came from there."""
    own_src = Path(__file__).resolve().parents[1] / "src"
    if not sys.path or sys.path[0] != str(own_src):
        sys.path.insert(0, str(own_src))
    try:
        from forge_gate import paths
    except Exception as e:  # noqa: BLE001 - any failure here is a broken instrument
        raise GateError(f"G0: cannot import forge_gate.paths from {own_src}: {e}") from None
    got = Path(os.path.realpath(paths.__file__))
    if not paths.is_under(got, paths.real(own_src)):
        raise GateError(f"G0: forge_gate.paths loaded from {got}, not from the tool's own {own_src}")
    return paths


# ---- small helpers (module level so tests can inject them) ---------------------------------

def _loadavg() -> float:
    return os.getloadavg()[0]


_sleep = time.sleep


def _run_id() -> str:
    return f"{time.time_ns()}-{os.getpid()}"


def _md5b(b: bytes) -> str:
    return hashlib.md5(b).hexdigest()


def _md5(p: Path) -> str:
    return _md5b(p.read_bytes())


def _umask() -> str:
    m = os.umask(0)
    os.umask(m)
    return f"{m:03o}"


def utc(t: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def strict_parser(prog: str) -> argparse.ArgumentParser:
    """No ``-h`` (a usage error, not exit 0); a usage error raises GateError("usage: ..."),
    never argparse's own exit, so it is reported on the tool's own error line."""
    ap = argparse.ArgumentParser(prog=prog, add_help=False)

    def _err(msg):
        raise GateError(f"usage: {msg}")
    ap.error = _err
    return ap


# ---- G3: the environment, built from nothing ------------------------------------------------

def gate_path(environ) -> str:
    if environ.get(PATH_KEY):
        return environ[PATH_KEY]
    dirs = list(BASE_PATH)
    home = environ.get("HOME", "")
    for extra in ((os.path.join(home, "homebrew", "bin") if home else None), "/opt/homebrew/bin"):
        if extra and os.path.isdir(extra):
            dirs.append(extra)
    return ":".join(dirs)


def build_env(environ, tree: Path, pyc: Path) -> dict:
    env = {"PATH": gate_path(environ), "HOME": environ.get("HOME", ""),
           "PYTHONPATH": str(tree / "src"), "PYTHONPYCACHEPREFIX": str(pyc),
           "FORGE_GATE_HOME": str(tree)}
    for k in ("LANG", "LC_ALL"):
        if k in environ:
            env[k] = environ[k]
    return env


# ---- G4/G5: preflights ---------------------------------------------------------------------

def require_tools(env, rec) -> None:
    """G4: ``timeout`` and ``git`` resolve on the suite's PATH, before anything runs."""
    for t in ("timeout", "git"):
        found = shutil.which(t, path=env["PATH"])
        rec[f"which_{t}"] = found
        if not found:
            raise GateError(f"{t} not found on gate PATH")


def tool_head(env, tool: Path):
    """git HEAD of the tool's own checkout, or None when it is not in one. Bounded like every
    other git call (S3e): a hung git is a GitTimeout (a GATE-ERROR), never a None."""
    try:
        return _git(env, tool.parent, "rev-parse", "HEAD").strip()
    except GitTimeout:
        raise
    except GateError:
        return None


def check_toplevel(env, tree: Path, paths) -> None:
    top = _git(env, tree, "rev-parse", "--show-toplevel").strip()
    if not (paths.is_under(paths.real(top), tree) and paths.is_under(tree, paths.real(top))):
        raise GateError(f"--tree {tree} is not a worktree's top level ({top})")


def import_preflight(python, tree: Path, env, paths, rec) -> None:
    """G5: under ``env`` and cwd DIR, ``forge_gate`` and ``pytest`` import, and ``forge_gate``
    resolves inside DIR/src. Records what it found."""
    pf = subprocess.run([python, "-c", PREFLIGHT], cwd=tree, env=env,
                        capture_output=True, text=True, timeout=120)
    if pf.returncode != 0:
        raise GateError(f"import preflight rc={pf.returncode}: "
                        f"{(pf.stderr.strip().splitlines() or ['?'])[-1][:200]}")
    try:
        info = json.loads(pf.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise GateError(f"import preflight printed no result: {pf.stdout[-200:]!r}") from None
    rec.update(forge_gate_file=info["forge_gate"], pytest_version=info["pytest"],
               python_version=info["python"], pytest_timeout_loaded=info["pytest_timeout"])
    if not paths.is_under(paths.real(info["forge_gate"]), paths.real(tree / "src")):
        raise GateError(f"import preflight: forge_gate imported from {info['forge_gate']}, "
                        f"not from {tree / 'src'}")


# ---- G6: load guard --------------------------------------------------------------------------

def threshold(arg, environ) -> float:
    if arg is not None:
        return float(arg)
    if environ.get(LOAD_KEY):
        return float(environ[LOAD_KEY])
    return (os.cpu_count() or 2) / 2


def wait_for_load(thr: float, wait_s: float):
    """Return (load, waited_s) once the 1-min load is <= thr; GateError after wait_s."""
    waited = 0.0
    load = _loadavg()
    while load > thr:
        if waited >= wait_s:
            raise GateError(f"load {load:.2f} > {thr:.2f} after waiting {waited:.0f} s")
        step = min(POLL_S, wait_s - waited)
        _sleep(step)
        waited += step
        load = _loadavg()
    return load, waited


# ---- signals (S3d, S3e) ----------------------------------------------------------------------

def install_handlers(signals, handler) -> dict:
    """Install ``handler`` for each signal that was NOT ignored when the tool started (S3e, F5):
    an inherited SIG_IGN (``nohup``, ``cmd &`` without job control) stays as it is, so that
    signal neither interrupts nor ends the run. Returns {signal: old handler} for the ones
    replaced; the caller puts exactly those back."""
    old = {}
    for s in signals:
        if signal.getsignal(s) == signal.SIG_IGN:
            continue
        old[s] = signal.signal(s, handler)
    return old


def default_signals_in_child(signals):
    """``preexec_fn`` for the suite's Popen (S3e review F1): the suite starts with the default
    disposition of each signal, whatever the tool inherited. Before S3e every one was caught, and
    exec resets a caught signal to SIG_DFL; an inherited SIG_IGN that the tool now leaves alone
    (F5) would otherwise reach the measured suite and change what its signal tests see."""
    def reset():
        for s in signals:
            signal.signal(s, signal.SIG_DFL)
    return reset


def what(e: BaseException) -> str:
    """What an interrupt says: its message, or its type's name when it has none (S3e, F6: a bare
    KeyboardInterrupt says ``KeyboardInterrupt``, not an empty string)."""
    return str(e) or type(e).__name__


# ---- G7: one pytest process group -----------------------------------------------------------

def kill_group(proc) -> None:
    """SIGKILL the run's whole process group (it was started with start_new_session), then reap."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    proc.wait()


def last_line(log: Path) -> str:
    out = log.read_text(encoding="utf-8", errors="replace").strip().splitlines()
    return out[-1] if out else ""


# ---- G8: junit -------------------------------------------------------------------------------

def read_junit(j: Path, started_ns: int):
    """Counts and per-case outcomes from THIS run's junit file only."""
    if not j.is_file():
        raise GateError(f"no junit file {j}")
    if j.stat().st_mtime_ns < started_ns:
        raise GateError(f"stale junit {j} (mtime before the run started)")
    try:
        root = ET.parse(j).getroot()
    except ET.ParseError as e:
        raise GateError(f"unreadable junit {j}: {e}") from None
    suites = [root] if root.tag == "testsuite" else root.findall("testsuite")
    if not suites:
        raise GateError(f"junit {j} has no testsuite")
    c = {k: sum(int(s.get(k, 0)) for s in suites) for k in ("tests", "failures", "errors", "skipped")}
    c["passed"] = c["tests"] - c["failures"] - c["errors"] - c["skipped"]
    cases = []
    for s in suites:
        for tc in s.iter("testcase"):
            kids = {k.tag for k in tc}
            out = ("error" if "error" in kids else "failed" if "failure" in kids
                   else "skipped" if "skipped" in kids else "passed")
            cases.append((tc.get("classname", ""), tc.get("name", ""), out))
    return c, cases


# ---- git -------------------------------------------------------------------------------------

def _git(env, tree: Path, *args) -> str:
    try:
        p = subprocess.run(["git", "--no-optional-locks", "-C", str(tree), *args], env=env,
                           capture_output=True, text=True, timeout=GIT_TIMEOUT_S)
    except subprocess.TimeoutExpired:  # run() has killed git; say so loudly, never wait on
        raise GitTimeout(f"git {' '.join(args)} TIMED OUT after {GIT_TIMEOUT_S:g} s and was "
                        "killed") from None
    if p.returncode != 0:
        raise GateError(f"git {' '.join(args)} failed rc={p.returncode}: {p.stderr.strip()[:200]}")
    return p.stdout


def _porcelain(env, tree):
    return _git(env, tree, "status", "--porcelain", "--untracked-files=all")


# ---- test paths and node ids (S2 review F8) ----------------------------------------------------

def _extra_path(x: str, tree: Path, paths) -> None:
    if x.startswith(("-", "@")):  # '@': pytest would read more args from that file
        raise GateError(f"usage: extra pytest arg {x!r} is not on the gate's allow-list")
    target = paths.real(tree / x.split("::", 1)[0])
    if not (paths.is_under(target, tree) and target.exists()):
        raise GateError(f"usage: extra pytest arg {x!r} is not an existing path inside the tested tree")


# ---- G12: the record ---------------------------------------------------------------------------

def inside_tree(record: Path, tree: Path, paths) -> bool:
    """The record, or the dir that holds it (and the junit and pycache files beside it), is
    inside the tested tree."""
    return paths.is_under(record, tree) or paths.is_under(record.parent, tree)


def append_record(record: Path, rec: dict) -> None:
    """One JSON line, O_APPEND. Raises OSError; the caller reports it."""
    fd = os.open(record, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    with os.fdopen(fd, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, sort_keys=True) + "\n")
