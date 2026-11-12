#!/bin/bash
# DOMAIN GUARDS — acceptance test for explicit rejection of semantically invalid
# negative numeric parameters in the Forge Gate canonical oracles.
#
# These seven defects were found by measurement with tools/domain_classifier.py.
# The maintainer approved fixing them. The fix is to RAISE on the invalid negative value,
# not to silently accept it. A domain guard is a hand-coded check, not a behavioural
# classifier.
#
# Seven confirmed defects (all the same shape: a parameter semantically required to be
# non-negative is silently accepted when negative):
#   linear_regression_gd_canonical_good.py  :: linear_regression_gd(learning_rate)
#   linear_regression_gd_canonical_good.py  :: linear_regression_gd(iterations)
#   logistic_regression_canonical_good.py   :: logistic_regression_fit(learning_rate)
#   logistic_regression_canonical_good.py   :: logistic_regression_fit(iterations)
#   perceptron_canonical_good.py            :: perceptron_fit(max_iterations)
#   polynomial_regression_canonical_good.py :: vandermonde_matrix(degree)
#   polynomial_regression_canonical_good.py :: fit_polynomial(degree)
#
# Each gate has TWO assertions:
#   1. negative value -> RAISE (the fix)
#   2. valid value    -> byte-identical to the current canonical output (Law-5 control)
#
# ZERO ruling:
#   * learning_rate=0 is allowed. It disables updates, which is degenerate but not
#     semantically impossible; it is a valid numeric value for the parameter.
#   * iterations=0 / max_iterations=0 are allowed. "Perform zero updates" is a
#     well-defined (if useless) operation and is not the same class of error as -1.
#   * degree=0 is allowed. A constant polynomial is a legitimate polynomial of degree 0.
#   * Only STRICTLY NEGATIVE values are required to raise.
#
# EXIT 0 = all domain guards accepted. EXIT 1 = rejected.

set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

PYTHON="/usr/bin/python3"
CORPUS="$ROOT/tests/corpus"
REGRESSION="$ROOT/tests/regression/domain_guards"
FAILED=0

note() { printf '%-70s %s\n' "$1" "$2"; }
gate() {
    if [ "$2" = "PASS" ]; then
        note "$1" "✅ PASS"
    else
        note "$1" "❌ FAIL"
        FAILED=1
    fi
}

mkdir -p "$REGRESSION"

# Run a Python expression and capture its stdout. Returns empty on any failure.
run_py() {
    local expr="$1"
    "$PYTHON" -c "$expr" 2>/dev/null
}

# Snapshot the current broken behaviour for a single negative case.
snapshot_broken() {
    local file="$1"
    local func="$2"
    local param="$3"
    local value="$4"
    local snapshot="$REGRESSION/${file%.py}_${func}_${param}_${value}.txt"
    local expr
    expr=$(cat <<PY
import sys
sys.path.insert(0, "$CORPUS")
from ${file%.py} import $func
X = [[1.0, 2.0], [2.0, 3.0], [3.0, 1.0], [-1.0, -2.0], [-2.0, -1.0], [-3.0, -3.0]]
y = [1.0, 2.0, 3.0, -1.0, -2.0, -3.0]
yb = [1, 1, 1, 0, 0, 0]
xv = [1.0, 2.0, 3.0]
try:
    if "$func" == "linear_regression_gd":
        r = $func(X, y, $param=$value)
    elif "$func" == "logistic_regression_fit":
        r = $func(X, yb, $param=$value)
    elif "$func" == "perceptron_fit":
        r = $func(X, yb, $param=$value)
    elif "$func" == "vandermonde_matrix":
        r = $func(xv, $value)
    elif "$func" == "fit_polynomial":
        r = $func(xv, xv, $value)
    else:
        r = "UNKNOWN_FUNCTION"
    print(repr(r))
except Exception as e:
    print(f"RAISED {type(e).__name__}: {e}")
PY
    )
    run_py "$expr" > "$snapshot"
}

# Assert that calling the function with a negative parameter raises.
assert_negative_raises() {
    local file="$1"
    local func="$2"
    local param="$3"
    local value="$4"
    local label="$file :: $func($param=$value)"

    snapshot_broken "$file" "$func" "$param" "$value"

    local expr
    expr=$(cat <<PY
import sys
sys.path.insert(0, "$CORPUS")
from ${file%.py} import $func
X = [[1.0, 2.0], [2.0, 3.0], [3.0, 1.0], [-1.0, -2.0], [-2.0, -1.0], [-3.0, -3.0]]
y = [1.0, 2.0, 3.0, -1.0, -2.0, -3.0]
yb = [1, 1, 1, 0, 0, 0]
xv = [1.0, 2.0, 3.0]
raised = False
try:
    if "$func" == "linear_regression_gd":
        $func(X, y, $param=$value)
    elif "$func" == "logistic_regression_fit":
        $func(X, yb, $param=$value)
    elif "$func" == "perceptron_fit":
        $func(X, yb, $param=$value)
    elif "$func" == "vandermonde_matrix":
        $func(xv, $value)
    elif "$func" == "fit_polynomial":
        $func(xv, xv, $value)
except Exception:
    raised = True
print("RAISE" if raised else "NO_RAISE")
PY
    )
    local out
    out=$(run_py "$expr")
    if [ "$out" = "RAISE" ]; then
        gate "$label raises" PASS
    else
        gate "$label raises" FAIL
    fi
}

# Assert that valid input produces byte-identical output to today's canonical oracle.
assert_valid_unchanged() {
    local file="$1"
    local func="$2"
    local param="$3"
    local value="$4"
    local label="$file :: $func($param=$value) valid unchanged"
    local snapshot="$REGRESSION/${file%.py}_${func}_${param}_valid.txt"

    # First, write the current canonical output snapshot if it does not exist.
    if [ ! -f "$snapshot" ]; then
        local expr
        expr=$(cat <<PY
import sys, pickle
sys.path.insert(0, "$CORPUS")
from ${file%.py} import $func
X = [[1.0, 2.0], [2.0, 3.0], [3.0, 1.0], [-1.0, -2.0], [-2.0, -1.0], [-3.0, -3.0]]
y = [1.0, 2.0, 3.0, -1.0, -2.0, -3.0]
yb = [1, 1, 1, 0, 0, 0]
xv = [1.0, 2.0, 3.0]
if "$func" == "linear_regression_gd":
    r = $func(X, y, $param=$value)
elif "$func" == "logistic_regression_fit":
    r = $func(X, yb, $param=$value)
elif "$func" == "perceptron_fit":
    r = $func(X, yb, $param=$value)
elif "$func" == "vandermonde_matrix":
    r = $func(xv, $value)
elif "$func" == "fit_polynomial":
    r = $func(xv, xv, $value)
with open("$snapshot", "wb") as f:
    pickle.dump(r, f)
PY
        )
        run_py "$expr"
    fi

    # Now run again and compare to the snapshot.
    local expr
    expr=$(cat <<PY
import sys, pickle
sys.path.insert(0, "$CORPUS")
from ${file%.py} import $func
X = [[1.0, 2.0], [2.0, 3.0], [3.0, 1.0], [-1.0, -2.0], [-2.0, -1.0], [-3.0, -3.0]]
y = [1.0, 2.0, 3.0, -1.0, -2.0, -3.0]
yb = [1, 1, 1, 0, 0, 0]
xv = [1.0, 2.0, 3.0]
if "$func" == "linear_regression_gd":
    r = $func(X, y, $param=$value)
elif "$func" == "logistic_regression_fit":
    r = $func(X, yb, $param=$value)
elif "$func" == "perceptron_fit":
    r = $func(X, yb, $param=$value)
elif "$func" == "vandermonde_matrix":
    r = $func(xv, $value)
elif "$func" == "fit_polynomial":
    r = $func(xv, xv, $value)
with open("$snapshot", "rb") as f:
    expected = pickle.load(f)
print("SAME" if repr(r) == repr(expected) else f"DIFF {repr(r)} vs {repr(expected)}")
PY
    )
    local out
    out=$(run_py "$expr")
    if [ "$out" = "SAME" ]; then
        gate "$label" PASS
    else
        gate "$label" FAIL
    fi
}

echo "=================================================================="
echo " DOMAIN GUARDS — canonical-oracle negative-parameter rejections"
echo " date: $(date -u +%FT%TZ)"
echo "=================================================================="

# Each confirmed defect: negative must raise, valid must remain byte-identical.
assert_negative_raises "linear_regression_gd_canonical_good.py" "linear_regression_gd" "learning_rate" "-1.0"
assert_valid_unchanged   "linear_regression_gd_canonical_good.py" "linear_regression_gd" "learning_rate" "0.01"

assert_negative_raises "linear_regression_gd_canonical_good.py" "linear_regression_gd" "iterations" "-1"
assert_valid_unchanged   "linear_regression_gd_canonical_good.py" "linear_regression_gd" "iterations" "100"

assert_negative_raises "logistic_regression_canonical_good.py" "logistic_regression_fit" "learning_rate" "-1.0"
assert_valid_unchanged   "logistic_regression_canonical_good.py" "logistic_regression_fit" "learning_rate" "0.1"

assert_negative_raises "logistic_regression_canonical_good.py" "logistic_regression_fit" "iterations" "-1"
assert_valid_unchanged   "logistic_regression_canonical_good.py" "logistic_regression_fit" "iterations" "100"

assert_negative_raises "perceptron_canonical_good.py" "perceptron_fit" "max_iterations" "-1"
assert_valid_unchanged   "perceptron_canonical_good.py" "perceptron_fit" "max_iterations" "100"

assert_negative_raises "polynomial_regression_canonical_good.py" "vandermonde_matrix" "degree" "-1"
assert_valid_unchanged   "polynomial_regression_canonical_good.py" "vandermonde_matrix" "degree" "2"

assert_negative_raises "polynomial_regression_canonical_good.py" "fit_polynomial" "degree" "-1"
assert_valid_unchanged   "polynomial_regression_canonical_good.py" "fit_polynomial" "degree" "2"

echo; echo "=================================================================="
if [ "$FAILED" -ne 0 ]; then
    echo ">>> REJECTED: one or more domain-guard assertions failed."
    exit 1
else
    echo ">>> ACCEPTED: all domain-guard assertions passed."
    exit 0
fi
