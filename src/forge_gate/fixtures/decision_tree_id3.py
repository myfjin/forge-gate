#!/usr/bin/env python3
"""Gate fixture for decision_tree_id3.

PASS is decided by classification accuracy on a separable synthetic dataset,
not by matching the AURA oracle's exact tree structure.
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
        print("usage: decision_tree_id3.py <user_file.py>", file=sys.stderr)
        sys.exit(2)

    user_path = sys.argv[1]
    aura_path = str(reference_path("decision_tree_id3"))

    user_mod = load_module(user_path, "user_impl")
    aura_mod = load_module(aura_path, "aura_impl")

    user_build = get_fn(user_mod, "build_tree")
    user_predict = get_fn(user_mod, "predict_tree")
    aura_build = get_fn(aura_mod, "build_tree")
    aura_predict = get_fn(aura_mod, "predict_tree")

    random.seed(42)
    n_samples = 100
    # Balanced classes with deliberate overlap: 15% of each class leaks across
    # the nominal x0 boundary, plus an uncorrelated second feature.
    # This puts correct trees ~0.83 and broken/random trees ~0.63 on the test set.
    X, y = [], []
    leak = 0.15
    for _ in range(n_samples // 2):
        x0 = random.uniform(0, 2.5) if random.random() < (1 - leak) else random.uniform(2.5, 5)
        x1 = random.uniform(0, 5)
        X.append([x0, x1])
        y.append(0)
    for _ in range(n_samples // 2):
        x0 = random.uniform(2.5, 5) if random.random() < (1 - leak) else random.uniform(0, 2.5)
        x1 = random.uniform(0, 5)
        X.append([x0, x1])
        y.append(1)

    # Shuffle then hold out 30 samples for testing to catch overfitting trees.
    indices = list(range(n_samples))
    random.shuffle(indices)
    train_idx, test_idx = indices[:70], indices[70:]
    X_train = [X[i] for i in train_idx]
    y_train = [y[i] for i in train_idx]
    X_test = [X[i] for i in test_idx]
    y_test = [y[i] for i in test_idx]

    user_tree = user_build(X_train, y_train, max_depth=5)
    aura_tree = aura_build(X_train, y_train, max_depth=5)

    user_preds = [user_predict(X_test[i], user_tree) for i in range(len(X_test))]
    aura_preds = [aura_predict(X_test[i], aura_tree) for i in range(len(X_test))]

    user_accuracy = sum(user_preds[i] == y_test[i] for i in range(len(X_test))) / len(X_test)
    aura_accuracy = sum(aura_preds[i] == y_test[i] for i in range(len(X_test))) / len(X_test)

    canonical_match = approx_eq(user_accuracy, aura_accuracy, rel_tol=1e-5)

    metrics = {
        "accuracy": round(user_accuracy, 6),
        "oracle_accuracy": round(aura_accuracy, 6),
        "canonical_match": canonical_match,
    }

    ok = user_accuracy >= 0.75

    emit({
        "status": "PASS" if ok else "FAIL",
        "pattern": "decision_tree_id3",
        "reason": "ID3 decision tree accuracy property holds" if ok else "ID3 decision tree accuracy property fails",
        "metrics": metrics,
    })


if __name__ == "__main__":
    main()
