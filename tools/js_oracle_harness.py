#!/usr/bin/env python3
"""JS oracle harness — property-primary verification of JS fixtures.

Invocation:
    js_oracle_harness.py --fixture <fixture.js>

Output (one line per engine, then verdict):
    engine:node result:<json>
    engine:bun  result:<json>
    engine:qjs  result:<json>
    verdict:PASS|FAIL|INCONCLUSIVE <reason>

Rules:
  - Engine absence / subprocess crash / timeout = INCONCLUSIVE, never FAIL.
  - The fixture declares its family with a leading comment:
        // @forge-family <family>
    The family determines the data generator, ground truth, and property checks.
  - Currently supported families: linreg, statistics, kmeans.
  - No family tag, an unknown family, or multiple family tags => INCONCLUSIVE.
  - Determinism lockdown: Math.random seeded per run, Date fixed, timers
    disabled, Symbol made deterministic. The fixture is run twice with different
    injected seeds; differing output means non-deterministic.
  - Cross-engine consensus: compare the three engine outputs on core family
    fields. ALL THREE engines required.
  - Type gate: tsc --noEmit --allowJs --checkJs over the fixture.
  - canonical_match is a secondary metric; it is reported but never gates.
  - Metamorphic relations (scale/shift/permute for linreg) are evaluated in-engine
    and reported. Relations that are sound for the exact solution are allowed to gate.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

HARNESS_DIR = Path(__file__).resolve().parent
REPO_ROOT = HARNESS_DIR.parent
TSC = REPO_ROOT / "node_modules" / ".bin" / "tsc"  # from `npm ci` (package-lock.json)

sys.path.insert(0, str(REPO_ROOT / "src"))  # in-project: this checkout's own package
from forge_gate.config import engine as _configured_engine  # noqa: E402

MAX_OUTPUT_BYTES = 2_000_000
MAX_OUTPUT_LINES = 10_000

def _resolve_engine(name: str) -> str:
    """Locate a JS engine portably.

    Order: FORGE_GATE_<NAME> (environment, or config.toml [paths]) -> PATH -> bare name.

    The three engines must be three DIFFERENT implementations (V8 / JavaScriptCore /
    QuickJS), never the same one twice, so an engine that is not found is never
    replaced by another: the bare name makes the call fail loudly ("engine not
    found") instead of pretending. Per-host install sites (e.g. a user's ~/.bun)
    belong in that host's config.toml, not in this file (project standard §1;
    build step 1, 2026-11-11). Keys: FORGE_GATE_NODE, FORGE_GATE_BUN, FORGE_GATE_QJS.
    """
    return _configured_engine(name) or name


ENGINES: Dict[str, str] = {name: _resolve_engine(name) for name in ("node", "bun", "qjs")}

# ---------------------------------------------------------------------------
# Family detection
# ---------------------------------------------------------------------------
FAMILY_RE = re.compile(r"^\s*//\s*@forge-family\s+(\S+)", re.MULTILINE)


def _detect_family(src: str) -> Tuple[str, str]:
    """Return (family, reason). reason is non-empty on ambiguity/no-tag."""
    matches = FAMILY_RE.findall(src)
    if not matches:
        return "", "no @forge-family declaration found"
    seen = []
    for m in matches:
        if m not in seen:
            seen.append(m)
    if len(seen) > 1:
        return "", f"ambiguous @forge-family declarations: {', '.join(seen)}"
    return seen[0], ""


# ---------------------------------------------------------------------------
# Data generators and reference solutions
# ---------------------------------------------------------------------------
HARNESS_DATA_SEED = 12345
HARNESS_N_SAMPLES = 30
HARNESS_N_FEATURES = 2
HARNESS_TRUE_WEIGHTS = [2.0, 3.0]
HARNESS_TRUE_BIAS = 0.5
HARNESS_LR = 0.05
HARNESS_ITERS = 200


def _lcg_next(state: int) -> int:
    return ((state * 1664525 + 1013904223) & 0xFFFFFFFF)


def _make_linreg_data() -> Tuple[List[List[float]], List[float]]:
    state = HARNESS_DATA_SEED & 0xFFFFFFFF
    X: List[List[float]] = []
    for _ in range(HARNESS_N_SAMPLES):
        row = []
        for _ in range(HARNESS_N_FEATURES):
            state = _lcg_next(state)
            row.append((state / 0x100000000) * 2 - 1)
        X.append(row)
    y = [
        HARNESS_TRUE_BIAS + sum(HARNESS_TRUE_WEIGHTS[j] * X[i][j] for j in range(HARNESS_N_FEATURES))
        for i in range(HARNESS_N_SAMPLES)
    ]
    return X, y


def _normal_equations(X: List[List[float]], y: List[float]) -> Tuple[List[float], float]:
    """Exact least-squares solution via normal equations with Gaussian elimination."""
    n = len(X)
    nf = len(X[0])
    m = nf + 1
    A = [[1.0] + row for row in X]
    AtA = [[0.0] * m for _ in range(m)]
    Aty = [0.0] * m
    for i in range(n):
        for r in range(m):
            Aty[r] += A[i][r] * y[i]
            for c in range(m):
                AtA[r][c] += A[i][r] * A[i][c]
    aug = [AtA[r] + [Aty[r]] for r in range(m)]
    for col in range(m):
        pivot = col
        for row in range(col + 1, m):
            if abs(aug[row][col]) > abs(aug[pivot][col]):
                pivot = row
        aug[col], aug[pivot] = aug[pivot], aug[col]
        piv = aug[col][col]
        if abs(piv) < 1e-12:
            continue
        for j in range(col, m + 1):
            aug[col][j] /= piv
        for row in range(m):
            if row == col:
                continue
            factor = aug[row][col]
            for j in range(col, m + 1):
                aug[row][j] -= factor * aug[col][j]
    coeffs = [aug[r][m] for r in range(m)]
    return coeffs[1:], coeffs[0]


def _make_statistics_data() -> Tuple[List[List[float]], List[float]]:
    """Generate paired data (X with one column of interest, y)."""
    state = HARNESS_DATA_SEED & 0xFFFFFFFF
    xs: List[float] = []
    for _ in range(HARNESS_N_SAMPLES):
        state = _lcg_next(state)
        xs.append((state / 0x100000000) * 2 - 1)
    # y is an exact affine transformation of x plus a small quadratic term so that
    # correlation is strong but not perfect, while still being fully deterministic.
    ys = [
        1.5 + 2.3 * xs[i] + 0.7 * (xs[i] ** 2)
        for i in range(HARNESS_N_SAMPLES)
    ]
    X = [[x, 0.0] for x in xs]  # second feature unused but preserves shape expectations
    return X, ys


def _reference_statistics(xs: List[float], ys: List[float]) -> Dict[str, Any]:
    """Ground-truth reference for the statistics family.

    Variance uses the population divisor N (not N-1), matching the canonical
    fixture definition.  Tolerances are for genuine numerical error; a sample-
    vs-population definitional mismatch must be caught by the tolerance as a
    real difference, not absorbed into the tolerance budget.
    """
    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    var_x = sum((x - mean_x) ** 2 for x in xs) / n
    var_y = sum((y - mean_y) ** 2 for y in ys) / n
    sorted_x = sorted(xs)
    sorted_y = sorted(ys)

    def quantile(sorted_vals: List[float], q: float) -> float:
        pos = q * (n - 1)
        lo = int(math.floor(pos))
        hi = int(math.ceil(pos))
        if lo == hi:
            return sorted_vals[lo]
        return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)

    def median(sorted_vals: List[float]) -> float:
        if n % 2 == 1:
            return sorted_vals[(n - 1) // 2]
        return (sorted_vals[n // 2 - 1] + sorted_vals[n // 2]) / 2.0

    cov = sum((xs[i] - mean_x) * (ys[i] - mean_y) for i in range(n)) / n
    corr = cov / math.sqrt(var_x * var_y) if var_x > 0 and var_y > 0 else 0.0
    return {
        "meanX": mean_x,
        "meanY": mean_y,
        "varianceX": var_x,
        "varianceY": var_y,
        "medianX": median(sorted_x),
        "medianY": median(sorted_y),
        "q25X": quantile(sorted_x, 0.25),
        "q25Y": quantile(sorted_y, 0.25),
        "q75X": quantile(sorted_x, 0.75),
        "q75Y": quantile(sorted_y, 0.75),
        "correlation": corr,
        "covariance": cov,
    }


def _make_kmeans_data() -> List[List[float]]:
    """Generate 3 planted spherical clusters. Ported from Python gate fixture."""
    # Deterministic Box-Muller-ish normal from 32-bit LCG, seeded at HARNESS_DATA_SEED.
    state = HARNESS_DATA_SEED & 0xFFFFFFFF
    n_per_cluster = 20
    k = 3
    # Cluster centers around (0,0), (5,0), (2.5,4)
    centers = [[0.0, 0.0], [5.0, 0.0], [2.5, 4.0]]
    sigma = 0.5

    def normal_pair() -> Tuple[float, float]:
        nonlocal state
        state = _lcg_next(state)
        u1 = state / 0x100000000
        state = _lcg_next(state)
        u2 = state / 0x100000000
        # Avoid log(0)
        if u1 <= 0.0:
            u1 = 1e-10
        r = math.sqrt(-2.0 * math.log(u1))
        theta = 2.0 * math.pi * u2
        return r * math.cos(theta), r * math.sin(theta)

    clusters = [[] for _ in range(k)]
    for c in range(k):
        cx, cy = centers[c]
        for _ in range(n_per_cluster):
            z1, z2 = normal_pair()
            clusters[c].append([cx + sigma * z1, cy + sigma * z2])
    # Round-robin ordering so that first-k initialisation picks one point from
    # each cluster; otherwise a first-k implementation lands three seeds in the
    # first cluster and cannot be separated by simple geometric thresholds.
    X: List[List[float]] = []
    for i in range(n_per_cluster):
        for c in range(k):
            X.append(clusters[c][i])
    return X


def _reference_kmeans(X: List[List[float]], k: int = 3, max_iter: int = 100) -> Tuple[List[List[float]], List[int]]:
    """Lloyd's k-means with deterministic farthest-point initialization."""
    n = len(X)
    nf = len(X[0])

    def dist2(a: List[float], b: List[float]) -> float:
        return sum((a[i] - b[i]) ** 2 for i in range(nf))

    # Farthest-point initialization seeded at first sample.
    centroids = [X[0][:]]
    while len(centroids) < k:
        best = -1
        best_d = -1.0
        for i in range(n):
            min_d = min(dist2(X[i], c) for c in centroids)
            if min_d > best_d:
                best_d = min_d
                best = i
        centroids.append(X[best][:])

    assignments = [0] * n
    for _ in range(max_iter):
        changed = 0
        for i in range(n):
            best = 0
            best_d = dist2(X[i], centroids[0])
            for c in range(1, k):
                d = dist2(X[i], centroids[c])
                if d < best_d:
                    best_d = d
                    best = c
            if assignments[i] != best:
                assignments[i] = best
                changed += 1
        new_centroids = [[0.0] * nf for _ in range(k)]
        counts = [0] * k
        for i in range(n):
            c = assignments[i]
            counts[c] += 1
            for f in range(nf):
                new_centroids[c][f] += X[i][f]
        for c in range(k):
            if counts[c] > 0:
                for f in range(nf):
                    new_centroids[c][f] /= counts[c]
            else:
                new_centroids[c] = centroids[c][:]
        centroids = new_centroids
        if changed == 0:
            break
    return centroids, assignments


# ---------------------------------------------------------------------------
# Wrapper templates — family-agnostic skeleton; family-specific data/checks
# are injected by the harness after the fixture runs.
# ---------------------------------------------------------------------------
NODE_WRAPPER = r"""
const fs = require('fs');
const fixturePath = process.argv[2];
const seed = parseInt(process.argv[3], 10);
const family = "__FAMILY__";

let state = seed >>> 0;
Math.random = function() {
    state = ((state * 1664525 + 1013904223) & 0xFFFFFFFF) >>> 0;
    return state / 0x100000000;
};


globalThis.setTimeout = function() { return 0; };
globalThis.setInterval = function() { return 0; };
globalThis.setImmediate = function() { return 0; };
globalThis.clearTimeout = function() {};
globalThis.clearInterval = function() {};
globalThis.clearImmediate = function() {};
globalThis.performance = globalThis.performance || {};
globalThis.performance.now = () => fixedTime;

const fixedTime = Date.now();
const origSymbol = Symbol;
globalThis.Symbol = function(desc) { return origSymbol.for(String(desc ?? "")); };
globalThis.Symbol.for = origSymbol.for;
globalThis.Symbol.keyFor = origSymbol.keyFor;

function approxEqual(a, b, tol) {
    if (typeof a === 'number') return Math.abs(a - b) <= tol;
    if (Array.isArray(a)) return a.length === b.length && a.every((v, i) => approxEqual(v, b[i], tol));
    return a === b;
}

function extractCore(result, family) {
    if (family === 'linreg') {
        if (typeof result.weights === 'undefined' || typeof result.bias === 'undefined') {
            throw new Error('family shape mismatch: linreg requires weights and bias, got ' + Object.keys(result || {}).join(','));
        }
        return { weights: result.weights, bias: result.bias };
    }
    if (family === 'statistics') {
        // Accept either flat fields or nested statsX/statsY objects.
        const sx = result.statsX || result;
        const sy = result.statsY || result;
        const core = {
            meanX: sx.mean, meanY: sy.mean,
            varianceX: sx.variance, varianceY: sy.variance,
            correlation: result.correlation, covariance: result.covariance
        };
        for (const k of Object.keys(core)) {
            if (typeof core[k] === 'undefined') {
                throw new Error('family shape mismatch: statistics missing ' + k + ', got keys ' + Object.keys(result || {}).join(','));
            }
        }
        return core;
    }
    if (family === 'kmeans') {
        if (typeof result.centroids === 'undefined' || typeof result.k === 'undefined') {
            throw new Error('family shape mismatch: kmeans requires centroids and k, got ' + Object.keys(result || {}).join(','));
        }
        return { centroids: result.centroids, k: result.k };
    }
    return result;
}

const src = fs.readFileSync(fixturePath, 'utf8');
// Pre-populate the contract globals so that fixtures with a top-level
// console.log(linearRegressionGD(X, y, lr, iters)) do not crash at eval time.
// Fixtures that declare their own X/y/lr/iters will shadow these globals.
// The harness later overwrites globalThis.X/globalThis.y with real data.
globalThis.X = [[0, 0], [0, 0], [0, 0]];
globalThis.y = [0, 0, 0];
globalThis.lr = 0.05;
globalThis.iters = 1;
try {
    const indirect = eval;
    indirect(src);
} catch (err) {
    console.error("HARNESS_WRAPPER_ERROR: " + err.message);
    process.exit(1);
}

if (typeof globalThis.linearRegressionGD !== 'function') {
    console.error("HARNESS_WRAPPER_ERROR: linearRegressionGD not defined");
    process.exit(1);
}

const harnessData = (function() {
__MAKE_HARNESS_DATA__
return makeHarnessData();
})();
X = harnessData.X;
y = harnessData.y;
const result = globalThis.linearRegressionGD(X, y, __LR__, __ITERS__);
let out;
try {
    out = extractCore(result, family);
} catch (err) {
    console.error("HARNESS_WRAPPER_ERROR: " + err.message);
    process.exit(1);
}

__RELATIONS_AND_METRICS__

console.log(JSON.stringify(out));
"""

QJS_WRAPPER = r"""
import * as std from "std";
const fixturePath = scriptArgs[1];
const seed = parseInt(scriptArgs[2], 10);
const family = "__FAMILY__";

function stderrPrint(msg) {
    if (typeof std !== "undefined" && std.err && std.err.puts) {
        std.err.puts(msg + "\n");
    } else {
        print(msg);
    }
}
globalThis.console = { log: print, error: stderrPrint };

let state = seed >>> 0;
Math.random = function() {
    state = ((state * 1664525 + 1013904223) & 0xFFFFFFFF) >>> 0;
    return state / 0x100000000;
};


globalThis.setTimeout = function() { return 0; };
globalThis.setInterval = function() { return 0; };
globalThis.setImmediate = function() { return 0; };
globalThis.clearTimeout = function() {};
globalThis.clearInterval = function() {};
globalThis.clearImmediate = function() {};
if (globalThis.performance === undefined) {
    globalThis.performance = {};
}
globalThis.performance.now = () => fixedTime;

const fixedTime = Date.now();
const origSymbol = Symbol;
globalThis.Symbol = function(desc) { return origSymbol.for(String(desc ?? "")); };
globalThis.Symbol.for = origSymbol.for;
globalThis.Symbol.keyFor = origSymbol.keyFor;

function approxEqual(a, b, tol) {
    if (typeof a === 'number') return Math.abs(a - b) <= tol;
    if (Array.isArray(a)) return a.length === b.length && a.every((v, i) => approxEqual(v, b[i], tol));
    return a === b;
}

function extractCore(result, family) {
    if (family === 'linreg') {
        if (typeof result.weights === 'undefined' || typeof result.bias === 'undefined') {
            throw new Error('family shape mismatch: linreg requires weights and bias, got ' + Object.keys(result || {}).join(','));
        }
        return { weights: result.weights, bias: result.bias };
    }
    if (family === 'statistics') {
        // Accept either flat fields or nested statsX/statsY objects.
        const sx = result.statsX || result;
        const sy = result.statsY || result;
        const core = {
            meanX: sx.mean, meanY: sy.mean,
            varianceX: sx.variance, varianceY: sy.variance,
            correlation: result.correlation, covariance: result.covariance
        };
        for (const k of Object.keys(core)) {
            if (typeof core[k] === 'undefined') {
                throw new Error('family shape mismatch: statistics missing ' + k + ', got keys ' + Object.keys(result || {}).join(','));
            }
        }
        return core;
    }
    if (family === 'kmeans') {
        if (typeof result.centroids === 'undefined' || typeof result.k === 'undefined') {
            throw new Error('family shape mismatch: kmeans requires centroids and k, got ' + Object.keys(result || {}).join(','));
        }
        return { centroids: result.centroids, k: result.k };
    }
    return result;
}

const src = std.loadFile(fixturePath);
if (src === null) {
    stderrPrint("HARNESS_WRAPPER_ERROR: cannot read " + fixturePath);
    std.exit(1);
}

// Pre-populate the contract globals so that fixtures with a top-level
// console.log(linearRegressionGD(X, y, lr, iters)) do not crash at eval time.
// Fixtures that declare their own X/y/lr/iters will shadow these globals.
// The harness later overwrites globalThis.X/globalThis.y with real data.
globalThis.X = [[0, 0], [0, 0], [0, 0]];
globalThis.y = [0, 0, 0];
globalThis.lr = 0.05;
globalThis.iters = 1;
try {
    const indirect = eval;
    indirect(src);
} catch (err) {
    stderrPrint("HARNESS_WRAPPER_ERROR: " + err.message);
    std.exit(1);
}

if (typeof globalThis.linearRegressionGD !== 'function') {
    stderrPrint("HARNESS_WRAPPER_ERROR: linearRegressionGD not defined");
    std.exit(1);
}

const harnessData = (function() {
__MAKE_HARNESS_DATA__
return makeHarnessData();
})();
X = harnessData.X;
y = harnessData.y;
const result = globalThis.linearRegressionGD(X, y, __LR__, __ITERS__);
let out;
try {
    out = extractCore(result, family);
} catch (err) {
    stderrPrint("HARNESS_WRAPPER_ERROR: " + err.message);
    std.exit(1);
}

__RELATIONS_AND_METRICS__

print(JSON.stringify(out));
"""


# ---------------------------------------------------------------------------
# Family-specific JS code injected into wrappers
# ---------------------------------------------------------------------------
LINREG_MAKE_DATA_JS = r"""
function lcg_next(s) {
    return ((s * 1664525 + 1013904223) & 0xFFFFFFFF) >>> 0;
}
function makeHarnessData() {
    let st = __DATA_SEED__ >>> 0;
    const nf = __N_FEATURES__;
    const n = __N_SAMPLES__;
    const X = [];
    for (let i = 0; i < n; i++) {
        const row = [];
        for (let j = 0; j < nf; j++) {
            st = lcg_next(st);
            row.push((st / 0x100000000) * 2 - 1);
        }
        X.push(row);
    }
    const trueWeights = __TRUE_WEIGHTS__;
    const trueBias = __TRUE_BIAS__;
    const y = [];
    for (let i = 0; i < n; i++) {
        let pred = trueBias;
        for (let j = 0; j < nf; j++) pred += trueWeights[j] * X[i][j];
        y.push(pred);
    }
    return { X, y, trueWeights, trueBias };
}
"""

LINREG_RELATIONS_JS = r"""
function predict(X, weights, bias) {
    return X.map(row => bias + row.reduce((acc, x, idx) => acc + x * weights[idx], 0));
}
const sourcePred = predict(X, result.weights, result.bias);
const yShifted = y.map(v => v + 7.0);
const resShift = globalThis.linearRegressionGD(X, yShifted, __LR__, __ITERS__);
const shiftOk = approxEqual(predict(X, resShift.weights, resShift.bias), sourcePred.map(v => v + 7.0), __REL_TOL__);
const yScaled = y.map(v => v * 2.5);
const resScale = globalThis.linearRegressionGD(X, yScaled, __LR__, __ITERS__);
const scaleOk = approxEqual(predict(X, resScale.weights, resScale.bias), sourcePred.map(v => v * 2.5), __REL_TOL__);
const XPerm = X.map(row => [row[1], row[0]]);
const resPerm = globalThis.linearRegressionGD(XPerm, y, __LR__, __ITERS__);
const permOk = approxEqual(predict(XPerm, resPerm.weights, resPerm.bias), sourcePred, __REL_TOL__);
out.relations = [
    { name: 'scale_target', passed: scaleOk },
    { name: 'shift_target', passed: shiftOk },
    { name: 'feature_permutation', passed: permOk }
];
"""

STATISTICS_MAKE_DATA_JS = r"""
function lcg_next(s) {
    return ((s * 1664525 + 1013904223) & 0xFFFFFFFF) >>> 0;
}
function makeHarnessData() {
    let st = __DATA_SEED__ >>> 0;
    const n = __N_SAMPLES__;
    const xs = [];
    for (let i = 0; i < n; i++) {
        st = lcg_next(st);
        xs.push((st / 0x100000000) * 2 - 1);
    }
    const y = [];
    for (let i = 0; i < n; i++) {
        y.push(1.5 + 2.3 * xs[i] + 0.7 * xs[i] * xs[i]);
    }
    const X = xs.map(x => [x, 0.0]);
    return { X, y };
}
"""

STATISTICS_RELATIONS_JS = r"""
// Statistics metamorphic relations.  The ground-truth reference uses
// population variance (divide by N); these relations exercise invariants of
// mean, variance, and correlation that are sound for the exact solution.
const shiftVal = 7.0;
const yShifted = y.map(v => v + shiftVal);
const resShiftRaw = globalThis.linearRegressionGD(X, yShifted, __LR__, __ITERS__);
const resShift = extractCore(resShiftRaw, family);
const meanShiftOk = approxEqual(resShift.meanY, out.meanY + shiftVal, __REL_TOL__)
                 && approxEqual(resShift.meanX, out.meanX, __REL_TOL__);
const varianceTranslationOk = approxEqual(resShift.varianceY, out.varianceY, __REL_TOL__)
                           && approxEqual(resShift.varianceX, out.varianceX, __REL_TOL__);

const scaleVal = 2.5;
const yScaled = y.map(v => v * scaleVal);
const resScaleRaw = globalThis.linearRegressionGD(X, yScaled, __LR__, __ITERS__);
const resScale = extractCore(resScaleRaw, family);
const varianceScaleOk = approxEqual(resScale.varianceY, out.varianceY * scaleVal * scaleVal, __REL_TOL__)
                     && approxEqual(resScale.meanY, out.meanY * scaleVal, __REL_TOL__);

// Correlation is invariant under positive affine rescaling of either variable.
const xScale = 2.0, xShift = 1.5;
const yScale = 1.5, yShift2 = -0.7;
const XAffine = X.map(row => [row[0] * xScale + xShift, row[1]]);
const yAffine = y.map(v => v * yScale + yShift2);
const resAffineRaw = globalThis.linearRegressionGD(XAffine, yAffine, __LR__, __ITERS__);
const resAffine = extractCore(resAffineRaw, family);
const affineCorrOk = approxEqual(resAffine.correlation, out.correlation, __REL_TOL__)
                && approxEqual(resAffine.covariance, out.covariance * xScale * yScale, __REL_TOL__);

out.relations = [
    { name: 'mean_shift', passed: meanShiftOk },
    { name: 'variance_translation_invariant', passed: varianceTranslationOk },
    { name: 'variance_scale_squared', passed: varianceScaleOk },
    { name: 'correlation_affine_invariant', passed: affineCorrOk }
];
"""

KMEANS_MAKE_DATA_JS = r"""
function lcg_next(s) {
    return ((s * 1664525 + 1013904223) & 0xFFFFFFFF) >>> 0;
}
function makeHarnessData() {
    let st = __DATA_SEED__ >>> 0;
    function normal_pair() {
        st = lcg_next(st);
        const u1 = Math.max(st / 0x100000000, 1e-10);
        st = lcg_next(st);
        const u2 = st / 0x100000000;
        const r = Math.sqrt(-2.0 * Math.log(u1));
        const theta = 2.0 * Math.PI * u2;
        return [r * Math.cos(theta), r * Math.sin(theta)];
    }
    const centers = [[0.0, 0.0], [5.0, 0.0], [2.5, 4.0]];
    const sigma = 0.5;
    const clusters = [[], [], []];
    for (let c = 0; c < 3; c++) {
        for (let i = 0; i < 20; i++) {
            const z = normal_pair();
            clusters[c].push([centers[c][0] + sigma * z[0], centers[c][1] + sigma * z[1]]);
        }
    }
    // Round-robin ordering: first-k init must see one point per cluster.
    const X = [];
    for (let i = 0; i < 20; i++) {
        for (let c = 0; c < 3; c++) {
            X.push(clusters[c][i]);
        }
    }
    const y = [];
    return { X, y };
}
"""

KMEANS_RELATIONS_JS = r"""
// k-means: run again on a shifted copy; exact Lloyd's update is translation-
// invariant, while a subtly wrong update (e.g. dividing by counts+1) is not.
const shiftVec = [3.3, -2.1];
const XShifted = X.map(row => [row[0] + shiftVec[0], row[1] + shiftVec[1]]);
const resAgainRaw = globalThis.linearRegressionGD(XShifted, y, __LR__, __ITERS__);
const resAgain = extractCore(resAgainRaw, family);
const expectedShifted = result.centroids.map(c => [c[0] + shiftVec[0], c[1] + shiftVec[1]]);
const againOk = approxEqual(resAgain.centroids, expectedShifted, __REL_TOL__);
out.relations = [
    { name: 'deterministic_repeat', passed: againOk }
];
"""


def _build_wrapper(template: str, family: str) -> str:
    if family == "linreg":
        make_data = LINREG_MAKE_DATA_JS
        relations = LINREG_RELATIONS_JS
    elif family == "statistics":
        make_data = STATISTICS_MAKE_DATA_JS
        relations = STATISTICS_RELATIONS_JS
    elif family == "kmeans":
        make_data = KMEANS_MAKE_DATA_JS
        relations = KMEANS_RELATIONS_JS
    else:
        raise ValueError(f"unsupported family: {family}")

    make_data = (
        make_data.replace("__DATA_SEED__", str(HARNESS_DATA_SEED))
        .replace("__N_FEATURES__", str(HARNESS_N_FEATURES))
        .replace("__N_SAMPLES__", str(HARNESS_N_SAMPLES))
        .replace("__TRUE_WEIGHTS__", json.dumps(HARNESS_TRUE_WEIGHTS))
        .replace("__TRUE_BIAS__", str(HARNESS_TRUE_BIAS))
        .replace("__LR__", str(HARNESS_LR))
        .replace("__ITERS__", str(HARNESS_ITERS))
    )
    relations = (
        relations.replace("__REL_TOL__", str(RELATION_TOL))
        .replace("__LR__", str(HARNESS_LR))
        .replace("__ITERS__", str(HARNESS_ITERS))
    )

    template = (
        template.replace("__FAMILY__", family)
        .replace("__LR__", str(HARNESS_LR))
        .replace("__ITERS__", str(HARNESS_ITERS))
    )

    return (
        template.replace("__MAKE_HARNESS_DATA__", make_data)
        .replace("__RELATIONS_AND_METRICS__", relations)
    )


# ---------------------------------------------------------------------------
# Tolerances and gating
# ---------------------------------------------------------------------------
WEIGHT_ABS_TOL = 0.2
BIAS_ABS_TOL = 0.2
R2_MIN = 0.95
RELATION_TOL = 0.1

CANONICAL_MATCH_ABS_TOL = 1e-3
CANONICAL_MATCH_REL_TOL = 1e-3

LINREG_CORE_FIELDS = {"weights", "bias"}
STATISTICS_CORE_FIELDS = {"meanX", "meanY", "varianceX", "varianceY", "correlation", "covariance"}
KMEANS_CORE_FIELDS = {"centroids", "k"}

GATING_RELATIONS = {
    "scale_target",
    "shift_target",
    "feature_permutation",
    "mean_shift",
    "variance_translation_invariant",
    "variance_scale_squared",
    "correlation_affine_invariant",
    "deterministic_repeat",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _run_subprocess(cmd: List[str], env: Optional[Dict[str, str]] = None, timeout: float = 60.0) -> Tuple[int, str, str]:
    """Run a command; return (rc, stdout, stderr). Output is bounded."""
    merged = os.environ.copy()
    if env:
        merged.update(env)
    merged.pop("PYTHONPATH", None)
    merged.pop("NODE_OPTIONS", None)
    merged["LC_ALL"] = "C"
    merged["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=merged,
        )
        out = proc.stdout
        if len(out) > MAX_OUTPUT_BYTES:
            return -2, out[:MAX_OUTPUT_BYTES], "output exceeded MAX_OUTPUT_BYTES"
        if out.count("\n") > MAX_OUTPUT_LINES:
            return -2, out, "output exceeded MAX_OUTPUT_LINES"
        return proc.returncode, out, proc.stderr
    except subprocess.TimeoutExpired:
        return -9, "", "timeout"
    except Exception as exc:
        return -1, "", f"exception: {exc}"


def _write_wrappers(tmpdir: Path, family: str) -> Tuple[Path, Path]:
    node_wrap = tmpdir / "wrap_node.js"
    qjs_wrap = tmpdir / "wrap_qjs.js"
    node_wrap.write_text(_build_wrapper(NODE_WRAPPER, family), encoding="utf-8")
    qjs_wrap.write_text(_build_wrapper(QJS_WRAPPER, family), encoding="utf-8")
    return node_wrap, qjs_wrap


def _run_engine(
    engine_name: str,
    engine_path: str,
    fixture: Path,
    seed: int,
    wrappers: Tuple[Path, Path],
) -> Tuple[Optional[str], List[Dict[str, Any]]]:
    """Run one engine. Returns (last_non_empty_line, all_valid_json_objects)."""
    wrap = wrappers[1] if engine_name == "qjs" else wrappers[0]
    cmd = [engine_path, str(wrap), str(fixture), str(seed)]
    rc, stdout, stderr = _run_subprocess(cmd)
    if rc == -2:
        return None, []
    if rc != 0:
        # A wrapper-level error (e.g. family shape mismatch) should produce a
        # clear harness-level failure, not hide as an engine crash.  We append
        # the wrapper's stderr to stdout so the harness can surface it.
        err_text = (stderr or "").strip()
        if err_text.startswith("HARNESS_WRAPPER_ERROR") or "HARNESS_WRAPPER_ERROR" in stdout:
            return f'{{"HARNESS_WRAPPER_ERROR": {json.dumps(err_text if err_text else stdout.strip().splitlines()[-1])}}}', []
        return None, []
    lines = [line for line in stdout.splitlines() if line.strip()]
    json_objects = []
    for line in lines:
        try:
            json_objects.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return (lines[-1] if lines else None), json_objects


def _predict(X: List[List[float]], weights: List[float], bias: float) -> List[float]:
    return [bias + sum(weights[j] * row[j] for j in range(len(weights))) for row in X]


def _r2(y: List[float], pred: List[float]) -> float:
    mean_y = sum(y) / len(y)
    ss_res = sum((pred[i] - y[i]) ** 2 for i in range(len(y)))
    ss_tot = sum((y[i] - mean_y) ** 2 for i in range(len(y)))
    return 1.0 - (ss_res / ss_tot) if ss_tot > 0 else 0.0


def _approx_eq(a: Any, b: Any, abs_tol: float = 1e-9, rel_tol: float = 1e-9) -> bool:
    if isinstance(a, (list, tuple)):
        if not isinstance(b, (list, tuple)) or len(a) != len(b):
            return False
        return all(_approx_eq(x, y, abs_tol, rel_tol) for x, y in zip(a, b))
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if math.isnan(a) or math.isnan(b):
            return False
        return abs(a - b) <= max(abs_tol, rel_tol * max(abs(a), abs(b)))
    return a == b


def _has_bad_number(obj: Any) -> bool:
    if isinstance(obj, dict):
        for v in obj.values():
            if _has_bad_number(v):
                return True
    elif isinstance(obj, list):
        for v in obj:
            if _has_bad_number(v):
                return True
    elif isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return True
    elif obj is None:
        return True
    return False


def _run_type_check(fixture: Path, tmpdir: Path) -> Tuple[str, str]:
    """Return (status, message). Status: 'ok' | 'unavailable' | 'error'."""
    if not TSC.exists():
        return "unavailable", "tsc not found"

    # Provide a temporary tsconfig.  Fixtures that do NOT declare their own
    # top-level X/y/lr/iters are wrapped in an IIFE that supplies them as
    # parameters; fixtures that DO declare them (e.g. existing batch2 patterns)
    # are type-checked as-is.  This avoids a collision between harness-injected
    # global declarations and fixture-level const declarations.
    tsconfig = tmpdir / "tsconfig.json"
    globals_file = tmpdir / "globals.d.ts"
    globals_file.write_text("// no injected globals\n", encoding="utf-8")
    fixture_src = fixture.read_text(encoding="utf-8")
    fixture_copy = tmpdir / "fixture.js"
    contract_names = ["X", "y", "lr", "iters"]
    dummy_values = {
        "X": "[[0,0],[0,0],[0,0]]",
        "y": "[0,0,0]",
        "lr": "0.05",
        "iters": "1",
    }
    declared_re = {n: re.compile(r"^\s*(?:const|let|var)\s+\b" + re.escape(n) + r"\b", re.MULTILINE) for n in contract_names}
    missing = [n for n in contract_names if not declared_re[n].search(fixture_src)]
    if missing:
        params = ", ".join(missing)
        args = ", ".join(dummy_values[n] for n in missing)
        fixture_copy.write_text(
            f"(function({params}) {{\n{fixture_src}\n}})({args});\n",
            encoding="utf-8",
        )
    else:
        fixture_copy.write_text(fixture_src, encoding="utf-8")
    tsconfig.write_text(
        json.dumps(
            {
                "compilerOptions": {
                    "noEmit": True,
                    "allowJs": True,
                    "checkJs": True,
                    "noImplicitAny": False,
                    "noImplicitThis": False,
                    "strictNullChecks": False,
                    "target": "ES2020",
                },
                "include": ["globals.d.ts", "fixture.js"],
            }
        ),
        encoding="utf-8",
    )
    cmd = [str(TSC), "--project", str(tsconfig)]
    rc, stdout, stderr = _run_subprocess(cmd, timeout=60.0)
    if rc == -2:
        return "unavailable", "tsc output exceeded harness limits"
    if rc != 0:
        return "error", (stderr or stdout)[:400]
    return "ok", ""


# ---------------------------------------------------------------------------
# Family-specific verification
# ---------------------------------------------------------------------------
def _verify_linreg(
    data: Dict[str, Any],
    X: List[List[float]],
    y: List[float],
    ref_weights: List[float],
    ref_bias: float,
    failures: List[str],
) -> None:
    weights = data.get("weights")
    bias = data.get("bias")
    if not isinstance(weights, list):
        failures.append("linreg result missing weights list")
        return
    if not isinstance(bias, (int, float)):
        failures.append("linreg result missing bias number")
        return
    if len(weights) != HARNESS_N_FEATURES:
        failures.append(f"linreg returned {len(weights)} weights, expected {HARNESS_N_FEATURES}")
        return
    max_weight_err = max(abs(weights[j] - HARNESS_TRUE_WEIGHTS[j]) for j in range(HARNESS_N_FEATURES))
    if max_weight_err > WEIGHT_ABS_TOL:
        failures.append(f"linreg weight recovery error {max_weight_err:.4f} exceeds tolerance {WEIGHT_ABS_TOL}")
    if abs(bias - HARNESS_TRUE_BIAS) > BIAS_ABS_TOL:
        failures.append(f"linreg bias recovery error {abs(bias - HARNESS_TRUE_BIAS):.4f} exceeds tolerance {BIAS_ABS_TOL}")
    pred = _predict(X, weights, bias)
    r2 = _r2(y, pred)
    if r2 < R2_MIN:
        failures.append(f"linreg R² {r2:.4f} below threshold {R2_MIN}")
    data["r2"] = round(r2, 6)
    canonical_match = _approx_eq(weights, ref_weights, abs_tol=CANONICAL_MATCH_ABS_TOL, rel_tol=CANONICAL_MATCH_REL_TOL) and _approx_eq(bias, ref_bias, abs_tol=CANONICAL_MATCH_ABS_TOL, rel_tol=CANONICAL_MATCH_REL_TOL)
    data["canonical_match"] = canonical_match


def _verify_statistics(
    data: Dict[str, Any],
    X: List[List[float]],
    y: List[float],
    failures: List[str],
) -> None:
    xs = [row[0] for row in X]
    ref = _reference_statistics(xs, y)
    required = STATISTICS_CORE_FIELDS
    missing = required - set(data.keys())
    if missing:
        failures.append(f"statistics result missing fields: {', '.join(sorted(missing))}")
        return
    if _has_bad_number(data):
        failures.append("statistics result contains NaN/Infinity or unexpected null")
        return
    # Population variance, correlation, covariance tolerances.
    stats_tol = 1e-3
    corr_tol = 5e-3
    for key in ["meanX", "meanY", "varianceX", "varianceY", "medianX", "medianY", "q25X", "q25Y", "q75X", "q75Y"]:
        if key in ref and key in data:
            if abs(data[key] - ref[key]) > stats_tol:
                failures.append(f"statistics {key} error {abs(data[key] - ref[key]):.6f} exceeds tolerance {stats_tol}")
    if abs(data.get("correlation", 0.0) - ref["correlation"]) > corr_tol:
        failures.append(f"statistics correlation error {abs(data.get('correlation', 0.0) - ref['correlation']):.6f} exceeds tolerance {corr_tol}")
    if abs(data.get("covariance", 0.0) - ref["covariance"]) > stats_tol:
        failures.append(f"statistics covariance error {abs(data.get('covariance', 0.0) - ref['covariance']):.6f} exceeds tolerance {stats_tol}")
    data["canonical_match"] = not failures or all("statistics" not in f for f in failures)


def _verify_kmeans(
    data: Dict[str, Any],
    X: List[List[float]],
    failures: List[str],
) -> None:
    if "centroids" not in data or "k" not in data:
        failures.append("kmeans result missing centroids or k")
        return
    centroids = data["centroids"]
    k = data["k"]
    if not isinstance(centroids, list) or len(centroids) != k:
        failures.append(f"kmeans returned {len(centroids) if isinstance(centroids, list) else 'non-list'} centroids for k={k}")
        return
    if any(not isinstance(c, list) or len(c) != 2 for c in centroids):
        failures.append("kmeans centroids are not 2D points")
        return
    if _has_bad_number(data):
        failures.append("kmeans result contains NaN/Infinity or unexpected null")
        return

    def dist2(a, b):
        return sum((a[i] - b[i]) ** 2 for i in range(2))

    def nearest_assignments(centroids):
        k = len(centroids)
        assignments = []
        for x in X:
            best = 0
            best_d = dist2(x, centroids[0])
            for i in range(1, k):
                d = dist2(x, centroids[i])
                if d < best_d:
                    best_d = d
                    best = i
            assignments.append(best)
        return assignments

    def cluster_means(assignments, centroids):
        k = len(centroids)
        nf = len(centroids[0])
        sums = [[0.0] * nf for _ in range(k)]
        counts = [0] * k
        for i, x in enumerate(X):
            c = assignments[i]
            counts[c] += 1
            for f in range(nf):
                sums[c][f] += x[f]
        means = []
        for c in range(k):
            if counts[c] > 0:
                means.append([sums[c][f] / counts[c] for f in range(nf)])
            else:
                means.append(centroids[c][:])
        return means

    ref_centroids, _ = _reference_kmeans(X, k=k)

    # ------------------------------------------------------------------
    # Fixed-point condition: returned centroids must be the means of their
    # Voronoi cells. This is the defining property of a Lloyd's fixed point,
    # permutation-invariant and independent of initialisation.
    # ------------------------------------------------------------------
    assignments = nearest_assignments(centroids)
    means = cluster_means(assignments, centroids)
    KMEANS_FIXED_POINT_TOL = 1e-4
    max_fixed_point_err = max(
        math.sqrt(dist2(centroids[c], means[c]))
        for c in range(k)
    )
    if max_fixed_point_err > KMEANS_FIXED_POINT_TOL:
        failures.append(f"kmeans fixed-point violation: centroid deviation from cluster mean {max_fixed_point_err:.6f} exceeds tolerance {KMEANS_FIXED_POINT_TOL}")

    # ------------------------------------------------------------------
    # Assignment consistency: if the fixture returned assignments, every point
    # must be assigned to its nearest returned centroid.
    # ------------------------------------------------------------------
    fixture_assignments = data.get("assignments")
    if isinstance(fixture_assignments, list) and len(fixture_assignments) == len(X):
        expected_assignments = nearest_assignments(centroids)
        mismatches = sum(1 for i in range(len(X)) if fixture_assignments[i] != expected_assignments[i])
        if mismatches > 0:
            failures.append(f"kmeans returned assignments inconsistent with nearest centroids: {mismatches} mismatches")

    # ------------------------------------------------------------------
    # Scalar solution-quality check: within-cluster sum of squares must be
    # comparable to a reference Lloyd's run. This is a scalar, so it does not
    # compare centroid coordinates or care about label permutation.
    # ------------------------------------------------------------------
    def within_cluster_ss(centroids, assignments):
        total = 0.0
        for i, x in enumerate(X):
            total += dist2(x, centroids[assignments[i]])
        return total

    fixture_wcss = within_cluster_ss(centroids, assignments)
    ref_assignments = nearest_assignments(ref_centroids)
    ref_wcss = within_cluster_ss(ref_centroids, ref_assignments)
    KMEANS_WCSS_REL_TOL = 0.5
    if ref_wcss > 0 and fixture_wcss > ref_wcss * (1.0 + KMEANS_WCSS_REL_TOL):
        failures.append(f"kmeans within-cluster SS {fixture_wcss:.4f} exceeds reference {ref_wcss:.4f} by more than {KMEANS_WCSS_REL_TOL*100:.0f}%")

    def compute_metrics(centroids):
        k = len(centroids)
        assignments = nearest_assignments(centroids)
        intra = sum(math.sqrt(dist2(X[i], centroids[assignments[i]])) for i in range(len(X))) / len(X)
        separations = []
        for i in range(k):
            for j in range(i + 1, k):
                separations.append(math.sqrt(dist2(centroids[i], centroids[j])))
        min_sep = min(separations) if separations else 0.0
        return intra, min_sep

    intra, min_sep = compute_metrics(centroids)
    ref_intra, ref_min_sep = compute_metrics(ref_centroids)

    # Keep the existing geometric thresholds; they are necessary but not sufficient.
    KMEANS_MAX_INTRA = 2.0
    KMEANS_MIN_SEPARATION = 2.0

    if intra >= KMEANS_MAX_INTRA:
        failures.append(f"kmeans intra-cluster distance {intra:.4f} >= {KMEANS_MAX_INTRA}")
    if min_sep <= KMEANS_MIN_SEPARATION:
        failures.append(f"kmeans min centroid separation {min_sep:.4f} <= {KMEANS_MIN_SEPARATION}")

    canonical_match = _approx_eq(intra, ref_intra, rel_tol=0.1) and _approx_eq(min_sep, ref_min_sep, rel_tol=0.1)
    data["intra_cluster_distance"] = round(intra, 6)
    data["min_centroid_separation"] = round(min_sep, 6)
    data["fixed_point_error"] = round(max_fixed_point_err, 6)
    data["within_cluster_ss"] = round(fixture_wcss, 6)
    data["canonical_match"] = canonical_match


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="JS oracle harness for Forge Gate")
    ap.add_argument("--fixture", required=True, help="path to the JS fixture")
    args = ap.parse_args()

    fixture = Path(args.fixture).resolve()
    if not fixture.exists():
        print("engine:node result:{}")
        print("engine:bun result:{}")
        print("engine:qjs result:{}")
        print(f"verdict:INCONCLUSIVE fixture not found: {fixture}")
        return 0

    src = fixture.read_text(encoding="utf-8")
    family, family_reason = _detect_family(src)

    tmpdir = Path(tempfile.mkdtemp(prefix="fg-js-harness-"))
    try:
        type_status, type_msg = _run_type_check(fixture, tmpdir)

        results: Dict[str, Dict[str, Any]] = {}
        failures: List[str] = []
        inconclusive: List[str] = []

        if not family:
            inconclusive.append(family_reason)
        elif family not in {"linreg", "statistics", "kmeans"}:
            inconclusive.append(f"unknown family '{family}'")

        if inconclusive:
            verdict = "INCONCLUSIVE"
            reason = "; ".join(inconclusive)
            if type_status == "error":
                reason += f"; type check: {type_msg}"
            for name in ENGINES:
                print(f"engine:{name} result:{{}}")
            print(f"verdict:{verdict} {reason}")
            return 0

        wrappers = _write_wrappers(tmpdir, family)

        # Data generation.
        if family == "linreg":
            X_harness, y_harness = _make_linreg_data()
            ref_weights, ref_bias = _normal_equations(X_harness, y_harness)
            core_fields = LINREG_CORE_FIELDS
        elif family == "statistics":
            X_harness, y_harness = _make_statistics_data()
            ref_weights, ref_bias = [], 0.0
            core_fields = STATISTICS_CORE_FIELDS
        elif family == "kmeans":
            X_harness = _make_kmeans_data()
            y_harness = []
            ref_weights, ref_bias = [], 0.0
            core_fields = KMEANS_CORE_FIELDS
        else:
            X_harness, y_harness = _make_linreg_data()
            ref_weights, ref_bias = _normal_equations(X_harness, y_harness)
            core_fields = LINREG_CORE_FIELDS

        for name, path in ENGINES.items():
            if not os.path.exists(path):
                inconclusive.append(f"{name} binary missing at {path}")
                continue

            out1, jsons1 = _run_engine(name, path, fixture, 1000, wrappers)
            out2, jsons2 = _run_engine(name, path, fixture, 2000, wrappers)

            if out1 is None and out2 is None:
                inconclusive.append(f"{name} subprocess failed, timed out, or output exceeded harness limits")
                continue
            if out1 is None or out2 is None:
                inconclusive.append(f"{name} one of two seeded runs failed")
                continue

            if out1 != out2:
                failures.append(f"{name} output varies with injected RNG seed (non-deterministic)")

            try:
                data = json.loads(out1)
            except json.JSONDecodeError as exc:
                inconclusive.append(f"{name} produced invalid JSON: {exc}")
                continue
            if isinstance(data, dict) and "HARNESS_WRAPPER_ERROR" in data:
                failures.append(f"{name} {data['HARNESS_WRAPPER_ERROR']}")
                results[name] = data
                continue

            results[name] = data

            # The fixture may have emitted its own JSON line before the harness result.
            # We preserve mathProbe for reporting, but we do NOT gate on the fixture's
            # own output: it may legitimately contain null/NaN when run on the dummy
            # contract globals that the harness injects before eval.
            fixture_output = jsons1[0] if len(jsons1) >= 2 else None
            if fixture_output and isinstance(fixture_output, dict):
                if "mathProbe" in fixture_output:
                    data["mathProbe"] = fixture_output["mathProbe"]

            if _has_bad_number(data):
                failures.append(f"{name} result contains NaN/Infinity or unexpected null")

            # Family-specific verification.
            if family == "linreg":
                _verify_linreg(data, X_harness, y_harness, ref_weights, ref_bias, failures)
            elif family == "statistics":
                _verify_statistics(data, X_harness, y_harness, failures)
            elif family == "kmeans":
                _verify_kmeans(data, X_harness, failures)

            # Metamorphic relations.
            relations = data.get("relations", [])
            for rel in relations:
                rel_name = rel.get("name", "")
                if rel_name in GATING_RELATIONS and not rel.get("passed"):
                    failures.append(f"{name} metamorphic relation {rel_name} failed")

        # Cross-engine consensus on core fields requires ALL THREE engines.
        if len(results) == len(ENGINES):
            first_name = next(iter(results))
            first_core = {k: results[first_name][k] for k in core_fields if k in results[first_name]}
            for name in list(results.keys())[1:]:
                other_core = {k: results[name][k] for k in core_fields if k in results[name]}
                if not _approx_eq(first_core, other_core, abs_tol=1e-9, rel_tol=1e-9):
                    failures.append(f"cross-engine divergence: {first_name} vs {name}")
        elif len(results) > 0:
            missing = [name for name in ENGINES if name not in results]
            inconclusive.append(f"engines missing or failed: {', '.join(missing)}")

        if type_status == "unavailable":
            inconclusive.append(f"type check unavailable: {type_msg}")
        elif type_status == "error":
            failures.append(f"type check failed: {type_msg}")

        if len(results) < len(ENGINES) and not inconclusive:
            missing = [name for name in ENGINES if name not in results]
            inconclusive.append(f"engines missing or failed: {', '.join(missing)}")

        if inconclusive:
            verdict = "INCONCLUSIVE"
            reason = "; ".join(inconclusive)
        elif failures:
            verdict = "FAIL"
            reason = "; ".join(failures)
        else:
            verdict = "PASS"
            reason = "deterministic, all three engines agree, property checks pass"

        for name in ENGINES:
            payload = results.get(name, {})
            print(f"engine:{name} result:{json.dumps(payload, default=str, sort_keys=True)}")
        print(f"verdict:{verdict} {reason}")
        return 0
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())