#!/usr/bin/env python3
"""fg_mutrun: run text-exact mutants against named tests and print verdicts the tested tree
cannot fake (shared-tools loop, slice S3; behaviour M1-M10 of the loop's design doc).

    fg_mutrun.py --tree DIR --python PY --spec FILE.json --record FILE --tag T
                 [--ids ID ...] [--timeout S] [--max-load F] [--wait S]

The spec is a JSON list of ``{"id", "file", "old", "new", "tests", "what"}``: replace the one
occurrence of ``old`` in ``file`` (relative to DIR) with ``new`` and run ``tests`` (paths or
node ids inside DIR).

Exit 0: every mutant KILLED and both controls green. 1: a mutant SURVIVED and nothing errored.
2: any ERROR, NOT-APPLIED, VOID, control failure, ABORT or usage error.

- M3/M7  the union of the tests runs unmutated before (CONTROL-BEFORE) and after (CONTROL-AFTER);
  no mutant runs on a red control, and a red CONTROL-AFTER makes every verdict VOID;
- M4     a mutant is proven applied (re-read, md5 changed, ``git diff`` names exactly its file);
- M5     the file is restored from its pristine bytes in ``finally`` and checked (md5, porcelain);
         a mismatch is an ABORT that leaves the tree as it is;
- M6     KILLED only on pytest rc 1 with a failure in that run's own fresh junit file: rc 1 also
         means "No module named pytest", and rc 2/3/4/5 are errors, never kills (rc 2 even with
         a failure in the junit); SURVIVED, and a green control, need at least one PASSED test;
- M10    the index is never touched (git runs with ``--no-optional-locks``), nothing is committed.

It reuses the gate's own code, which lives in ``fg_common.py`` beside it (slice S3c; loaded
by path, bound here as ``fg``): G0 (its own ``forge_gate.paths``), G3 (the environment built
from nothing), G4/G5 preflights, G6 (load guard) and G7/G8 (a fresh junit per run, own process
group, wall timeout). Stdlib only, Python 3.10+.
"""
from __future__ import annotations

import importlib.util
import json
import os
import platform
import re
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path, PurePosixPath

KILLED, SURVIVED, ERROR, NOT_APPLIED, VOID, ABORT = (
    "KILLED", "SURVIVED", "ERROR", "NOT-APPLIED", "VOID", "ABORT")
SPEC_KEYS = ("id", "file", "old", "new", "tests", "what")
ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")


def _load_common():
    """The gate's shared code, fg_common.py, from THIS tool's own directory (never an installed
    or tested copy)."""
    here = Path(__file__).resolve().parent / "fg_common.py"
    spec = importlib.util.spec_from_file_location("fg_common_for_fg_mutrun", here)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


try:
    fg = _load_common()
    MutrunError = fg.GateError
    _GATE_LOAD_ERROR = None
except Exception as _e:  # noqa: BLE001 - reported by main() as a usage-class error, exit 2
    fg, _GATE_LOAD_ERROR = None, f"{type(_e).__name__}: {_e}"

    class MutrunError(Exception):
        """Stands in for fg_common.GateError when fg_common.py cannot be loaded."""


class _Interrupted(BaseException):
    """SIGTERM/SIGHUP/SIGINT: unwind through every ``finally`` so the mutated file is restored."""


_SIGNALS = (signal.SIGTERM, signal.SIGHUP, signal.SIGINT)
# S3e (F4): while run_pytest's Popen runs, a signal is only noted here, not raised: raised inside
# Popen it would unwind before ``proc`` is assigned and leave the new pytest group running.
_DEFER = {"on": False, "signum": None}


def _on_signal(signum, _frame):
    if _DEFER["on"]:
        if _DEFER["signum"] is None:
            _DEFER["signum"] = signum
        return
    # All three are ignored before unwinding, so no second signal of ANY kind can break the
    # restore that the unwinding runs (S3 review r2 F2). main() puts the old handlers back.
    for s in _SIGNALS:
        signal.signal(s, signal.SIG_IGN)
    raise _Interrupted(f"signal {signum}")


def _block_signals():
    """Block the three signals; return the interrupt that a pending handler raised while doing
    so (or None), never raise it. CPython's ``pthread_sigmask`` runs pending handlers AFTER it has
    changed the mask (S3c probe A, both hosts): the call raises with the mask already blocked and
    its return value lost (S3b-2 review N1). So no caller relies on that return value, and an
    interrupt raised here is handed back to be raised where the restore still runs."""
    caught = None
    for _ in range(4):  # after the first handler ran, all three are ignored: the next try holds
        try:
            signal.pthread_sigmask(signal.SIG_BLOCK, _SIGNALS)
            return caught
        except (_Interrupted, KeyboardInterrupt) as e:
            caught = caught or e
    return caught


def _set_mask(mask):
    """SIG_SETMASK, returning (never raising) the interrupt a pending handler raised inside the
    call (probe A: the mask is already set by then). S3d review F1: the restore's unmask is where
    a signal that arrived while blocked is delivered, and its bookkeeping must still run."""
    try:
        signal.pthread_sigmask(signal.SIG_SETMASK, mask)
    except (_Interrupted, KeyboardInterrupt) as e:
        return e
    return None


def _unblocked(base_mask, fn, *args):
    """Run ``fn`` under the caller's own mask (an interrupt must reach ``proc.wait`` and kill the
    pytest group), and block the three signals again before returning (N1): the step out of the
    run happens with them blocked, inside the caller's try, so its restore always runs."""
    try:
        signal.pthread_sigmask(signal.SIG_SETMASK, base_mask)
        return fn(*args)
    finally:
        pending = _block_signals()
        if pending is not None:
            raise pending


def _write_bytes(p: Path, data: bytes) -> None:  # module level: tests inject a faulty writer
    with open(p, "wb") as fh:
        fh.write(data)


def _read_back(p: Path) -> bytes:  # module level: tests inject a faulty read-back (S3d)
    return p.read_bytes() if p.exists() else b""


# ---- spec ------------------------------------------------------------------------------------

def load_spec(path: Path, ids):
    """The mutants to run, validated, in spec order; ``ids`` (if given) selects by id."""
    try:
        raw = path.read_bytes()
        muts = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError) as e:
        raise MutrunError(f"spec {path}: {e}") from None
    if not isinstance(muts, list) or not muts:
        raise MutrunError(f"spec {path}: not a non-empty JSON list")
    seen = set()
    for i, m in enumerate(muts):
        if not isinstance(m, dict) or set(m) != set(SPEC_KEYS):
            raise MutrunError(f"spec entry {i}: keys must be exactly {', '.join(SPEC_KEYS)}")
        if not (isinstance(m["id"], str) and ID_RE.match(m["id"])):
            raise MutrunError(f"spec entry {i}: id {m['id']!r} is not [A-Za-z0-9._-]+")
        if m["id"] in seen:
            raise MutrunError(f"spec: duplicate id {m['id']!r}")
        seen.add(m["id"])
        for k in ("file", "old", "new", "what"):
            if not isinstance(m[k], str):
                raise MutrunError(f"spec {m['id']}: {k} must be a string")
        if not m["old"] or not m["file"]:
            raise MutrunError(f"spec {m['id']}: file and old must be non-empty")
        if not (isinstance(m["tests"], list) and m["tests"]
                and all(isinstance(t, str) and t for t in m["tests"])):
            raise MutrunError(f"spec {m['id']}: tests must be a non-empty list of strings")
    if ids:
        unknown = [i for i in ids if i not in seen]
        if unknown:
            raise MutrunError(f"--ids not in the spec: {', '.join(unknown)}")
        muts = [m for m in muts if m["id"] in ids]
    return muts, fg._md5b(raw)


def check_tests(tests, tree: Path, paths) -> None:
    """Each test entry is a path or node id inside DIR, never a pytest option (S2 review F8)."""
    for t in tests:
        try:
            fg._extra_path(t, tree, paths)
        except MutrunError as e:
            raise MutrunError(f"spec test entry {t!r}: {e}") from None


def mutant_file(m, tree: Path, paths) -> Path:
    """M4: ``file`` is relative, has no ``..``, and resolves inside DIR."""
    f = m["file"]
    pp = PurePosixPath(f)
    if pp.is_absolute() or os.path.isabs(f) or ".." in pp.parts:
        raise MutrunError(f"file {f!r} must be relative, with no '..'")
    target = paths.real(tree / pp)
    if not paths.is_under(target, tree):
        raise MutrunError(f"file {f!r} resolves outside the tested tree ({target})")
    if not target.is_file():
        raise MutrunError(f"file {f!r} is not a file in the tested tree")
    return tree / pp


# ---- M6: verdict -------------------------------------------------------------------------------

def _passed(c) -> int:
    return c["tests"] - c["failures"] - c["errors"] - c["skipped"]


def mutant_verdict(rc, counts, error) -> str:
    """KILLED only on rc 1 with tests >= 1 and failures >= 1 in this run's own junit;
    SURVIVED only on rc 0 with at least one PASSED test and no failure or error (an all-skipped
    run proves nothing, S3 review F4); anything else is ERROR."""
    if error is not None or counts is None:
        return ERROR
    t, f, e = counts["tests"], counts["failures"], counts["errors"]
    if rc == 1 and t >= 1 and f >= 1:
        return KILLED
    if rc == 0 and _passed(counts) >= 1 and f == 0 and e == 0:
        return SURVIVED
    return ERROR


def control_green(res) -> bool:
    c = res["counts"]
    return (res["error"] is None and c is not None and res["rc"] == 0 and _passed(c) >= 1
            and c["failures"] == 0 and c["errors"] == 0 and res.get("porcelain_after") == "")


# ---- G7/G8: one pytest run, fresh files -------------------------------------------------------

def run_pytest(ns, tree: Path, rdir: Path, label: str, tests) -> dict:
    run = fg._run_id()
    j = rdir / f"junit-{ns.tag}-{label}-{run}.xml"
    log = rdir / f"log-{ns.tag}-{label}-{run}.txt"
    pyc = rdir / f"pyc-{ns.tag}-{label}-{run}"
    env = fg.build_env(os.environ, tree, pyc)
    res = {"label": label, "junit": str(j), "log": str(log), "pycache_prefix": str(pyc),
           "rc": None, "counts": None, "last_line": "", "error": None, "duration_s": None}
    if j.exists():
        res["error"] = f"junit {j} exists before the run"
        return res
    cmd = [ns.python, "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--junitxml={j}", *tests]
    started_ns = time.time_ns()
    t0 = time.monotonic()
    try:
        lf = open(log, "xb")  # a log that already exists is never overwritten
    except FileExistsError:
        res["error"] = f"log {log} exists before the run"
        return res
    with lf:
        proc = None
        _DEFER.update(on=True, signum=None)  # S3e (F4): a signal inside Popen waits for ``proc``
        try:
            try:
                proc = subprocess.Popen(cmd, cwd=tree, env=env, stdout=lf,
                                        stderr=subprocess.STDOUT, start_new_session=True,
                                        preexec_fn=fg.default_signals_in_child(_SIGNALS))
            finally:
                _DEFER["on"] = False
                if _DEFER["signum"] is not None:  # raised here, ``proc`` held (or no child at all)
                    _on_signal(_DEFER["signum"], None)
            rc = proc.wait(timeout=ns.timeout)
        except BaseException as e:  # a timeout, or an interrupt: the whole group goes first
            if proc is not None:
                fg.kill_group(proc)
            if not isinstance(e, subprocess.TimeoutExpired):
                raise
            res["duration_s"] = round(time.monotonic() - t0, 1)
            res["error"] = f"timeout after {ns.timeout:.0f} s (process group killed)"
            return res
    res["duration_s"] = round(time.monotonic() - t0, 1)
    res["rc"] = rc
    res["last_line"] = fg.last_line(log)
    try:
        res["counts"], _ = fg.read_junit(j, started_ns)
    except MutrunError as e:
        res["error"] = str(e)
    return res


# ---- main ----------------------------------------------------------------------------------------

def _parse(argv):
    ap = fg.strict_parser("fg_mutrun.py")  # -h is a usage error; a usage error raises
    for a in ("--tree", "--python", "--spec", "--record", "--tag"):
        ap.add_argument(a, required=True)
    ap.add_argument("--ids", nargs="+", default=[])
    ap.add_argument("--timeout", type=float, default=fg.DEFAULT_TIMEOUT_S)
    ap.add_argument("--max-load", type=float, default=None, dest="max_load")
    ap.add_argument("--wait", type=float, default=fg.DEFAULT_WAIT_S)
    return ap.parse_args(argv)


def _ctl_line(name, res) -> str:
    n = res["counts"]["tests"] if res["counts"] is not None else "none"
    tail = "" if control_green(res) else " RED"
    if not control_green(res):  # the reason is in the record, so the line stays as in the doc
        res["why"] = res["error"] or ("tree changed: " + res["porcelain_after"].rstrip("\n")
                                      .replace("\n", "; ") if res.get("porcelain_after")
                                      else res["last_line"])
    return f"{name}{tail} rc={'none' if res['rc'] is None else res['rc']} tests={n}"


def _why_line(res) -> str:
    """The line after a RED control line: its reason, so the output points at it (r2 F4)."""
    return "why=" + str(res.get("why") or "").rstrip("\n").replace("\n", "; ")


def _mut_line(r) -> str:
    rc = "none" if r["rc"] is None else r["rc"]
    tail = r["reason"] or r["last_line"]
    return f"{r['id']} {r['verdict']} rc={rc} | {r['what']} | {tail}"


def _restore(r, p, pristine, env, tree) -> None:
    # M5: restore from the pristine bytes, whatever happened above, then check.
    restore_err = None
    try:
        _write_bytes(p, pristine)
    except OSError as e:
        restore_err = f"restore write failed: {e}"
    # S3d: a failed check is recorded beside the outcome, never raised: raising here would
    # replace an interrupt in flight, and lose md5_restored with it.
    try:
        r["md5_restored"] = fg._md5b(_read_back(p))
    except OSError as e:
        r["md5_restored"], r["md5_restored_error"] = None, f"reading the file back failed: {e}"
        restore_err = restore_err or f"restore check failed: {r['md5_restored_error']}"
    try:
        porcelain = fg._porcelain(env, tree)
    except (MutrunError, OSError) as e:
        porcelain, r["porcelain_error"] = None, f"{type(e).__name__}: {e}"
        restore_err = restore_err or f"restore check failed: git status: {r['porcelain_error']}"
    r["porcelain_after_restore"] = porcelain
    if restore_err:
        r["verdict"], r["reason"] = ABORT, restore_err
    elif r["md5_restored"] != r["md5_pristine"]:
        r["verdict"] = ABORT
        r["reason"] = f"restore mismatch: md5 {r['md5_restored']} != pristine {r['md5_pristine']}"
    elif porcelain != "":
        r["verdict"] = ABORT
        r["reason"] = "tree not clean after restore: " + porcelain.rstrip("\n").replace("\n", "; ")


def _one_mutant(m, ns, tree, rdir, paths, env, sink) -> dict:
    r = {"id": m["id"], "what": m["what"], "file": m["file"], "verdict": ERROR, "rc": None,
         "reason": "", "last_line": "", "run": None}
    sink.append(r)  # in the record before anything is written: an interrupt cannot lose it (r2 F1)
    try:
        p = mutant_file(m, tree, paths)
        check_tests(m["tests"], tree, paths)
    except MutrunError as e:
        r["reason"] = str(e)
        return r
    pristine = p.read_bytes()
    r["md5_pristine"] = fg._md5b(pristine)
    try:
        text = pristine.decode("utf-8")
    except UnicodeDecodeError as e:
        r["reason"] = f"file is not UTF-8: {e}"
        return r
    n = text.count(m["old"])
    if n != 1:
        r["verdict"], r["reason"] = NOT_APPLIED, f"NOT-APPLIED: old text found {n}x"
        return r
    mutated = text.replace(m["old"], m["new"], 1).encode("utf-8")
    try:
        base_mask = signal.pthread_sigmask(signal.SIG_BLOCK, [])  # read only; nothing written yet
    except (_Interrupted, KeyboardInterrupt) as e:
        r["reason"] = f"interrupted: {fg.what(e)}"
        raise
    intr = None  # the interrupt this mutant unwinds with, if any (S3d: never lost to the restore)
    try:
        # N1: the three signals are BLOCKED from before the write to the end of the restore,
        # except while pytest runs (_unblocked). So the step from the try body into the restore
        # below never has a window where a first signal can skip it.
        pending = _block_signals()
        if pending is not None:
            raise pending
        _write_bytes(p, mutated)
        # M4: prove the mutant is on disk, and is the only change in the tree.
        now = p.read_bytes()
        r["md5_mutated"] = fg._md5b(now)
        diff = [ln for ln in fg._git(env, tree, "diff", "--name-only").splitlines() if ln]
        r["diff_names"] = diff
        if m["new"].encode("utf-8") not in now:
            r["verdict"], r["reason"] = NOT_APPLIED, "NOT-APPLIED: new text not in the file after writing"
        elif r["md5_mutated"] == r["md5_pristine"]:
            r["verdict"], r["reason"] = NOT_APPLIED, "NOT-APPLIED: file md5 unchanged after writing"
        elif diff != [PurePosixPath(m["file"]).as_posix()]:
            r["verdict"], r["reason"] = NOT_APPLIED, f"NOT-APPLIED: git diff names {diff}, not [{m['file']}]"
        else:
            res = _unblocked(base_mask, run_pytest, ns, tree, rdir, f"m-{m['id']}", m["tests"])
            r["run"] = res
            r["rc"], r["last_line"] = res["rc"], res["last_line"]
            r["verdict"] = mutant_verdict(res["rc"], res["counts"], res["error"])
            if res["error"]:
                r["reason"] = res["error"]
    except (MutrunError, OSError) as e:
        r["verdict"], r["reason"] = ERROR, f"{type(e).__name__}: {e}"
    except (_Interrupted, KeyboardInterrupt) as e:  # say so in the result; restore below
        intr = e
        r["verdict"], r["reason"] = ERROR, f"interrupted: {fg.what(e)}"
        raise
    except BaseException as e:  # an instrument fault is never labelled an interrupt (S3b-1 F4)
        r["verdict"], r["reason"] = ERROR, f"fault: {type(e).__name__}: {e}"
        raise
    finally:
        # The three signals are BLOCKED during the restore and its checks (review S3b-1 F3): one
        # that lands here stays pending until they are done, then unwinds as usual. They are
        # already blocked here (N1); this block only makes sure, and cannot skip the restore.
        pending, late = _block_signals(), None
        try:
            _restore(r, p, pristine, env, tree)
        finally:
            late = _set_mask(base_mask)
        pending = pending or late
        intr = intr or pending
        if intr is not None:  # the interrupt stays the outcome; a failed restore sits beside it
            r["interrupted"] = fg.what(intr)
            if r["verdict"] == ABORT:
                if not r["reason"].startswith("interrupted"):
                    r["reason"] = f"interrupted: {r['interrupted']}; {r['reason']}"
            else:
                r["verdict"], r["reason"] = ERROR, f"interrupted: {r['interrupted']}"
        if pending is not None:
            raise pending
    return r


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    t_start = time.time()
    if fg is None:
        print(f"MUTRUN ERROR cannot load fg_common.py beside the tool: {_GATE_LOAD_ERROR}")
        return 2
    try:
        paths = fg._own_paths()  # G0
        ns = _parse(argv)
    except MutrunError as e:
        print(f"MUTRUN ERROR {e}")
        return 2
    tree = paths.real(ns.tree)
    record = paths.real(ns.record)
    rdir = record.parent
    if fg.inside_tree(record, tree, paths):
        print(f"MUTRUN ERROR record {record} is inside the tested tree {tree}")
        return 2
    tool = Path(__file__).resolve()
    common = Path(fg.__file__).resolve()
    rec = {"utc": fg.utc(t_start), "tag": ns.tag,
           "argv": argv, "tool": str(tool), "tool_md5": fg._md5(tool), "tool_head": None,
           "gate_file": None, "gate_md5": None,  # S3c: fg_gate.py is no longer loaded
           "common_file": str(common), "common_md5": fg._md5(common),
           "paths_file": str(paths.real(paths.__file__)), "host": socket.gethostname(),
           "platform": platform.platform(), "python": ns.python, "tree": str(tree),
           "umask": fg._umask(), "spec": str(paths.real(ns.spec)), "spec_md5": None,
           "control_before": None, "mutants": [], "control_after": None, "porcelain_after": None}
    out = []  # printed after the record is written (M9), so a VOID can replace a verdict first
    code = 2
    env = None
    run = fg._run_id()  # G3: the helper pycache dirs are fresh for every run too (S3 review F6)
    rec["pycache_git"] = str(rdir / f"pyc-{ns.tag}-git-{run}")
    rec["pycache_preflight"] = str(rdir / f"pyc-{ns.tag}-preflight-{run}")
    old_handlers = fg.install_handlers(_SIGNALS, _on_signal)  # an inherited SIG_IGN stays (S3e)
    try:
        rdir.mkdir(parents=True, exist_ok=True)
        muts, rec["spec_md5"] = load_spec(Path(ns.spec), ns.ids)
        env = fg.build_env(os.environ, tree, Path(rec["pycache_git"]))
        rec["PATH"] = env["PATH"]
        fg.require_tools(env, rec)  # G4
        rec["tool_head"] = fg.tool_head(env, tool)
        fg.check_toplevel(env, tree, paths)
        # M2: clean before, nothing staged.
        before = fg._porcelain(env, tree)
        rec["head"] = fg._git(env, tree, "rev-parse", "HEAD").strip()
        rec["porcelain_before"] = before
        if before:
            raise MutrunError(f"tree not clean before: {before.strip().splitlines()[:5]}")
        union = sorted({t for m in muts for t in m["tests"]})
        check_tests(union, tree, paths)
        fg.import_preflight(ns.python, tree,  # G5
                            fg.build_env(os.environ, tree, Path(rec["pycache_preflight"])), paths, rec)
        thr = fg.threshold(ns.max_load, os.environ)  # G6
        rec["max_load"] = thr
        rec["load_before"], rec["waited_s"] = fg.wait_for_load(thr, ns.wait)
        # M3: CONTROL-BEFORE.
        cb = run_pytest(ns, tree, rdir, "control-before", union)
        cb["porcelain_after"] = fg._porcelain(env, tree)
        rec["control_before"] = cb
        print(_ctl_line("CONTROL-BEFORE", cb), flush=True)
        if not control_green(cb):
            rec["porcelain_after"] = cb["porcelain_after"]
            out.append(_why_line(cb))
            out.append(f"porcelain_after={cb['porcelain_after'].rstrip(chr(10)).replace(chr(10), '; ')}")
            raise _Done()
        results, aborted = rec["mutants"], False  # each result is recorded before it runs
        for m in muts:
            r = _one_mutant(m, ns, tree, rdir, paths, env, results)
            if r["verdict"] == ABORT:
                aborted = True
                break
        if aborted:
            ctl = ["CONTROL-AFTER NOT-RUN (abort: the tree is left as it is)"]
        else:
            ca = run_pytest(ns, tree, rdir, "control-after", union)  # M7
            ca["porcelain_after"] = fg._porcelain(env, tree)
            rec["control_after"] = ca
            ctl = [_ctl_line("CONTROL-AFTER", ca)]
            if not control_green(ca):
                ctl.append(_why_line(ca))
                for r in results:
                    r["verdict_before_void"], r["verdict"] = r["verdict"], VOID
        porcelain = fg._porcelain(env, tree)
        rec["porcelain_after"] = porcelain
        surv = [r["id"] for r in results if r["verdict"] == SURVIVED]
        errs = [r["id"] for r in results if r["verdict"] not in (KILLED, SURVIVED)]
        nkill = sum(r["verdict"] == KILLED for r in results)
        out += [_mut_line(r) for r in results]
        out += [*ctl, f"SURVIVORS: {' '.join(surv) or 'none'}", f"ERRORS: {' '.join(errs) or 'none'}",
                f"KILLED: {nkill}/{len(muts)}",
                f"porcelain_after={porcelain.rstrip(chr(10)).replace(chr(10), '; ')}"]
        if aborted or errs or rec["control_after"] is None or not control_green(rec["control_after"]):
            code = 2
        elif surv:
            code = 1
        else:
            code = 0
    except _Done:
        code = 2
    except MutrunError as e:
        out.append(f"MUTRUN ERROR {e}")
        code = 2
    except (_Interrupted, KeyboardInterrupt) as e:
        # The mutant's own finally has TRIED the restore: report what the tree IS, never what
        # the restore was meant to do (S3 review r2 F1).
        left, unknown = None, None
        if env is not None:
            try:
                left = fg._porcelain(env, tree)
            except Exception as pe:  # noqa: BLE001 - reported, never a verdict
                unknown = f"{type(pe).__name__}: {pe}"
        rec["porcelain_after"] = left  # None when it could not be read: never a guess (N2)
        if unknown is not None:
            rec["porcelain_error"] = unknown
            state = (f"the tree state is UNKNOWN: reading it with git status failed ({unknown}); "
                     "check the tree by hand before trusting it")
        elif left is None:
            state = "no mutant had been applied"
        elif left == "":
            state = "the tree is clean"
        else:
            state = "the tree is LEFT AS IT IS: " + left.rstrip("\n").replace("\n", "; ")
        out.append(f"MUTRUN ERROR interrupted ({fg.what(e)}): {state}; see the record")
        code = 2
    except Exception as e:  # noqa: BLE001 - an instrument fault is never a verdict
        out.append(f"MUTRUN ERROR fault: {type(e).__name__}: {e}")
        code = 2
    finally:
        for s, h in old_handlers.items():
            signal.signal(s, h)
    rec.update(exit=code, summary=out, total_s=round(time.time() - t_start, 1))
    try:  # M9: the record goes down before the summary is printed
        fg.append_record(record, rec)
    except OSError as e:
        print(f"MUTRUN ERROR record write failed: {e}")
        return 2
    for ln in out:
        print(ln)
    return code


class _Done(Exception):
    """CONTROL-BEFORE was red: no mutant runs."""


if __name__ == "__main__":
    try:
        _code = main()
    except BaseException as _e:  # noqa: BLE001 - a crash must never exit 1 (the SURVIVED code)
        print(f"MUTRUN ERROR fault: {type(_e).__name__}: {_e}")
        _code = 2
    sys.exit(_code)
