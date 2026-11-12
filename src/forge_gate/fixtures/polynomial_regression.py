#!/usr/bin/env python3
"""Gate fixture for polynomial_regression.

PASS is decided by how well recovered coefficients match the planted ground-truth
polynomial, not by matching the AURA oracle's exact numerical path.
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
        print("usage: polynomial_regression.py <user_file.py>", file=sys.stderr)
        sys.exit(2)

    user_path = sys.argv[1]
    aura_path = str(reference_path("polynomial_regression"))

    user_mod = load_module(user_path, "user_impl")
    aura_mod = load_module(aura_path, "aura_impl")

    user_fit = get_fn(user_mod, "fit_polynomial")
    user_eval = get_fn(user_mod, "eval_poly")
    aura_fit = get_fn(aura_mod, "fit_polynomial")
    aura_eval = get_fn(aura_mod, "eval_poly")

    random.seed(42)
    true_coeffs = [2.0, 3.0, 0.5]
    x_vals = [float(i) * 0.5 for i in range(20)]
    y_vals = [user_eval(true_coeffs, x) + random.gauss(0, 0.1) for x in x_vals]

    user_coeffs = user_fit(x_vals, y_vals, 2)
    aura_coeffs = aura_fit(x_vals, y_vals, 2)

    user_pred = [user_eval(user_coeffs, x) for x in x_vals]
    mse = sum((user_pred[i] - y_vals[i]) ** 2 for i in range(len(y_vals))) / len(y_vals)

    canonical_match = approx_eq(user_coeffs, aura_coeffs, rel_tol=1e-4)

    metrics = {
        "mse": round(mse, 6),
        "coeff_0": round(user_coeffs[0], 6),
        "coeff_1": round(user_coeffs[1], 6),
        "coeff_2": round(user_coeffs[2], 6),
        "canonical_match": canonical_match,
    }

    ok = all(abs(user_coeffs[i] - true_coeffs[i]) < 0.2 for i in range(3))

    emit({
        "status": "PASS" if ok else "FAIL",
        "pattern": "polynomial_regression",
        "reason": "Polynomial coefficients recover planted values" if ok else "Polynomial coefficients do not recover planted values",
        "metrics": metrics,
    })


if __name__ == "__main__":
    main()
