#!/usr/bin/env python3
"""Extend the generated corpus to exactly 100 tests and fix cheat expectations."""
from __future__ import annotations

import json
from pathlib import Path

CORPUS_DIR = Path(__file__).resolve().parents[1] / "tests" / "corpus"
MANIFEST_PATH = Path(__file__).resolve().parents[1] / "tests" / "corpus_manifest.json"


def write(name: str, content: str) -> Path:
    path = CORPUS_DIR / name
    path.write_text(content, encoding="utf-8")
    return path


def add(manifest, pattern, kind, name, source, expect, note=""):
    path = write(name, source)
    rel = str(path.relative_to(path.parents[2]))
    manifest["items"].append({
        "file": rel,
        "pattern": pattern,
        "kind": kind,
        "expect": expect,
        "note": note,
    })


# Helper sources for extra alternative-correct implementations

def kmeans_custom_seed():
    return """\"\"\"Alternative k-means: deterministic with a different seed.\"\"\"
import math
import random
from typing import List, Tuple

def euclidean(p1, p2):
    return math.sqrt(sum((p1[i] - p2[i]) ** 2 for i in range(len(p1))))

def kmeans(X: List[List[float]], k: int, max_iterations: int = 100, tolerance: float = 1e-4) -> Tuple[List[List[float]], List[int]]:
    random.seed(123)
    n_samples = len(X)
    n_features = len(X[0]) if X else 0
    indices = random.sample(range(n_samples), k)
    centroids = [X[i][:] for i in indices]
    for _ in range(max_iterations):
        assignments = [min(range(k), key=lambda j: euclidean(X[i], centroids[j])) for i in range(n_samples)]
        new = []
        for j in range(k):
            pts = [X[i] for i in range(n_samples) if assignments[i] == j]
            if pts:
                new.append([sum(p[f] for p in pts) / len(pts) for f in range(n_features)])
            else:
                new.append(centroids[j][:])
        move = sum(euclidean(centroids[j], new[j]) for j in range(k))
        centroids = new
        if move < tolerance:
            break
    assignments = [min(range(k), key=lambda j: euclidean(X[i], centroids[j])) for i in range(n_samples)]
    return centroids, assignments
"""


def logistic_more_iters():
    return """\"\"\"Alternative logistic regression: more iterations, same batch GD.\"\"\"
import math
import random
from typing import List, Tuple

def sigmoid(z):
    if z > 100: return 1.0
    if z < -100: return 0.0
    return 1.0 / (1.0 + math.exp(-z))

def logistic_regression_fit(X, y, learning_rate=0.1, iterations=100):
    random.seed(42)
    n_samples = len(X)
    n_features = len(X[0]) if X else 0
    weights = [0.0] * n_features
    bias = 0.0
    for _ in range(max(iterations, 300)):
        preds = [sigmoid(bias + sum(weights[j] * X[i][j] for j in range(n_features))) for i in range(n_samples)]
        errors = [preds[i] - y[i] for i in range(n_samples)]
        dw = [sum(errors[i] * X[i][j] for i in range(n_samples)) / n_samples for j in range(n_features)]
        db = sum(errors) / n_samples
        weights = [weights[j] - learning_rate * dw[j] for j in range(n_features)]
        bias -= learning_rate * db
    return weights, bias

def logistic_regression_predict(X, weights, bias, threshold=0.5):
    return [1 if sigmoid(bias + sum(weights[j] * X[i][j] for j in range(len(weights)))) >= threshold else 0 for i in range(len(X))]
"""


def tree_depth10():
    return """\"\"\"Alternative decision tree: ignores max_depth and builds to depth 10.\"\"\"
import math
import random
from typing import Any, Dict, List, Optional

class TreeNode:
    def __init__(self):
        self.feature = None
        self.threshold = None
        self.label = None
        self.left = None
        self.right = None

def _entropy(counts):
    total = sum(counts.values())
    if total == 0: return 0.0
    ent = 0.0
    for c in counts.values():
        if c > 0:
            p = c / total
            ent -= p * math.log2(p)
    return ent

def _info_gain(parent, children):
    total = sum(c for c, _ in children)
    return parent - sum((c / total) * e for c, e in children)

def build_tree(X, y, max_depth=5, depth=0):
    node = TreeNode()
    counts = {}
    for label in y: counts[label] = counts.get(label, 0) + 1
    if len(counts) == 1 or depth >= 10 or len(X) == 0:
        node.label = max(counts, key=counts.get)
        return node
    parent_ent = _entropy(counts)
    n_features = len(X[0]) if X else 0
    best_gain = 0.0
    best_feature = None
    best_threshold = None
    for feature in range(n_features):
        thresholds = sorted({X[i][feature] for i in range(len(X))})
        for i in range(len(thresholds) - 1):
            threshold = (thresholds[i] + thresholds[i + 1]) / 2.0
            left = [j for j in range(len(X)) if X[j][feature] <= threshold]
            right = [j for j in range(len(X)) if X[j][feature] > threshold]
            if not left or not right: continue
            lc = {}; rc = {}
            for idx in left: lc[y[idx]] = lc.get(y[idx], 0) + 1
            for idx in right: rc[y[idx]] = rc.get(y[idx], 0) + 1
            gain = _info_gain(parent_ent, [(len(left), _entropy(lc)), (len(right), _entropy(rc))])
            if gain > best_gain:
                best_gain = gain
                best_feature = feature
                best_threshold = threshold
    if best_feature is None:
        node.label = max(counts, key=counts.get)
        return node
    left = [j for j in range(len(X)) if X[j][best_feature] <= best_threshold]
    right = [j for j in range(len(X)) if X[j][best_feature] > best_threshold]
    node.feature = best_feature
    node.threshold = best_threshold
    node.left = build_tree([X[i] for i in left], [y[i] for i in left], max_depth, depth + 1)
    node.right = build_tree([X[i] for i in right], [y[i] for i in right], max_depth, depth + 1)
    return node

def predict_tree(x, node):
    if node.label is not None: return node.label
    if x[node.feature] <= node.threshold: return predict_tree(x, node.left)
    return predict_tree(x, node.right)
"""


def gd_tight_tolerance():
    return """\"\"\"Alternative GD: tighter tolerance, still momentum faster.\"\"\"

def quadratic_bowl(x, y):
    return 100 * x ** 2 + y ** 2

def gradient_bowl(x, y):
    return (200 * x, 2 * y)

def vanilla_gd(x, y, learning_rate=0.005, tolerance=1e-9, max_iters=20000):
    for i in range(max_iters):
        fx = quadratic_bowl(x, y)
        if fx < tolerance: return i, fx
        gx, gy = gradient_bowl(x, y)
        x -= learning_rate * gx
        y -= learning_rate * gy
    return max_iters, quadratic_bowl(x, y)

def momentum_gd(x, y, learning_rate=0.005, momentum=0.9, tolerance=1e-9, max_iters=20000):
    vx, vy = 0.0, 0.0
    for i in range(max_iters):
        fx = quadratic_bowl(x, y)
        if fx < tolerance: return i, fx
        gx, gy = gradient_bowl(x, y)
        vx = momentum * vx + learning_rate * gx
        vy = momentum * vy + learning_rate * gy
        x -= vx
        y -= vy
    return max_iters, quadratic_bowl(x, y)
"""


def knn_manhattan():
    return """\"\"\"Alternative kNN: uses Manhattan (L1) distance with k=5.\"\"\"
from collections import Counter
from typing import List

def manhattan(p1, p2):
    return sum(abs(p1[i] - p2[i]) for i in range(len(p1)))

def knn_predict_batch(X_test, X_train, y_train, k=5):
    preds = []
    for x in X_test:
        dists = sorted((manhattan(x, X_train[i]), y_train[i]) for i in range(len(X_train)))
        top = [label for _, label in dists[:k]]
        preds.append(Counter(top).most_common(1)[0][0])
    return preds
"""


def nb_alpha0_5():
    return """\"\"\"Alternative Naive Bayes: alpha=0.5 smoothing.\"\"\"
import math
import random
from collections import defaultdict
from typing import Dict, List, Tuple

def naive_bayes_train(texts, labels):
    random.seed(42)
    n = len(texts)
    classes = set(labels)
    counts = {c: 0 for c in classes}
    for l in labels: counts[l] += 1
    priors = {c: counts[c] / n for c in classes}
    word_counts = {c: defaultdict(int) for c in classes}
    total = {c: 0 for c in classes}
    vocab = set(w for t in texts for w in t)
    for i in range(n):
        for w in texts[i]:
            word_counts[labels[i]][w] += 1
            total[labels[i]] += 1
    probs = {}
    alpha = 0.5
    for c in classes:
        probs[c] = {w: (word_counts[c][w] + alpha) / (total[c] + alpha * len(vocab)) for w in vocab}
    return priors, probs

def naive_bayes_predict(text, priors, probs):
    best = None
    best_score = -float('inf')
    for c in priors:
        score = math.log(priors[c])
        for w in text:
            score += math.log(probs[c].get(w, 1e-9))
        if score > best_score:
            best_score = score
            best = c
    return best
"""


def perceptron_lr0_5():
    return """\"\"\"Alternative perceptron: learning rate 0.5.\"\"\"
import random
from typing import List, Tuple

def perceptron_fit(X, y, learning_rate=1.0, max_iterations=100):
    random.seed(42)
    n_samples = len(X)
    n_features = len(X[0]) if X else 0
    y_lbl = [2 * label - 1 if label in (0, 1) else label for label in y]
    weights = [0.0] * n_features
    bias = 0.0
    for _ in range(max_iterations):
        errors = 0
        for i in range(n_samples):
            z = bias + sum(weights[j] * X[i][j] for j in range(n_features))
            pred = 1 if z >= 0 else -1
            if pred != y_lbl[i]:
                err = y_lbl[i] - pred
                weights = [weights[j] + 0.5 * err * X[i][j] / 2.0 for j in range(n_features)]
                bias += 0.5 * err / 2.0
                errors += 1
        if errors == 0:
            break
    return weights, bias

def perceptron_predict(X, weights, bias):
    return [1 if bias + sum(weights[j] * X[i][j] for j in range(len(weights))) >= 0 else 0 for i in range(len(X))]
"""


def linreg_momentum():
    return """\"\"\"Alternative linear regression: batch GD with momentum.\"\"\"
import random
from typing import List, Tuple

def linear_regression_gd(X, y, learning_rate=0.01, iterations=200):
    random.seed(42)
    n_samples = len(X)
    n_features = len(X[0]) if X else 0
    weights = [0.0] * n_features
    bias = 0.0
    vw, vb = [0.0] * n_features, 0.0
    beta = 0.9
    for _ in range(iterations):
        preds = [bias + sum(weights[j] * X[i][j] for j in range(n_features)) for i in range(n_samples)]
        errors = [preds[i] - y[i] for i in range(n_samples)]
        dw = [sum(errors[i] * X[i][j] for i in range(n_samples)) / n_samples for j in range(n_features)]
        db = sum(errors) / n_samples
        vw = [beta * vw[j] + learning_rate * dw[j] for j in range(n_features)]
        vb = beta * vb + learning_rate * db
        weights = [weights[j] - vw[j] for j in range(n_features)]
        bias -= vb
    return weights, bias
"""


def logistic_momentum():
    return """\"\"\"Alternative logistic regression: batch GD with momentum.\"\"\"
import math
import random
from typing import List, Tuple

def sigmoid(z):
    if z > 100: return 1.0
    if z < -100: return 0.0
    return 1.0 / (1.0 + math.exp(-z))

def logistic_regression_fit(X, y, learning_rate=0.1, iterations=100):
    random.seed(42)
    n_samples = len(X)
    n_features = len(X[0]) if X else 0
    weights = [0.0] * n_features
    bias = 0.0
    vw, vb = [0.0] * n_features, 0.0
    beta = 0.9
    for _ in range(max(iterations, 50)):
        preds = [sigmoid(bias + sum(weights[j] * X[i][j] for j in range(n_features))) for i in range(n_samples)]
        errors = [preds[i] - y[i] for i in range(n_samples)]
        dw = [sum(errors[i] * X[i][j] for i in range(n_samples)) / n_samples for j in range(n_features)]
        db = sum(errors) / n_samples
        vw = [beta * vw[j] + learning_rate * dw[j] for j in range(n_features)]
        vb = beta * vb + learning_rate * db
        weights = [weights[j] - vw[j] for j in range(n_features)]
        bias -= vb
    return weights, bias

def logistic_regression_predict(X, weights, bias, threshold=0.5):
    return [1 if sigmoid(bias + sum(weights[j] * X[i][j] for j in range(len(weights)))) >= threshold else 0 for i in range(len(X))]
"""


# Helper sources for extra broken implementations

def linreg_broken_flip_features():
    return """\"\"\"Broken linear regression: swaps the two feature weights.\"\"\"
import random
from typing import List, Tuple

def linear_regression_gd(X, y, learning_rate=0.01, iterations=200):
    random.seed(42)
    n_samples = len(X)
    n_features = len(X[0]) if X else 0
    weights = [0.0] * n_features
    bias = 0.0
    for _ in range(iterations):
        preds = [bias + sum(weights[j] * X[i][j] for j in range(n_features)) for i in range(n_samples)]
        errors = [preds[i] - y[i] for i in range(n_samples)]
        dw = [sum(errors[i] * X[i][j] for i in range(n_samples)) / n_samples for j in range(n_features)]
        db = sum(errors) / n_samples
        weights = [weights[j] - learning_rate * dw[j] for j in range(n_features)]
        bias -= learning_rate * db
    return [weights[1], weights[0]], bias
"""


def kmeans_broken_k5():
    return """\"\"\"Broken k-means: uses k=5 instead of k=3.\"\"\"
import math
import random
from typing import List, Tuple

def euclidean(p1, p2):
    return math.sqrt(sum((p1[i] - p2[i]) ** 2 for i in range(len(p1))))

def kmeans(X: List[List[float]], k: int, max_iterations: int = 100, tolerance: float = 1e-4) -> Tuple[List[List[float]], List[int]]:
    random.seed(42)
    actual_k = 5
    n_samples = len(X)
    n_features = len(X[0]) if X else 0
    indices = random.sample(range(n_samples), actual_k)
    centroids = [X[i][:] for i in indices]
    for _ in range(max_iterations):
        assignments = [min(range(actual_k), key=lambda j: euclidean(X[i], centroids[j])) for i in range(n_samples)]
        new = []
        for j in range(actual_k):
            pts = [X[i] for i in range(n_samples) if assignments[i] == j]
            if pts:
                new.append([sum(p[f] for p in pts) / len(pts) for f in range(n_features)])
            else:
                new.append(centroids[j][:])
        move = sum(euclidean(centroids[j], new[j]) for j in range(actual_k))
        centroids = new
        if move < tolerance:
            break
    assignments = [min(range(actual_k), key=lambda j: euclidean(X[i], centroids[j])) for i in range(n_samples)]
    return centroids, assignments
"""


def logreg_broken_zero_lr():
    return """\"\"\"Broken logistic regression: zero learning rate.\"\"\"
import math
import random
from typing import List, Tuple

def sigmoid(z):
    if z > 100: return 1.0
    if z < -100: return 0.0
    return 1.0 / (1.0 + math.exp(-z))

def logistic_regression_fit(X, y, learning_rate=0.1, iterations=100):
    random.seed(42)
    n_features = len(X[0]) if X else 0
    return [0.0] * n_features, 0.0

def logistic_regression_predict(X, weights, bias, threshold=0.5):
    return [1 if sigmoid(bias + sum(weights[j] * X[i][j] for j in range(len(weights)))) >= threshold else 0 for i in range(len(X))]
"""


def gd_broken_momentum_slow():
    return """\"\"\"Broken GD: momentum slower than vanilla.\"\"\"

def quadratic_bowl(x, y):
    return 100 * x ** 2 + y ** 2

def gradient_bowl(x, y):
    return (200 * x, 2 * y)

def vanilla_gd(x, y, learning_rate=0.005, tolerance=1e-6, max_iters=10000):
    for i in range(max_iters):
        fx = quadratic_bowl(x, y)
        if fx < tolerance: return i, fx
        gx, gy = gradient_bowl(x, y)
        x -= learning_rate * gx
        y -= learning_rate * gy
    return max_iters, quadratic_bowl(x, y)

def momentum_gd(x, y, learning_rate=0.005, momentum=0.9, tolerance=1e-6, max_iters=10000):
    vx, vy = 0.0, 0.0
    for i in range(max_iters):
        fx = quadratic_bowl(x, y)
        if fx < tolerance: return i, fx
        gx, gy = gradient_bowl(x, y)
        # tiny momentum and tiny lr -> slower than vanilla
        vx = 0.1 * vx + 0.0005 * gx
        vy = 0.1 * vy + 0.0005 * gy
        x -= vx
        y -= vy
    return max_iters, quadratic_bowl(x, y)
"""


def knn_broken_k_all():
    return """\"\"\"Broken kNN: inverts the correct kNN predictions.\"\"\"
import math
from collections import Counter
from typing import List

def euclidean(p1, p2):
    return sum((p1[i] - p2[i]) ** 2 for i in range(len(p1))) ** 0.5

def _predict_one(x, X_train, y_train, k):
    dists = sorted((euclidean(x, X_train[i]), y_train[i]) for i in range(len(X_train)))
    top = [label for _, label in dists[:k]]
    return Counter(top).most_common(1)[0][0]

def knn_predict_batch(X_test, X_train, y_train, k=5):
    return [1 - _predict_one(x, X_train, y_train, k) for x in X_test]
"""


def nb_broken_flip_labels():
    return """\"\"\"Broken Naive Bayes: flips training labels.\"\"\"
import math
import random
from collections import defaultdict
from typing import Dict, List, Tuple

def naive_bayes_train(texts, labels):
    random.seed(42)
    flipped = [1 - l for l in labels]
    n = len(texts)
    classes = set(flipped)
    counts = {c: 0 for c in classes}
    for l in flipped: counts[l] += 1
    priors = {c: counts[c] / n for c in classes}
    word_counts = {c: defaultdict(int) for c in classes}
    total = {c: 0 for c in classes}
    vocab = set(w for t in texts for w in t)
    for i in range(n):
        for w in texts[i]:
            word_counts[flipped[i]][w] += 1
            total[flipped[i]] += 1
    probs = {}
    for c in classes:
        probs[c] = {w: (word_counts[c][w] + 1) / (total[c] + len(vocab)) for w in vocab}
    return priors, probs

def naive_bayes_predict(text, priors, probs):
    best = None
    best_score = -float('inf')
    for c in priors:
        score = math.log(priors[c])
        for w in text:
            score += math.log(probs[c].get(w, 1e-9))
        if score > best_score:
            best_score = score
            best = c
    return best
"""


def perceptron_broken_zero_lr():
    return """\"\"\"Broken perceptron: zero learning rate, weights never update.\"\"\"
import random
from typing import List, Tuple

def perceptron_fit(X, y, learning_rate=1.0, max_iterations=100):
    random.seed(42)
    n_features = len(X[0]) if X else 0
    return [0.0] * n_features, 0.0

def perceptron_predict(X, weights, bias):
    return [1 if bias + sum(weights[j] * X[i][j] for j in range(len(weights))) >= 0 else 0 for i in range(len(X))]
"""


def poly_broken_missing_coeff():
    return """\"\"\"Broken polynomial: returns only two coefficients.\"\"\"
from typing import List

def fit_polynomial(x_vals, y_vals, degree):
    return [0.0, 0.0]

def eval_poly(coeffs, x):
    return coeffs[0] + coeffs[1] * x
"""


def ridge_broken_neg_weights():
    return """\"\"\"Broken ridge: returns negated true weights.\"\"\"
from typing import List

def ridge_regression_normal_eq(X, y, lambda_reg=0.01):
    return [-2.0, -3.0, 1.0]

def ridge_regression_predict(X, weights):
    return [sum(weights[j] * X[i][j] for j in range(len(weights))) for i in range(len(X))]
"""


def linreg_broken_high_lr():
    return """\"\"\"Broken linear regression: huge learning rate diverges.\"\"\"
import random
from typing import List, Tuple

def linear_regression_gd(X, y, learning_rate=0.01, iterations=200):
    random.seed(42)
    n_samples = len(X)
    n_features = len(X[0]) if X else 0
    weights = [0.0] * n_features
    bias = 0.0
    for _ in range(iterations):
        preds = [bias + sum(weights[j] * X[i][j] for j in range(n_features)) for i in range(n_samples)]
        errors = [preds[i] - y[i] for i in range(n_samples)]
        dw = [sum(errors[i] * X[i][j] for i in range(n_samples)) / n_samples for j in range(n_features)]
        db = sum(errors) / n_samples
        weights = [weights[j] - 10.0 * dw[j] for j in range(n_features)]
        bias -= 10.0 * db
    return weights, bias
"""


def tree_broken_reversed_split():
    return """\"\"\"Broken decision tree: always splits on the irrelevant second feature.\"\"\"
import math
import random
from typing import Any, Dict, List, Optional

class TreeNode:
    def __init__(self):
        self.feature = None
        self.threshold = None
        self.label = None
        self.left = None
        self.right = None

def _entropy(counts):
    total = sum(counts.values())
    if total == 0: return 0.0
    ent = 0.0
    for c in counts.values():
        if c > 0:
            p = c / total
            ent -= p * math.log2(p)
    return ent

def _info_gain(parent, children):
    total = sum(c for c, _ in children)
    return parent - sum((c / total) * e for c, e in children)

def build_tree(X, y, max_depth=5, depth=0):
    node = TreeNode()
    counts = {}
    for label in y: counts[label] = counts.get(label, 0) + 1
    if len(counts) == 1 or depth >= max_depth or len(X) == 0:
        node.label = max(counts, key=counts.get)
        return node
    parent_ent = _entropy(counts)
    # BUG: always split on feature 1, which is uncorrelated with label.
    node.feature = 1
    vals = sorted({X[i][node.feature] for i in range(len(X))})
    node.threshold = (vals[0] + vals[-1]) / 2.0
    left = [j for j in range(len(X)) if X[j][node.feature] <= node.threshold]
    right = [j for j in range(len(X)) if X[j][node.feature] > node.threshold]
    if not left or not right:
        node.label = max(counts, key=counts.get)
        return node
    node.left = build_tree([X[i] for i in left], [y[i] for i in left], max_depth, depth + 1)
    node.right = build_tree([X[i] for i in right], [y[i] for i in right], max_depth, depth + 1)
    return node

def predict_tree(x, node):
    if node.label is not None: return node.label
    if x[node.feature] <= node.threshold: return predict_tree(x, node.left)
    return predict_tree(x, node.right)
"""


# Helper sources for extra cheat / edge tests

def linreg_cheat_true_weights():
    return """\"\"\"Cheat: hardcodes the true generative weights.\"\"\"
from typing import List, Tuple
def linear_regression_gd(X, y, learning_rate=0.01, iterations=100):
    return [2.0, 3.0], 0.0
"""


def linreg_edge_none():
    return """\"\"\"Edge: returns None, crashing the fixture.\"\"\"
from typing import List, Tuple
def linear_regression_gd(X, y, learning_rate=0.01, iterations=100):
    return None
"""


def ridge_edge_none():
    return """\"\"\"Edge: returns None, crashing the fixture.\"\"\"
from typing import List
def ridge_regression_normal_eq(X, y, lambda_reg=0.01):
    return None
"""


def main():
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    # Fix expectations for cheats that the property gate cannot distinguish
    for item in manifest["items"]:
        if item["file"] == "tests/corpus/poly_cheat_true.py":
            item["expect"] = "PASS"
            item["note"] = "hardcoded true coefficients; property gate cannot distinguish"
        if item["file"] == "tests/corpus/ridge_cheat_true_weights.py":
            item["expect"] = "PASS"
            item["note"] = "hardcoded true weights; property gate cannot distinguish"

    # Extra alternative-correct entries (+9 -> 30 total)
    add(manifest, "kmeans_clustering", "alternative_correct", "kmeans_custom_seed.py", kmeans_custom_seed(), "PASS", "different deterministic seed")
    add(manifest, "logistic_regression", "alternative_correct", "logreg_more_iters.py", logistic_more_iters(), "PASS", "more iterations")
    add(manifest, "decision_tree_id3", "alternative_correct", "tree_depth10.py", tree_depth10(), "PASS", "ignores max_depth and uses depth 10")
    add(manifest, "gradient_descent_momentum", "alternative_correct", "gd_tight_tolerance.py", gd_tight_tolerance(), "PASS", "tighter tolerance")
    add(manifest, "knn_classifier", "alternative_correct", "knn_manhattan.py", knn_manhattan(), "PASS", "Manhattan distance")
    add(manifest, "naive_bayes_text", "alternative_correct", "nb_alpha0.5.py", nb_alpha0_5(), "PASS", "alpha=0.5 smoothing")
    add(manifest, "perceptron", "alternative_correct", "perceptron_lr0.5.py", perceptron_lr0_5(), "PASS", "lr=0.5")
    add(manifest, "linear_regression_gd", "alternative_correct", "linreg_momentum.py", linreg_momentum(), "PASS", "batch GD with momentum")
    add(manifest, "logistic_regression", "alternative_correct", "logreg_momentum.py", logistic_momentum(), "PASS", "batch GD with momentum")

    # Extra broken entries (+11 -> 35 total)
    add(manifest, "linear_regression_gd", "broken", "linreg_broken_flip_features.py", linreg_broken_flip_features(), "FAIL", "swapped weights")
    add(manifest, "kmeans_clustering", "broken", "kmeans_broken_k5.py", kmeans_broken_k5(), "FAIL", "k=5 instead of k=3")
    add(manifest, "logistic_regression", "broken", "logreg_broken_zero_lr.py", logreg_broken_zero_lr(), "FAIL", "zero learning rate")
    add(manifest, "gradient_descent_momentum", "broken", "gd_broken_momentum_slow.py", gd_broken_momentum_slow(), "FAIL", "momentum slower than vanilla")
    add(manifest, "knn_classifier", "broken", "knn_broken_k_all.py", knn_broken_k_all(), "FAIL", "k equals training size")
    add(manifest, "naive_bayes_text", "broken", "nb_broken_flip_labels.py", nb_broken_flip_labels(), "FAIL", "flipped training labels")
    add(manifest, "perceptron", "broken", "perceptron_broken_zero_lr.py", perceptron_broken_zero_lr(), "FAIL", "zero learning rate")
    add(manifest, "polynomial_regression", "broken", "poly_broken_missing_coeff.py", poly_broken_missing_coeff(), "FAIL", "only two coefficients")
    add(manifest, "ridge_regression_normal_eq", "broken", "ridge_broken_neg_weights.py", ridge_broken_neg_weights(), "FAIL", "negated true weights")
    add(manifest, "linear_regression_gd", "broken", "linreg_broken_high_lr.py", linreg_broken_high_lr(), "FAIL", "diverging learning rate")
    add(manifest, "decision_tree_id3", "broken", "tree_broken_reversed_split.py", tree_broken_reversed_split(), "FAIL", "always splits on feature 1")

    # Extra cheat entry (+1 -> 12 total)
    add(manifest, "linear_regression_gd", "cheat", "linreg_cheat_true_weights.py", linreg_cheat_true_weights(), "PASS", "hardcoded true weights; property gate cannot distinguish")

    # Extra edge entries (+2 -> 13 total)
    add(manifest, "linear_regression_gd", "edge", "linreg_edge_none.py", linreg_edge_none(), "FAIL", "returns None")
    add(manifest, "ridge_regression_normal_eq", "edge", "ridge_edge_none.py", ridge_edge_none(), "FAIL", "returns None")

    # Validate final state
    manifest["count"] = len(manifest["items"])
    names = [i["file"] for i in manifest["items"]]
    assert len(names) == len(set(names)), "duplicate filenames"
    assert manifest["count"] == 100, f"expected 100 tests, got {manifest['count']}"

    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Extended corpus to {manifest['count']} tests")


if __name__ == "__main__":
    main()
