#!/bin/bash
# ACCEPTANCE TEST for the GUARD LENS (scalar NaN/Inf finiteness check).
#
# The Guard lens needs no oracle. It feeds NaN/Inf into a function's scalar
# parameters and checks whether a finiteness guard exists. This works on any
# code, not just patterns in the Forge Gate catalog.
#
# This test is written BEFORE the lens exists. 4Q builds the lens against it.
#
# EXIT 0 = ACCEPTED (all asserted gates pass). EXIT 1 = REJECTED.

set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

GUARD_LENS="$ROOT/tools/guard_lens.py"
FORGE_GATE="$ROOT/src/forge_gate"
PYTHON="/usr/bin/python3"

FAILED=0
INCONCLUSIVE=0
CANONICAL_FAILURES=()

note() { printf '%-62s %s\n' "$1" "$2"; }
gate() {
    if [ "$2" = "PASS" ]; then
        note "$1" "✅ PASS"
    elif [ "$2" = "INCONCLUSIVE" ]; then
        note "$1" "⚠️  INCONCLUSIVE"
        INCONCLUSIVE=1
    else
        note "$1" "❌ FAIL"
        FAILED=1
    fi
}

# ---------------------------------------------------------------- criterion
# NaN/Inf HANDLING CRITERION (load-bearing — the lens is built to this rule):
#
# NaN input is CONCLUSIVE. A scalar NaN must not silently become a finite
# float result, a non-NaN collection, or a mixture of finite and NaN float values.
# If NaN input raises OR returns only NaN/Inf floats, the code is HANDLED and
# the lens must NOT object. Any other outcome from NaN input is a DEFECT and the
# lens MUST object.
#
# +/-Inf input is NOT CONCLUSIVE. Many functions have correct finite limits at
# +/-Inf (sigmoid(+Inf)=1.0, sigmoid(-Inf)=0.0, tanh(±Inf)=±1.0, etc.). The
# Guard lens has no oracle, so it must NOT object to finite float output from
# +/-Inf input. If an Inf input raises or returns Inf/NaN floats, that is also
# HANDLED.
#
# COMPONENTWISE REACHABILITY RULE: in a returned collection, a finite float
# component is a DEFECT only if it CHANGED relative to the clean baseline. If
# it is UNCHANGED, the component is independent of the poisoned input and the
# result is CORRECT (e.g., a separable gradient). A component that is itself
# NaN/Inf is correct propagation.
#
#   (nan, 20.0)  vs clean (2000.0, 20.0) -> y unchanged -> CORRECT
#   (nan, 1.0)   vs clean (0.4,   0.6)  -> y CHANGED   -> DEFECT
#
# MULTI-SHAPE GUARD: "unchanged" can be a coincidence (e.g., both values 0.0,
# or two computations colliding). The lens must verify independence across
# SEVERAL GENUINELY DIFFERENT argument shapes — varying row count, feature
# count, magnitude, label balance — before treating a finite component as
# correct. Independence that survives many shapes is real; once may be luck.
#
# MIXED-COLLECTION RULE: when the returned value is a collection, assess only
# the FLOAT-typed elements. Int-typed elements are counters / statuses /
# lengths, not numeric results. (10000, nan) -> HANDLED; (10000, 3.5) -> DEFECT.
#
# TOLERANCE=NaN RULE: a NaN tolerance/epsilon that disables an early-exit test
# (e.g. fx < tolerance) is WEAK, not fabrication. The numeric value is still
# sound; only the iteration-count/status meaning is corrupted. The lens must
# NOT object when the float result itself is correct and only an int counter or
# status is affected. If a NaN tolerance causes a finite float result to be
# wrong, that is a DEFECT. Implemented as a parameter-name heuristic
# (tolerance/tol/epsilon/eps/atol/rtol), labelled honestly, because the lens
# has no oracle and cannot judge the correctness of a finite float result.
#
# In short: NaN -> finite float  = DEFECT.  Inf -> finite float  = ALLOWED.
#           NaN -> raise / all-floats-poison = HANDLED.
#           Component finite AND unchanged vs clean across multiple shapes = HANDLED.
#           Component finite AND changed vs clean = DEFECT.
#
# This criterion prevents Law-5 failures: a gate that fails correct saturation,
# correct NaN propagation, or separable partial computation is worse than no gate.
#
# The lens is ADVISORY first. Once the 10 canonical oracles are fixed to survive
# this criterion, it may be promoted to gating.

# ---------------------------------------------------------------- harness interface
# Expected invocation:
#   python3 "$ROOT/tools/guard_lens.py" --fixture <fixture.py>
# Expected stdout:
#   verdict:PASS|FAIL|INCONCLUSIVE [reason]
#   detail: <optional machine-readable detail>
#
# A harness crash/missing lens is retried; if it persists the fixture is
# INCONCLUSIVE and never accused.
#
# REACHABILITY RULE: If the poisoned parameter is not reached by the test (the
# clean baseline output is identical to the poisoned output, or the clean call
# cannot even exercise the code), the lens must report INCONCLUSIVE
# "parameter unreachable", never a DEFECT. A harness that could not exercise the
# code must not accuse the code. This state is distinct from PASS: "could not
# measure" and "measured and clean" are different facts.

run_lens() {
    local fixture="$1"
    local out="" rc=0
    for _try in 1 2 3; do
        out=$(timeout 60 "$PYTHON" "$GUARD_LENS" --fixture "$fixture" 2>/dev/null)
        rc=$?
        if [ "$rc" -ne 0 ] || [ -z "$out" ] || echo "$out" | grep -qE "^verdict:ERROR"; then
            sleep 1
            continue
        fi
        echo "$out"
        return 0
    done
    echo "verdict:INCONCLUSIVE guard lens unavailable or crashed after 3 attempts (rc=$rc)"
    return 1
}

parse_verdict() {
    local out="$1"
    echo "$out" | grep -E "^verdict:" | head -1 | sed 's/^verdict://'
}

# ---------------------------------------------------------------- lens present?
echo "=================================================================="
echo " GUARD LENS (NaN/Inf finiteness) — ACCEPTANCE TEST"
echo " date: $(date -u +%FT%TZ)"
echo "=================================================================="

echo; echo "--- LENS PRESENT? ---"
if [ ! -f "$GUARD_LENS" ]; then
    gate "guard lens exists at $GUARD_LENS" FAIL
    echo; echo ">>> REJECTED: guard lens not found. The lens cannot be accepted."
    exit 1
else
    gate "guard lens exists at $GUARD_LENS" PASS
fi

# ---------------------------------------------------------------- fixtures
# Per-run, per-user temp directory. Never a fixed shared path.
FIXTURE_DIR=$(mktemp -d /tmp/guard_lens_fixtures.XXXXXX)
if [ ! -d "$FIXTURE_DIR" ]; then
    echo "FATAL: could not create fixture directory"
    exit 1
fi
chmod 700 "$FIXTURE_DIR"

write_fixture() {
    local name="$1"
    local path="$FIXTURE_DIR/$name"
    cat > "$path"
    rc=$?
    if [ "$rc" -ne 0 ]; then
        echo "FATAL: failed to write fixture $path (exit $rc)"
        exit 1
    fi
    if [ ! -f "$path" ] || [ ! -s "$path" ]; then
        echo "FATAL: fixture $path missing or empty after write"
        exit 1
    fi
    printf '%s  %s\n' "$(md5sum "$path" | awk '{print $1}')" "$path"
}

# ---------------------------------------------------------------- fixture: guarded scalar returns finite
write_fixture "guarded_scalar.py" <<'PY'
import math
import sys

def guarded_scale(x: float, factor: float) -> float:
    if not (isinstance(x, (int, float)) and isinstance(factor, (int, float))):
        raise TypeError("scalar required")
    if not (math.isfinite(x) and math.isfinite(factor)):
        raise ValueError("finite scalar required")
    return x * factor

if __name__ == "__main__":
    args = sys.argv[1:]
    x = float(args[0]) if args else 2.0
    factor = float(args[1]) if len(args) > 1 else 3.0
    print(guarded_scale(x, factor))
PY

# ---------------------------------------------------------------- fixture: unguarded scalar returns finite from NaN (the NaN comparison trap)
write_fixture "unguarded_scalar.py" <<'PY'
import sys

def unguarded_scale(x: float, factor: float) -> float:
    # The defensive branch fires for NaN because every comparison against NaN is False,
    # fabricating a confident 0.0 from poison input.
    if x > 0:
        return x * factor
    return 0.0

if __name__ == "__main__":
    args = sys.argv[1:]
    x = float(args[0]) if args else 2.0
    factor = float(args[1]) if len(args) > 1 else 3.0
    print(unguarded_scale(x, factor))
PY

# ---------------------------------------------------------------- fixture: propagates NaN/Inf out (handled, advisory does not object)
write_fixture "propagate_nan.py" <<'PY'
def propagate_scale(x: float, factor: float) -> float:
    # No explicit guard, but poison in -> poison out with no finite contamination.
    return x * factor

if __name__ == "__main__":
    import sys
    args = sys.argv[1:]
    x = float(args[0]) if args else 2.0
    factor = float(args[1]) if len(args) > 1 else 3.0
    print(propagate_scale(x, factor))
PY

# ---------------------------------------------------------------- fixture: raises on NaN/Inf (handled)
write_fixture "raises_on_nan.py" <<'PY'
def raising_scale(x: float, factor: float) -> float:
    import math
    if math.isnan(x) or math.isinf(x) or math.isnan(factor) or math.isinf(factor):
        raise ValueError("NaN/Inf not allowed")
    return x * factor

if __name__ == "__main__":
    import sys
    args = sys.argv[1:]
    x = float(args[0]) if args else 2.0
    factor = float(args[1]) if len(args) > 1 else 3.0
    print(raising_scale(x, factor))
PY

# ---------------------------------------------------------------- fixture: unexercisable (baseline always raises -- kills M02-inconclusive-off)
write_fixture "unexercisable.py" <<'PY'
# The baseline call raises unconditionally, so the guard lens cannot build
# any valid observation for any scalar parameter. Every scalar lands in
# params_unexercised and `exercised` stays False. The lens must report
# INCONCLUSIVE, never PASS -- an unexercised run has measured nothing.
#
# This fixture exists to kill M02-inconclusive-off. See §8 of the campaign
# brief: the honesty-path defence at guard_lens.py:1191 (`if not exercised:`)
# was undefended -- M02 mutates it to `if False:` and the fixture falls
# through to the PASS at line 1218. Without a strict-INCONCLUSIVE gate, no
# test would notice.
def unexercisable(x: float, y: float) -> float:
    raise RuntimeError("baseline always fails -- lens cannot exercise this code")

if __name__ == "__main__":
    unexercisable(1.0, 2.0)
PY

# ---------------------------------------------------------------- fixture: correct saturation at +/-Inf (must NOT object)
write_fixture "correct_saturation.py" <<'PY'
import math
import sys

def saturating_sigmoid(z: float) -> float:
    """sigmoid(-Inf)=0.0 and sigmoid(+Inf)=1.0 are the exact limits."""
    if z > 100:
        return 1.0
    if z < -100:
        return 0.0
    return 1.0 / (1.0 + math.exp(-z))

if __name__ == "__main__":
    args = sys.argv[1:]
    z = float(args[0]) if args else 0.0
    print(saturating_sigmoid(z))
PY

# ---------------------------------------------------------------- fixture: correct NaN propagation with int counter (must NOT object)
write_fixture "count_and_nan.py" <<'PY'
import sys

def count_and_nan(x: float) -> tuple[int, float]:
    # Common Python shape: (loop_counter, result). Result is poison, counter is int.
    return (10000, x * 3.0)

if __name__ == "__main__":
    args = sys.argv[1:]
    x = float(args[0]) if args else 2.0
    print(count_and_nan(x))
PY

# ---------------------------------------------------------------- fixture: separable gradient (must NOT object)
write_fixture "separable_gradient.py" <<'PY'
import sys

def separable_gradient(x: float, y: float) -> tuple[float, float]:
    # f(x,y) = 100x^2 + y^2. Gradient is separable: dy/dy does not depend on x.
    # NaN in x leaves y-component unchanged vs clean baseline.
    return (200.0 * x, 2.0 * y)

if __name__ == "__main__":
    args = sys.argv[1:]
    x = float(args[0]) if args else 10.0
    y = float(args[1]) if len(args) > 1 else 10.0
    print(separable_gradient(x, y))
PY

# ---------------------------------------------------------------- fixture: coupled normalise trap (must OBJECT)
write_fixture "coupled_fabrication.py" <<'PY'
import sys

def coupled_normalise(a: float, b: float) -> tuple[float, float]:
    # A coupled computation where NaN comparison falls through and the finite
    # component b is silently replaced by a confident 1.0. b CHANGES vs clean.
    tot = a + b
    if tot > 0:
        return (a / tot, b / tot)
    return (a, 1.0)

if __name__ == "__main__":
    args = sys.argv[1:]
    a = float(args[0]) if args else 2.0
    b = float(args[1]) if len(args) > 1 else 3.0
    print(coupled_normalise(a, b))
PY

# ---------------------------------------------------------------- fixture: NaN tolerance disables early exit but value is sound (must NOT object)
write_fixture "nan_tolerance_weak.py" <<'PY'
import math
import sys

def vanilla_gd_like(x: float, y: float, tolerance: float) -> tuple[int, float]:
    # NaN tolerance makes fx < tolerance always False, so loop runs to max_iters.
    # The objective value is MORE converged, not wrong; only the counter is misleading.
    lr = 0.005
    fx = 100.0 * x * x + y * y
    for i in range(10000):
        if fx < tolerance:
            return (i, fx)
        gx, gy = 200.0 * x, 2.0 * y
        x -= lr * gx
        y -= lr * gy
        fx = 100.0 * x * x + y * y
    return (10000, fx)

if __name__ == "__main__":
    args = sys.argv[1:]
    x = float(args[0]) if args else 10.0
    y = float(args[1]) if len(args) > 1 else 10.0
    tol = float(args[2]) if len(args) > 2 else 1e-6
    print(vanilla_gd_like(x, y, tol))
PY

# ---------------------------------------------------------------- fixture: poisoned parameter is not reached (must NOT object)
write_fixture "unreached_param.py" <<'PY'
import sys

def fit_with_unreached_lr(X: list, y: list, learning_rate: float) -> tuple[list, float]:
    # With degenerate single-sample data the function returns initial weights
    # before learning_rate is ever used. Poisoning learning_rate cannot affect output.
    n_features = len(X[0]) if X else 0
    weights = [0.0] * n_features
    bias = 0.0
    return (weights, bias)

if __name__ == "__main__":
    args = sys.argv[1:]
    X = [[1.0, 2.0]]
    y = [1]
    lr = float(args[0]) if args else 1.0
    print(fit_with_unreached_lr(X, y, lr))
PY

# ---------------------------------------------------------------- fixture: poisoned parameter is reached and fabricates (must OBJECT)
write_fixture "reached_param.py" <<'PY'
import sys

def reached_fabrication(x: float, threshold: float) -> float:
    # The poisoned scalar x is actually used in the comparison.
    # NaN -> every comparison is False -> returns finite 7.0 (fabrication).
    # Clean output is non-degenerate (5.0) so reachability is provable.
    return 5.0 if x > threshold else 7.0

if __name__ == "__main__":
    args = sys.argv[1:]
    x = float(args[0]) if args else 2.0
    threshold = float(args[1]) if len(args) > 1 else 0.5
    print(reached_fabrication(x, threshold))
PY

# ---------------------------------------------------------------- fixture: shape-dependent coincidence trap (must OBJECT)
# G19/G20: the multi-shape guard in the criterion must be real, not decorative.
# At the lens's default shape the clean and poisoned finite components collide
# on the same value, but at a different shape the NaN would change the result.
# A single-shape implementation reports "unreachable" and passes. This gate
# forces the implementation to test multiple genuinely different shapes.
write_fixture "shape_dependent_coincidence.py" <<'PY'
def shape_dependent_coincidence(x: float, y: float) -> tuple[float, float]:
    # Missing NaN guard. NaN comparison is False, so the else branch returns
    # (1.5, 1.0). At the default shape (x=2.0, y=0.5) the clean branch also
    # returns (1.5, 1.0), so a single-shape lens sees identical output and
    # passes. At (x=2.0, y=2.0) clean returns (3.0, 1.0) and poisoned returns
    # (1.5, 1.0) — the NaN clearly changed a finite component.
    if x > 0:
        return (y + 1.0, 1.0)
    return (1.5, 1.0)
PY

# ---------------------------------------------------------------- fixture: genuinely shape-independent component (must NOT object)
# Control for the gate above: a component that is unchanged by the poisoned
# input across ALL shapes, not just the default one. The lens must still pass.
write_fixture "shape_independent_component.py" <<'PY'
def shape_independent_component(x: float, y: float) -> tuple[float, float]:
    # f(x, y) returns the y-component verbatim; x does not influence it at all.
    # NaN in x leaves y unchanged for every possible shape.
    return (x, y)
PY

# ---------------------------------------------------------------- fixture: reachability via source literals (must be REACHED, not unreachable)
# U3: a function that branches on membership in a set of numeric literals.
# Generic shapes [1, 10, 0, -1, 100, 2, -10] all return the same bool, so the
# lens wrongly reports the parameter as unreachable. The discriminating values are
# written in the source itself; a fixed lens harvests those literals and proves
# the parameter is reached. This gate requires the lens to report REACHED,
# regardless of verdict, so the detail must not contain "parameter unreachable".
write_fixture "literal_membership.py" <<'PY'
def is_transient_status(status_code: int) -> bool:
    """Return True for HTTP status codes that are safe to retry."""
    return status_code in (408, 429, 500, 502, 503, 504)
PY

# ---------------------------------------------------------------- fixture: scope-coverage arithmetic (U5)
# Mixes three coverage buckets, measured against the actual current lens:
#   * judged:     status_code in check_status() is now reached via literal harvesting
#                 (408/429/500); x in judged_function() is a plain scalar.
#   * unreachable: ignore_param() never uses its parameter, so clean == poison.
#   * unexercised: value in apply_callback() has a callable sibling; the lens
#                 cannot build a valid baseline, so the parameter is not probed.
# Expected truth: total=4, judged=2, unreachable=1, unexercised=1.
write_fixture "scope_fixture.py" <<'PY'
def ignore_param(status_code: int) -> bool:
    """Parameter is genuinely unused: clean and poison outputs are identical."""
    return True


def check_status(status_code: int) -> bool:
    """Reached via literal harvesting (U4): source literals 408/429/500 discriminate."""
    return status_code in (408, 429, 500)


def judged_function(x: float) -> float:
    """Plain scalar: NaN changes output, so lens can judge it."""
    return x * 2.0


def apply_callback(value: float, callback) -> float:
    """Unexercised: callback is not a scalar and baseline cannot be built."""
    return callback(value)
PY

# ---------------------------------------------------------------- run asserted fixtures
echo; echo "--- ASSERTED FIXTURES ---"

# guarded_scalar must NOT object (raises on poison)
out=$(run_lens "$FIXTURE_DIR/guarded_scalar.py")
if [ $? -ne 0 ]; then
    gate "guarded_scalar.py: lens ran without crashing" INCONCLUSIVE
else
    v=$(parse_verdict "$out")
    if echo "$v" | grep -qE "^PASS"; then
        gate "guarded_scalar.py: no objection to guarded code" PASS
    elif echo "$v" | grep -qE "^INCONCLUSIVE"; then
        gate "guarded_scalar.py: no objection to guarded code" INCONCLUSIVE
    else
        gate "guarded_scalar.py: no objection to guarded code" FAIL
    fi
fi

# unguarded_scalar MUST object (NaN in -> finite out)
out=$(run_lens "$FIXTURE_DIR/unguarded_scalar.py")
if [ $? -ne 0 ]; then
    gate "unguarded_scalar.py: lens objects to silent finite output" INCONCLUSIVE
else
    v=$(parse_verdict "$out")
    if echo "$v" | grep -qE "^FAIL"; then
        gate "unguarded_scalar.py: lens objects to silent finite output" PASS
    elif echo "$v" | grep -qE "^INCONCLUSIVE"; then
        gate "unguarded_scalar.py: lens objects to silent finite output" INCONCLUSIVE
    else
        gate "unguarded_scalar.py: lens objects to silent finite output" FAIL
    fi
fi

# propagate_nan must NOT object (poison in -> poison out)
out=$(run_lens "$FIXTURE_DIR/propagate_nan.py")
if [ $? -ne 0 ]; then
    gate "propagate_nan.py: lens accepts pure NaN propagation" INCONCLUSIVE
else
    v=$(parse_verdict "$out")
    if echo "$v" | grep -qE "^PASS"; then
        gate "propagate_nan.py: lens accepts pure NaN propagation" PASS
    elif echo "$v" | grep -qE "^INCONCLUSIVE"; then
        gate "propagate_nan.py: lens accepts pure NaN propagation" INCONCLUSIVE
    else
        gate "propagate_nan.py: lens accepts pure NaN propagation" FAIL
    fi
fi

# raises_on_nan must NOT object (loud failure)
out=$(run_lens "$FIXTURE_DIR/raises_on_nan.py")
if [ $? -ne 0 ]; then
    gate "raises_on_nan.py: lens accepts raising code" INCONCLUSIVE
else
    v=$(parse_verdict "$out")
    if echo "$v" | grep -qE "^PASS"; then
        gate "raises_on_nan.py: lens accepts raising code" PASS
    elif echo "$v" | grep -qE "^INCONCLUSIVE"; then
        gate "raises_on_nan.py: lens accepts raising code" INCONCLUSIVE
    else
        gate "raises_on_nan.py: lens accepts raising code" FAIL
    fi
fi

# unexercisable.py MUST report INCONCLUSIVE -- kills M02-inconclusive-off.
# PASS here means the lens returned "all scalar NaN/Inf inputs handled or
# propagated cleanly" for code that was never actually run. That is the
# fail-open that guard_lens.py:1191 exists to prevent, and this gate is the
# thing that notices when the prevention is removed. So PASS is RED here,
# not GREEN -- the ONLY green verdict is INCONCLUSIVE.
out=$(run_lens "$FIXTURE_DIR/unexercisable.py")
if [ $? -ne 0 ]; then
    gate "unexercisable.py: unexercised code is INCONCLUSIVE, not PASS" INCONCLUSIVE
else
    v=$(parse_verdict "$out")
    if echo "$v" | grep -qE "^INCONCLUSIVE"; then
        gate "unexercisable.py: unexercised code is INCONCLUSIVE, not PASS" PASS
    else
        gate "unexercisable.py: unexercised code is INCONCLUSIVE, not PASS" FAIL
    fi
fi

# correct_saturation must NOT object (finite limit at +/-Inf is correct)
out=$(run_lens "$FIXTURE_DIR/correct_saturation.py")
if [ $? -ne 0 ]; then
    gate "correct_saturation.py: lens accepts correct Inf saturation" INCONCLUSIVE
else
    v=$(parse_verdict "$out")
    if echo "$v" | grep -qE "^PASS"; then
        gate "correct_saturation.py: lens accepts correct Inf saturation" PASS
    elif echo "$v" | grep -qE "^INCONCLUSIVE"; then
        gate "correct_saturation.py: lens accepts correct Inf saturation" INCONCLUSIVE
    else
        gate "correct_saturation.py: lens accepts correct Inf saturation" FAIL
    fi
fi

# count_and_nan must NOT object (int counter + poison float = handled)
out=$(run_lens "$FIXTURE_DIR/count_and_nan.py")
if [ $? -ne 0 ]; then
    gate "count_and_nan.py: lens accepts int counter with NaN result" INCONCLUSIVE
else
    v=$(parse_verdict "$out")
    if echo "$v" | grep -qE "^PASS"; then
        gate "count_and_nan.py: lens accepts int counter with NaN result" PASS
    elif echo "$v" | grep -qE "^INCONCLUSIVE"; then
        gate "count_and_nan.py: lens accepts int counter with NaN result" INCONCLUSIVE
    else
        gate "count_and_nan.py: lens accepts int counter with NaN result" FAIL
    fi
fi

# separable_gradient must NOT object (finite component unchanged by poison)
out=$(run_lens "$FIXTURE_DIR/separable_gradient.py")
if [ $? -ne 0 ]; then
    gate "separable_gradient.py: lens accepts separable gradient" INCONCLUSIVE
else
    v=$(parse_verdict "$out")
    if echo "$v" | grep -qE "^PASS"; then
        gate "separable_gradient.py: lens accepts separable gradient" PASS
    elif echo "$v" | grep -qE "^INCONCLUSIVE"; then
        gate "separable_gradient.py: lens accepts separable gradient" INCONCLUSIVE
    else
        gate "separable_gradient.py: lens accepts separable gradient" FAIL
    fi
fi

# coupled_fabrication MUST object (finite component changed by poison)
out=$(run_lens "$FIXTURE_DIR/coupled_fabrication.py")
if [ $? -ne 0 ]; then
    gate "coupled_fabrication.py: lens objects to coupled NaN trap" INCONCLUSIVE
else
    v=$(parse_verdict "$out")
    if echo "$v" | grep -qE "^FAIL"; then
        gate "coupled_fabrication.py: lens objects to coupled NaN trap" PASS
    elif echo "$v" | grep -qE "^INCONCLUSIVE"; then
        gate "coupled_fabrication.py: lens objects to coupled NaN trap" INCONCLUSIVE
    else
        gate "coupled_fabrication.py: lens objects to coupled NaN trap" FAIL
    fi
fi

# nan_tolerance_weak must NOT object (NaN tolerance only corrupts int counter)
out=$(run_lens "$FIXTURE_DIR/nan_tolerance_weak.py")
if [ $? -ne 0 ]; then
    gate "nan_tolerance_weak.py: lens accepts NaN tolerance with sound value" INCONCLUSIVE
else
    v=$(parse_verdict "$out")
    if echo "$v" | grep -qE "^PASS"; then
        gate "nan_tolerance_weak.py: lens accepts NaN tolerance with sound value" PASS
    elif echo "$v" | grep -qE "^INCONCLUSIVE"; then
        gate "nan_tolerance_weak.py: lens accepts NaN tolerance with sound value" INCONCLUSIVE
    else
        gate "nan_tolerance_weak.py: lens accepts NaN tolerance with sound value" FAIL
    fi
fi

# unreached_param must NOT object (poisoned learning_rate never exercised)
out=$(run_lens "$FIXTURE_DIR/unreached_param.py")
if [ $? -ne 0 ]; then
    gate "unreached_param.py: lens accepts unreachable poisoned param" INCONCLUSIVE
else
    v=$(parse_verdict "$out")
    if echo "$v" | grep -qE "^PASS"; then
        gate "unreached_param.py: lens accepts unreachable poisoned param" PASS
    elif echo "$v" | grep -qE "^INCONCLUSIVE"; then
        gate "unreached_param.py: lens accepts unreachable poisoned param" PASS
    else
        gate "unreached_param.py: lens accepts unreachable poisoned param" FAIL
    fi
fi

# reached_param MUST object (poisoned learning_rate actually used and fabricates)
out=$(run_lens "$FIXTURE_DIR/reached_param.py")
if [ $? -ne 0 ]; then
    gate "reached_param.py: lens objects to reachable poisoned param" INCONCLUSIVE
else
    v=$(parse_verdict "$out")
    if echo "$v" | grep -qE "^FAIL"; then
        gate "reached_param.py: lens objects to reachable poisoned param" PASS
    elif echo "$v" | grep -qE "^INCONCLUSIVE"; then
        gate "reached_param.py: lens objects to reachable poisoned param" INCONCLUSIVE
    else
        gate "reached_param.py: lens objects to reachable poisoned param" FAIL
    fi
fi

# shape_dependent_coincidence MUST object (single-shape lens misses NaN comparison trap)
out=$(run_lens "$FIXTURE_DIR/shape_dependent_coincidence.py")
if [ $? -ne 0 ]; then
    gate "shape_dependent_coincidence.py: lens detects shape-dependent NaN trap" INCONCLUSIVE
else
    v=$(parse_verdict "$out")
    if echo "$v" | grep -qE "^FAIL"; then
        gate "shape_dependent_coincidence.py: lens detects shape-dependent NaN trap" PASS
    elif echo "$v" | grep -qE "^INCONCLUSIVE"; then
        gate "shape_dependent_coincidence.py: lens detects shape-dependent NaN trap" INCONCLUSIVE
    else
        gate "shape_dependent_coincidence.py: lens detects shape-dependent NaN trap" FAIL
    fi
fi

# shape_independent_component must NOT object (genuinely independent across all shapes)
out=$(run_lens "$FIXTURE_DIR/shape_independent_component.py")
if [ $? -ne 0 ]; then
    gate "shape_independent_component.py: lens accepts truly independent component" INCONCLUSIVE
else
    v=$(parse_verdict "$out")
    if echo "$v" | grep -qE "^PASS"; then
        gate "shape_independent_component.py: lens accepts truly independent component" PASS
    elif echo "$v" | grep -qE "^INCONCLUSIVE"; then
        gate "shape_independent_component.py: lens accepts truly independent component" INCONCLUSIVE
    else
        gate "shape_independent_component.py: lens accepts truly independent component" FAIL
    fi
fi

# literal_membership: the parameter must be reported as REACHED, not unreachable.
# Today's tool says "parameter unreachable" (FAIL). Fixed tool says "parameter reached"
# or simply omits the unreachable label (PASS).
out=$(run_lens "$FIXTURE_DIR/literal_membership.py")
if [ $? -ne 0 ]; then
    gate "literal_membership.py: lens sees parameter as reached" INCONCLUSIVE
else
    if echo "$out" | grep -qE "is_transient_status\(status_code=NaN\).*parameter unreachable"; then
        gate "literal_membership.py: lens sees parameter as reached" FAIL
    else
        gate "literal_membership.py: lens sees parameter as reached" PASS
    fi
fi

# Also run against the real file to confirm the fixture models the same trace.
out_real=$(run_lens "$ROOT/tests/userside/retry_http.py")
if [ $? -ne 0 ]; then
    gate "retry_http.py (real): lens sees status_code as reached" INCONCLUSIVE
else
    if echo "$out_real" | grep -qE "is_transient_status\(status_code=NaN\).*parameter unreachable"; then
        gate "retry_http.py (real): lens sees status_code as reached" FAIL
    else
        gate "retry_http.py (real): lens sees status_code as reached" PASS
    fi
fi

# ---------------------------------------------------------------- fixture: scope lines must cover all parameters and add up
# U5: the lens must emit machine-readable coverage counts so a report layer can
# distinguish UNMATCHED PASS from MATCHED PASS. Every parameter must land in exactly
# one bucket and the arithmetic must hold.
out=$(run_lens "$FIXTURE_DIR/scope_fixture.py")
if [ $? -ne 0 ]; then
    gate "scope_fixture.py: lens emits scope lines" INCONCLUSIVE
else
    # Extract scope counts. Expect keys: scope:params_total, scope:params_judged,
    # scope:params_unreachable, scope:params_unexercised, scope:functions_total,
    # scope:functions_scanned, scope:mode.
    total=$(echo "$out" | grep -E "^scope:params_total=" | head -1 | sed 's/^scope:params_total=//')
    judged=$(echo "$out" | grep -E "^scope:params_judged=" | head -1 | sed 's/^scope:params_judged=//')
    unreachable=$(echo "$out" | grep -E "^scope:params_unreachable=" | head -1 | sed 's/^scope:params_unreachable=//')
    unexercised=$(echo "$out" | grep -E "^scope:params_unexercised=" | head -1 | sed 's/^scope:params_unexercised=//')
    aborted=$(echo "$out" | grep -E "^scope:params_aborted=" | head -1 | sed 's/^scope:params_aborted=//')
    mode=$(echo "$out" | grep -E "^scope:mode=" | head -1 | sed 's/^scope:mode=//')

    if [ -z "$total" ] || [ -z "$judged" ] || [ -z "$unreachable" ] || [ -z "$unexercised" ] || [ -z "$aborted" ] || [ -z "$mode" ]; then
        gate "scope_fixture.py: lens emits all required scope lines" FAIL
    else
        # For this fixture the expected truth is:
        #   params_total = 4 (status_code from ignore_param, status_code from check_status,
        #                     x, value)
        #   params_judged = 2 (status_code from check_status via U4 literal harvesting,
        #                      x from judged_function)
        #   params_unreachable = 1 (status_code from ignore_param; output identical always)
        #   params_unexercised = 1 (value from apply_callback; callback baseline invalid)
        #   mode = UNMATCHED
        # params_aborted joined the sum on 2026-09-05. A bucket left OUT of this
        # arithmetic is worse than no sum check at all: it would silently tolerate
        # parameters vanishing into it. Every bucket the lens emits must appear here.
        if [ "$total" -eq 4 ] && [ "$judged" -eq 2 ] && [ "$unreachable" -eq 1 ] && [ "$unexercised" -eq 1 ] && [ "$aborted" -eq 0 ] && [ "$((judged + unreachable + unexercised + aborted))" -eq "$total" ] && [ "$mode" = "UNMATCHED" ]; then
            gate "scope_fixture.py: scope counts add up and match truth" PASS
        else
            gate "scope_fixture.py: scope counts add up and match truth" FAIL
            echo "       got total=$total judged=$judged unreachable=$unreachable unexercised=$unexercised aborted=$aborted mode=$mode"
        fi
    fi
fi

# ================================================================ HONESTY-PATH GATES
# WHY THESE EXIST — mutation audit 2026-09-03, tools/mutate_guard.py.
#
# Fifteen surgical mutants each deleted ONE defence from guard_lens.py. Seven were
# killed by this suite. EIGHT SURVIVED IT UNTOUCHED — and every single survivor lived
# in the HONESTY path: the code that decides "we could not measure this" rather than
# "your code is clean". The defect-detection path was well covered; the path that
# admits ignorance was not covered at all.
#
# The hole was structural. The only scope assertion in this file (scope_fixture.py)
# checks that the buckets SUM to params_total — and the sum stays correct no matter how
# badly the CLASSIFICATION breaks, so three separate bucket guards could each be deleted
# with that gate still green. It also never asserted functions_scanned.
#
# Each gate below therefore pins the exact bucket a parameter must land in, and names
# the mutant it is required to kill. A gate here that cannot go red is worthless:
# after changing any of them, re-run
#     python3 tools/mutate_guard.py --config tools/mutate_guard.json
# and require the named mutant to be KILLED.

# scope_gate <fixture> <label> <verdict-regex> <total> <judged> <unreachable> <unexercised> [functions_scanned] [aborted]
#
# `aborted` defaults to 0, so every call that does not mention it ASSERTS IT IS
# ZERO. That is deliberate: a silent new bucket is how classification defects
# hide. params_aborted was added 2026-09-05 after 6E found that a resource-killed
# probe was being filed as `unreachable` -- a bucket meaning "we measured and it
# did not matter" -- when nothing had been observed at all.
scope_gate() {
    local fx="$1" label="$2" vre="$3" et="$4" ej="$5" eu="$6" ex="$7" efs="${8:-}" eab="${9:-0}"
    local out rc v total judged unreachable unexercised fscanned aborted ok

    out=$(run_lens "$FIXTURE_DIR/$fx")
    rc=$?
    if [ "$rc" -ne 0 ]; then
        gate "$label" INCONCLUSIVE
        return
    fi

    v=$(parse_verdict "$out")
    total=$(echo "$out"       | grep -E "^scope:params_total="       | head -1 | sed 's/.*=//')
    judged=$(echo "$out"      | grep -E "^scope:params_judged="      | head -1 | sed 's/.*=//')
    unreachable=$(echo "$out" | grep -E "^scope:params_unreachable=" | head -1 | sed 's/.*=//')
    unexercised=$(echo "$out" | grep -E "^scope:params_unexercised=" | head -1 | sed 's/.*=//')
    fscanned=$(echo "$out"    | grep -E "^scope:functions_scanned="  | head -1 | sed 's/.*=//')
    aborted=$(echo "$out"     | grep -E "^scope:params_aborted="     | head -1 | sed 's/.*=//')

    if [ -z "$total" ] || [ -z "$judged" ] || [ -z "$unreachable" ] || [ -z "$unexercised" ] || [ -z "$fscanned" ] || [ -z "$aborted" ]; then
        gate "$label" FAIL
        echo "       lens did not emit all required scope lines"
        return
    fi

    ok=1
    echo "$v" | grep -qE "$vre"        || ok=0
    [ "$total"       -eq "$et" ]       || ok=0
    [ "$judged"      -eq "$ej" ]       || ok=0
    [ "$unreachable" -eq "$eu" ]       || ok=0
    [ "$unexercised" -eq "$ex" ]       || ok=0
    [ "$aborted"     -eq "$eab" ]      || ok=0
    if [ -n "$efs" ]; then
        [ "$fscanned" -eq "$efs" ]     || ok=0
    fi

    if [ "$ok" -eq 1 ]; then
        gate "$label" PASS
    else
        gate "$label" FAIL
        echo "       got      verdict=$v"
        echo "                total=$total judged=$judged unreachable=$unreachable unexercised=$unexercised functions_scanned=$fscanned aborted=$aborted"
        echo "       expected verdict~$vre"
        echo "                total=$et judged=$ej unreachable=$eu unexercised=$ex functions_scanned=${efs:-any} aborted=$eab"
    fi
}

# --- fixtures for the honesty path ------------------------------------------------

# M07: a NaN that changes the SHAPE of the result. Fabricates a shorter, finite
# answer instead of propagating. The finite element it invents is a defect.
write_fixture "shape_shift_fabrication.py" <<'PY'
def shrink_on_nan(x: float) -> list:
    """Fabricates a SHORTER all-finite result when x is NaN."""
    if x != x:
        return [1.0]
    return [x, x * 2.0, x * 3.0]
PY

# M11: U6. A sibling parameter that cannot be synthesised (an unannotated callback)
# makes the whole function unexercisable, so it must not be counted as SCANNED.
write_fixture "unsynthesisable_sibling.py" <<'PY'
def apply_callback(value: float, callback) -> float:
    """Unsynthesisable sibling: U6 must skip this function before scanning it."""
    return callback(value)


def plain_scalar(x: float) -> float:
    """An ordinary judged parameter, so the fixture is not vacuous."""
    return x * 2.0
PY

# M12: the clean baseline is KILLED by the CPU limit on the first candidate value.
# A lens that ignores the kill walks on to a later literal and reaches a
# "discriminating" observation it never earned.
write_fixture "killed_baseline.py" <<'PY'
def slow_on_default(x: float) -> float:
    """The shape default (2.0) burns CPU and is killed by RLIMIT_CPU;
    the harvested literal 7.0 would return instantly."""
    if x == 2.0:
        while True:
            pass
    return x * 7.0
PY

# M13: the ONLY signal is a correct loud failure. Raising on poison is handling it,
# and the parameter must be credited as JUDGED, not written off as unreachable.
write_fixture "raise_only_guard.py" <<'PY'
import math


def guarded_only(x: float) -> float:
    """Handles poison by raising -- the raise is the only signal the lens gets."""
    if not math.isfinite(x):
        raise ValueError("non-finite input")
    return x * 2.0
PY

# M14: an all-zero float baseline is no-op output, not a reachability signal.
write_fixture "zero_baseline.py" <<'PY'
def always_zero(x: float) -> float:
    """Clean baseline is 0.0 -- degenerate, so nothing was really exercised."""
    return 0.0
PY

# M15: an empty collection baseline is likewise degenerate.
write_fixture "empty_baseline.py" <<'PY'
def always_empty(x: float) -> list:
    """Clean baseline is an empty collection -- degenerate."""
    return []
PY

# M17 / GAP 1 (6E, 2026-09-04). The CLEAN baseline is valid and non-degenerate;
# every POISONED probe hangs and is killed. Nothing is ever observed. Before
# params_aborted existed this landed in params_unreachable -- a bucket whose
# definition is "every OBSERVED poison matched the clean baseline" -- and was
# byte-identical in every scope column to a genuinely unreachable parameter.
# killed_baseline.py cannot cover this: there the CLEAN value dies.
write_fixture "poison_killed.py" <<'PY'
import math


def poison_killed(x: float) -> float:
    """Valid clean baseline; every non-finite input hangs until the CPU limit."""
    if math.isfinite(x):
        return x * 2.0
    while True:
        pass
PY

# M04: a parameter that is genuinely UNREACHABLE while the baseline is valid.
# The pre-existing unreached_param.py cannot cover this: its all-zero return is
# DEGENERATE, so it is classified by the degeneracy path and never reaches the
# reachability guard. Only a non-degenerate baseline exercises that guard.
write_fixture "unused_scalar.py" <<'PY'
def unused_scalar(x: float, unused: float) -> float:
    """`unused` never reaches the result, and the baseline is NOT degenerate."""
    return x * 3.0 + 1.0
PY

# --- gates -------------------------------------------------------------------------
echo; echo "--- HONESTY PATH (scope buckets + could-not-measure) ---"

# kills M07-length-mismatch-blind
scope_gate "shape_shift_fabrication.py" \
    "shape_shift_fabrication.py: NaN that changes result shape is a defect" \
    "^FAIL" 1 1 0 0 1

# kills M11-u6-off — the bucket totals are IDENTICAL with and without U6;
# functions_scanned is the only column that separates them.
scope_gate "unsynthesisable_sibling.py" \
    "unsynthesisable_sibling.py: U6 function not counted as scanned" \
    "^PASS" 2 1 0 1 1

# kills M12-killed-probe-counts
# RECLASSIFIED 2026-09-05: was unexercised=1. The clean value is CPU-killed, which
# is a resource abort, not "no valid baseline could be built for other reasons".
# Both kill paths now land in params_aborted.
scope_gate "killed_baseline.py" \
    "killed_baseline.py: CPU-killed baseline is ABORTED, not unexercised" \
    "^INCONCLUSIVE.*aborted" 1 0 0 0 1 1

# kills M13-raise-not-handled
# ⛔ THE VERDICT REGEX HERE IS LOAD-BEARING — do not loosen it back to "^PASS".
# 4Q broke this gate on 2026-09-04: deleting the WHOLE raise-credit block (not just
# `param_handled_raise = True`, which my mutant M13 removed and which the surviving
# `continue` catches) lets the Exception fall through into shape_observations. It
# then reads as discrimination, the parameter counts as JUDGED, and every bucket
# matches. Reproduced: pristine and mutant both emit PASS 1 1 0 0 1.
# The lens's verdict TEXT does differ -- "handled or propagated cleanly" becomes
# "all NaN inputs handled; N Inf observation(s) recorded" -- so the information was
# in the output all along and the old "^PASS" threw it away. Assert the wording.
scope_gate "raise_only_guard.py" \
    "raise_only_guard.py: correct loud failure counts as JUDGED" \
    "^PASS.*handled or propagated cleanly" 1 1 0 0 1

# kills M14-degenerate-float-blind AND M02-inconclusive-off
# (nothing was exercised, so the verdict must be INCONCLUSIVE, never PASS)
scope_gate "zero_baseline.py" \
    "zero_baseline.py: all-zero baseline is degenerate, verdict INCONCLUSIVE" \
    "^INCONCLUSIVE" 1 0 0 1 1

# kills M15-degenerate-empty-blind AND M02-inconclusive-off
scope_gate "empty_baseline.py" \
    "empty_baseline.py: empty baseline is degenerate, verdict INCONCLUSIVE" \
    "^INCONCLUSIVE" 1 0 0 1 1

# kills M17-aborted-is-unreachable. THE POINT OF THIS GATE is the pair of ZEROES:
# unreachable=0 with aborted=1. Asserting only "aborted=1" would still pass a lens
# that double-counted into both buckets, and asserting only INCONCLUSIVE would pass
# the pre-2026-09-05 lens, which also said INCONCLUSIVE while calling it unreachable.
scope_gate "poison_killed.py" \
    "poison_killed.py: killed poison probe is ABORTED, never unreachable" \
    "^INCONCLUSIVE.*aborted.*nothing observed" 1 0 0 0 1 1

# kills M04-reachability-off FOR THE RIGHT REASON.
# unreached_param.py already existed and its gate passed, but it was passing on the
# degeneracy path -- deleting the reachability guard left it green. Here the
# baseline is valid, so `unused` can only land in the unreachable bucket if the
# reachability guard is doing its job.
scope_gate "unused_scalar.py" \
    "unused_scalar.py: unreachable param with a VALID baseline" \
    "^PASS" 2 1 1 0 1

# kills M10-no-inf-probe.
# ⛔ THIS GATE IS POSITIVE ON PURPOSE. The pre-existing correct_saturation.py gate
# only asserts the lens does NOT object -- and a lens that never probes Inf at all
# also never objects, so deleting POISON_INF outright left that gate green. Assert
# that the Inf observations were actually RECORDED.
out=$(run_lens "$FIXTURE_DIR/correct_saturation.py")
if [ $? -ne 0 ]; then
    gate "correct_saturation.py: Inf probes actually ran" INCONCLUSIVE
else
    inf_details=$(echo "$out" | grep -cE "^detail:.*(\+Inf|-Inf)")
    if echo "$out" | grep -qE "^verdict:PASS.*Inf observation\(s\) recorded" && [ "$inf_details" -eq 2 ]; then
        gate "correct_saturation.py: Inf probes actually ran" PASS
    else
        gate "correct_saturation.py: Inf probes actually ran" FAIL
        echo "       expected a PASS verdict recording Inf observations and 2 Inf detail lines"
        echo "       got verdict=$(parse_verdict "$out") inf_detail_lines=$inf_details"
    fi
fi

# ---------------------------------------------------------------- canonical oracles: record objections, do not assert pass
echo; echo "--- CANONICAL ORACLE OBJECTIONS (promotion checklist) ---"

CANONICAL_ORACLES=(
    "linear_regression_gd"
    "kmeans_clustering"
    "logistic_regression"
    "decision_tree_id3"
    "gradient_descent_momentum"
    "knn_classifier"
    "naive_bayes_text"
    "perceptron"
    "polynomial_regression"
    "ridge_regression_normal_eq"
)

for pattern in "${CANONICAL_ORACLES[@]}"; do
    oracle="$ROOT/tests/corpus/${pattern}_canonical_good.py"
    if [ ! -f "$oracle" ]; then
        note "$pattern" "oracle missing"
        continue
    fi
    out=$(run_lens "$oracle")
    if [ $? -ne 0 ]; then
        note "$pattern" "INCONCLUSIVE"
    else
        v=$(parse_verdict "$out")
        if echo "$v" | grep -qE "^FAIL"; then
            note "$pattern" "OBJECTS"
            CANONICAL_FAILURES+=("$pattern")
        elif echo "$v" | grep -qE "^INCONCLUSIVE"; then
            note "$pattern" "INCONCLUSIVE"
        else
            note "$pattern" "clean"
        fi
    fi
done

# ---------------------------------------------------------------- red-proof check
echo; echo "--- RED-PROOF STATUS ---"
if [ ! -f "$GUARD_LENS" ]; then
    gate "red-proof: lens missing so test must be rejected" PASS
else
    # Before the lens is built correctly, the unguarded fixture should still fail
    # because it has no guard. This is the sanity check that the ruler can go red.
    red_out=$(run_lens "$FIXTURE_DIR/unguarded_scalar.py")
    if [ $? -ne 0 ]; then
        gate "red-proof: unguarded fixture rejected" INCONCLUSIVE
    else
        red_v=$(parse_verdict "$red_out")
        if echo "$red_v" | grep -qE "^FAIL"; then
            gate "red-proof: unguarded fixture rejected" PASS
        else
            gate "red-proof: unguarded fixture rejected" FAIL
        fi
    fi
fi

# ---------------------------------------------------------------- final
echo; echo "=================================================================="
if [ ${#CANONICAL_FAILURES[@]} -gt 0 ]; then
    echo "Canonical oracles the Guard lens objects to (must be fixed before gating):"
    printf '  %s\n' "${CANONICAL_FAILURES[@]}"
fi

if [ "$FAILED" -ne 0 ]; then
    echo ">>> REJECTED: one or more asserted gates failed."
    exit 1
elif [ "$INCONCLUSIVE" -ne 0 ]; then
    echo ">>> INCONCLUSIVE: lens or fixture could not be evaluated."
    exit 2
else
    echo ">>> ACCEPTED: all asserted gates pass."
    exit 0
fi
