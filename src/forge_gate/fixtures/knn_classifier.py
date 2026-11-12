#!/usr/bin/env python3
"""Gate fixture for knn_classifier.

PASS is decided by classification accuracy on a separable synthetic dataset,
not by matching the AURA oracle's exact predictions.
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
        print("usage: knn_classifier.py <user_file.py>", file=sys.stderr)
        sys.exit(2)

    user_path = sys.argv[1]
    aura_path = str(reference_path("knn_classifier"))

    user_mod = load_module(user_path, "user_impl")
    aura_mod = load_module(aura_path, "aura_impl")

    user_fn = get_fn(user_mod, "knn_predict_batch")
    aura_fn = get_fn(aura_mod, "knn_predict_batch")

    random.seed(42)
    n_train = 80
    X_train, y_train = [], []
    for _ in range(n_train):
        label = random.choice([0, 1])
        if label == 0:
            X_train.append([random.gauss(0, 1.2), random.gauss(0, 1.2)])
        else:
            X_train.append([random.gauss(2.5, 1.2), random.gauss(0, 1.2)])
        y_train.append(label)

    n_test = 40
    X_test, y_test = [], []
    for _ in range(n_test):
        label = random.choice([0, 1])
        if label == 0:
            X_test.append([random.gauss(0, 1.0), random.gauss(0, 1.0)])
        else:
            X_test.append([random.gauss(2.5, 1.0), random.gauss(0, 1.0)])
        y_test.append(label)

    user_preds = user_fn(X_test, X_train, y_train, k=5)
    aura_preds = aura_fn(X_test, X_train, y_train, k=5)

    user_accuracy = sum(user_preds[i] == y_test[i] for i in range(n_test)) / n_test
    aura_accuracy = sum(aura_preds[i] == y_test[i] for i in range(n_test)) / n_test

    canonical_match = approx_eq(user_accuracy, aura_accuracy, rel_tol=1e-5)

    metrics = {
        "accuracy": round(user_accuracy, 6),
        "oracle_accuracy": round(aura_accuracy, 6),
        "canonical_match": canonical_match,
    }

    ok = user_accuracy >= 0.75

    emit({
        "status": "PASS" if ok else "FAIL",
        "pattern": "knn_classifier",
        "reason": "kNN accuracy property holds" if ok else "kNN accuracy property fails",
        "metrics": metrics,
    })


if __name__ == "__main__":
    main()
