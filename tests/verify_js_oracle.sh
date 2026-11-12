#!/bin/bash
# ACCEPTANCE TEST for the JS ORACLE HARNESS — written BEFORE the harness exists.
#
# Anyone can run this. It prints numbers and a single final verdict. No judgment required:
# the output IS the verdict. Zero LLM in the verdict path.
#
#   bash "$ROOT/tests/verify_js_oracle.sh"
#
# EXIT 0 = ACCEPTED. EXIT 1 = REJECTED (and it names which gate failed).

set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

HARNESS="$ROOT/tools/js_oracle_harness.py"
ENGINES=(
    "$(command -v node 2>/dev/null || echo "/usr/bin/node")"
    "$(command -v bun 2>/dev/null || echo "$HOME/.bun/bin/bun")"
    "$(command -v qjs 2>/dev/null || echo "$HOME/.local/bin/qjs")"
)

FAILED=0
INCONCLUSIVE=0

note() { printf '%-58s %s\n' "$1" "$2"; }
gate() {
    if [ "$2" = "PASS" ]; then
        note "$1" "✅ PASS"
    else
        note "$1" "❌ FAIL"
        FAILED=1
    fi
}

# ---------------------------------------------------------------- harness interface
# The harness is expected at HARNESS and must support:
#   js_oracle_harness.py --fixture <fixture.js>
# It must print lines in this exact format:
#   engine:node result:<json>
#   engine:bun  result:<json>
#   engine:qjs  result:<json>
#   verdict:PASS|FAIL|INCONCLUSIVE [reason]
# If the harness cannot run an engine it must print INCONCLUSIVE, not FAIL.
# A harness crash/missing engine is retried; if it persists the fixture is INCONCLUSIVE.

run_harness() {
    local fixture="$1"
    local out="" rc=0
    for _try in 1 2 3; do
        out=$(timeout 60 "$HARNESS" --fixture "$fixture" 2>/dev/null)
        rc=$?
        # Empty output or timeout is a harness problem, not a verdict about the code.
        if [ "$rc" -ne 0 ] || [ -z "$out" ] || echo "$out" | grep -qE "^verdict:ERROR"; then
            sleep 1
            continue
        fi
        echo "$out"
        return 0
    done
    # Persisted harness error: report INCONCLUSIVE, never accuse the fixture.
    echo "verdict:INCONCLUSIVE harness error or no output after 3 attempts (rc=$rc)"
    return 1
}

parse_verdict() {
    local out="$1"
    echo "$out" | grep -E "^verdict:" | head -1 | sed 's/^verdict://'
}

parse_engine_result() {
    local out="$1" engine="$2"
    echo "$out" | grep -E "^engine:${engine} result:" | head -1 | sed "s/^engine:${engine} result://"
}

# ---------------------------------------------------------------- engines present?
echo "=================================================================="
echo " JS ORACLE HARNESS — ACCEPTANCE TEST"
echo " date: $(date -u +%FT%TZ)"
echo "=================================================================="

echo; echo "--- ENGINES ---"
MISSING_ENGINES=0
for e in "${ENGINES[@]}"; do
    if [ -x "$e" ]; then
        note "$(basename "$e") at $e" "found"
    else
        note "$(basename "$e") at $e" "MISSING"
        MISSING_ENGINES=1
    fi
done

# ---------------------------------------------------------------- harness present?
echo; echo "--- HARNESS PRESENT? ---"
if [ ! -f "$HARNESS" ]; then
    gate "harness exists at $HARNESS" FAIL
    echo; echo ">>> REJECTED: harness not found. The oracle cannot be accepted."
    exit 1
elif [ ! -x "$HARNESS" ]; then
    gate "harness is executable" FAIL
    echo; echo ">>> REJECTED: harness exists but is not executable."
    exit 1
else
    gate "harness exists and is executable" PASS
fi

# ---------------------------------------------------------------- fixtures
# Per-run, per-user temp directory. Never a fixed shared path that could contain
# stale files owned by another user. A failed write must abort loudly.
FIXTURE_DIR=$(mktemp -d /tmp/js_oracle_fixtures.XXXXXX)
FIXTURE_START_TIME=$(date +%s)
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
    if [ ! -f "$path" ]; then
        echo "FATAL: fixture $path did not appear after write"
        exit 1
    fi
    if [ ! -s "$path" ]; then
        echo "FATAL: fixture $path is empty after write"
        exit 1
    fi
    # Guard against stale files that pre-existed (should be impossible with mktemp,
    # but explicit check makes the assumption visible).
    local mtime
    mtime=$(stat -c %Y "$path" 2>/dev/null || stat -f %m "$path" 2>/dev/null)
    if [ -n "$mtime" ] && [ "$mtime" -lt "$FIXTURE_START_TIME" ]; then
        echo "FATAL: fixture $path mtime ($mtime) predates this run start ($FIXTURE_START_TIME) — stale file?"
        exit 1
    fi
    md5sum "$path"
}

echo; echo "--- FIXTURES (md5 before gates) ---"

write_fixture "correct_linreg.js" <<'JSEOF'
// @forge-family linreg
function linearRegressionGD(X, y, lr, iters) {
    const n = X.length;
    const nf = X[0].length;
    const w = new Array(nf).fill(0.0);
    let b = 0.0;
    for (let i = 0; i < iters; i++) {
        const dw = new Array(nf).fill(0.0);
        let db = 0.0;
        for (let j = 0; j < n; j++) {
            let p = b;
            for (let k = 0; k < nf; k++) p += w[k] * X[j][k];
            const e = p - y[j];
            db += e;
            for (let k = 0; k < nf; k++) dw[k] += e * X[j][k];
        }
        db /= n;
        for (let k = 0; k < nf; k++) dw[k] /= n;
        for (let k = 0; k < nf; k++) w[k] -= lr * dw[k];
        b -= lr * db;
    }
    return { weights: w, bias: b };
}

const X = [[1, 2], [2, 3], [3, 5], [4, 7], [5, 8]];
const y = [1, 2, 3, 4, 5];
const result = linearRegressionGD(X, y, 0.01, 1000);
// Math-library probe: engines MAY legitimately differ here. Do not gate on exact match.
result.mathProbe = Math.sin(1.0) + Math.cos(1.0) + Math.exp(0.5) + Math.log(1.5) + Math.pow(2.1, 3.2);
console.log(JSON.stringify(result));
JSEOF

write_fixture "subtly_wrong_linreg.js" <<'JSEOF'
// @forge-family linreg
function linearRegressionGD(X, y, lr, iters) {
    const n = X.length;
    const nf = X[0].length;
    const w = new Array(nf).fill(0.0);
    let b = 0.0;
    for (let i = 0; i < iters; i++) {
        const dw = new Array(nf).fill(0.0);
        let db = 0.0;
        for (let j = 0; j < n; j++) {
            let p = b;
            for (let k = 0; k < nf; k++) p += w[k] * X[j][k];
            const e = p - y[j];
            db += e;
            for (let k = 0; k < nf; k++) dw[k] += e * X[j][k];
        }
        db /= n;
        for (let k = 0; k < nf; k++) dw[k] /= n;
        for (let k = 0; k < nf; k++) w[k] -= lr * dw[k];
        // Bug: bias update uses half the learning rate. Right shape, wrong answer, no crash.
        b -= (lr * 0.5) * db;
    }
    return { weights: w, bias: b };
}

const X = [[1, 2], [2, 3], [3, 5], [4, 7], [5, 8]];
const y = [1, 2, 3, 4, 5];
const result = linearRegressionGD(X, y, 0.01, 1000);
result.mathProbe = Math.sin(1.0) + Math.cos(1.0) + Math.exp(0.5) + Math.log(1.5) + Math.pow(2.1, 3.2);
console.log(JSON.stringify(result));
JSEOF

write_fixture "non_deterministic_linreg.js" <<'JSEOF'
// @forge-family linreg
function linearRegressionGD(X, y, lr, iters) {
    const n = X.length;
    const nf = X[0].length;
    const w = new Array(nf).fill(0.0);
    let b = 0.0;
    for (let i = 0; i < iters; i++) {
        const dw = new Array(nf).fill(0.0);
        let db = 0.0;
        for (let j = 0; j < n; j++) {
            let p = b;
            for (let k = 0; k < nf; k++) p += w[k] * X[j][k];
            const e = p - y[j];
            db += e;
            for (let k = 0; k < nf; k++) dw[k] += e * X[j][k];
        }
        db /= n;
        for (let k = 0; k < nf; k++) dw[k] /= n;
        for (let k = 0; k < nf; k++) w[k] -= lr * dw[k];
        b -= lr * db;
    }
    // Non-deterministic: unseeded Math.random().
    return { weights: w.map(v => v + Math.random() * 1e-12), bias: b };
}

const X = [[1, 2], [2, 3], [3, 5], [4, 7], [5, 8]];
const y = [1, 2, 3, 4, 5];
const result = linearRegressionGD(X, y, 0.01, 1000);
console.log(JSON.stringify(result));
JSEOF

write_fixture "coercion_silent_linreg.js" <<'JSEOF'
// @forge-family linreg
function linearRegressionGD(X, y, lr, iters) {
    const n = X.length;
    const nf = X[0].length;
    const w = new Array(nf).fill(0.0);
    let b = 0.0;
    for (let i = 0; i < iters; i++) {
        const dw = new Array(nf).fill(0.0);
        let db = 0.0;
        for (let j = 0; j < n; j++) {
            let p = b;
            for (let k = 0; k < nf; k++) p += w[k] * X[j][k];
            const e = p - y[j];
            db += e;
            for (let k = 0; k < nf; k++) dw[k] += e * X[j][k];
        }
        db /= n;
        for (let k = 0; k < nf; k++) dw[k] /= n;
        for (let k = 0; k < nf; k++) w[k] -= lr * dw[k];
        b -= lr * db;
    }
    // Coercion-silent bug: loss becomes NaN via 0/0, and NaN comparisons are always false.
    // No error is thrown. The wrong value propagates silently into the returned bias.
    const loss = 0 / 0; // NaN
    return { weights: w, bias: loss == 0 ? b : b + loss };
}

const X = [[1, 2], [2, 3], [3, 5], [4, 7], [5, 8]];
const y = [1, 2, 3, 4, 5];
const result = linearRegressionGD(X, y, 0.01, 1000);
result.mathProbe = Math.sin(1.0) + Math.cos(1.0) + Math.exp(0.5) + Math.log(1.5) + Math.pow(2.1, 3.2);
console.log(JSON.stringify(result));
JSEOF

# ---------------------------------------------------------------- GATE 1: correct code must PASS (Law 5)
echo; echo "--- GATE 1: CORRECT implementation -> must PASS ---"
OUT1=$(run_harness "$FIXTURE_DIR/correct_linreg.js")
V1=$(parse_verdict "$OUT1")
echo "    harness verdict: $V1"
if echo "$V1" | grep -qE "^INCONCLUSIVE"; then
    note "correct fixture" "⚠️ INCONCLUSIVE ($V1)"
    INCONCLUSIVE=$((INCONCLUSIVE + 1))
elif echo "$V1" | grep -qE "^PASS"; then
    gate "correct_linreg.js -> PASS" PASS
else
    gate "correct_linreg.js -> PASS" FAIL
    echo "    (a gate that fails correct code kills the tool — Law 5)"
fi

# ---------------------------------------------------------------- GATE 2: subtly wrong must FAIL
echo; echo "--- GATE 2: SUBTLY WRONG -> must FAIL ---"
OUT2=$(run_harness "$FIXTURE_DIR/subtly_wrong_linreg.js")
V2=$(parse_verdict "$OUT2")
echo "    harness verdict: $V2"
if echo "$V2" | grep -qE "^INCONCLUSIVE"; then
    note "subtly_wrong fixture" "⚠️ INCONCLUSIVE ($V2)"
    INCONCLUSIVE=$((INCONCLUSIVE + 1))
elif echo "$V2" | grep -qE "^FAIL"; then
    gate "subtly_wrong_linreg.js -> FAIL" PASS
else
    gate "subtly_wrong_linreg.js -> FAIL" FAIL
fi

# ---------------------------------------------------------------- GATE 3: non-deterministic must FAIL
echo; echo "--- GATE 3: NON-DETERMINISTIC -> must FAIL ---"
OUT3=$(run_harness "$FIXTURE_DIR/non_deterministic_linreg.js")
V3=$(parse_verdict "$OUT3")
echo "    harness verdict: $V3"
if echo "$V3" | grep -qE "^INCONCLUSIVE"; then
    note "non-deterministic fixture" "⚠️ INCONCLUSIVE ($V3)"
    INCONCLUSIVE=$((INCONCLUSIVE + 1))
elif echo "$V3" | grep -qE "^FAIL"; then
    gate "non_deterministic_linreg.js -> FAIL" PASS
else
    gate "non_deterministic_linreg.js -> FAIL" FAIL
fi

# ---------------------------------------------------------------- GATE 4: coercion-silent must FAIL
echo; echo "--- GATE 4: COERCION-SILENT -> must FAIL ---"
OUT4=$(run_harness "$FIXTURE_DIR/coercion_silent_linreg.js")
V4=$(parse_verdict "$OUT4")
echo "    harness verdict: $V4"
if echo "$V4" | grep -qE "^INCONCLUSIVE"; then
    note "coercion-silent fixture" "⚠️ INCONCLUSIVE ($V4)"
    INCONCLUSIVE=$((INCONCLUSIVE + 1))
elif echo "$V4" | grep -qE "^FAIL"; then
    gate "coercion_silent_linreg.js -> FAIL" PASS
else
    gate "coercion_silent_linreg.js -> FAIL" FAIL
fi

# ---------------------------------------------------------------- GATE 5 (last: heaviest — cross-engine float delta on CORRECT)
echo; echo "--- GATE 5 (heaviest, last): CROSS-ENGINE FLOAT DELTA on CORRECT fixture ---"
if [ -n "$OUT1" ] && echo "$OUT1" | grep -qE "^engine:node result:"; then
    R_NODE=$(parse_engine_result "$OUT1" "node")
    R_BUN=$(parse_engine_result "$OUT1" "bun")
    R_QJS=$(parse_engine_result "$OUT1" "qjs")

    MATH_NODE=$(echo "$R_NODE" | python3 -c "import sys,json,math; v=json.load(sys.stdin).get('mathProbe'); print(v if isinstance(v,(int,float)) and math.isfinite(v) else 'NaN')")
    MATH_BUN=$(echo "$R_BUN" | python3 -c "import sys,json,math; v=json.load(sys.stdin).get('mathProbe'); print(v if isinstance(v,(int,float)) and math.isfinite(v) else 'NaN')")
    MATH_QJS=$(echo "$R_QJS" | python3 -c "import sys,json,math; v=json.load(sys.stdin).get('mathProbe'); print(v if isinstance(v,(int,float)) and math.isfinite(v) else 'NaN')")

    echo "    node mathProbe: $MATH_NODE"
    echo "    bun  mathProbe: $MATH_BUN"
    echo "    qjs  mathProbe: $MATH_QJS"

    DELTA=$(python3 -c "
import sys
vals = sys.argv[1:]
if 'NaN' in vals:
    print('NaN in probe — cannot compute delta')
else:
    vals = [float(v) for v in vals]
    mx = max(vals); mn = min(vals); d = abs(mx - mn)
    print(f'max={mx:.17g} min={mn:.17g} delta={d:.17g}')
" "$MATH_NODE" "$MATH_BUN" "$MATH_QJS")
    echo "    observed delta: $DELTA"
    gate "float delta measured and printed" PASS
else
    gate "float delta measured and printed" FAIL
    echo "    (could not read engine outputs from harness)"
fi

# ---------------------------------------------------------------- cleanup
echo; echo "--- cleanup ---"
rm -rf "$FIXTURE_DIR"

# ---------------------------------------------------------------- final verdict
echo
if [ "$MISSING_ENGINES" -ne 0 ]; then
    echo ">>> REJECTED: one or more required engines are missing."
    exit 1
fi

if [ "$INCONCLUSIVE" -ne 0 ]; then
    echo "    $INCONCLUSIVE fixture(s) returned INCONCLUSIVE (harness error, not a code verdict)."
fi

echo "=================================================================="
if [ "$FAILED" -eq 0 ] && [ "$INCONCLUSIVE" -eq 0 ]; then
    echo " RESULT: ✅ ACCEPTED — all 5 gates green"
    echo "=================================================================="
    exit 0
else
    echo " RESULT: ❌ REJECTED — at least one gate failed or was inconclusive (see above)"
    echo "=================================================================="
    exit 1
fi
