"""Child process for scout's chaos engine (build step 6b-2; design v1.4.5 §4.3, decision L3).

Run as ``python -I chaos_runner.py <lib_root> <probe> <out.json>`` by ``engines/chaos.py`` only.
It loads the ported validator (``chaos_port.py``) from its own folder, points the port's ``LIB``
at ``<lib_root>`` and its ``HERE`` at the out file's folder, and then:

1. THE GATE: runs the port's own ``negative_controls()``, the exact code the validator's
   ``main()`` runs before any probe, GATE_RUNS times in a row. One survival in any run fails the
   gate. Some NCs are races (on the deploy host ``seqlock_snapshot``'s NC dies about 1 run in 50),
   so one lucky run must not pass a gate the validator's own run would fail. With repeats, a
   scout PASS is strictly rarer than a validator PASS (p^3 <= p), never easier. The validator writes no report when it returns False (ABORT).
   Scout is STRICTER in one way. The validator counts these as a clean death of an unsafe twin:
   - a raise inside the NC;
   - a failed Rust compile, a missing ``rustc``, or a timeout.
   This child records each such case as ``unclean``, and the parent reads any ``unclean`` NC as
   a gate failure. So scout's gate can never pass where the NCs did not really run.
2. THE PROBE: runs the port's own ``scan(only={probe})``, so the card and its verdict are made
   by the validator's code, not a re-implementation.
3. Records which attributes the probe read on the loaded pattern module (``touched``), so the
   parent backs only the functions the probe drove directly.

The JSON written to ``<out.json>``: ``{"probe", "gate": {"ok", "survived", "unclean"}, "card",
"touched"}``, or ``{"error": ...}`` if the harness itself failed. Everything the port or a
pattern prints goes to stderr. The parent never reads stdout.
"""
import contextlib
import importlib.util
import json
import os
import sys
import traceback
import types
from pathlib import Path

GATE_RUNS = 3
GATE_ONLY = "__gate_only__"   # must equal engines/chaos.py GATE_ONLY
_TOUCHED: set = set()
_RECORD = [False]


class _Recording(types.ModuleType):
    """A loaded pattern module that records the public names read on it while recording is on."""

    def __getattribute__(self, name):
        if _RECORD[0] and not name.startswith("_"):
            _TOUCHED.add(name)
        return super().__getattribute__(name)


def _port(lib_root: Path, here: Path):
    spec = importlib.util.spec_from_file_location("forge_gate_chaos_port",
                                                  Path(__file__).with_name("chaos_port.py"))
    port = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(port)
    port.LIB = Path(lib_root)
    port.HERE = Path(here)
    orig_load = port._load

    def _load(relpath, name):
        m = orig_load(relpath, name)
        m.__class__ = _Recording
        return m

    port._load = _load
    return port


def _gate(port) -> dict:
    """The validator's own negative_controls(), with each NC wrapped to record an unclean death."""
    unclean: dict = {}
    current = [None]
    orig_rust = port._rust_chaos

    def rust_spy(relpath, probe_main, fixture=False):
        try:
            h = orig_rust(relpath, probe_main, fixture)
        except BaseException as e:            # missing rustc, TimeoutExpired, ...
            unclean.setdefault(current[0], f"rust harness raised {type(e).__name__}")
            raise
        if "compile_err" in h:
            unclean.setdefault(current[0], "rust compile failed")
        return h

    def wrap(name, nc):
        def run():
            current[0] = name
            try:
                return nc()
            except BaseException as e:
                unclean.setdefault(name, f"raised {type(e).__name__}: {e}"[:300])
                raise
        return run

    died: dict = {}
    real = port.PROBES
    wrapped = []
    for name, rel, probe, nc, fault in real:
        w = wrap(name, nc)

        def record(w=w, name=name):
            r = w()
            died[name] = bool(r)
            return r
        wrapped.append((name, rel, probe, record, fault))
    port.PROBES = wrapped
    port._rust_chaos = rust_spy
    try:
        ok = port.negative_controls()
    finally:
        port.PROBES = real
        port._rust_chaos = orig_rust
    # negative_controls() counts a raise as a death; died[] holds only the NCs that returned.
    survived = sorted(n for n, d in died.items() if d is False)
    return {"ok": bool(ok), "survived": survived, "unclean": unclean}


def _gates(port, runs: int = GATE_RUNS, on_run=None) -> dict:
    """The gate GATE_RUNS times; ok only if every run was ok. Survived/unclean are unions.
    ``on_run`` gets the running union after EACH run, so a child that dies or hangs later has
    already written what it saw (review F2: a survival must never go unrecorded)."""
    survived, unclean, ok = set(), {}, True
    for i in range(runs):
        g = _gate(port)
        ok = ok and g["ok"]
        survived |= set(g["survived"])
        for k, v in g["unclean"].items():
            unclean.setdefault(k, v)
        if on_run is not None:
            on_run({"ok": ok, "survived": sorted(survived), "unclean": dict(unclean), "runs": i + 1})
    return {"ok": ok, "survived": sorted(survived), "unclean": unclean, "runs": runs}


def _write(out: Path, obj) -> None:
    """Atomic replace: the parent reads the last whole write, never half a file."""
    tmp = out.with_name(out.name + ".part")
    tmp.write_text(json.dumps(obj))
    os.replace(tmp, out)


def main(argv) -> int:
    lib_root, probe, out = Path(argv[1]), argv[2], Path(argv[3])
    seen = {}

    def on_run(g):
        seen["gate"] = g
        _write(out, {"probe": probe, "partial_gate": g})
    try:
        with contextlib.redirect_stdout(sys.stderr):
            port = _port(lib_root, out.parent)
            if probe == GATE_ONLY:                 # calibration: the gate alone, no probe
                gate = _gates(port, on_run=on_run)
            elif probe not in {p[0] for p in port.PROBES}:
                raise ValueError(f"no such probe: {probe!r}")
            else:
                gate = _gates(port, on_run=on_run)
        if probe == GATE_ONLY:
            _write(out, {"probe": probe, "gate": gate})
            return 0
        with contextlib.redirect_stdout(sys.stderr):
            _TOUCHED.clear()
            _RECORD[0] = True
            try:
                cards = port.scan(only={probe}, verbose=False)
            finally:
                _RECORD[0] = False
        if len(cards) != 1:
            raise ValueError(f"scan(only={{{probe!r}}}) returned {len(cards)} cards, want 1")
        res = {"probe": probe, "gate": gate, "card": cards[0], "touched": sorted(_TOUCHED)}
    except BaseException as e:
        res = {"error": f"{type(e).__name__}: {e}"[:500], "trace": traceback.format_exc()[-1500:]}
        if "gate" in seen:              # the gate runs it finished still count (review F2)
            res["partial_gate"] = seen["gate"]
    _write(out, res)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
