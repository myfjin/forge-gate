#!/usr/bin/env python3
"""Guard lens — gating NaN/Inf finiteness check for scalar parameters.

STATUS: GATING. A genuine NaN-defect FAIL fails the build. `guard` is in both
ENFORCED_EXTRA_LENSES and ENFORCED_LENSES (src/forge_gate/lenses.py), and
_run_guard() sets objected=True on a FAIL verdict. This header read ADVISORY
until 2026-09-04; it had been false since the lens was promoted to gating.

The lens reports INCONCLUSIVE, never PASS, when it could not measure: an
unexercised parameter must not read as a clean one. That honesty path is what
makes gating defensible, and tests/verify_guard_lens.sh covers it.

The Guard lens needs no oracle. It feeds NaN/Inf into a function's scalar
parameters and checks whether the result hides the poison. The criterion:

  - code raises on NaN/Inf input                              -> HANDLED, PASS
  - code returns NaN/Inf in every float element              -> HANDLED, PASS
  - for NaN input, a returned finite float component that is
    UNCHANGED vs the clean baseline is independent of the
    poisoned input                                            -> HANDLED, PASS
  - for NaN input, a returned finite float component that is
    CHANGED vs the clean baseline was influenced by the NaN   -> DEFECT, FAIL
  - a MIXTURE of finite and NaN/Inf float elements from NaN
    input where any finite component CHANGED                  -> DEFECT, FAIL

Only float elements are judged. Ints and bools are treated as counters,
status flags or lengths, not numeric results, so a (count, NaN) tuple is
handled. Inf is recorded as an observation but never accused: a finite result
from +/-Inf may be the correct saturation limit (e.g. sigmoid(±Inf)), and this
lens has no oracle to tell the difference.

A scalar parameter is any parameter annotated as float/int (or with a numeric
default) that is not a container. Container parameters (list/dict/tuple/etc.)
are filled with small synthetic fixtures so the function can be exercised.

LIMITS (read before trusting):
  * The lens can only exercise a function whose non-poisoned arguments can be
    synthesised. G11 added shape-coherent synthesis for common matrix/vector
    siblings (e.g. X and y share a row count; x_test matches feature count),
    but exotic signatures still report INCONCLUSIVE.
  * The tolerance-name rule (tolerance/tol/epsilon/eps/atol/rtol) is a
    parameter-name HEURISTIC, honestly labelled, not a measurement.
  * Componentwise reachability ("finite component unchanged vs clean baseline
    is OK") IS implemented in G12, and G21 enforces it across MULTI_SHAPE_COUNT
    genuinely different argument shapes. A finite component is only called
    independent if it is unchanged in EVERY shape; any shape showing a changed
    finite component is a swallowed-NaN defect.
  * Fixture execution IS isolated in G17: each fixture is imported and called
    in a subprocess with start_new_session=True, a hard wall-clock timeout,
    os.killpg cleanup, and per-process CPU/memory limits. A fixture that
    exceeds any limit is INCONCLUSIVE, never FAIL.

FALSE-POSITIVE CLASSES CLOSED by this version:
  1. Inf-is-not-NaN: finite saturation limits at +/-Inf are allowed.
  2. Counter-is-not-a-result: an int counter/status next to a NaN float is
     handled; only float elements are judged.
  3. Unexercised-is-not-defective: if the clean baseline cannot exercise the
     code, the verdict is INCONCLUSIVE "parameter unreachable", never DEFECT.
  4. Separable-is-not-swallowed: a finite component unchanged by a poisoned
     input is mathematically independent and must not be accused.
     Componentwise comparison is now enforced; the multi-shape guard is
     adopted in the criterion and will be exercised by the fixtures."

  5. Literal-harvest-is-not-domain-knowledge: numeric constants from the
     function's own source are added as probe shapes (U4). This makes
     membership and comparison branches reachable without hard-coding any
     semantic map.
  6. Unsynthesisable-sibling-is-not-exercisable: if a function has a
     parameter the lens cannot synthesise (e.g. an unannotated callback),
     its scalar siblings are counted as unexercised rather than judged on
     invalid calls (U6).

Output contract:
  * verdict:PASS|FAIL|INCONCLUSIVE <reason>
  * detail:<line> (optional, limited to 50 lines)
  * scope:params_total=N
  * scope:params_judged=N
  * scope:params_unreachable=N
  * scope:params_unexercised=N
  * scope:functions_total=N
  * scope:functions_scanned=N
  * scope:mode=UNMATCHED

The scope lines partition every scalar parameter into exactly one bucket
(judged, unreachable, unexercised) so a report layer can distinguish an
unmatched PASS (no oracle comparison) from a matched PASS.

The lens is advisory: it reports FAIL but does not gate. Its purpose is to
surface missing finiteness guards that silently turn poison into plausible
output.
"""
from __future__ import annotations

import argparse
import ast
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

# Hard wall-clock timeout for a single fixture run. Short enough to prevent a
# submitted infinite loop from cooking the deploy host; long enough for real ML fixtures to
# finish their clean baseline. Must stay below the isolation test's 15s ceiling.
FIXTURE_TIMEOUT_SECONDS = 10

# CPU seconds allowed per fixture subprocess. Soft == hard so SIGKILL fires.
# Three baseline calls for a single-scalar fixture must fit inside the 15s test.
FIXTURE_CPU_LIMIT_SECONDS = 2

# Address-space limit per fixture subprocess. Large allocations that exceed this
# are treated as "could not measure" (INCONCLUSIVE), not as a clean PASS.
FIXTURE_MEMORY_LIMIT_BYTES = 256 * 1024 * 1024

# Number of genuinely different argument shapes to try when proving that a
# finite component is independent of a poisoned scalar. Coincidence at one
# shape is not evidence; independence must survive all shapes.
MULTI_SHAPE_COUNT = 4

# Maximum numeric literals harvested from a function's own source to use as
# additional probe shapes. U4: the discriminating values are often written in
# the code itself (status codes, thresholds, magic numbers); trying them makes
# membership/comparison branches reachable without domain knowledge.
MAX_HARVESTED_LITERALS = 8

POISON_NAN = [float("nan")]
POISON_INF = [float("inf"), float("-inf")]

# Sentinel returned when a parameter cannot be synthesised at all (e.g. an
# unannotated callback).  Such parameters are not scalar poison targets and,
# if present as siblings, prevent the whole function from being exercised.
_UNABLE = object()


TOLERANCE_NAMES = frozenset({"tolerance", "tol", "epsilon", "eps", "atol", "rtol"})


def _valid_for(ann: Any, default: Any = inspect.Parameter.empty, pname: str = "") -> Tuple[Any, bool]:
    """Build a valid baseline argument from a type hint or parameter name.

    Ported from c1-measurement/validator-COPY-from-dot5/executor.py (in au's ai-lab
    on the deploy host; not part of this repo).
    Precedence: declared default, annotation, parameter name, unknown.
    Returns (value, ok); ok=False means the caller must supply a fallback.
    """
    if default is not inspect.Parameter.empty and default is not None:
        return default, True

    s = str(ann).lower().replace("typing.", "")

    if "callable" in s:
        return _UNABLE, False
    if "list[list" in s:
        return [[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]], True
    if "list[int]" in s:
        return [0, 1, 0, 1, 1, 0], True
    if "list" in s or "sequence" in s or "vector" in s:
        return [1.0, 2.0, 3.0, 4.0, 5.0], True
    if "tuple" in s:
        return (1.0, 2.0), True
    if "dict" in s:
        return {"a": 1.0, "b": 2.0}, True
    if "int" in s:
        return 5, True
    if "float" in s or "number" in s:
        return 0.5, True
    if "str" in s:
        return "the quick brown fox", True
    if "bool" in s:
        return True, True

    n = pname.lower()
    if n in ("callback", "fn", "func", "function"):
        return _UNABLE, False
    if any(w in n for w in ("data", "samples", "values", "series", "arr", "xs", "ys", "points", "scores", "counts")):
        return [1.0, 2.0, 3.0, 4.0, 5.0], True
    if any(w in n for w in ("text", "name", "key", "line", "doc", "word")):
        return "the quick brown fox", True
    if n in ("x", "y", "z") or any(w in n for w in ("num", "count", "n_", "size", "k", "depth", "width", "seed")):
        return 5, True
    if any(w in n for w in ("prob", "rate", "alpha", "p1", "p2", "u", "var", "mean", "mu", "threshold", "eps", "value", "val")):
        return 0.5, True
    return None, False


def _value_is_scalar(v: Any) -> bool:
    """True for int/float numeric values (bools are status flags, not scalars)."""
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _baseline_for(param: inspect.Parameter, scalar_idx: int, sibling_names=frozenset(), shape_idx: int = 0) -> Tuple[Any, bool]:
    """Pick a baseline value and whether it is a scalar poison target.

    Rule (per G9a refinement):
        1. has a usable default -> use the default
        2. has an annotation -> use _valid_for; non-scalar values are used as-is,
           scalar values are routed to the varied scalar fallback so
           multi-scalar signatures do not collapse onto a single constant
        3. unannotated -> try _valid_for's name guess; scalar guesses fall back
           to the varied scalar default, container guesses are used as-is;
           a bare lowercase `y` paired with an uppercase `X` is treated as labels
        4. a failed/degenerate clean call then earns INCONCLUSIVE honestly
    """
    # Rule 1: honour declared defaults, but treat None as "no default".
    if param.default is not inspect.Parameter.empty and param.default is not None:
        return param.default, _value_is_scalar(param.default)

    def _choose(val: Any) -> Tuple[Any, bool]:
        """Container values are used directly; scalar candidates are replaced by
        the varied scalar fallback to prevent multi-scalar collapse.
        The _UNABLE sentinel passes through unchanged."""
        if val is _UNABLE:
            return _UNABLE, False
        if val is None:
            return _scalar_default(param, scalar_idx, shape_idx), True
        if _value_is_scalar(val):
            return _scalar_default(param, scalar_idx, shape_idx), True
        return val, False

    # Rule 2: annotation only; do not let a name guess override an annotation.
    if param.annotation is not inspect.Parameter.empty:
        val, ok = _valid_for(param.annotation, inspect.Parameter.empty, param.name)
        if ok:
            return _choose(val)

    # Rule 3: corpus-specific refinements for unannotated ML signatures.
    n = param.name.lower()
    if n in ("features", "x_train", "x_test") or param.name == "X":
        return _varying_matrix(shape_idx), False
    if n in ("labels", "targets", "y_train", "y_test"):
        return _varying_label_vector(shape_idx), False
    if n in ("texts", "documents"):
        return [["the quick brown fox"], ["the quick brown fox"]], False
    if n in ("text", "name", "key", "line", "doc", "word"):
        return "the quick brown fox", False
    if n in ("weights", "coeffs", "x_vals", "y_vals", "p1", "p2",
             "point", "vector", "sample", "values", "counts"):
        return _varying_float_vector(shape_idx), False
    if n in ("data", "samples", "series", "arr", "xs", "ys", "points",
             "scores"):
        return _varying_float_vector(shape_idx), False
    if n in ("priors", "probs", "probabilities"):
        return {"a": 1.0, "b": 2.0}, False
    if n in ("node", "tree", "root"):
        return None, False
    if n in ("callback", "fn", "func", "function"):
        return _UNABLE, False
    if param.name.lower() == "y" and "X" in sibling_names:
        return _varying_label_vector(shape_idx), False

    # Rule 3 continued: _valid_for name guess for remaining unannotated params.
    val, ok = _valid_for("", inspect.Parameter.empty, param.name)
    if ok:
        return _choose(val)

    return _scalar_default(param, scalar_idx, shape_idx), True


def _varying_matrix(shape_idx: int) -> List[List[float]]:
    """Return genuinely different matrix shapes across shape_idx."""
    shapes = [
        [[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]],
        [[7.0, 8.0], [9.0, 10.0], [11.0, 12.0], [13.0, 14.0]],
        [[15.0, 16.0, 17.0], [18.0, 19.0, 20.0], [21.0, 22.0, 23.0]],
        [[24.0, 25.0], [26.0, 27.0]],
    ]
    return shapes[shape_idx % len(shapes)]


def _varying_float_vector(shape_idx: int) -> List[float]:
    """Return genuinely different 1-D float vectors across shape_idx."""
    vectors = [
        [1.0, 2.0, 3.0, 4.0, 5.0],
        [10.0, 20.0, 30.0],
        [0.1, 0.2, 0.3, 0.4, 0.5, 0.6],
        [100.0, -50.0, 25.0],
    ]
    return vectors[shape_idx % len(vectors)]


def _varying_label_vector(shape_idx: int) -> List[int]:
    """Return genuinely different int label vectors across shape_idx."""
    labels = [
        [0, 1, 0, 1, 1, 0],
        [1, 0, 1, 0],
        [0, 0, 1, 1, 0, 1, 1],
        [1, 1, 0, 0, 1],
    ]
    return labels[shape_idx % len(labels)]


def _float_values(obj: Any) -> List[float]:
    """Return every float inside obj; ints and bools are excluded.

    Floats inside lists, tuples, dicts, dataclasses and namedtuples are
    collected so a (counter, result) tuple can be assessed by its result
    element alone.
    """
    if isinstance(obj, bool):
        return []
    if isinstance(obj, int):
        return []
    if isinstance(obj, float):
        return [obj]
    if isinstance(obj, (list, tuple)):
        return [v for x in obj for v in _float_values(x)]
    if isinstance(obj, dict):
        return [v for x in obj.values() for v in _float_values(x)]
    if hasattr(obj, "__dataclass_fields__"):
        from dataclasses import asdict
        return _float_values(asdict(obj))
    if hasattr(obj, "_asdict"):
        return _float_values(obj._asdict())
    if hasattr(obj, "__dict__") and not isinstance(obj, type):
        return _float_values(vars(obj))
    return []


def _assess_floats(values: List[float]) -> str:
    """Classify a list of extracted float values against the criterion."""
    if not values:
        return "unknown"
    has_nan = any(math.isnan(v) for v in values)
    has_inf = any(math.isinf(v) for v in values)
    has_finite = any(math.isfinite(v) for v in values)
    if has_nan or has_inf:
        if not has_finite:
            return "poison_only"
        return "mixture"
    if has_finite:
        return "finite"
    return "unknown"


def _is_nan(v: Any) -> bool:
    return isinstance(v, float) and math.isnan(v)


def _is_inf(v: Any) -> bool:
    return isinstance(v, float) and math.isinf(v)


def _is_poison(v: Any) -> bool:
    return _is_nan(v) or _is_inf(v)


def _is_scalar_param(param: inspect.Parameter, sibling_names=frozenset(), shape_idx: int = 0) -> bool:
    """True if the chosen baseline value for this parameter is a scalar."""
    # VAR_POSITIONAL / VAR_KEYWORD are not individually poisonable.
    if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
        return False
    _, is_scalar = _baseline_for(param, 0, sibling_names, shape_idx)
    return is_scalar


SCALAR_DEFAULTS = [2.0, 0.5, 10.0, -3.0, 1.0, 0.01, 100.0, -0.5]


def _scalar_default(p: inspect.Parameter, idx: int, shape_idx: int = 0) -> Any:
    """Pick a deterministic scalar fallback for an un-typed parameter.

    This is the "scalar 1.0 fallback" called only when annotation and name
    give no guess.  A varied sequence keeps multi-scalar signatures from
    collapsing into identical values, which would hide reachability.
    For multi-shape probing we also rotate per shape_idx so genuinely
    different magnitudes are exercised.
    """
    ann = p.annotation
    is_int = (
        ann is int
        or (isinstance(ann, str) and ann.strip() == "int")
        or (isinstance(p.default, int) and not isinstance(p.default, bool))
    )
    base = SCALAR_DEFAULTS[idx % len(SCALAR_DEFAULTS)]
    # Rotate magnitude per shape to avoid coincidental collisions.
    multipliers = [1.0, 10.0, 0.1, -1.0, 100.0, -0.1, 2.0, -10.0]
    raw = base * multipliers[shape_idx % len(multipliers)]
    return int(round(raw)) if is_int else raw


def _container_shape(value: Any) -> Optional[Tuple[int, ...]]:
    """Return shape for a regular container: (length,) for 1-D, (rows, cols) for 2-D."""
    if not isinstance(value, (list, tuple)) or len(value) == 0:
        return None
    # 1-D vector: no nested list/tuple.
    if not any(isinstance(x, (list, tuple)) for x in value):
        return (len(value),)
    # 2-D matrix: every row is a list/tuple of the same length.
    if not all(isinstance(row, (list, tuple)) and len(row) > 0 for row in value):
        return None
    cols = len(value[0])
    if not all(len(row) == cols for row in value):
        return None
    return (len(value), cols)


def _param_shape_role(name: str, shape: Tuple[int, ...]) -> str:
    """Classify a container's role from its name and shape.

    Role tells the coherence pass how this container relates to siblings:
      matrix_data  - training/feature matrix, anchors rows/cols
      matrix_test  - test matrix, must match matrix_data columns
      matrix_la    - linear-algebra operand, often needs to be square
      label_vector - per-row target/label, must match matrix_data rows
      sample_vector - single sample/query, must match matrix_data columns
    """
    n = name.lower()
    if len(shape) == 1:
        if n in ("y", "y_train", "y_test", "labels", "targets", "classes",
                 "assignments", "groups"):
            return "label_vector"
        if n in ("x_test", "test_point", "query", "sample", "point",
                 "instance", "features"):
            return "sample_vector"
        if n in ("text", "doc", "word", "document"):
            return "text"
        return "vector"
    if len(shape) == 2:
        if n in ("x_test", "x_test_data", "test_data", "test_set"):
            return "matrix_test"
        if n in ("x_train", "x", "data", "samples", "features", "train_data",
                 "dataset"):
            return "matrix_data"
        return "matrix"
    return "other"


def _resize_vector(value: Any, length: int) -> Any:
    """Resize a 1-D list/tuple to the requested length, cycling original items."""
    if length <= 0 or not isinstance(value, (list, tuple)):
        return value
    base = list(value)
    if len(base) == length:
        return value
    if len(base) > length:
        return type(value)(base[:length])
    extended = [base[i % len(base)] for i in range(length)]
    return type(value)(extended)


def _resize_matrix_cols(value: Any, cols: int) -> Any:
    """Resize each row of a matrix to the requested column count."""
    if cols <= 0 or not isinstance(value, (list, tuple)):
        return value
    base = [list(row) for row in value]
    new_rows = []
    for row in base:
        if len(row) == cols:
            new_rows.append(row)
        elif len(row) > cols:
            new_rows.append(row[:cols])
        else:
            extended = [row[i % len(row)] if row else 0.0 for i in range(cols)]
            new_rows.append(extended)
    return type(value)(new_rows)


def _coherent_kwargs(kwargs: Dict[str, Any], sig: inspect.Signature) -> Dict[str, Any]:
    """Adjust generated container arguments so related shapes are compatible.

    The per-type builder (_valid_for) cannot see sibling parameters, so a
    matrix X and a vector y may get unrelated sizes. This pass resizes
    label vectors to match matrix rows and sample/test vectors to match
    matrix columns. Matrices in the same signature are forced to share a
    column count (feature dimension) so X_train and X_test stay compatible.
    """
    roles: Dict[str, str] = {}
    shapes: Dict[str, Tuple[int, ...]] = {}
    for name, val in kwargs.items():
        shape = _container_shape(val)
        if shape is not None:
            roles[name] = _param_shape_role(name, shape)
            shapes[name] = shape

    if not shapes:
        return kwargs

    # Anchor row/column counts on a data matrix, preferring a training matrix.
    data_matrix: Optional[str] = None
    for name, role in roles.items():
        if role == "matrix_data":
            data_matrix = name
            break
    if data_matrix is None:
        # Fall back to any 2-D matrix if no clear data matrix exists.
        for name, shape in shapes.items():
            if len(shape) == 2:
                data_matrix = name
                break
    if data_matrix is None:
        return kwargs

    n_rows, n_cols = shapes[data_matrix]

    # Label vectors must match the data matrix row count.
    for name, role in roles.items():
        if role == "label_vector":
            kwargs[name] = _resize_vector(kwargs[name], n_rows)

    # Sample/test vectors must match the data matrix column count.
    for name, role in roles.items():
        if role == "sample_vector":
            kwargs[name] = _resize_vector(kwargs[name], n_cols)

    # All matrices in the signature must share the same column count.
    for name, shape in shapes.items():
        if len(shape) != 2:
            continue
        rows, cols = shape
        if cols != n_cols:
            kwargs[name] = _resize_matrix_cols(kwargs[name], n_cols)

    return kwargs


def _build_args(
    sig: inspect.Signature,
    poison_idx: Optional[int] = None,
    poison_value: Optional[float] = None,
    shape_idx: int = 0,
) -> Tuple[List[Any], Dict[str, Any], Optional[str], List[Any]]:
    """Construct positional/dict args for a test call.

    Returns (args, kwargs, poisoned_name, scalar_clean_defaults).
    shape_idx selects one of MULTI_SHAPE_COUNT genuinely different argument
    shapes for the OTHER parameters; the poisoned scalar is always set via
    scalar_defaults so it stays the clean baseline value in the clean run.
    """
    args: List[Any] = []
    kwargs: Dict[str, Any] = {}
    scalar_params: List[inspect.Parameter] = []
    scalar_defaults: List[Any] = []
    sibling_names = set(sig.parameters)

    for name, p in sig.parameters.items():
        val, is_scalar = _baseline_for(p, len(scalar_params), sibling_names, shape_idx)
        kwargs[name] = val
        if is_scalar:
            scalar_params.append(p)
            scalar_defaults.append(val)

    # G11: ensure matrix/vector siblings share a coherent shape. The per-type
    # builder above cannot see sibling parameters, so labels may not match rows
    # or a test point may not match feature count.
    kwargs = _coherent_kwargs(kwargs, sig)

    if poison_idx is not None and poison_idx < len(scalar_params):
        p = scalar_params[poison_idx]
        kwargs[p.name] = poison_value
    return (
        args,
        kwargs,
        scalar_params[poison_idx].name if poison_idx is not None and poison_idx < len(scalar_params) else None,
        scalar_defaults,
    )


def _is_degenerate(obj: Any) -> bool:
    """True if a clean baseline result looks like no-op output.

    Degenerate values (all-zero weights, empty collections, None) mean the
    fixture probably did not exercise the algorithm, so a comparison is
    not a valid reachability signal. Ints are counters/statuses/lengths,
    not numeric results, so a single int (even 0) is not degenerate.
    """
    if obj is None:
        return True
    if isinstance(obj, bool):
        return False
    if isinstance(obj, int):
        return False
    if isinstance(obj, float):
        return obj == 0.0
    if isinstance(obj, (list, tuple)):
        if len(obj) == 0:
            return True
        return all(_is_degenerate(x) for x in obj)
    if isinstance(obj, dict):
        if len(obj) == 0:
            return True
        return all(_is_degenerate(x) for x in obj.values())
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
            return ("__Inf__" if obj > 0 else "__-Inf__")
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


class FixtureTimeoutError(Exception):
    """Raised when a fixture subprocess does not return within the timeout."""
    pass


def _limit_resources() -> None:
    """Set tight per-process resource ceilings in fixture subprocesses.

    G17 defence in depth: even if a fixture escapes the wall-clock timeout by
    forking or blocking, CPU and address-space hard limits kill it quickly.
    """
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


def _load_module(path: Path) -> Any:
    """Return the module after loading it in a separate, time-bounded child process.

    G17: the fixture is submitted AI-generated code from strangers. It may contain
    infinite loops, unbounded recursion, forking, or large allocations. Running it
    in-process gives it the lens's privileges and lets it hang/cook the machine.

    We therefore load the module inside a child Python process:
      - start_new_session=True gives the child its own process group
      - a hard wall-clock timeout in the parent reaps runaway fixtures
      - os.killpg(os.getpgid(p.pid), SIGKILL) kills the whole tree, never p.kill()

    The child serialises the names of public functions back to the parent.  The
    parent then re-imports the file to obtain live function objects.  The second
    in-process import is safe because the child already proved the top-level module
    executes within the timeout; module-level loops are caught by the same timeout.
    """
    probe_script = textwrap.dedent(
        """
        import importlib.util
        import inspect
        import sys
        path = sys.argv[1]
        spec = importlib.util.spec_from_file_location("guard_fixture_under_test", path)
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
            raise FixtureTimeoutError(
                f"fixture exceeded {FIXTURE_TIMEOUT_SECONDS}s"
            )
        if p.returncode != 0:
            raise ImportError(f"fixture import failed: {err.strip() or 'unknown error'}")
        function_names = [line.strip() for line in out.splitlines() if line.strip()]
    finally:
        try:
            os.unlink(child_path)
        except OSError:
            pass

    # Second in-process import is safe: the child proved module-level code terminates
    # within FIXTURE_TIMEOUT_SECONDS.  We need live callables in this process.
    spec = importlib.util.spec_from_file_location("guard_fixture_under_test", str(path))
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # If the child saw functions but the in-process import disagrees, trust the child.
    if function_names:
        for name in function_names:
            if not hasattr(mod, name):
                raise ImportError(f"fixture function {name} missing after import")
    return mod


def _json_safe(obj: Any) -> Any:
    """Convert a fixture result to a JSON-serialisable form for IPC.

    Preserves NaN/Inf (json handles them as Python extensions), converts
    tuples to lists, and falls back to repr for exotic objects.
    """
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
    """Run a single function call in a subprocess, returning (raised, result, was_killed).

    This is the second half of G17 isolation.  _load_module only proves the
    module imports safely; the actual function calls exercise poisoned inputs,
    which may trigger adversarial code paths.  We run each call in a fresh child
    process with the same timeout + killpg + resource-limit discipline.

    Returns:
      (True, exception_message, was_killed) if the child raised or crashed.
      (False, result, False) if it returned.
    A timeout, crash, or resource violation is treated as "could not measure" by
    the caller.  was_killed is True when the child had to be SIGKILLed because it
    hit the wall-clock timeout or RLIMIT_CPU/AS.
    """
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
        spec = importlib.util.spec_from_file_location("guard_fixture_under_test", path)
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


def _find_candidate_functions(mod: Any) -> List[Tuple[str, Callable]]:
    """Find top-level functions that look like the code's public API."""
    out = []
    for name in dir(mod):
        if name.startswith("_"):
            continue
        obj = getattr(mod, name)
        if callable(obj) and inspect.isfunction(obj) and obj.__module__ == mod.__name__:
            out.append((name, obj))
    return out


def _harvest_numeric_literals(fn: Callable) -> List[float]:
    """Collect numeric constants from a function's source.

    U4: a function that branches on specific numbers (status codes, thresholds,
    enum values) is only reachable if we probe those numbers.  We read what the
    code itself says about itself, not a domain map.

    Returns a deduplicated list of finite float/int literals, capped at
    MAX_HARVESTED_LITERALS.  Source that cannot be retrieved yields an empty list.
    """
    try:
        source = inspect.getsource(fn)
    except Exception:
        return []
    try:
        tree = ast.parse(textwrap.dedent(source))
    except Exception:
        return []

    literals: set = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant):
            value = node.value
            if isinstance(value, bool):
                continue
            if isinstance(value, (int, float)) and math.isfinite(value):
                literals.add(float(value))

    ordered = sorted(literals, key=lambda v: (abs(v), v < 0))
    return ordered[:MAX_HARVESTED_LITERALS]


def _run_call(fn: Callable, args: List[Any], kwargs: Dict[str, Any]) -> Tuple[bool, Any, bool]:
    """Run a single function call in a subprocess, returning (raised, result, was_killed).

    The lens now isolates fixture execution.  This shim JSON-serialises the
    argument bundle and delegates to the subprocess runner.  Exotic arguments
    that are not JSON-serialisable are reported as raised/INCONCLUSIVE.
    """
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


def _componentwise_nan_defects(clean: Any, poisoned: Any) -> int:
    """Compare float elements pairwise across clean and poisoned results.

    G12 componentwise reachability rule:
      - poisoned component is NaN/Inf           -> correct propagation, no defect
      - poisoned component finite and UNCHANGED -> independent of poison, no defect
      - poisoned component finite and CHANGED   -> NaN influenced it = DEFECT

    If the number or nesting of float elements differs, the NaN changed
    control flow; any finite component in the poisoned result is a defect.
    """
    clean_floats = _float_values(clean)
    poisoned_floats = _float_values(poisoned)
    if len(clean_floats) != len(poisoned_floats):
        return sum(1 for v in poisoned_floats if math.isfinite(v))
    defects = 0
    for c, p in zip(clean_floats, poisoned_floats):
        if not math.isfinite(p):
            continue
        if _canonical(c) != _canonical(p):
            defects += 1
    return defects


def _assess_result(result: Any) -> str:
    """Classify a return value by its float elements against the criterion."""
    return _assess_floats(_float_values(result))


def evaluate_fixture(path: Path) -> Tuple[str, str, List[str], Dict[str, Any]]:
    """Return (verdict, reason, detail_lines, scope_counts).

    Scope counts partition every scalar parameter into exactly one bucket so
    downstream reports can say what an UNMATCHED PASS actually measured:
      * params_total        — scalar parameters found in scanned functions
      * params_judged       — at least one poison produced a discriminating result
      * params_unreachable  — every observed poison returned identical output to clean
      * params_unexercised  — no valid baseline could be built for any poison
      * functions_total     — public functions in the module
      * functions_scanned   — public functions with at least one scalar parameter
      * mode                — UNMATCHED (this lens has no reference oracle)
    """
    scope: Dict[str, Any] = {
        "params_total": 0,
        "params_judged": 0,
        "params_unreachable": 0,
        "params_unexercised": 0,
        "params_aborted": 0,
        "functions_total": 0,
        "functions_scanned": 0,
        "mode": "UNMATCHED",
    }

    try:
        mod = _load_module(path)
    except FixtureTimeoutError as exc:
        return "INCONCLUSIVE", str(exc), [], scope
    except Exception as exc:
        return "INCONCLUSIVE", f"could not load fixture: {type(exc).__name__}: {exc}", [], scope

    candidates = _find_candidate_functions(mod)
    scope["functions_total"] = len(candidates)
    if not candidates:
        return "PASS", "no public functions to test", [], scope

    nan_objections: List[str] = []
    nan_weak: List[str] = []
    nan_unreachable: List[str] = []
    nan_aborted: List[str] = []
    inf_notes: List[str] = []
    tested = 0
    exercised = False
    for fn_name, fn in candidates:
        try:
            sig = inspect.signature(fn)
        except Exception:
            continue
        scalar_params = [p for p in sig.parameters.values() if _is_scalar_param(p, set(sig.parameters), 0)]

        # U6: if any sibling parameter is structurally unsynthesisable (e.g. an
        # unannotated callback), the whole function cannot be exercised honestly.
        # Count its scalar parameters as unexercised and move on.
        has_unable_sibling = any(
            _baseline_for(p, 0, set(sig.parameters), 0)[0] is _UNABLE
            for p in sig.parameters.values()
        )
        if has_unable_sibling:
            scope["params_total"] += len(scalar_params)
            scope["params_unexercised"] += len(scalar_params)
            continue

        if not scalar_params:
            continue
        tested += 1

        # U4: harvest the function's own numeric literals so membership/comparison
        # branches (status codes, thresholds, magic numbers) become reachable.
        fn_literals = _harvest_numeric_literals(fn)

        for idx, p in enumerate(scalar_params):
            scope["params_total"] += 1
            param_had_valid_baseline = False
            param_handled_raise = False
            param_had_discrimination = False
            param_probe_aborted = False

            for poison in POISON_NAN + POISON_INF:
                # G21 + U4: probe each scalar poison across MULTI_SHAPE_COUNT
                # genuinely different shapes for the OTHER parameters, plus any
                # numeric literals the function itself mentions for the poisoned
                # parameter. A finite component is only called "independent" if it
                # is unchanged in EVERY observation; any observation where it changes
                # proves the poison reached it.
                shape_observations: List[Tuple[Any, Any]] = []
                baseline_skips = 0
                raised_handled = 0
                probe_aborted = False

                for shape_idx in range(MULTI_SHAPE_COUNT):
                    try:
                        args, kwargs, poisoned_name, scalar_defaults = _build_args(sig, idx, poison, shape_idx)
                    except Exception:
                        baseline_skips += 1
                        continue

                    # Candidate clean values for the poisoned parameter: the shape's
                    # default plus the function's own literals (deduped, capped).
                    candidate_values: List[Any] = [scalar_defaults[idx]]
                    for lit in fn_literals:
                        if lit != scalar_defaults[idx]:
                            candidate_values.append(lit)
                    if len(candidate_values) > MAX_HARVESTED_LITERALS + 1:
                        candidate_values = candidate_values[:MAX_HARVESTED_LITERALS + 1]

                    for clean_value in candidate_values:
                        # Baseline: run with a clean value for the poisoned parameter.
                        clean_kwargs = dict(kwargs)
                        clean_kwargs[p.name] = clean_value
                        clean_raised, clean_result, clean_killed = _run_call(fn, args, clean_kwargs)
                        if clean_raised:
                            if clean_killed:
                                # The fixture consumed too much CPU/time even with a clean
                                # input; further shapes/candidates will not help.
                                probe_aborted = True
                                break
                            # A normal exception on this candidate: skip it.
                            baseline_skips += 1
                            continue
                        if _is_degenerate(clean_result):
                            # This candidate produced no-op output; skip it.
                            baseline_skips += 1
                            continue

                        param_had_valid_baseline = True
                        raised, result, was_killed = _run_call(fn, args, kwargs)
                        if raised:
                            if was_killed:
                                # The poisoned call was killed; treat the whole probe as
                                # "could not measure" rather than as handled/raised.
                                probe_aborted = True
                                break
                            # Loud failure is handled for both NaN and Inf; we know the
                            # baseline reached this branch, so the raise is a real response.
                            raised_handled += 1
                            param_handled_raise = True
                            continue
                        shape_observations.append((clean_result, result))

                    if probe_aborted:
                        break

                if probe_aborted:
                    # A resource KILL is not a measurement. The clean baseline may have
                    # been perfectly valid; what failed is that the budget ran out before
                    # anything was observed. The old code emitted "baseline invalid:
                    # raised or degenerate" here, which sent a reader hunting for a bad
                    # baseline that does not exist.
                    param_probe_aborted = True
                    nan_aborted.append(
                        f"{fn_name}({poisoned_name}=...) probe ABORTED: resource limit "
                        f"reached, nothing observed"
                    )
                    continue
                if not shape_observations and raised_handled == 0:
                    nan_unreachable.append(
                        f"{fn_name}({poisoned_name}=...) baseline invalid: raised or degenerate"
                    )
                    continue

                exercised = True

                if not shape_observations:
                    # Every shape that had a valid baseline raised on poison;
                    # the poison is handled by loud failure.
                    continue

                poison_label = "NaN" if _is_nan(poison) else ("+Inf" if poison > 0 else "-Inf")

                # Reachability guard (multi-shape): if poison produced identical
                # output to clean at every observed shape, the parameter is unreachable.
                if all(_canonical(r) == _canonical(c) for c, r in shape_observations):
                    nan_unreachable.append(
                        f"{fn_name}({poisoned_name}={poison_label}) parameter unreachable: "
                        f"poisoned output identical to clean baseline"
                    )
                    continue

                param_had_discrimination = True

                if _is_nan(poison):
                    # G12 + G21: componentwise reachability across all shapes.
                    defects_per_shape = [
                        _componentwise_nan_defects(c, r) for c, r in shape_observations
                    ]
                    if all(d == 0 for d in defects_per_shape):
                        # Finite component is unchanged across all observed shapes -> independent.
                        continue
                    # At least one shape shows a changed finite component -> defect.
                    total_defects = sum(defects_per_shape)
                    is_tol = poisoned_name and poisoned_name.lower() in TOLERANCE_NAMES
                    note = (
                        f"{fn_name}({poisoned_name}=NaN) componentwise: "
                        f"{total_defects} finite component(s) changed vs clean across {len(defects_per_shape)} shape(s)"
                    )
                    if is_tol:
                        nan_weak.append(note + " [tolerance-name heuristic, not measured]")
                    else:
                        nan_objections.append(note)
                else:
                    # Inf -> finite/mixture may be correct saturation; record, do not accuse.
                    # Use the first shape's classification as representative.
                    clean_result, result = shape_observations[0]
                    assessment = _assess_result(result)
                    inf_notes.append(
                        f"{fn_name}({poisoned_name}={poison_label}) returned {assessment}: {type(result).__name__}"
                    )
            if param_handled_raise or param_had_discrimination:
                scope["params_judged"] += 1
            elif param_probe_aborted:
                # ⛔ THIS BRANCH MUST PRECEDE params_unreachable. `unreachable` is
                # defined as "every OBSERVED poison returned output identical to the
                # clean baseline". An aborted probe observed NOTHING, so filing it there
                # claims a measurement that never happened.
                # Found by 6E 2026-09-04 and reproduced with controls: an aborted
                # parameter and a genuinely-unreachable one emitted BYTE-IDENTICAL
                # buckets, separated only by the verdict word -- and report.py renders
                # the buckets without recomputing them, so the two were indistinguishable
                # downstream. Both kill paths land here: the clean baseline being killed
                # and the poisoned probe being killed. Either way the honest answer is
                # "we stopped early", not "we measured and it did not matter".
                scope["params_aborted"] += 1
            elif param_had_valid_baseline:
                scope["params_unreachable"] += 1
            else:
                scope["params_unexercised"] += 1

    scope["functions_scanned"] = tested
    if not tested:
        return "PASS", "no scalar parameters found to test", [], scope

    detail = nan_objections + nan_weak + inf_notes + nan_unreachable + nan_aborted
    if nan_objections:
        return "FAIL", f"{len(nan_objections)} scalar NaN defect(s) found", detail, scope
    if not exercised:
        # Every scalar parameter we found was unreachable; the lens could not
        # actually exercise the code, so it must report INCONCLUSIVE.
        # COUNT PARAMETERS, NOT DETAIL LINES. The lists get one entry per poison
        # value per parameter, so len() reported "3 parameter(s)" for a
        # single-parameter function. The scope counts were already correct; the
        # headline just was not using them. Name every non-zero bucket: "aborted"
        # and "unreachable" are different claims and must not share a word.
        _parts = []
        if scope["params_aborted"]:
            _parts.append(
                f"{scope['params_aborted']} parameter(s) aborted "
                f"(resource limit, nothing observed)"
            )
        if scope["params_unreachable"]:
            _parts.append(f"{scope['params_unreachable']} parameter(s) unreachable")
        if scope["params_unexercised"]:
            _parts.append(
                f"{scope['params_unexercised']} parameter(s) unexercised (no valid baseline)"
            )
        return "INCONCLUSIVE", "; ".join(_parts) or "nothing could be measured", detail, scope
    if nan_weak:
        return "PASS", f"all NaN inputs handled; {len(nan_weak)} tolerance observation(s) recorded", detail, scope
    if inf_notes:
        return "PASS", f"all NaN inputs handled; {len(inf_notes)} Inf observation(s) recorded", detail, scope
    if nan_unreachable:
        return "PASS", f"all NaN inputs handled; {scope['params_unreachable']} parameter(s) recorded as unreachable", detail, scope
    return "PASS", "all scalar NaN/Inf inputs handled or propagated cleanly", detail, scope


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description="Guard lens (NaN/Inf finiteness)")
    ap.add_argument("--fixture", required=True, help="Python fixture file to analyse")
    args = ap.parse_args(argv[1:])

    fixture = Path(args.fixture).resolve()
    if not fixture.exists():
        print("verdict:INCONCLUSIVE fixture not found")
        print("detail: path does not exist")
        return 0

    verdict, reason, detail, scope = evaluate_fixture(fixture)
    print(f"verdict:{verdict} {reason}")
    if detail:
        for line in detail[:50]:
            print(f"detail:{line}")
    print(f"scope:params_total={scope['params_total']}")
    print(f"scope:params_judged={scope['params_judged']}")
    print(f"scope:params_unreachable={scope['params_unreachable']}")
    print(f"scope:params_unexercised={scope['params_unexercised']}")
    print(f"scope:params_aborted={scope['params_aborted']}")
    print(f"scope:functions_total={scope['functions_total']}")
    print(f"scope:functions_scanned={scope['functions_scanned']}")
    print(f"scope:mode={scope['mode']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
