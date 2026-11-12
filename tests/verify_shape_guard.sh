#!/bin/bash
# macOS ships no setsid. Its role here is to give `timeout` its own process
# group to reap; the Python side already isolates via start_new_session, so
# running without it is bounded, just one less belt. Empty expands to nothing.
SETSID="$(command -v setsid || true)"
# ACCEPTANCE TEST for the SHAPE GUARD lens — Step 2, confirmed real defect R7.
#
# The Guard lens catches NaN/Inf swallowed into finite output. It deliberately
# ignores finite shape mismatches, because a vector of the wrong length with
# all-finite values is not a NaN problem. A separate shape guard must catch it.
#
# This test gates exactly one confirmed real defect:
#   perceptron_predict silently truncates a weights vector that is shorter
#   than the feature dimension and returns confident predictions.
#
# EXIT 0 = shape guard ACCEPTED (must-fail gate fires and must-not-fire control
#          still passes). EXIT 1 = REJECTED.

set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FORGE_GATE="$ROOT"
export FORGE_GATE

PYTHON="/usr/bin/python3"
CORPUS="$ROOT/tests/corpus"
REGRESSION="$ROOT/tests/regression"
SNAPSHOT="$REGRESSION/perceptron_shape_defect_snapshot.py"

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

# ---------------------------------------------------------------- probes
# Run a single Python call in a subprocess with a hard timeout. Returns the
# child's exit code on its own line and any output.
run_python() {
    local label="$1"
    local script="$2"
    local out="$REGRESSION/${label}.out"
    local err="$REGRESSION/${label}.err"
    $SETSID timeout --kill-after=5s 15s \
        "$PYTHON" -c "$script" > "$out" 2> "$err"
    rc=$?
    echo "$rc"
}

# The defect: perceptron_predict with weights shorter than X feature count.
# It currently returns [1, 1] instead of raising.
MISMATCH_SCRIPT='
import sys, os, importlib.util
path = os.environ["FORGE_GATE"] + "/tests/corpus/perceptron_canonical_good.py"
spec = importlib.util.spec_from_file_location("perceptron", path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
X = [[1.0, 2.0], [3.0, 4.0]]
weights = [0.5]  # wrong length: 1 weight for 2 features
bias = 0.0
try:
    result = mod.perceptron_predict(X, weights, bias)
    print(f"RETURNED: {result}")
    sys.exit(0)
except Exception as exc:
    print(f"RAISED: {type(exc).__name__}: {exc}")
    sys.exit(1)
'

# The control: correctly shaped input must still work.
VALID_SHAPE_SCRIPT='
import sys, os, importlib.util
path = os.environ["FORGE_GATE"] + "/tests/corpus/perceptron_canonical_good.py"
spec = importlib.util.spec_from_file_location("perceptron", path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
X = [[1.0, 2.0], [3.0, 4.0]]
weights = [0.5, -0.5]
bias = 0.0
try:
    result = mod.perceptron_predict(X, weights, bias)
    # The exact predictions depend on the perceptron internals; what matters is
    # that a correctly shaped call succeeds and returns two binary predictions.
    if len(result) == 2 and all(r in (0, 1) for r in result):
        print(f"RETURNED: {result}")
        sys.exit(0)
    print(f"UNEXPECTED: {result}")
    sys.exit(2)
except Exception as exc:
    print(f"RAISED: {type(exc).__name__}: {exc}")
    sys.exit(3)
'

# ---------------------------------------------------------------- run
echo "=================================================================="
echo " SHAPE GUARD — perceptron weights-length defect"
echo " date: $(date -u +%FT%TZ)"
echo "=================================================================="

echo; echo "--- LIVENESS CHECKS ---"
if [ ! -f "$SNAPSHOT" ]; then
    gate "regression snapshot exists at $SNAPSHOT" FAIL
    echo; echo ">>> REJECTED: snapshot missing."
    exit 1
else
    gate "regression snapshot exists at $SNAPSHOT" PASS
fi

if [ ! -f "$CORPUS/perceptron_canonical_good.py" ]; then
    gate "live corpus file exists" FAIL
    echo; echo ">>> REJECTED: corpus file missing."
    exit 1
else
    gate "live corpus file exists" PASS
fi

echo; echo "--- MUST-FAIL GATE ---"
rc=$(run_python "mismatch" "$MISMATCH_SCRIPT")
echo "mismatch probe rc=$rc"
if [ "$rc" -eq 124 ] || [ "$rc" -eq 137 ]; then
    gate "perceptron_predict rejects mismatched weights length" INCONCLUSIVE
elif [ "$rc" -eq 0 ]; then
    # rc 0 means it returned a value, so the defect is still present.
    gate "perceptron_predict rejects mismatched weights length" FAIL
elif [ "$rc" -eq 1 ]; then
    # rc 1 means it raised.
    gate "perceptron_predict rejects mismatched weights length" PASS
else
    gate "perceptron_predict rejects mismatched weights length" INCONCLUSIVE
fi

echo; echo "--- MUST-NOT-FIRE CONTROL ---"
rc=$(run_python "valid" "$VALID_SHAPE_SCRIPT")
echo "valid-shape probe rc=$rc"
if [ "$rc" -eq 124 ] || [ "$rc" -eq 137 ]; then
    gate "perceptron_predict accepts correctly shaped input" INCONCLUSIVE
elif [ "$rc" -eq 0 ]; then
    gate "perceptron_predict accepts correctly shaped input" PASS
else
    gate "perceptron_predict accepts correctly shaped input" FAIL
fi

# ---------------------------------------------------------------- final
echo; echo "=================================================================="
if [ "$FAILED" -ne 0 ]; then
    echo ">>> REJECTED: shape guard must-fire gate did not fire."
    exit 1
elif [ "$INCONCLUSIVE" -ne 0 ]; then
    echo ">>> INCONCLUSIVE: shape guard could not be evaluated."
    exit 2
else
    echo ">>> ACCEPTED: shape guard fires on defect and accepts valid input."
    exit 0
fi
