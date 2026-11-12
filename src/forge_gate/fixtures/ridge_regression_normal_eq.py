#!/usr/bin/env python3
"""Gate fixture for ridge_regression_normal_eq.

PASS is decided by regression quality and recovery of the planted weights, not
by matching the AURA oracle's exact numerical path.
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
        print("usage: ridge_regression_normal_eq.py <user_file.py>", file=sys.stderr)
        sys.exit(2)

    user_path = sys.argv[1]
    aura_path = str(reference_path("ridge_regression_normal_eq"))

    user_mod = load_module(user_path, "user_impl")
    aura_mod = load_module(aura_path, "aura_impl")

    user_ridge = get_fn(user_mod, "ridge_regression_normal_eq")
    user_predict = get_fn(user_mod, "ridge_regression_predict")
    aura_ridge = get_fn(aura_mod, "ridge_regression_normal_eq")
    aura_predict = get_fn(aura_mod, "ridge_regression_predict")

    random.seed(42)
    n_samples = 30
    n_features = 3
    X = [[random.uniform(-5, 5) for _ in range(n_features)] for _ in range(n_samples)]
    true_weights = [2.0, 3.0, -1.0]
    y = [sum(true_weights[j] * X[i][j] for j in range(n_features)) + random.gauss(0, 0.5)
         for i in range(n_samples)]

    user_weights = user_ridge(X, y, lambda_reg=0.01)
    aura_weights = aura_ridge(X, y, lambda_reg=0.01)

    user_preds = user_predict(X, user_weights)
    ss_res = sum((user_preds[i] - y[i]) ** 2 for i in range(n_samples))
    ss_tot = sum((y[i] - sum(y) / len(y)) ** 2 for i in range(n_samples))
    r2 = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else 0.0

    canonical_match = approx_eq(user_weights, aura_weights, rel_tol=1e-4)

    metrics = {
        "r2": round(r2, 6),
        "weight_0": round(user_weights[0], 6),
        "weight_1": round(user_weights[1], 6),
        "weight_2": round(user_weights[2], 6),
        "canonical_match": canonical_match,
    }

    ok = (
        r2 > 0.8
        and all(abs(user_weights[i] - true_weights[i]) < 1.0 for i in range(n_features))
    )

    emit({
        "status": "PASS" if ok else "FAIL",
        "pattern": "ridge_regression_normal_eq",
        "reason": "Ridge regression quality and weight-recovery properties hold" if ok else "Ridge regression quality or weight-recovery properties fail",
        "metrics": metrics,
    })


if __name__ == "__main__":
    main()
