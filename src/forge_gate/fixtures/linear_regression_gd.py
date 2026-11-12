#!/usr/bin/env python3
"""Gate fixture for linear_regression_gd.

PASS is decided by correctness properties against ground-truth data, not by
matching the AURA oracle's exact numerical path.
"""
import random
import sys
from pathlib import Path

_pkg = Path(__file__).resolve().parents[2]
if str(_pkg) not in sys.path:
    sys.path.insert(0, str(_pkg))

from forge_gate.catalog import reference_path
from forge_gate.fixture_engine import approx_eq, emit, get_fn, load_module


def main():
    if len(sys.argv) < 2:
        print("usage: linear_regression_gd.py <user_file.py>", file=sys.stderr)
        sys.exit(2)

    user_path = sys.argv[1]
    aura_path = str(reference_path("linear_regression_gd"))

    user_mod = load_module(user_path, "user_impl")
    aura_mod = load_module(aura_path, "aura_impl")

    user_fn = get_fn(user_mod, "linear_regression_gd")
    aura_fn = get_fn(aura_mod, "linear_regression_gd")

    random.seed(42)
    n_samples = 30
    X = [[random.uniform(-5, 5), random.uniform(-5, 5)] for _ in range(n_samples)]
    y = [2.0 * X[i][0] + 3.0 * X[i][1] + random.gauss(0, 0.5) for i in range(n_samples)]

    user_weights, user_bias = user_fn(X, y, learning_rate=0.01, iterations=200)
    aura_weights, aura_bias = aura_fn(X, y, learning_rate=0.01, iterations=200)

    # Compute R² using user weights
    predictions = [user_bias + sum(user_weights[j] * X[i][j] for j in range(2)) for i in range(n_samples)]
    mean_y = sum(y) / len(y)
    ss_res = sum((predictions[i] - y[i]) ** 2 for i in range(n_samples))
    ss_tot = sum((y[i] - mean_y) ** 2 for i in range(n_samples))
    r2 = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else 0.0

    # Property check: the underlying data was generated from y ≈ 2*x1 + 3*x2.
    canonical_match = (
        approx_eq(user_weights, aura_weights, rel_tol=1e-3)
        and approx_eq(user_bias, aura_bias, rel_tol=1e-3)
    )

    metrics = {
        "r2": round(r2, 6),
        "weight_0": round(user_weights[0], 6),
        "weight_1": round(user_weights[1], 6),
        "bias": round(user_bias, 6),
        "canonical_match": canonical_match,
    }

    ok = (
        r2 > 0.8
        and abs(user_weights[0] - 2.0) < 0.5
        and abs(user_weights[1] - 3.0) < 0.5
    )

    emit({
        "status": "PASS" if ok else "FAIL",
        "pattern": "linear_regression_gd",
        "reason": "Linear regression quality properties hold" if ok else "Linear regression quality properties fail",
        "metrics": metrics,
    })


if __name__ == "__main__":
    main()
