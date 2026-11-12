#!/bin/bash
# macOS ships no setsid. Its role here is to give `timeout` its own process
# group to reap; the Python side already isolates via start_new_session, so
# running without it is bounded, just one less belt. Empty expands to nothing.
SETSID="$(command -v setsid || true)"
# ACCEPTANCE TEST for the NEGATIVE-DOMAIN BEHAVIOURAL CLASSIFIER (R1 class).
#
# The Guard lens handles NaN/Inf. The Shape Guard handles shape mismatches.
# This classifier handles a third defect class: a numeric parameter that is
# semantically required to be positive (learning rate, iteration count, etc.) but
# accepts negative values and silently produces wrong output.
#
# Tool under test:
#   python3 "$ROOT/tools/domain_classifier.py" --fixture <file> --param <name>
#
# Expected tool stdout (exact contract 4Q must implement):
#   verdict:DEFECT <reason>         diverges or collapses
#   verdict:HANDLED <reason>         raises
#   verdict:INCONCLUSIVE <reason>    unreachable · proportionate · not_numerically_comparable ·
#                                       parameter_not_in_examined_function · not_found ·
#                                       not_measurable_by_magnitude
#
# Honest-reporting rule:
#   * "not_found" must mean the parameter genuinely does not exist anywhere in the module's
#     public-callable functions.
#   * If the parameter exists but was in a public function the tool did not examine, the tool
#     must not report "not_found"; it must either examine that function or say it did not look.
#   * Small discrete label outputs cannot be trend-measured by magnitude; report
#     "not_measurable_by_magnitude" rather than fabricating a collapse.
#
# The inline harness below is the REFERENCE SPECIFICATION that 4Q must reproduce.
# It is not the thing under test; the assertions are driven by the external tool.
#
# Criterion (reading the trend across -1, -10, -100):
#   diverges OR collapses  -> DEFECT       (provable without an oracle)
#   raises                  -> HANDLED
#   output unchanged        -> UNREACHABLE  -> INCONCLUSIVE
#   bounded change          -> CANNOT DECIDE -> INCONCLUSIVE, never a defect
#
# The collapse-vs-unreachable discriminator is bit-identical comparison to the
# clean baseline, not magnitude-smallness. Collapse changes the output; unreachable
# does not.
#
# EXIT 0 = classifier accepted. EXIT 1 = rejected.

set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

PYTHON="/usr/bin/python3"
CLASSIFIER="$ROOT/tools/domain_classifier.py"
FIXTURE_DIR=$(mktemp -d /tmp/guard_domain_classifier.XXXXXX)
if [ ! -d "$FIXTURE_DIR" ]; then
    echo "FATAL: mktemp failed"
    exit 1
fi
HARNESS="$FIXTURE_DIR/domain_classifier_harness.py"

cleanup() {
    for pid in $(pgrep -f 'python3 .*guard_lens' 2>/dev/null || true); do
        if [ "$pid" -eq $$ ]; then continue; fi
        exe=$(readlink "/proc/$pid/exe" 2>/dev/null || echo "")
        case "$exe" in */python*|python*) : ;; *) continue ;; esac
        cmd=$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || echo "")
        if echo "$cmd" | grep -qE 'guard_lens\.py'; then
            kill -9 "$pid" 2>/dev/null || true
        fi
    done
    rm -rf "$FIXTURE_DIR"
}
trap cleanup EXIT

FAILED=0
INCONCLUSIVE=0

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

# Run a fixture through the external tool under test.
run_fixture() {
    local label="$1"
    local fixture="$2"
    local param="$3"
    local out="$FIXTURE_DIR/${label}.out"
    local err="$FIXTURE_DIR/${label}.err"
    $SETSID timeout --kill-after=5s 20s \
        "$PYTHON" "$CLASSIFIER" --fixture "$fixture" --param "$param" > "$out" 2> "$err"
    rc=$?
    if [ "$rc" -eq 124 ] || [ "$rc" -eq 137 ]; then
        echo "TIMEOUT"
        return
    fi
    if [ "$rc" -ne 0 ]; then
        echo "ERROR rc=$rc"
        head -5 "$err" 2>/dev/null || true
        return
    fi
    head -1 "$out" 2>/dev/null || echo "EMPTY"
}

# ---------------------------------------------------------------- fixtures
write_fixture() {
    local name="$1"
    local path="$FIXTURE_DIR/$name"
    cat > "$path"
    rc=$?
    if [ "$rc" -ne 0 ]; then echo "FATAL: write failed $name"; exit 1; fi
    if [ ! -s "$path" ]; then echo "FATAL: empty fixture $name"; exit 1; fi
}

# 1. DIVERGES: negative learning rate makes gradient descent explode.
write_fixture "diverges_negative_lr.py" <<'PY'
def fit(X: list, y: list, learning_rate: float) -> tuple[list, float]:
    iterations = 5
    weights = [0.0] * len(X[0])
    bias = 0.0
    for _ in range(iterations):
        preds = [bias + sum(weights[j] * X[i][j] for j in range(len(weights))) for i in range(len(X))]
        errors = [preds[i] - y[i] for i in range(len(X))]
        dw = [sum(errors[i] * X[i][j] for i in range(len(X))) / len(X) for j in range(len(weights))]
        db = sum(errors) / len(X)
        weights = [weights[j] - learning_rate * dw[j] for j in range(len(weights))]
        bias -= learning_rate * db
    return weights, bias
PY

# 2. COLLAPSES: negative iteration count makes range(-n) empty, returning initial zeros.
# The clean baseline (positive iterations) returns a non-zero update, so this is a
# visible change — collapse, not unreachable invariance.
write_fixture "collapses_negative_iters.py" <<'PY'
def fit(X: list, y: list, iterations: int) -> tuple[list, float]:
    learning_rate = 0.01
    weights = [0.0] * len(X[0])
    bias = 0.0
    for _ in range(iterations):
        preds = [bias + sum(weights[j] * X[i][j] for j in range(len(weights))) for i in range(len(X))]
        errors = [preds[i] - y[i] for i in range(len(X))]
        dw = [sum(errors[i] * X[i][j] for i in range(len(X))) / len(X) for j in range(len(weights))]
        db = sum(errors) / len(X)
        weights = [weights[j] - learning_rate * dw[j] for j in range(len(weights))]
        bias -= learning_rate * db
    return weights, bias
PY

# 3. RAISES: function rejects negative values outright.
write_fixture "raises_negative.py" <<'PY'
def fit(X: list, y: list, learning_rate: float) -> tuple[list, float]:
    if learning_rate < 0:
        raise ValueError("learning_rate must be non-negative")
    weights = [0.0] * len(X[0])
    return weights, 0.0
PY

# 4. UNREACHABLE: the parameter is accepted but never reaches the computation.
# Clean baseline and all negative probes return identical output.
write_fixture "unreachable_negative.py" <<'PY'
def fit(X: list, y: list, learning_rate: float) -> tuple[list, float]:
    weights = [0.5] * len(X[0])
    return weights, 1.0
PY

# 5. LEGITIMATELY NEGATIVE (continuous): a bias-like parameter where negative values are
# mathematically valid. Output changes but stays bounded; must be INCONCLUSIVE.
write_fixture "legit_negative_bias.py" <<'PY'
def shift_clip(x: float, bias: float) -> float:
    return max(-1.0, min(1.0, x + bias))
PY

# 6. LEGITIMATELY NEGATIVE (discrete labels): a function returning binary labels where a
# bias-like parameter correctly drives every prediction to class 0. Magnitude of the
# output going to 0 means "all class 0", not "computation collapsed". The classifier must
# refuse to magnitude-trend categorical output and report INCONCLUSIVE.
# Broken tool will say DEFECT collapses. Fixed tool must say INCONCLUSIVE not_measurable_by_magnitude.
# Fixture shape mirrors tests/corpus/perceptron_canonical_good.py: first function is
# irrelevant; second function takes X/weights/bias and returns binary labels. With the
# tool's baseline weights=[0.5,-0.5] and X=[[1,2],[3,4],[5,6]], every baseline label is 1
# (magnitude=3) and every negative probe flips every label to 0 (magnitude=0), exactly
# matching the real perceptron trace:
#   baseline_magnitude=3.0 probe=-1.0:0.0 probe=-10.0:0.0 probe=-100.0:0.0
write_fixture "legit_negative_bias_discrete.py" <<'PY'
def perceptron_fit(X: list, y: list, learning_rate: float = 1.0, max_iterations: int = 100) -> tuple[list, float]:
    """First public function: irrelevant to this test."""
    return [0.0] * len(X[0]), 0.0


def perceptron_predict(X: list, weights: list, bias: float) -> list:
    """Second public function: negative bias correctly drives every prediction to class 0.
    The output is binary labels; magnitude collapse is a legitimate decision-boundary shift."""
    if X and len(X[0]) != len(weights):
        raise ValueError("dimension mismatch")
    return [1 if (bias + sum(weights[j] * X[i][j] for j in range(len(weights)))) >= 0 else 0 for i in range(len(X))]
PY

# 7. NOT NUMERICALLY COMPARABLE: the function returns a non-numeric structure
# (tree/dict) that cannot be trend-measured at all. Tool must be honest and
# report INCONCLUSIVE rather than fabricate a verdict.
write_fixture "non_numeric_output.py" <<'PY'
def fit(X: list, y: list, max_depth: int) -> dict:
    # max_depth is a real parameter but the returned object is not numeric;
    # the classifier has no oracle to decide whether a tree at depth -1 vs depth 5
    # is correct or broken.
    return {"depth": max_depth, "root": {"left": None, "right": None}}
PY

# 8. PARAMETER ON SECOND PUBLIC FUNCTION: the target parameter lives on a callable
# that is not the first public function in the module. The classifier must be able
# to examine every top-level public function, not stop at the first one.
# The second function has a negative-count collapse defect, so the expected verdict is
# DEFECT. The broken tool, which only looks at the first function, will answer
# "parameter 'max_iters' not found" (INCONCLUSIVE), making this gate FAIL.
# After the fix the tool finds predict_steps and reports DEFECT "collapses".
write_fixture "second_public_function.py" <<'PY'
def fit(X: list, y: list, learning_rate: float) -> tuple[list, float]:
    """First public function: irrelevant to this test."""
    weights = [0.0] * len(X[0])
    return weights, 0.0


def predict_steps(x: float, max_iters: int) -> float:
    """Second public function: negative max_iters makes range() empty, so the
    loop body never runs and the result collapses to the initial value 0.0.
    With a positive max_iters the loop accumulates a non-zero value."""
    value = 0.0
    for _ in range(max_iters):
        value += 0.5
    return value
PY

# ---------------------------------------------------------------- reference harness
# The harness below is the SPECIFICATION 4Q must implement in tools/domain_classifier.py.
# This file does NOT run the harness; it is here so the contract is self-contained.
write_fixture "domain_classifier_harness.py" <<'PY'
# REFERENCE IMPLEMENTATION — not executed by this acceptance test.
# Reproduce this behaviour in tools/domain_classifier.py.
import importlib.util
import math

PROBE_VALUES_FLOAT = [-1.0, -10.0, -100.0]
PROBE_VALUES_INT = [-1, -10, -100]


def load_fixture(path: str):
    spec = importlib.util.spec_from_file_location("domain_fixture", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for name in dir(mod):
        obj = getattr(mod, name)
        if callable(obj) and not name.startswith("_"):
            return obj
    raise RuntimeError("no public function in fixture")


def magnitude(v) -> float:
    if isinstance(v, bool):
        return 0.0
    if isinstance(v, (int, float)):
        return abs(float(v)) if math.isfinite(v) else float("inf")
    if isinstance(v, (list, tuple)):
        return sum(magnitude(x) for x in v)
    if isinstance(v, dict):
        return sum(magnitude(x) for x in v.values())
    return 0.0


def is_numeric(v) -> bool:
    if isinstance(v, bool):
        return False
    if isinstance(v, (int, float)):
        return True
    if isinstance(v, (list, tuple)):
        return all(is_numeric(x) for x in v)
    if isinstance(v, dict):
        return all(is_numeric(x) for x in v.values())
    return False


def classify(baseline, results: list) -> tuple[str, str]:
    raised = sum(1 for r in results if r is None)

    if raised == len(results):
        return "HANDLED", "raises"

    if not all(is_numeric(r) for r in results + [baseline]):
        return "INCONCLUSIVE", "not_numerically_comparable"

    if all(r == baseline for r in results):
        return "INCONCLUSIVE", "unreachable"

    probe_mags = [magnitude(r) for r in results if r is not None]
    if not probe_mags:
        return "HANDLED", "raises"

    baseline_mag = magnitude(baseline)

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
PY

# ---------------------------------------------------------------- run
echo "=================================================================="
echo " DOMAIN CLASSIFIER — negative-parameter behavioural trend"
echo " tool: $CLASSIFIER"
echo " date: $(date -u +%FT%TZ)"
echo "=================================================================="

if [ ! -f "$CLASSIFIER" ]; then
    echo ">>> REJECTED: classifier tool not found at $CLASSIFIER."
    exit 1
fi

run_gate() {
    local label="$1"
    local expected="$2"
    local param="$3"
    local fixture="$FIXTURE_DIR/${label}.py"
    v=$(run_fixture "$label" "$fixture" "$param")
    echo "$label: $v"
    if echo "$v" | grep -qE "^verdict:${expected}\b"; then
        gate "$label: expected ${expected}" PASS
    else
        gate "$label: expected ${expected}" FAIL
    fi
}

# DEFECT outcomes
run_gate "diverges_negative_lr" "DEFECT" "learning_rate"
run_gate "collapses_negative_iters" "DEFECT" "iterations"

# HANDLED
run_gate "raises_negative" "HANDLED" "learning_rate"

# INCONCLUSIVE outcomes
run_gate "unreachable_negative" "INCONCLUSIVE" "learning_rate"
run_gate "legit_negative_bias" "INCONCLUSIVE" "bias"
run_gate "legit_negative_bias_discrete" "INCONCLUSIVE" "bias"
run_gate "non_numeric_output" "INCONCLUSIVE" "max_depth"

# SECOND PUBLIC FUNCTION gate: parameter 'max_iters' exists only on the SECOND
# public function. Broken tool answers INCONCLUSIVE "not found" (gate FAILS).
# Fixed tool examines every public function and reports DEFECT "collapses".
run_gate "second_public_function" "DEFECT" "max_iters"

# ---------------------------------------------------------------- final
echo; echo "=================================================================="
if [ "$FAILED" -ne 0 ]; then
    echo ">>> REJECTED: one or more classifier gates failed."
    exit 1
elif [ "$INCONCLUSIVE" -ne 0 ]; then
    echo ">>> INCONCLUSIVE: classifier could not be fully evaluated."
    exit 2
else
    echo ">>> ACCEPTED: all classifier gates landed in expected buckets."
    exit 0
fi
