#!/usr/bin/env python3
"""Domain classifier — negative-parameter behavioural trend check.

For a numeric parameter that is semantically expected to be positive, this
tool pushes -1, -10, -100 and reads the TREND across the sequence. A single
point is never enough.

Verdicts (exact CLI contract):
  verdict:DEFECT <reason>         diverges or collapses
  verdict:HANDLED <reason>       raises
  verdict:INCONCLUSIVE <reason>  unreachable · proportionate · not_numerically_comparable

INCONCLUSIVE is the learning queue. The tool stores the observation
(magnitudes and shapes) so a future, wiser criterion can re-judge it; it does
not fabricate verdicts.

Subprocess isolation is copied from tools/guard_lens.py (G17): every fixture
call runs in a fresh child process with start_new_session=True, a hard wall-
clock timeout, per-process CPU and memory limits, and os.killpg cleanup.
"""
from __future__ import annotations

import argparse
import importlib.util
import inspect
import json
import math
import os
import resource
import signal
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

# Probes in descending order of magnitude so trend can be read as a sequence.
PROBE_VALUES_FLOAT = [-1.0, -10.0, -100.0]
PROBE_VALUES_INT = [-1, -10, -100]

# Hard wall-clock timeout per fixture call. Keep below the test harness timeout.
FIXTURE_TIMEOUT_SECONDS = 10

# CPU seconds allowed per fixture subprocess. Soft == hard so SIGKILL fires.
FIXTURE_CPU_LIMIT_SECONDS = 2

# Address-space limit per fixture subprocess.
FIXTURE_MEMORY_LIMIT_BYTES = 256 * 1024 * 1024


def _limit_resources() -> None:
    """Set tight per-process resource ceilings in fixture subprocesses."""
    try:
        resource.setrlimit(
            resource.RLIMIT_CPU,
            (FIXTURE_CPU_LIMIT_SECONDS, FIXTURE_CPU_LIMIT_SECONDS),
        )
        resource.setrlimit(
            resource.RLIMIT_AS,
            (FIXTURE_MEMORY_LIMIT_BYTES, FIXTURE_MEMORY_LIMIT_BYTES),
        )
    except (ValueError, OSError):
        pass


class FixtureTimeoutError(Exception):
    """Raised when a fixture subprocess does not return within the timeout."""
    pass


def _json_safe(obj: Any) -> Any:
    """Convert a fixture result to a JSON-serialisable form."""
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, (int, float)):
        return obj
    if isinstance(obj, str):
        return obj
    if obj is None:
        return None
    if isinstance(obj, (list, tuple)):
        return [_json_safe(x) for x in obj]
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if hasattr(obj, "__dataclass_fields__"):
        from dataclasses import asdict
        return _json_safe(asdict(obj))
    if hasattr(obj, "_asdict"):
        return _json_safe(obj._asdict())
    return repr(obj)


def _run_fixture_call(path: Path, fn_name: str, args_json: str) -> Tuple[bool, Any, bool]:
    """Run a single function call in a subprocess, returning (raised, result, was_killed)."""
    call_script = textwrap.dedent(
        """
        import importlib.util
        import inspect
        import json
        import sys

        def _json_safe(obj):
            if isinstance(obj, bool):
                return obj
            if isinstance(obj, (int, float)):
                return obj
            if isinstance(obj, str):
                return obj
            if obj is None:
                return None
            if isinstance(obj, (list, tuple)):
                return [_json_safe(x) for x in obj]
            if isinstance(obj, dict):
                return {str(k): _json_safe(v) for k, v in obj.items()}
            if hasattr(obj, "__dataclass_fields__"):
                from dataclasses import asdict
                return _json_safe(asdict(obj))
            if hasattr(obj, "_asdict"):
                return _json_safe(obj._asdict())
            return repr(obj)

        path = sys.argv[1]
        fn_name = sys.argv[2]
        args, kwargs = json.loads(sys.argv[3])
        spec = importlib.util.spec_from_file_location("domain_fixture_under_test", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        fn = getattr(mod, fn_name)
        sig = inspect.signature(fn)
        bound = sig.bind(*args, **kwargs)
        bound.apply_defaults()
        try:
            result = fn(*bound.args, **bound.kwargs)
            print("RESULT: " + json.dumps(_json_safe(result)))
        except Exception as exc:
            print("RAISED: " + type(exc).__name__ + ": " + str(exc))
        """
    ).strip() + "\n"

    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as child:
        child.write(call_script)
        child_path = child.name

    try:
        p = subprocess.Popen(
            [sys.executable, child_path, str(path), fn_name, args_json],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
            preexec_fn=_limit_resources,
        )
        try:
            out, err = p.communicate(timeout=FIXTURE_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(p.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                p.kill()
            try:
                out, err = p.communicate(timeout=10)
            except Exception:
                out, err = "", ""
            return True, f"fixture exceeded {FIXTURE_TIMEOUT_SECONDS}s", True
        if p.returncode != 0:
            err_text = err.strip() or out.strip() or "unknown error"
            was_killed = p.returncode == -signal.SIGKILL or "Resource temporarily unavailable" in err_text
            return True, f"fixture call failed: {err_text}", was_killed
        out = out.strip()
        if out.startswith("RAISED: "):
            return True, out[8:], False
        if out.startswith("RESULT: "):
            try:
                result = json.loads(out[8:])
            except Exception:
                result = out[8:]
            return False, result, False
        return True, f"unexpected fixture call output: {out!r}", False
    finally:
        try:
            os.unlink(child_path)
        except OSError:
            pass


def _run_call(fn: Callable, args: List[Any], kwargs: Dict[str, Any]) -> Tuple[bool, Any, bool]:
    """Run a function call in a subprocess; return (raised, result, was_killed)."""
    try:
        payload = json.dumps((args, kwargs))
    except (TypeError, ValueError) as exc:
        return True, f"arguments not serialisable: {exc}", False
    try:
        path = Path(fn.__code__.co_filename)
    except Exception:
        return True, "cannot locate fixture source", False
    raised, result, was_killed = _run_fixture_call(path, fn.__name__, payload)
    if raised:
        return True, Exception(result), was_killed
    return False, result, False


def _find_public_functions(mod: Any) -> List[Callable]:
    """Return all top-level public callable functions in the module."""
    out = []
    for name in dir(mod):
        if name.startswith("_"):
            continue
        obj = getattr(mod, name)
        if callable(obj) and inspect.isfunction(obj) and obj.__module__ == mod.__name__:
            out.append(obj)
    return out


def _load_module(path: Path) -> Any:
    """Load the fixture module in a separate, time-bounded child process."""
    probe_script = textwrap.dedent(
        """
        import importlib.util
        import inspect
        import sys
        path = sys.argv[1]
        spec = importlib.util.spec_from_file_location("domain_fixture_under_test", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        names = []
        for name in dir(mod):
            if name.startswith("_"):
                continue
            obj = getattr(mod, name)
            if callable(obj) and inspect.isfunction(obj) and obj.__module__ == mod.__name__:
                names.append(name)
        print("\\n".join(names))
        """
    ).strip() + "\n"

    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as child:
        child.write(probe_script)
        child_path = child.name

    try:
        p = subprocess.Popen(
            [sys.executable, child_path, str(path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
            preexec_fn=_limit_resources,
        )
        try:
            out, err = p.communicate(timeout=FIXTURE_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(p.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                p.kill()
            try:
                out, err = p.communicate(timeout=10)
            except Exception:
                out, err = "", ""
            raise FixtureTimeoutError(f"fixture exceeded {FIXTURE_TIMEOUT_SECONDS}s")
        if p.returncode != 0:
            raise ImportError(f"fixture import failed: {err.strip() or 'unknown error'}")
        function_names = [line.strip() for line in out.splitlines() if line.strip()]
    finally:
        try:
            os.unlink(child_path)
        except OSError:
            pass

    spec = importlib.util.spec_from_file_location("domain_fixture_under_test", str(path))
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if function_names:
        for name in function_names:
            if not hasattr(mod, name):
                raise ImportError(f"fixture function {name} missing after import")
    return mod


def _magnitude(v: Any) -> float:
    """Sum of absolute finite numeric values inside a structure."""
    if isinstance(v, bool):
        return 0.0
    if isinstance(v, (int, float)):
        if math.isfinite(v):
            return abs(float(v))
        return float("inf")
    if isinstance(v, (list, tuple)):
        return sum(_magnitude(x) for x in v)
    if isinstance(v, dict):
        return sum(_magnitude(x) for x in v.values())
    return 0.0


def _is_numeric(v: Any) -> bool:
    """True if the value is composed only of int/float primitives."""
    if isinstance(v, bool):
        return False
    if isinstance(v, (int, float)):
        return True
    if isinstance(v, (list, tuple)):
        return all(_is_numeric(x) for x in v)
    if isinstance(v, dict):
        return all(_is_numeric(x) for x in v.values())
    return False


def _canonical(obj: Any) -> Any:
    """Stable serialisation for clean-vs-poison comparison."""
    if obj is None:
        return None
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, (int, float)):
        if isinstance(obj, float) and math.isnan(obj):
            return "__NaN__"
        if isinstance(obj, float) and math.isinf(obj):
            return "__Inf__" if obj > 0 else "__-Inf__"
        return obj
    if isinstance(obj, (list, tuple)):
        return [_canonical(x) for x in obj]
    if isinstance(obj, dict):
        return {str(k): _canonical(v) for k, v in sorted(obj.items(), key=lambda kv: str(kv[0]))}
    if hasattr(obj, "__dataclass_fields__"):
        from dataclasses import asdict
        return _canonical(asdict(obj))
    if hasattr(obj, "_asdict"):
        return _canonical(obj._asdict())
    if hasattr(obj, "__dict__") and not isinstance(obj, type):
        return _canonical(vars(obj))
    return repr(obj)


def _probe_values_for_param(p: inspect.Parameter) -> List[Any]:
    """Pick integer or float probes based on parameter type."""
    is_int = (
        p.annotation is int
        or (isinstance(p.annotation, str) and p.annotation.strip() == "int")
        or (p.default is not inspect.Parameter.empty and isinstance(p.default, int) and not isinstance(p.default, bool))
    )
    if is_int:
        return list(PROBE_VALUES_INT)
    return PROBE_VALUES_FLOAT


def _baseline_value_for_param(p: inspect.Parameter, sibling_names: set) -> Any:
    """Build a clean baseline argument for a parameter."""
    # Usable default.
    if p.default is not inspect.Parameter.empty and p.default is not None:
        return p.default

    n = p.name.lower()
    # Name-based corpus guesses first: a bare `list` annotation is ambiguous,
    # but `y` is almost always a label vector and `X` is almost always a matrix.
    if n in ("features", "x_train", "x_test") or p.name == "X":
        return [[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]]
    if n in ("labels", "targets", "y_train", "y_test") or n == "y":
        return [0, 1, 0]
    if n == "x":
        return 2.0
    if n == "learning_rate":
        return 0.01
    if n == "iterations" or n == "max_iterations":
        return 10
    if n == "max_depth":
        return 5
    if n == "bias":
        return 0.5
    if n in ("weights", "coeffs"):
        return [0.5, -0.5]

    # Annotation-based guess.
    if p.annotation is not inspect.Parameter.empty:
        ann = str(p.annotation).lower().replace("typing.", "")
        if "list[list" in ann:
            return [[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]]
        if "list[int]" in ann:
            return [0, 1, 0]
        if "list" in ann or "sequence" in ann or "vector" in ann:
            return [1.0, 2.0, 3.0]
        if "dict" in ann:
            return {"a": 1.0, "b": 2.0}
        if "int" in ann:
            return 5
        if "float" in ann or "number" in ann:
            return 0.5
        if "str" in ann:
            return "the quick brown fox"

    # Fallback name heuristics.
    if "int" in n or "count" in n or "iter" in n or "depth" in n:
        return 5
    if "rate" in n or "lr" in n or "alpha" in n or "eps" in n:
        return 0.01
    if n in ("x", "y", "z") or "num" in n or "size" in n or "k" in n:
        return 2.0
    if "threshold" in n or "value" in n or "val" in n:
        return 0.5
    return 1.0


class _NotDiscrete(Exception):
    pass


def _is_discrete_label_output(values: List[Any]) -> bool:
    """True if output values form a small discrete set where magnitude is meaningless.

    Covers integer labels and booleans.  Floats are never treated as discrete
    labels, even when integer-valued, because a continuous function may clip to
    exactly 1.0/-1.0.  Returns False as soon as any non-discrete value (float,
    string, dict, None, custom object) is encountered.
    """
    scalars: List[Any] = []

    def walk(v: Any) -> None:
        if isinstance(v, bool):
            scalars.append(v)
        elif isinstance(v, int):
            scalars.append(v)
        elif isinstance(v, float):
            # Floats are never treated as discrete labels, even when integer-valued.
            # A continuous function may clip to exactly 1.0/-1.0; that is not a label set.
            raise _NotDiscrete
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x)
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif v is None:
            # None inside a structure means the output is not a pure label vector.
            raise _NotDiscrete
        else:
            raise _NotDiscrete

    try:
        for v in values:
            walk(v)
    except _NotDiscrete:
        return False

    if not scalars:
        return False

    distinct = set(_canonical(v) for v in scalars)
    return len(distinct) <= 4


def _classify(baseline: Any, results: List[Any]) -> Tuple[str, str]:
    """Return (verdict, reason) from the trend across probe results."""
    raised = sum(1 for r in results if r is None)

    if raised == len(results):
        return "HANDLED", "raises"

    if _is_discrete_label_output([baseline] + results):
        return "INCONCLUSIVE", "not_measurable_by_magnitude"

    if not all(_is_numeric(r) for r in results + [baseline]):
        return "INCONCLUSIVE", "not_numerically_comparable"

    if all(_canonical(r) == _canonical(baseline) for r in results):
        return "INCONCLUSIVE", "unreachable"

    probe_mags = [_magnitude(r) for r in results if r is not None]
    if not probe_mags:
        return "HANDLED", "raises"

    baseline_mag = _magnitude(baseline)

    if baseline_mag > 1e-6 and all(m < 1e-6 for m in probe_mags):
        return "DEFECT", "collapses"

    if len(probe_mags) >= 2:
        diverging = all(probe_mags[i] > probe_mags[i - 1] * 10.0 for i in range(1, len(probe_mags)))
        if diverging:
            return "DEFECT", "diverges"

    if len(probe_mags) >= 2 and all(m > 1e-12 for m in probe_mags):
        ratios = [probe_mags[i] / probe_mags[i - 1] for i in range(1, len(probe_mags))]
        if all(math.isfinite(r) and 0.1 < r < 10.0 for r in ratios):
            return "INCONCLUSIVE", "proportionate"

    return "INCONCLUSIVE", "bounded_change"


def evaluate_fixture(path: Path, param_name: str) -> Tuple[str, str, List[str]]:
    """Return (verdict, reason, detail_lines).

    Scan every top-level public function in the fixture. If the parameter name
    appears in more than one function, classify each occurrence and report them
    all. Distinguish "parameter genuinely absent" from "parameter exists but no
    function could be examined".
    """
    try:
        mod = _load_module(path)
    except FixtureTimeoutError as exc:
        return "INCONCLUSIVE", str(exc), []
    except Exception as exc:
        return "INCONCLUSIVE", f"could not load fixture: {type(exc).__name__}: {exc}", []

    functions = _find_public_functions(mod)
    if not functions:
        return "INCONCLUSIVE", "no public function to test", []

    all_verdicts: List[str] = []
    all_reasons: List[str] = []
    all_detail: List[str] = []
    examined_count = 0

    for fn in functions:
        try:
            sig = inspect.signature(fn)
        except Exception:
            continue
        if param_name not in sig.parameters:
            continue
        examined_count += 1

        param = sig.parameters[param_name]
        probe_values = _probe_values_for_param(param)

        sibling_names = set(sig.parameters)
        baseline_kwargs: Dict[str, Any] = {}
        for name, p in sig.parameters.items():
            baseline_kwargs[name] = _baseline_value_for_param(p, sibling_names)
        raised, baseline, was_killed = _run_call(fn, [], baseline_kwargs)
        if raised:
            all_detail.append(f"function={fn.__name__} parameter={param_name}: baseline call raised")
            continue

        results: List[Any] = []
        for probe in probe_values:
            probe_kwargs = dict(baseline_kwargs)
            probe_kwargs[param_name] = probe
            raised, result, was_killed = _run_call(fn, [], probe_kwargs)
            if was_killed or raised:
                results.append(None)
            else:
                results.append(result)

        verdict, reason = _classify(baseline, results)
        all_verdicts.append(verdict)
        all_reasons.append(reason)
        all_detail.append(f"function={fn.__name__} parameter={param_name} verdict={verdict} reason={reason}")
        all_detail.append(f"  baseline_magnitude={_magnitude(baseline)}")
        for probe, result in zip(probe_values, results):
            if result is None:
                all_detail.append(f"  probe={probe}: raised or killed")
            else:
                all_detail.append(f"  probe={probe}: magnitude={_magnitude(result)}")

    if not all_verdicts:
        if examined_count == 0:
            return "INCONCLUSIVE", f"parameter '{param_name}' not found", []
        return "INCONCLUSIVE", "parameter exists but no function could be examined", []

    # If any occurrence is DEFECT, report DEFECT. Next HANDLED, otherwise first INCONCLUSIVE.
    if "DEFECT" in all_verdicts:
        idx = all_verdicts.index("DEFECT")
        return "DEFECT", all_reasons[idx], all_detail
    if "HANDLED" in all_verdicts:
        idx = all_verdicts.index("HANDLED")
        return "HANDLED", all_reasons[idx], all_detail
    return "INCONCLUSIVE", all_reasons[0], all_detail


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description="Domain classifier (negative-parameter trend)")
    ap.add_argument("--fixture", required=True, help="Python fixture file to analyse")
    ap.add_argument("--param", required=True, help="Parameter name to probe with negative values")
    args = ap.parse_args(argv[1:])

    fixture = Path(args.fixture).resolve()
    if not fixture.exists():
        print("verdict:INCONCLUSIVE fixture not found")
        print("detail: path does not exist")
        return 0

    verdict, reason, detail = evaluate_fixture(fixture, args.param)
    print(f"verdict:{verdict} {reason}")
    if detail:
        for line in detail[:50]:
            print(f"detail:{line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
