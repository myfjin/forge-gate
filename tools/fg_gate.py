#!/usr/bin/env python3
"""fg_gate: run the forgeproof suite on a worktree and print one verdict that the tested
tree cannot fake (shared-tools loop, slice S2; behaviour G0-G12 of the loop's design doc).

    fg_gate.py --tree DIR --python PY --record FILE --tag T [--must-run ENTRY ...]
               [--max-load F] [--wait S] [--timeout S] [-- extra pytest args]

Exit 0 GREEN, 1 RED, 2 GATE-ERROR. The last stdout line is
``GATE <GREEN|RED|ERROR> rc=<rc|none> passed=.. failed=.. errors=.. skipped=.. tree=<hash> [reason]``.
One JSON line is appended to FILE before that line is printed.

The tool is a measuring instrument: nothing the tested tree does may change the verdict.
- G0  it loads its OWN ``forgeproof.paths`` (``<tool checkout>/src``), never the tested tree's;
- G3  the suite's environment is built from nothing (``env -i`` semantics);
- G4/G5 a missing ``timeout``/``git``, a missing pytest, or ``import forgeproof`` resolving
  outside ``DIR/src`` (an editable install elsewhere) is an error, not a red or green;
- G6  a loaded box waits (poll every 30 s), then errors;
- G7/G8 counts come only from this run's own fresh junit file;
- G9  a suite that changes the tree is an error, and nothing is restored;
- G10 named must-run tests must have passed, not been skipped or gone missing.

The code it shares with fg_mutrun.py lives in ``fg_common.py`` beside it (slice S3c), loaded
from this file's own directory by path; without it the gate is a GATE-ERROR, never a verdict.

Settings (tool env vars, not config keys): ``FORGE_GATE_GATE_PATH`` (the suite's PATH) and
``FORGE_GATE_GATE_MAX_LOAD`` (the load threshold). Stdlib only, Python 3.10+.
"""
from __future__ import annotations

import importlib.util
import os
import platform
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

GREEN, RED, ERROR = "GREEN", "RED", "ERROR"
EXIT = {GREEN: 0, RED: 1, ERROR: 2}
STAGED_X = set("MADR")


def _load_common():
    """fg_common.py from THIS tool's own directory (never an installed or tested copy)."""
    here = Path(__file__).resolve().parent / "fg_common.py"
    spec = importlib.util.spec_from_file_location("fg_common_for_fg_gate", here)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


try:
    C = _load_common()
    GateError = C.GateError
    _COMMON_LOAD_ERROR = None
except Exception as _e:  # noqa: BLE001 - reported by main() as a GATE-ERROR, exit 2
    C, _COMMON_LOAD_ERROR = None, f"{type(_e).__name__}: {_e}"

    class GateError(Exception):
        """Stands in for fg_common.GateError when fg_common.py cannot be loaded."""


class _Interrupted(BaseException):
    """SIGTERM/SIGHUP/SIGINT (S3d): unwind so the pytest group is killed and the record written."""


_SIGNALS = (signal.SIGTERM, signal.SIGHUP, signal.SIGINT)
# S3e (F4): while Popen runs, a signal is only noted here, not raised: raised inside Popen it
# would unwind before ``proc`` is assigned and leave the new pytest group running. _run raises
# the noted signal as soon as Popen has returned, with ``proc`` known.
_DEFER = {"on": False, "signum": None}


def _on_signal(signum, _frame):
    if _DEFER["on"]:
        if _DEFER["signum"] is None:
            _DEFER["signum"] = signum
        return
    # All three are ignored before unwinding, so a second signal cannot break the group kill or
    # the record write; main() puts the old handlers back after the verdict line.
    for s in _SIGNALS:
        signal.signal(s, signal.SIG_IGN)
    raise _Interrupted(f"signal {signum}")


# ---- G10: must-run -----------------------------------------------------------------------------

def _entry_matches(entry: str, classname: str, name: str) -> bool:
    parts = entry.split("::")
    f = parts[0]
    if not f.endswith(".py"):
        return False
    mod = f[:-3].replace("/", ".")
    if len(parts) == 1:
        return classname == mod or classname.startswith(mod + ".")
    want_cls = ".".join([mod, *parts[1:-1]])
    return classname == want_cls and (name == parts[-1] or name.startswith(parts[-1] + "["))


def must_run(entries, cases):
    """{entry: outcome}; outcome is 'passed', 'missing', or the worst outcome seen."""
    res = {}
    for e in entries:
        outs = [o for cl, n, o in cases if _entry_matches(e, cl, n)]
        if not outs:
            res[e] = "missing"
        else:
            bad = [o for o in ("error", "failed", "skipped") if o in outs]
            res[e] = bad[0] if bad else "passed"
    return res


# ---- G11: verdict ----------------------------------------------------------------------------

def verdict(rc, counts, tree_ok: bool, must) -> tuple:
    """(verdict, reason) once G0-G8 were sound (G11).

    GREEN: rc 0, tests >= 1, no failure or error, tree unchanged, every must-run passed.
    RED:   tree unchanged and either (rc 1 with failures >= 1) or a must-run entry not passed;
           a must-run miss counts as RED only when pytest itself ended normally (rc 0 or 1).
    ERROR: everything else (rc 1 without failures is not a test failure: P8)."""
    if not tree_ok:
        return ERROR, "tree dirtied"
    bad_must = [f"must-run: {e} {o}" for e, o in (must or {}).items() if o != "passed"]
    f, er, t = counts["failures"], counts["errors"], counts["tests"]
    if rc == 0 and t >= 1 and f == 0 and er == 0 and not bad_must:
        return GREEN, ""
    if (rc == 1 and f >= 1) or (bad_must and rc in (0, 1)):
        return RED, "; ".join(bad_must)
    return ERROR, f"pytest rc={rc} with tests={t} failures={f} errors={er}"


# ---- G9 ----------------------------------------------------------------------------------------

def _changed_paths(before: str, after: str):
    a, b = set(before.splitlines()), set(after.splitlines())
    return sorted({ln[3:] for ln in a ^ b})


# ---- main --------------------------------------------------------------------------------------

# S2 review F8: an allow-list, not a deny-list. pytest's option grammar is open-ended
# (-o/--override-ini, combined short flags, @argfiles), so an arg not named here is refused.
EXTRA_FLAGS = ("-x", "-v", "-q")  # each as its own token
EXTRA_VALUE_FLAGS = ("-k", "-m", "--deselect", "--maxfail")  # value: the next token, or --long=V


def check_extra(extra, tree: Path, paths) -> None:
    """Extra pytest args pass an allow-list (S2 review F8); each token must be one of:
    - a flag in EXTRA_FLAGS, as its own token (so no combined short flags like ``-qc<ini>``);
    - a flag in EXTRA_VALUE_FLAGS with its value as the next token, or ``--deselect=V`` /
      ``--maxfail=V``. A ``-k``/``-m`` value may not start with ``-`` or ``@``; a ``--maxfail``
      value is ASCII digits; a ``--deselect`` value is checked as a path, below;
    - a path or node id (``path::name``) that does not start with ``-`` or ``@`` and whose path
      part, resolved, exists inside the tested tree.
    Anything else is a GATE-ERROR naming the arg, raised before pytest starts. Only argv is
    checked: what the tested tree's own ini or conftest files tell pytest to do is not."""
    i = 0
    while i < len(extra):
        x = extra[i]
        i += 1
        if x in EXTRA_FLAGS:
            continue
        if x in EXTRA_VALUE_FLAGS:
            if i == len(extra):
                raise GateError(f"usage: extra pytest arg {x!r} needs a value")
            flag, val = x, extra[i]
            i += 1
        elif x.startswith("--") and x.partition("=")[0] in EXTRA_VALUE_FLAGS:
            flag, _, val = x.partition("=")
        else:
            C._extra_path(x, tree, paths)
            continue
        if flag == "--deselect":
            C._extra_path(val, tree, paths)
        elif flag == "--maxfail":
            if not (val.isascii() and val.isdigit()):
                raise GateError(f"usage: extra pytest arg {flag} {val!r} is not a whole number")
        elif val.startswith(("-", "@")):
            raise GateError(f"usage: extra pytest arg {flag} {val!r} may not start with '-' or '@'")


def _parse(argv):
    ap = C.strict_parser("fg_gate.py")  # -h is a usage error, not exit 0
    ap.add_argument("--tree", required=True)
    ap.add_argument("--python", required=True)
    ap.add_argument("--record", required=True)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--must-run", nargs="+", default=[], dest="must_run")
    ap.add_argument("--max-load", type=float, default=None, dest="max_load")
    ap.add_argument("--wait", type=float, default=C.DEFAULT_WAIT_S)
    ap.add_argument("--timeout", type=float, default=C.DEFAULT_TIMEOUT_S)
    if "--" in argv:
        i = argv.index("--")
        own, extra = argv[:i], argv[i + 1:]
    else:
        own, extra = argv, []
    ns = ap.parse_args(own)  # a usage error is a GATE-ERROR line too, never argparse's own exit
    return ns, extra


def _line(v, rc, counts, tree, reason) -> str:
    c = counts or {}
    g = lambda k: c.get(k, "none")  # noqa: E731
    s = (f"GATE {v} rc={'none' if rc is None else rc} passed={g('passed')} failed={g('failures')} "
         f"errors={g('errors')} skipped={g('skipped')} tree={tree or 'none'}")
    return s + (f" {reason}" if reason else "")


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    t_start = time.time()
    if C is None:  # no shared code: no trusted instrument, nothing is written anywhere
        print(_line(ERROR, None, None, None,
                    f"cannot load fg_common.py beside the tool: {_COMMON_LOAD_ERROR}"))
        return EXIT[ERROR]
    try:
        paths = C._own_paths()
    except GateError as e:  # no trusted canonicaliser: nothing is written anywhere
        print(_line(ERROR, None, None, None, str(e)))
        return EXIT[ERROR]
    try:
        ns, extra = _parse(argv)
    except GateError as e:
        print(_line(ERROR, None, None, None, str(e)))
        return EXIT[ERROR]

    tree = paths.real(ns.tree)
    record = paths.real(ns.record)
    rdir = record.parent
    # G12: the record (and with it the junit and pycache files, which live beside it) is outside DIR.
    if C.inside_tree(record, tree, paths):
        print(_line(ERROR, None, None, None, f"record {record} is inside the tested tree {tree}"))
        return EXIT[ERROR]

    run = C._run_id()
    tool = Path(__file__).resolve()
    common = Path(C.__file__).resolve()
    rec = {"utc": C.utc(t_start), "tag": ns.tag,
           "run_id": run, "argv": argv, "tool": str(tool), "tool_md5": C._md5(tool),
           "common_file": str(common), "common_md5": C._md5(common),
           "tool_head": None, "paths_file": str(paths.real(paths.__file__)),
           "host": socket.gethostname(), "platform": platform.platform(), "python": ns.python,
           "tree": str(tree), "umask": C._umask(), "must_run": None, "rc": None,
           "load_before": None, "load_after": None, "waited_s": None}
    old_handlers = C.install_handlers(_SIGNALS, _on_signal)  # S3d; inherited SIG_IGN kept (S3e)
    try:
        return _run(ns, extra, tree, record, rdir, run, tool, rec, t_start, paths)
    finally:
        for s, h in old_handlers.items():
            signal.signal(s, h)


def _run(ns, extra, tree, record, rdir, run, tool, rec, t_start, paths) -> int:
    rc, counts, tree_hash, v, reason = None, None, None, ERROR, ""
    proc = None
    try:
        rdir.mkdir(parents=True, exist_ok=True)
        check_extra(extra, tree, paths)
        pyc = rdir / f"pyc-{ns.tag}-{run}"
        j = rdir / f"junit-{ns.tag}-{run}.xml"
        log = rdir / f"log-{ns.tag}-{run}.txt"
        env = C.build_env(os.environ, tree, pyc)
        rec.update(PATH=env["PATH"], junit=str(j), log=str(log), pycache_prefix=str(pyc))
        C.require_tools(env, rec)  # G4: tools on the suite's PATH, before anything runs.
        rec["tool_head"] = C.tool_head(env, tool)
        C.check_toplevel(env, tree, paths)
        # G2: clean before (staged entries only).
        before = C._porcelain(env, tree)
        rec["porcelain_before"] = before
        bad = [ln for ln in before.splitlines() if not (ln[:1] in STAGED_X and ln[1:2] == " ")]
        rec["head"] = C._git(env, tree, "rev-parse", "HEAD").strip()
        tree_hash = C._git(env, tree, "write-tree").strip()
        rec["tree_before"] = tree_hash
        rec["staged"] = [ln for ln in before.splitlines() if ln not in bad]
        if bad:
            raise GateError(f"tree not clean before: {'; '.join(bad[:5])}")
        rec["git_version"] = C._git(env, tree, "--version").strip()
        C.import_preflight(ns.python, tree, env, paths, rec)  # G5: same env and cwd as the run.
        # G6: load guard.
        thr = C.threshold(ns.max_load, os.environ)
        rec["max_load"] = thr
        rec["load_first"] = C._loadavg()
        load, waited = C.wait_for_load(thr, ns.wait)
        rec.update(load_before=load, waited_s=waited)
        # G7: run. Fresh junit that must not exist; own process group; wall timeout.
        if j.exists():
            raise GateError(f"junit {j} exists before the run")
        cmd = [ns.python, "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--junitxml={j}", *extra]
        rec["cmd"] = cmd
        started_ns = time.time_ns()
        t0 = time.monotonic()
        with open(log, "wb") as lf:
            _DEFER.update(on=True, signum=None)  # F4: a signal inside Popen waits for ``proc``
            try:
                proc = subprocess.Popen(cmd, cwd=tree, env=env, stdout=lf,
                                        stderr=subprocess.STDOUT, start_new_session=True,
                                        preexec_fn=C.default_signals_in_child(_SIGNALS))
            finally:
                _DEFER["on"] = False
                if _DEFER["signum"] is not None:  # raised here, ``proc`` held (or no child at all):
                    _on_signal(_DEFER["signum"], None)  # the interrupt handler below kills the group
            try:
                rc = proc.wait(timeout=ns.timeout)
            except BaseException as e:  # a timeout, or an interrupt (S3d): the whole group goes
                C.kill_group(proc)
                rec["group_killed"] = True
                rec["duration_s"] = round(time.monotonic() - t0, 1)
                if not isinstance(e, subprocess.TimeoutExpired):
                    raise
                raise GateError(f"timeout after {ns.timeout:.0f} s (process group killed)") from None
        rec["duration_s"] = round(time.monotonic() - t0, 1)
        rec["rc"] = rc
        rec["load_after"] = C._loadavg()
        rec["last_line"] = C.last_line(log)
        # G8: counts from this run's junit only.
        counts, cases = C.read_junit(j, started_ns)
        rec.update(counts)
        # G9: tree after.
        after = C._porcelain(env, tree)
        tree_after = C._git(env, tree, "write-tree").strip()
        rec.update(porcelain_after=after, tree_after=tree_after)
        tree_ok = after == before and tree_after == tree_hash
        # G10: must-run.
        mr = must_run(ns.must_run, cases)
        rec["must_run"] = mr
        v, reason = verdict(rc, counts, tree_ok, mr)
        if not tree_ok:  # nothing is restored: the tree stays as the suite left it
            changed = _changed_paths(before, after) or ["(index: write-tree changed)"]
            reason = f"tree dirtied: {', '.join(changed)}"
    except GateError as e:
        v, reason = ERROR, str(e)
    except (_Interrupted, KeyboardInterrupt) as e:  # S3d: never GREEN, never RED, group gone
        if proc is not None and proc.returncode is None:  # landed between Popen and its wait
            C.kill_group(proc)
            rec["group_killed"] = True
        rec["interrupted"] = C.what(e)
        v, reason = ERROR, f"interrupted ({rec['interrupted']})" + (
            "; pytest process group killed" if rec.get("group_killed") else "")
    except Exception as e:  # noqa: BLE001 - an instrument fault is never a verdict on the code
        v, reason = ERROR, f"gate fault: {type(e).__name__}: {e}"
    for s in _SIGNALS:  # the record and the line go out whole; main() restores the handlers
        signal.signal(s, signal.SIG_IGN)
    rec.update(verdict=v, reason=reason, total_s=round(time.time() - t_start, 1))
    # G12: the record goes down before the verdict is printed.
    try:
        C.append_record(record, rec)
    except OSError as e:
        print(_line(ERROR, rc, counts, tree_hash, f"record write failed: {e}"))
        return EXIT[ERROR]
    print(_line(v, rc, counts, tree_hash, reason))
    return EXIT[v]


if __name__ == "__main__":
    try:
        _code = main()
    except BaseException as _e:  # noqa: BLE001 - a crash or a late signal must never exit 1 (RED)
        print(_line(ERROR, None, None, None, f"gate fault: {type(_e).__name__}: {_e}"))
        _code = EXIT[ERROR]
    sys.exit(_code)
