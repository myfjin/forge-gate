#!/bin/bash
# ACCEPTANCE TEST for multi-family support in the JS oracle harness.
#
# Anyone can run this. It prints numbers and a single final verdict. No judgment required:
# the output IS the verdict. Zero LLM in the verdict path.
#
#   bash "$ROOT/tests/verify_js_families.sh"
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
# Expected invocation: js_oracle_harness.py --fixture <fixture.js>
# Expected stdout:
#   engine:node result:<json>
#   engine:bun  result:<json>
#   engine:qjs  result:<json>
#   verdict:PASS|FAIL|INCONCLUSIVE [reason]
# If the harness cannot run an engine it must print INCONCLUSIVE, not FAIL.

run_harness() {
    local fixture="$1"
    local out="" rc=0
    for _try in 1 2 3; do
        out=$(timeout 60 "$HARNESS" --fixture "$fixture" 2>/dev/null)
        rc=$?
        # Empty output, timeout, or explicit harness error is a harness problem, not a verdict.
        if [ "$rc" -ne 0 ] || [ -z "$out" ] || echo "$out" | grep -qE "^verdict:ERROR"; then
            sleep 1
            continue
        fi
        echo "$out"
        return 0
    done
    echo "verdict:INCONCLUSIVE harness error or no output after 3 attempts (rc=$rc)"
    return 1
}

parse_verdict() {
    local out="$1"
    echo "$out" | grep -E "^verdict:" | head -1 | sed 's/^verdict://'
}

# ---------------------------------------------------------------- engines present?
echo "=================================================================="
echo " JS ORACLE FAMILY SUPPORT — ACCEPTANCE TEST"
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
# Per-run, per-user temp directory. Never a fixed shared path.
FIXTURE_DIR=$(mktemp -d /tmp/js_family_fixtures.XXXXXX)
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
    local mtime
    mtime=$(stat -c %Y "$path" 2>/dev/null || stat -f %m "$path" 2>/dev/null)
    if [ -n "$mtime" ] && [ "$mtime" -lt "$FIXTURE_START_TIME" ]; then
        echo "FATAL: fixture $path mtime ($mtime) predates this run start ($FIXTURE_START_TIME) — stale file?"
        exit 1
    fi
    md5sum "$path"
}

echo; echo "--- FIXTURES (md5 before gates) ---"

# FAMILY: linreg — correct
write_fixture "linreg_correct.js" <<'JSEOF'
// @forge-family linreg
/**
 * Batch gradient descent for linear regression.
 */
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
console.log(JSON.stringify(linearRegressionGD(X, y, lr, iters)));
JSEOF

# FAMILY: linreg — subtly wrong (half bias learning rate)
write_fixture "linreg_subtly_wrong.js" <<'JSEOF'
// @forge-family linreg
/**
 * Subtly wrong: bias update uses half the learning rate.
 */
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
        b -= (lr * 0.5) * db;
    }
    return { weights: w, bias: b };
}
console.log(JSON.stringify(linearRegressionGD(X, y, lr, iters)));
JSEOF

# FAMILY: linreg — non-deterministic
write_fixture "linreg_nondet.js" <<'JSEOF'
// @forge-family linreg
/**
 * Non-deterministic: unseeded Math.random.
 */
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
    return { weights: w.map(v => v + Math.random() * 1e-12), bias: b };
}
console.log(JSON.stringify(linearRegressionGD(X, y, lr, iters)));
JSEOF

# FAMILY: linreg — correct alternative (normal equations)
write_fixture "linreg_normal_eq.js" <<'JSEOF'
// @forge-family linreg
/**
 * Correct alternative: closed-form least squares via normal equations.
 */
function linearRegressionGD(X, y, lr, iters) {
    const n = X.length;
    const nf = X[0].length;
    const A = [];
    for (let i = 0; i < n; i++) A.push([1].concat(X[i]));
    const AtA = [];
    for (let i = 0; i <= nf; i++) {
        AtA.push(new Array(nf + 1).fill(0));
        for (let j = 0; j <= nf; j++) {
            let s = 0;
            for (let k = 0; k < n; k++) s += A[k][i] * A[k][j];
            AtA[i][j] = s;
        }
    }
    const Aty = [];
    for (let i = 0; i <= nf; i++) {
        let s = 0;
        for (let k = 0; k < n; k++) s += A[k][i] * y[k];
        Aty.push(s);
    }
    const N = nf + 1;
    for (let col = 0; col < N; col++) {
        let maxRow = col;
        for (let r = col + 1; r < N; r++) {
            if (Math.abs(AtA[r][col]) > Math.abs(AtA[maxRow][col])) maxRow = r;
        }
        [AtA[col], AtA[maxRow]] = [AtA[maxRow], AtA[col]];
        [Aty[col], Aty[maxRow]] = [Aty[maxRow], Aty[col]];
        for (let r = col + 1; r < N; r++) {
            const f = AtA[r][col] / AtA[col][col];
            for (let c = col; c < N; c++) AtA[r][c] -= f * AtA[col][c];
            Aty[r] -= f * Aty[col];
        }
    }
    const theta = new Array(N);
    for (let i = N - 1; i >= 0; i--) {
        let s = Aty[i];
        for (let j = i + 1; j < N; j++) s -= AtA[i][j] * theta[j];
        theta[i] = s / AtA[i][i];
    }
    return { weights: theta.slice(1), bias: theta[0] };
}
console.log(JSON.stringify(linearRegressionGD(X, y, lr, iters)));
JSEOF

# FAMILY: statistics — correct
write_fixture "statistics_correct.js" <<'JSEOF'
// @forge-family statistics
/**
 * Correct: mean, variance (two-pass), median, quantile, correlation, covariance.
 */
function summarize(xs) {
    const n = xs.length;
    const mean = xs.reduce((a, b) => a + b, 0) / n;
    const variance = xs.reduce((a, b) => a + (b - mean) * (b - mean), 0) / n;
    const sorted = xs.slice().sort((a, b) => a - b);
    const median = n % 2 === 1 ? sorted[(n - 1) / 2] : (sorted[n / 2 - 1] + sorted[n / 2]) / 2;
    const quantile = (q) => {
        const pos = q * (n - 1);
        const lo = Math.floor(pos);
        const hi = Math.ceil(pos);
        if (lo === hi) return sorted[lo];
        return sorted[lo] + (sorted[hi] - sorted[lo]) * (pos - lo);
    };
    return { mean, variance, median, q25: quantile(0.25), q75: quantile(0.75) };
}
function linearRegressionGD(X, y, lr, iters) {
    const xs = X.map(row => row[0]);
    const ys = y;
    const sx = summarize(xs);
    const sy = summarize(ys);
    const n = xs.length;
    let num = 0, den = 0;
    for (let i = 0; i < n; i++) {
        num += (xs[i] - sx.mean) * (ys[i] - sy.mean);
        den += (xs[i] - sx.mean) * (xs[i] - sx.mean);
    }
    const correlation = den === 0 ? 0 : num / Math.sqrt(den * (n * sy.variance));
    const covariance = num / n;
    return { statsX: sx, statsY: sy, correlation, covariance };
}
console.log(JSON.stringify(linearRegressionGD(X, y, lr, iters)));
JSEOF

# FAMILY: statistics — subtly wrong
write_fixture "statistics_subtly_wrong.js" <<'JSEOF'
// @forge-family statistics
/**
 * Subtly wrong: variance divides by n-1 instead of n, shifting quantiles slightly.
 */
function summarize(xs) {
    const n = xs.length;
    const mean = xs.reduce((a, b) => a + b, 0) / n;
    const variance = xs.reduce((a, b) => a + (b - mean) * (b - mean), 0) / (n - 1);
    const sorted = xs.slice().sort((a, b) => a - b);
    const median = n % 2 === 1 ? sorted[(n - 1) / 2] : (sorted[n / 2 - 1] + sorted[n / 2]) / 2;
    const quantile = (q) => {
        const pos = q * (n - 1);
        const lo = Math.floor(pos);
        const hi = Math.ceil(pos);
        if (lo === hi) return sorted[lo];
        return sorted[lo] + (sorted[hi] - sorted[lo]) * (pos - lo);
    };
    return { mean, variance, median, q25: quantile(0.25), q75: quantile(0.75) };
}
function linearRegressionGD(X, y, lr, iters) {
    const xs = X.map(row => row[0]);
    const ys = y;
    const sx = summarize(xs);
    const sy = summarize(ys);
    const n = xs.length;
    let num = 0, den = 0;
    for (let i = 0; i < n; i++) {
        num += (xs[i] - sx.mean) * (ys[i] - sy.mean);
        den += (xs[i] - sx.mean) * (xs[i] - sx.mean);
    }
    const correlation = den === 0 ? 0 : num / Math.sqrt(den * (n * sy.variance));
    const covariance = num / n;
    return { statsX: sx, statsY: sy, correlation, covariance };
}
console.log(JSON.stringify(linearRegressionGD(X, y, lr, iters)));
JSEOF

# FAMILY: statistics — correct alternative (Welford online variance)
write_fixture "statistics_welford.js" <<'JSEOF'
// @forge-family statistics
/**
 * Correct alternative: variance via Welford's online algorithm.
 */
function summarize(xs) {
    let mean = 0;
    let M2 = 0;
    for (let i = 0; i < xs.length; i++) {
        const x = xs[i];
        const delta = x - mean;
        mean += delta / (i + 1);
        M2 += delta * (x - mean);
    }
    const variance = M2 / xs.length;
    const sorted = xs.slice().sort((a, b) => a - b);
    const n = xs.length;
    const median = n % 2 === 1 ? sorted[(n - 1) / 2] : (sorted[n / 2 - 1] + sorted[n / 2]) / 2;
    const quantile = (q) => {
        const pos = q * (n - 1);
        const lo = Math.floor(pos);
        const hi = Math.ceil(pos);
        if (lo === hi) return sorted[lo];
        return sorted[lo] + (sorted[hi] - sorted[lo]) * (pos - lo);
    };
    return { mean, variance, median, q25: quantile(0.25), q75: quantile(0.75) };
}
function linearRegressionGD(X, y, lr, iters) {
    const xs = X.map(row => row[0]);
    const ys = y;
    const sx = summarize(xs);
    const sy = summarize(ys);
    const n = xs.length;
    let num = 0, den = 0;
    for (let i = 0; i < n; i++) {
        num += (xs[i] - sx.mean) * (ys[i] - sy.mean);
        den += (xs[i] - sx.mean) * (xs[i] - sx.mean);
    }
    const correlation = den === 0 ? 0 : num / Math.sqrt(den * (n * sy.variance));
    const covariance = num / n;
    return { statsX: sx, statsY: sy, correlation, covariance };
}
console.log(JSON.stringify(linearRegressionGD(X, y, lr, iters)));
JSEOF

# FAMILY: statistics — non-deterministic
write_fixture "statistics_nondet.js" <<'JSEOF'
// @forge-family statistics
/**
 * Non-deterministic: unseeded random perturbation to variance.
 */
function summarize(xs) {
    const n = xs.length;
    const mean = xs.reduce((a, b) => a + b, 0) / n;
    const variance = xs.reduce((a, b) => a + (b - mean) * (b - mean), 0) / n + Math.random() * 1e-12;
    const sorted = xs.slice().sort((a, b) => a - b);
    const median = n % 2 === 1 ? sorted[(n - 1) / 2] : (sorted[n / 2 - 1] + sorted[n / 2]) / 2;
    const quantile = (q) => {
        const pos = q * (n - 1);
        const lo = Math.floor(pos);
        const hi = Math.ceil(pos);
        if (lo === hi) return sorted[lo];
        return sorted[lo] + (sorted[hi] - sorted[lo]) * (pos - lo);
    };
    return { mean, variance, median, q25: quantile(0.25), q75: quantile(0.75) };
}
function linearRegressionGD(X, y, lr, iters) {
    const xs = X.map(row => row[0]);
    const ys = y;
    const sx = summarize(xs);
    const sy = summarize(ys);
    const n = xs.length;
    let num = 0, den = 0;
    for (let i = 0; i < n; i++) {
        num += (xs[i] - sx.mean) * (ys[i] - sy.mean);
        den += (xs[i] - sx.mean) * (xs[i] - sx.mean);
    }
    const correlation = den === 0 ? 0 : num / Math.sqrt(den * (n * sy.variance));
    const covariance = num / n;
    return { statsX: sx, statsY: sy, correlation, covariance };
}
console.log(JSON.stringify(linearRegressionGD(X, y, lr, iters)));
JSEOF

# FAMILY: kmeans — correct
write_fixture "kmeans_correct.js" <<'JSEOF'
// @forge-family kmeans
/**
 * Correct k-means for 3 planted spherical clusters.
 */
function linearRegressionGD(X, y, lr, iters) {
    const k = 3;
    // Seedable random-ish deterministic init: first k points as seeds.
    let centroids = X.slice(0, k).map(row => row.slice());
    const maxIter = 50;
    for (let it = 0; it < maxIter; it++) {
        const assignments = X.map(row => {
            let best = 0, bestD = Infinity;
            for (let c = 0; c < k; c++) {
                let d = 0;
                for (let f = 0; f < row.length; f++) d += (row[f] - centroids[c][f]) ** 2;
                if (d < bestD) { bestD = d; best = c; }
            }
            return best;
        });
        const newCentroids = centroids.map(c => c.slice());
        const counts = new Array(k).fill(0);
        for (let i = 0; i < X.length; i++) {
            const c = assignments[i];
            counts[c]++;
            for (let f = 0; f < X[i].length; f++) newCentroids[c][f] += X[i][f];
        }
        for (let c = 0; c < k; c++) {
            if (counts[c] > 0) {
                for (let f = 0; f < newCentroids[c].length; f++) newCentroids[c][f] /= counts[c] + 1;
            }
        }
        centroids = newCentroids;
    }
    return { centroids: centroids, k: k };
}
console.log(JSON.stringify(linearRegressionGD(X, y, lr, iters)));
JSEOF

# FAMILY: kmeans — subtly wrong (init swapped / converges to wrong partition)
write_fixture "kmeans_subtly_wrong.js" <<'JSEOF'
// @forge-family kmeans
/**
 * Subtly wrong: all centroids initialized to the same point, so clusters collapse.
 */
function linearRegressionGD(X, y, lr, iters) {
    const k = 3;
    let centroids = [];
    for (let c = 0; c < k; c++) centroids.push(X[0].slice());
    const maxIter = 50;
    for (let it = 0; it < maxIter; it++) {
        const assignments = X.map(row => {
            let best = 0, bestD = Infinity;
            for (let c = 0; c < k; c++) {
                let d = 0;
                for (let f = 0; f < row.length; f++) d += (row[f] - centroids[c][f]) ** 2;
                if (d < bestD) { bestD = d; best = c; }
            }
            return best;
        });
        const newCentroids = centroids.map(c => c.slice());
        const counts = new Array(k).fill(0);
        for (let i = 0; i < X.length; i++) {
            const c = assignments[i];
            counts[c]++;
            for (let f = 0; f < X[i].length; f++) newCentroids[c][f] += X[i][f];
        }
        for (let c = 0; c < k; c++) {
            if (counts[c] > 0) {
                for (let f = 0; f < newCentroids[c].length; f++) newCentroids[c][f] /= counts[c] + 1;
            }
        }
        centroids = newCentroids;
    }
    return { centroids: centroids, k: k };
}
console.log(JSON.stringify(linearRegressionGD(X, y, lr, iters)));
JSEOF

# FAMILY: kmeans — correct alternative (k-means++ deterministic variant)
write_fixture "kmeans_plusplus.js" <<'JSEOF'
// @forge-family kmeans
/**
 * Correct alternative: k-means++ style deterministic seeding (first random-ish, then farthest).
 */
function linearRegressionGD(X, y, lr, iters) {
    const k = 3;
    let centroids = [X[0].slice()];
    while (centroids.length < k) {
        let best = -1, bestD = -1;
        for (let i = 0; i < X.length; i++) {
            let minD = Infinity;
            for (let c = 0; c < centroids.length; c++) {
                let d = 0;
                for (let f = 0; f < X[i].length; f++) d += (X[i][f] - centroids[c][f]) ** 2;
                if (d < minD) minD = d;
            }
            if (minD > bestD) { bestD = minD; best = i; }
        }
        centroids.push(X[best].slice());
    }
    const maxIter = 50;
    for (let it = 0; it < maxIter; it++) {
        const assignments = X.map(row => {
            let best = 0, bestD = Infinity;
            for (let c = 0; c < k; c++) {
                let d = 0;
                for (let f = 0; f < row.length; f++) d += (row[f] - centroids[c][f]) ** 2;
                if (d < bestD) { bestD = d; best = c; }
            }
            return best;
        });
        const newCentroids = centroids.map(c => new Array(c.length).fill(0));
        const counts = new Array(k).fill(0);
        for (let i = 0; i < X.length; i++) {
            const c = assignments[i];
            counts[c]++;
            for (let f = 0; f < X[i].length; f++) newCentroids[c][f] += X[i][f];
        }
        for (let c = 0; c < k; c++) {
            if (counts[c] > 0) {
                for (let f = 0; f < newCentroids[c].length; f++) newCentroids[c][f] /= counts[c];
            } else {
                newCentroids[c] = centroids[c].slice();
            }
        }
        centroids = newCentroids;
    }
    return { centroids: centroids, k: k };
}
console.log(JSON.stringify(linearRegressionGD(X, y, lr, iters)));
JSEOF

# FAMILY: kmeans — non-deterministic
write_fixture "kmeans_nondet.js" <<'JSEOF'
// @forge-family kmeans
/**
 * Non-deterministic: uses Date.now() to pick random centroid index.
 */
function linearRegressionGD(X, y, lr, iters) {
    const k = 3;
    let centroids = [];
    for (let c = 0; c < k; c++) centroids.push(X[(Date.now() + c) % X.length].slice());
    const maxIter = 50;
    for (let it = 0; it < maxIter; it++) {
        const assignments = X.map(row => {
            let best = 0, bestD = Infinity;
            for (let c = 0; c < k; c++) {
                let d = 0;
                for (let f = 0; f < row.length; f++) d += (row[f] - centroids[c][f]) ** 2;
                if (d < bestD) { bestD = d; best = c; }
            }
            return best;
        });
        const newCentroids = centroids.map(c => new Array(c.length).fill(0));
        const counts = new Array(k).fill(0);
        for (let i = 0; i < X.length; i++) {
            const c = assignments[i];
            counts[c]++;
            for (let f = 0; f < X[i].length; f++) newCentroids[c][f] += X[i][f];
        }
        for (let c = 0; c < k; c++) {
            if (counts[c] > 0) {
                for (let f = 0; f < newCentroids[c].length; f++) newCentroids[c][f] /= counts[c];
            }
        }
        centroids = newCentroids;
    }
    return { centroids: centroids, k: k };
}
console.log(JSON.stringify(linearRegressionGD(X, y, lr, iters)));
JSEOF

# CROSS-FAMILY ATTACKS

# 1. Misdeclared family: says statistics, implements kmeans
write_fixture "misdeclared_statistics.js" <<'JSEOF'
// @forge-family statistics
/**
 * Misdeclared: claims statistics, actually implements k-means.
 */
function linearRegressionGD(X, y, lr, iters) {
    const k = 3;
    let centroids = X.slice(0, k).map(row => row.slice());
    const maxIter = 50;
    for (let it = 0; it < maxIter; it++) {
        const assignments = X.map(row => {
            let best = 0, bestD = Infinity;
            for (let c = 0; c < k; c++) {
                let d = 0;
                for (let f = 0; f < row.length; f++) d += (row[f] - centroids[c][f]) ** 2;
                if (d < bestD) { bestD = d; best = c; }
            }
            return best;
        });
        const newCentroids = centroids.map(c => new Array(c.length).fill(0));
        const counts = new Array(k).fill(0);
        for (let i = 0; i < X.length; i++) {
            const c = assignments[i];
            counts[c]++;
            for (let f = 0; f < X[i].length; f++) newCentroids[c][f] += X[i][f];
        }
        for (let c = 0; c < k; c++) {
            if (counts[c] > 0) {
                for (let f = 0; f < newCentroids[c].length; f++) newCentroids[c][f] /= counts[c];
            }
        }
        centroids = newCentroids;
    }
    return { centroids: centroids, k: k };
}
console.log(JSON.stringify(linearRegressionGD(X, y, lr, iters)));
JSEOF

# 2. No family declared
write_fixture "no_family.js" <<'JSEOF'
/**
 * No family declared. Must be INCONCLUSIVE, not silently treated as linreg.
 */
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
console.log(JSON.stringify(linearRegressionGD(X, y, lr, iters)));
JSEOF

# 3. Unknown family
write_fixture "unknown_family.js" <<'JSEOF'
// @forge-family quantum
/**
 * Unknown family. Must be INCONCLUSIVE with a clear reason.
 */
function linearRegressionGD(X, y, lr, iters) {
    return { weights: [2.0, 3.0], bias: 0.5 };
}
console.log(JSON.stringify(linearRegressionGD(X, y, lr, iters)));
JSEOF

# 4. Two family tags
write_fixture "two_family_tags.js" <<'JSEOF'
// @forge-family linreg
// @forge-family statistics
/**
 * Two conflicting family declarations. Must not silently pick one.
 */
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
console.log(JSON.stringify(linearRegressionGD(X, y, lr, iters)));
JSEOF

# 5. Family tag in dead code / string
write_fixture "tag_in_string.js" <<'JSEOF'
// @forge-family linreg
/**
 * Linreg file with an additional @forge-family string inside a variable.
 * The real declaration is linreg; the string must not change family.
 */
function linearRegressionGD(X, y, lr, iters) {
    const label = "@forge-family statistics";
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
console.log(JSON.stringify(linearRegressionGD(X, y, lr, iters)));
JSEOF

# 6. Regression guard — existing batch2 linreg pattern 1 (normal equations)
# Copy the real file and prepend the family tag so the guard tests what ships.
SRC1="/home/me/ai-lab/js-patterns-staging/batch2/linreg_normal_eq.js"
if [ ! -f "$SRC1" ]; then
    # Source lives in ~/ai-lab/js-patterns-staging, which exists only on the deploy host.
    # Skip rather than abort: "I could not run this gate" is not "this failed",
    # and it must never read as a pass either. Suite exits INCONCLUSIVE below.
    echo "SKIP: regression guard source not present on this host: $SRC1"
    SKIPPED_GUARDS=$((${SKIPPED_GUARDS:-0} + 1))
else
{
    echo '// @forge-family linreg'
    cat "$SRC1"
} > "$FIXTURE_DIR/regression_guard_1.js"
md5sum "$FIXTURE_DIR/regression_guard_1.js"
fi

# 7. Regression guard — existing batch2 linreg pattern 2 (Cholesky)
SRC2="/home/me/ai-lab/js-patterns-staging/batch2/linreg_cholesky.js"
if [ ! -f "$SRC2" ]; then
    # Source lives in ~/ai-lab/js-patterns-staging, which exists only on the deploy host.
    # Skip rather than abort: "I could not run this gate" is not "this failed",
    # and it must never read as a pass either. Suite exits INCONCLUSIVE below.
    echo "SKIP: regression guard source not present on this host: $SRC2"
    SKIPPED_GUARDS=$((${SKIPPED_GUARDS:-0} + 1))
else
{
    echo '// @forge-family linreg'
    cat "$SRC2"
} > "$FIXTURE_DIR/regression_guard_2.js"
md5sum "$FIXTURE_DIR/regression_guard_2.js"
fi

# ---------------------------------------------------------------- run gates
check_fixture() {
    local label="$1"
    local fixture="$2"
    local expected="$3"
    local detail="$4"

    OUT=$(run_harness "$fixture")
    V=$(parse_verdict "$OUT")
    echo "    harness verdict: $V"

    if echo "$V" | grep -qE "^INCONCLUSIVE"; then
        if [ "$expected" = "INCONCLUSIVE" ]; then
            gate "$label -> INCONCLUSIVE" PASS
        else
            note "$label" "⚠️ INCONCLUSIVE ($V)"
            INCONCLUSIVE=$((INCONCLUSIVE + 1))
        fi
    elif echo "$V" | grep -qE "^PASS"; then
        if [ "$expected" = "PASS" ]; then
            gate "$label -> PASS" PASS
        else
            gate "$label -> FAIL (saw PASS)" FAIL
            echo "      detail: $detail"
        fi
    elif echo "$V" | grep -qE "^FAIL"; then
        if [ "$expected" = "FAIL" ]; then
            gate "$label -> FAIL" PASS
        else
            gate "$label -> PASS (saw FAIL)" FAIL
            echo "      detail: $detail"
        fi
    else
        note "$label" "⚠️ UNEXPECTED VERDICT: $V"
        INCONCLUSIVE=$((INCONCLUSIVE + 1))
    fi
}

echo; echo "--- PER-FAMILY GATES ---"

echo; echo "FAMILY: linreg"
check_fixture "linreg correct" "$FIXTURE_DIR/linreg_correct.js" "PASS" "correct batch GD"
check_fixture "linreg subtly wrong" "$FIXTURE_DIR/linreg_subtly_wrong.js" "FAIL" "half bias LR"
check_fixture "linreg non-deterministic" "$FIXTURE_DIR/linreg_nondet.js" "FAIL" "Math.random"
check_fixture "linreg correct alternative" "$FIXTURE_DIR/linreg_normal_eq.js" "PASS" "normal equations"

echo; echo "FAMILY: statistics"
check_fixture "statistics correct" "$FIXTURE_DIR/statistics_correct.js" "PASS" "two-pass variance"
check_fixture "statistics subtly wrong" "$FIXTURE_DIR/statistics_subtly_wrong.js" "FAIL" "n-1 variance"
check_fixture "statistics correct alternative" "$FIXTURE_DIR/statistics_welford.js" "PASS" "Welford variance"
check_fixture "statistics non-deterministic" "$FIXTURE_DIR/statistics_nondet.js" "FAIL" "random variance"

echo; echo "FAMILY: kmeans"
check_fixture "kmeans correct" "$FIXTURE_DIR/kmeans_correct.js" "PASS" "first-k init"
check_fixture "kmeans subtly wrong" "$FIXTURE_DIR/kmeans_subtly_wrong.js" "FAIL" "collapsed init"
check_fixture "kmeans correct alternative" "$FIXTURE_DIR/kmeans_plusplus.js" "PASS" "farthest init"
check_fixture "kmeans non-deterministic" "$FIXTURE_DIR/kmeans_nondet.js" "FAIL" "Date.now init"

echo; echo "CROSS-FAMILY ATTACKS"
check_fixture "misdeclared family" "$FIXTURE_DIR/misdeclared_statistics.js" "FAIL" "declares statistics, implements kmeans"
check_fixture "no family declared" "$FIXTURE_DIR/no_family.js" "INCONCLUSIVE" "must not default to linreg"
check_fixture "unknown family" "$FIXTURE_DIR/unknown_family.js" "INCONCLUSIVE" "quantum is unknown"
check_fixture "two family tags" "$FIXTURE_DIR/two_family_tags.js" "INCONCLUSIVE" "ambiguous family"
check_fixture "tag in dead code/string" "$FIXTURE_DIR/tag_in_string.js" "PASS" "string must not override real declaration"
check_fixture "regression guard #1" "$FIXTURE_DIR/regression_guard_1.js" "PASS" "existing batch2 normal_eq"
check_fixture "regression guard #2" "$FIXTURE_DIR/regression_guard_2.js" "PASS" "existing batch2 cholesky"

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
    echo "    $INCONCLUSIVE fixture(s) returned INCONCLUSIVE (harness error or unimplemented family, not a code verdict)."
fi

echo "=================================================================="
if [ "$FAILED" -eq 0 ] && [ "${SKIPPED_GUARDS:-0}" -gt 0 ]; then
    echo " RESULT: ⚠️  INCONCLUSIVE — ${SKIPPED_GUARDS} regression guard(s) skipped:"
    echo "   their source data lives only on the deploy host. Everything that COULD run, passed."
    echo "=================================================================="
    exit 2
fi
if [ "$FAILED" -eq 0 ] && [ "$INCONCLUSIVE" -eq 0 ]; then
    echo " RESULT: ✅ ACCEPTED — all gates green"
    echo "=================================================================="
    exit 0
else
    echo " RESULT: ❌ REJECTED — at least one gate failed or was inconclusive (see above)"
    echo "=================================================================="
    exit 1
fi
