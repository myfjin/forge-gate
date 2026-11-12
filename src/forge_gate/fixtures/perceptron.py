#!/usr/bin/env python3
"""Gate fixture for perceptron.

PASS is decided by classification accuracy on a linearly separable synthetic
dataset, not by matching the AURA oracle's exact weight vector.
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
        print("usage: perceptron.py <user_file.py>", file=sys.stderr)
        sys.exit(2)

    user_path = sys.argv[1]
    aura_path = str(reference_path("perceptron"))

    user_mod = load_module(user_path, "user_impl")
    aura_mod = load_module(aura_path, "aura_impl")

    user_fit = get_fn(user_mod, "perceptron_fit")
    user_predict = get_fn(user_mod, "perceptron_predict")
    aura_fit = get_fn(aura_mod, "perceptron_fit")
    aura_predict = get_fn(aura_mod, "perceptron_predict")

    random.seed(42)
    n_samples = 80
    X, y = [], []
    for _ in range(n_samples):
        label = random.choice([0, 1])
        x1 = random.uniform(-3, 3)
        # Overlapping classes around the boundary x2 = x1. A correct perceptron
        # scores ~0.88; a broken one that ignores the data scores ~0.55.
        noise = random.gauss(0, 0.8)
        if label == 0:
            x2 = x1 - 0.9 + noise
        else:
            x2 = x1 + 0.9 + noise
        X.append([x1, x2])
        y.append(label)

    user_w, user_b = user_fit(X, y, learning_rate=1.0, max_iterations=100)
    aura_w, aura_b = aura_fit(X, y, learning_rate=1.0, max_iterations=100)

    user_preds = user_predict(X, user_w, user_b)
    aura_preds = user_predict(X, aura_w, aura_b)

    user_accuracy = sum(user_preds[i] == y[i] for i in range(n_samples)) / n_samples
    aura_accuracy = sum(aura_preds[i] == y[i] for i in range(n_samples)) / n_samples

    canonical_match = (
        approx_eq(user_accuracy, aura_accuracy, rel_tol=1e-5)
        and approx_eq(user_w, aura_w, rel_tol=1e-2)
    )

    metrics = {
        "accuracy": round(user_accuracy, 6),
        "oracle_accuracy": round(aura_accuracy, 6),
        "weight_0": round(user_w[0], 6),
        "weight_1": round(user_w[1], 6),
        "canonical_match": canonical_match,
    }

    ok = user_accuracy >= 0.75

    emit({
        "status": "PASS" if ok else "FAIL",
        "pattern": "perceptron",
        "reason": "Perceptron accuracy property holds" if ok else "Perceptron accuracy property fails",
        "metrics": metrics,
    })


if __name__ == "__main__":
    main()
