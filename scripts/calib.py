#!/usr/bin/env python3
"""Empirical calibration for perceptron + decision_tree fixtures.

Loads every relevant corpus impl, runs it on candidate data settings, prints
accuracies so we can pick a threshold that separates correct from broken.
"""
import importlib.util
import random
import sys

C = str(__import__("pathlib").Path(__file__).resolve().parents[1] / "tests" / "corpus")


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------- PERCEPTRON ----------------
def gen_perceptron(seed, spread, center):
    random.seed(seed)
    n = 40
    X, y = [], []
    for _ in range(n // 2):
        X.append([random.gauss(-center, spread), random.gauss(-center, spread)])
        y.append(0)
    for _ in range(n // 2):
        X.append([random.gauss(center, spread), random.gauss(center, spread)])
        y.append(1)
    return X, y


PERC = {
    "GOOD canonical": "perceptron_canonical_good",
    "GOOD pocket": "perceptron_pocket",
    "GOOD avg": "perceptron_avg",
    "GOOD lr0.5": "perceptron_lr0.5",
    "BROK subtract": "perceptron_broken_subtract",
    "BROK lr_tiny(all0)": "perceptron_broken_lr_tiny",
    "BROK zero_lr(all1)": "perceptron_broken_zero_lr",
    "CHEAT all_1": "perceptron_cheat_all_1",
    "EDGE random_w": "perceptron_edge_random_weights",
}


def run_perceptron(spread, center):
    X, y = gen_perceptron(42, spread, center)
    print(f"\n=== PERCEPTRON  spread={spread} center={center} ===")
    for tag, f in PERC.items():
        try:
            m = load(f"{C}/{f}.py", f"perc_{f}")
            w, b = m.perceptron_fit(X, y, learning_rate=1.0, max_iterations=50)
            preds = m.perceptron_predict(X, w, b)
            acc = sum(preds[i] == y[i] for i in range(len(y))) / len(y)
            print(f"  {tag:22} acc={acc:.3f}")
        except Exception as e:
            print(f"  {tag:22} ERROR {e}")


# ---------------- DECISION TREE ----------------
def gen_tree(seed, n, leak):
    random.seed(seed)
    X, y = [], []
    for _ in range(n):
        label = random.choice([0, 1])
        if label == 0:
            x0 = random.uniform(0, 2.5) if random.random() >= leak else random.uniform(2.5, 5)
        else:
            x0 = random.uniform(2.5, 5) if random.random() >= leak else random.uniform(0, 2.5)
        x1 = random.uniform(0, 5)  # pure noise
        X.append([x0, x1])
        y.append(label)
    return X, y


TREE = {
    "GOOD canonical": "decision_tree_id3_canonical_good",
    "GOOD gini": "tree_gini",
    "GOOD depth3": "tree_depth3",
    "GOOD depth10": "tree_depth10",
    "BROK random_split": "tree_broken_random_split",
    "BROK reversed_split": "tree_broken_reversed_split",
    "BROK majority": "tree_broken_majority",
    "CHEAT const0": "tree_cheat_const",
}


def run_tree(leak):
    # separate train/test draws from same distribution, distinct seeds
    Xtr, ytr = gen_tree(42, 120, leak)
    Xte, yte = gen_tree(1234, 60, leak)
    print(f"\n=== TREE  leak={leak}  (train=120 test=60, eval on TEST) ===")
    for tag, f in TREE.items():
        try:
            m = load(f"{C}/{f}.py", f"tree_{f}")
            tree = m.build_tree(Xtr, ytr, max_depth=5)
            preds = [m.predict_tree(Xte[i], tree) for i in range(len(Xte))]
            acc = sum(preds[i] == yte[i] for i in range(len(yte))) / len(yte)
            # also training acc for contrast
            ptr = [m.predict_tree(Xtr[i], tree) for i in range(len(Xtr))]
            tra = sum(ptr[i] == ytr[i] for i in range(len(ytr))) / len(ytr)
            print(f"  {tag:22} TEST={acc:.3f}  train={tra:.3f}")
        except Exception as e:
            print(f"  {tag:22} ERROR {e}")


if __name__ == "__main__":
    for c in (2.0, 2.5):
        for s in (0.7, 0.9, 1.1):
            run_perceptron(s, c)
    for leak in (0.08, 0.12, 0.15):
        run_tree(leak)
