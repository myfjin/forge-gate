#!/usr/bin/env python3
"""Generate the 100-test diverse corpus for Forge Gate property validation."""
from __future__ import annotations

import json
import os
from pathlib import Path

CORPUS_DIR = Path(__file__).resolve().parents[1] / "tests" / "corpus"
MANIFEST_PATH = Path(__file__).resolve().parents[1] / "tests" / "corpus_manifest.json"


def write(name: str, content: str) -> Path:
    path = CORPUS_DIR / name
    path.write_text(content, encoding="utf-8")
    return path


def linreg_normal_eq():
    return """\"\"\"Alternative correct linear regression via normal equations.\"\"\"
import random
from typing import List, Tuple

def _mean(v: List[float]) -> float:
    return sum(v) / len(v) if v else 0.0

def linear_regression_gd(
    X: List[List[float]], y: List[float], learning_rate: float = 0.01, iterations: int = 100
) -> Tuple[List[float], float]:
    random.seed(42)
    n_samples = len(X)
    n_features = len(X[0]) if X else 0
    X_b = [[1.0] + row for row in X]
    XtX = [[sum(X_b[i][j] * X_b[i][k] for i in range(n_samples)) for k in range(n_features + 1)] for j in range(n_features + 1)]
    Xty = [sum(X_b[i][j] * y[i] for i in range(n_samples)) for j in range(n_features + 1)]
    # Gaussian elimination
    n = len(XtX)
    aug = [XtX[i][:] + [Xty[i]] for i in range(n)]
    for col in range(n):
        max_row = col
        for r in range(col + 1, n):
            if abs(aug[r][col]) > abs(aug[max_row][col]):
                max_row = r
        aug[col], aug[max_row] = aug[max_row], aug[col]
        for r in range(col + 1, n):
            factor = aug[r][col] / aug[col][col]
            for c in range(col, n + 1):
                aug[r][c] -= factor * aug[col][c]
    beta = [0.0] * n
    for i in range(n - 1, -1, -1):
        beta[i] = aug[i][n]
        for j in range(i + 1, n):
            beta[i] -= aug[i][j] * beta[j]
        beta[i] /= aug[i][i]
    return beta[1:], beta[0]
"""


def linreg_sgd():
    return """\"\"\"Alternative correct linear regression via stochastic gradient descent.\"\"\"
import random
from typing import List, Tuple

def linear_regression_gd(
    X: List[List[float]], y: List[float], learning_rate: float = 0.01, iterations: int = 100
) -> Tuple[List[float], float]:
    random.seed(42)
    n_samples = len(X)
    n_features = len(X[0]) if X else 0
    weights = [0.0] * n_features
    bias = 0.0
    indices = list(range(n_samples))
    for _ in range(iterations):
        random.shuffle(indices)
        for i in indices:
            pred = bias + sum(weights[j] * X[i][j] for j in range(n_features))
            error = pred - y[i]
            bias -= learning_rate * error
            for j in range(n_features):
                weights[j] -= learning_rate * error * X[i][j]
    return weights, bias
"""


def linreg_higher_lr():
    return """\"\"\"Correct linreg using batch GD with a higher learning rate but fewer iterations.\"\"\"
import random
from typing import List, Tuple

def _mean(v: List[float]) -> float:
    return sum(v) / len(v) if v else 0.0

def linear_regression_gd(
    X: List[List[float]], y: List[float], learning_rate: float = 0.01, iterations: int = 100
) -> Tuple[List[float], float]:
    random.seed(42)
    n_samples = len(X)
    n_features = len(X[0]) if X else 0
    weights = [0.0] * n_features
    bias = 0.0
    for _ in range(max(50, iterations // 4)):
        preds = [bias + sum(weights[j] * X[i][j] for j in range(n_features)) for i in range(n_samples)]
        errors = [preds[i] - y[i] for i in range(n_samples)]
        dw = [sum(errors[i] * X[i][j] for i in range(n_samples)) / n_samples for j in range(n_features)]
        db = sum(errors) / n_samples
        weights = [weights[j] - 0.05 * dw[j] for j in range(n_features)]
        bias -= 0.05 * db
    return weights, bias
"""


def linreg_broken_sign():
    return """\"\"\"Broken linreg: gradient ascent.\"\"\"
import random
from typing import List, Tuple

def linear_regression_gd(
    X: List[List[float]], y: List[float], learning_rate: float = 0.01, iterations: int = 100
) -> Tuple[List[float], float]:
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
        weights = [weights[j] + learning_rate * dw[j] for j in range(n_features)]
        bias += learning_rate * db
    return weights, bias
"""


def linreg_broken_one_iter():
    return """\"\"\"Broken linreg: only one iteration, no convergence.\"\"\"
import random
from typing import List, Tuple

def linear_regression_gd(
    X: List[List[float]], y: List[float], learning_rate: float = 0.01, iterations: int = 100
) -> Tuple[List[float], float]:
    random.seed(42)
    n_samples = len(X)
    n_features = len(X[0]) if X else 0
    weights = [0.0] * n_features
    bias = 0.0
    preds = [bias + sum(weights[j] * X[i][j] for j in range(n_features)) for i in range(n_samples)]
    errors = [preds[i] - y[i] for i in range(n_samples)]
    dw = [sum(errors[i] * X[i][j] for i in range(n_samples)) / n_samples for j in range(n_features)]
    db = sum(errors) / n_samples
    weights = [weights[j] - learning_rate * dw[j] for j in range(n_features)]
    bias -= learning_rate * db
    return weights, bias
"""


def linreg_broken_zero_lr():
    return """\"\"\"Broken linreg: zero learning rate leaves weights at zero.\"\"\"
import random
from typing import List, Tuple

def linear_regression_gd(
    X: List[List[float]], y: List[float], learning_rate: float = 0.01, iterations: int = 100
) -> Tuple[List[float], float]:
    random.seed(42)
    n_features = len(X[0]) if X else 0
    return [0.0] * n_features, 0.0
"""


def linreg_cheat_const():
    return """\"\"\"Cheat: returns constant unrelated values.\"\"\"
from typing import List, Tuple
def linear_regression_gd(X, y, learning_rate=0.01, iterations=100) -> Tuple[List[float], float]:
    return [999.0, -999.0], 999.0
"""


def linreg_cheat_hardcoded_oracle():
    return """\"\"\"Cheat: hardcodes the known oracle output (allowed by disclosed limitation).\"\"\"
from typing import List, Tuple
def linear_regression_gd(X, y, learning_rate=0.01, iterations=100) -> Tuple[List[float], float]:
    return [2.02429, 2.984377], -0.067942
"""


def linreg_edge_syntax():
    return """\"\"\"Edge: syntax error.\"\"\"
def linear_regression_gd(X, y, learning_rate=0.01, iterations=100):
    return [0.0  # missing bracket
"""


def linreg_edge_nan():
    return """\"\"\"Edge: returns NaN weights.\"\"\"
import math
from typing import List, Tuple
def linear_regression_gd(X, y, learning_rate=0.01, iterations=100) -> Tuple[List[float], float]:
    return [float('nan')] * 2, float('nan')
"""


def kmeans_alt_init():
    return """\"\"\"Alternative k-means: deterministic k-means++ style init.\"\"\"
import math
import random
from typing import List, Tuple

def euclidean(p1, p2):
    return math.sqrt(sum((p1[i] - p2[i]) ** 2 for i in range(len(p1))))

def kmeans(X: List[List[float]], k: int, max_iterations: int = 100, tolerance: float = 1e-4) -> Tuple[List[List[float]], List[int]]:
    random.seed(42)
    n_samples = len(X)
    n_features = len(X[0]) if X else 0
    # Pick first center randomly deterministically, then farthest remaining point as rest.
    indices = list(range(n_samples))
    centroids = [X[indices[0]][:]]
    while len(centroids) < k:
        dists = [min(euclidean(x, c) for c in centroids) for x in X]
        idx = dists.index(max(dists))
        centroids.append(X[idx][:])
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


def kmeans_alt_median():
    return """\"\"\"Alternative k-means: standard algorithm with a different seed.\"\"\"
import math
import random
from typing import List, Tuple

def euclidean(p1, p2):
    return math.sqrt(sum((p1[i] - p2[i]) ** 2 for i in range(len(p1))))

def kmeans(X: List[List[float]], k: int, max_iterations: int = 100, tolerance: float = 1e-4) -> Tuple[List[List[float]], List[int]]:
    random.seed(7)
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


def kmeans_broken_farthest():
    return """\"\"\"Broken k-means: assigns to farthest centroid.\"\"\"
import math
import random
from typing import List, Tuple

def euclidean(p1, p2):
    return math.sqrt(sum((p1[i] - p2[i]) ** 2 for i in range(len(p1))))

def kmeans(X: List[List[float]], k: int, max_iterations: int = 100, tolerance: float = 1e-4) -> Tuple[List[List[float]], List[int]]:
    random.seed(42)
    n_samples = len(X)
    n_features = len(X[0]) if X else 0
    indices = random.sample(range(n_samples), k)
    centroids = [X[i][:] for i in indices]
    for _ in range(max_iterations):
        assignments = [max(range(k), key=lambda j: euclidean(X[i], centroids[j])) for i in range(n_samples)]
        new = []
        for j in range(k):
            pts = [X[i] for i in range(n_samples) if assignments[i] == j]
            if pts:
                new.append([sum(p[f] for p in pts) / len(pts) for f in range(n_features)])
            else:
                new.append(centroids[j][:])
        centroids = new
    assignments = [max(range(k), key=lambda j: euclidean(X[i], centroids[j])) for i in range(n_samples)]
    return centroids, assignments
"""


def kmeans_broken_no_update():
    return """\"\"\"Broken k-means: never updates centroids after init.\"\"\"
import math
import random
from typing import List, Tuple

def euclidean(p1, p2):
    return math.sqrt(sum((p1[i] - p2[i]) ** 2 for i in range(len(p1))))

def kmeans(X: List[List[float]], k: int, max_iterations: int = 100, tolerance: float = 1e-4) -> Tuple[List[List[float]], List[int]]:
    random.seed(42)
    n_samples = len(X)
    indices = random.sample(range(n_samples), k)
    centroids = [X[i][:] for i in indices]
    assignments = [min(range(k), key=lambda j: euclidean(X[i], centroids[j])) for i in range(n_samples)]
    return centroids, assignments
"""


def kmeans_broken_k2():
    return """\"\"\"Broken k-means: requests k=2 instead of k=3.\"\"\"
import math
import random
from typing import List, Tuple

def euclidean(p1, p2):
    return math.sqrt(sum((p1[i] - p2[i]) ** 2 for i in range(len(p1))))

def kmeans(X: List[List[float]], k: int, max_iterations: int = 100, tolerance: float = 1e-4) -> Tuple[List[List[float]], List[int]]:
    random.seed(42)
    actual_k = 2
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
        centroids = new
    assignments = [min(range(actual_k), key=lambda j: euclidean(X[i], centroids[j])) for i in range(n_samples)]
    return centroids, assignments
"""


def kmeans_cheat_random_assign():
    return """\"\"\"Cheat: random assignments.\"\"\"
import random
from typing import List, Tuple
def kmeans(X, k, max_iterations=100, tolerance=1e-4) -> Tuple[List[List[float]], List[int]]:
    random.seed(42)
    return [[0.0, 0.0]] * k, [random.randrange(k) for _ in X]
"""


def kmeans_edge_empty():
    return """\"\"\"Edge: returns empty centroids.\"\"\"
from typing import List, Tuple
def kmeans(X, k, max_iterations=100, tolerance=1e-4) -> Tuple[List[List[float]], List[int]]:
    return [], []
"""


def logreg_alt_newtonish():
    return """\"\"\"Alternative logistic regression: Newton-like update with Hessian approximation.\"\"\"
import math
import random
from typing import List, Tuple

def sigmoid(z):
    if z > 100: return 1.0
    if z < -100: return 0.0
    return 1.0 / (1.0 + math.exp(-z))

def logistic_regression_fit(X: List[List[float]], y: List[int], learning_rate: float = 0.01, iterations: int = 100) -> Tuple[List[float], float]:
    random.seed(42)
    n_samples = len(X)
    n_features = len(X[0]) if X else 0
    weights = [0.0] * n_features
    bias = 0.0
    for _ in range(min(iterations, 20)):
        grad_w = [0.0] * n_features
        grad_b = 0.0
        for i in range(n_samples):
            z = bias + sum(weights[j] * X[i][j] for j in range(n_features))
            pred = sigmoid(z)
            error = pred - y[i]
            grad_b += error
            for j in range(n_features):
                grad_w[j] += error * X[i][j]
        grad_b /= n_samples
        grad_w = [g / n_samples for g in grad_w]
        # Newton-ish larger step
        step = 2.0
        weights = [weights[j] - step * grad_w[j] for j in range(n_features)]
        bias -= step * grad_b
    return weights, bias

def logistic_regression_predict(X: List[List[float]], weights: List[float], bias: float, threshold: float = 0.5) -> List[int]:
    preds = []
    for i in range(len(X)):
        z = bias + sum(weights[j] * X[i][j] for j in range(len(weights)))
        preds.append(1 if sigmoid(z) >= threshold else 0)
    return preds
"""


def logreg_alt_high_lr():
    return """\"\"\"Alternative logistic regression: higher learning rate, fewer iterations.\"\"\"
import math
import random
from typing import List, Tuple

def sigmoid(z):
    if z > 100: return 1.0
    if z < -100: return 0.0
    return 1.0 / (1.0 + math.exp(-z))

def logistic_regression_fit(X, y, learning_rate=0.01, iterations=100):
    random.seed(42)
    n_samples = len(X)
    n_features = len(X[0]) if X else 0
    weights = [0.0] * n_features
    bias = 0.0
    for _ in range(max(20, iterations // 5)):
        preds = [sigmoid(bias + sum(weights[j] * X[i][j] for j in range(n_features))) for i in range(n_samples)]
        errors = [preds[i] - y[i] for i in range(n_samples)]
        dw = [sum(errors[i] * X[i][j] for i in range(n_samples)) / n_samples for j in range(n_features)]
        db = sum(errors) / n_samples
        weights = [weights[j] - 0.5 * dw[j] for j in range(n_features)]
        bias -= 0.5 * db
    return weights, bias

def logistic_regression_predict(X, weights, bias, threshold=0.5):
    return [1 if sigmoid(bias + sum(weights[j] * X[i][j] for j in range(len(weights)))) >= threshold else 0 for i in range(len(X))]
"""


def logreg_broken_sign():
    return """\"\"\"Broken logistic regression: gradient sign flipped.\"\"\"
import math
import random
from typing import List, Tuple

def sigmoid(z):
    if z > 100: return 1.0
    if z < -100: return 0.0
    return 1.0 / (1.0 + math.exp(-z))

def logistic_regression_fit(X, y, learning_rate=0.01, iterations=100):
    random.seed(42)
    n_samples = len(X)
    n_features = len(X[0]) if X else 0
    weights = [0.0] * n_features
    bias = 0.0
    for _ in range(iterations):
        preds = [sigmoid(bias + sum(weights[j] * X[i][j] for j in range(n_features))) for i in range(n_samples)]
        errors = [preds[i] - y[i] for i in range(n_samples)]
        dw = [sum(errors[i] * X[i][j] for i in range(n_samples)) / n_samples for j in range(n_features)]
        db = sum(errors) / n_samples
        weights = [weights[j] + learning_rate * dw[j] for j in range(n_features)]
        bias += learning_rate * db
    return weights, bias

def logistic_regression_predict(X, weights, bias, threshold=0.5):
    return [1 if sigmoid(bias + sum(weights[j] * X[i][j] for j in range(len(weights)))) >= threshold else 0 for i in range(len(X))]
"""


def logreg_broken_one_iter():
    return """\"\"\"Broken logistic regression: zero iterations, weights stay zero.\"\"\"
import math
import random
from typing import List, Tuple

def sigmoid(z):
    if z > 100: return 1.0
    if z < -100: return 0.0
    return 1.0 / (1.0 + math.exp(-z))

def logistic_regression_fit(X, y, learning_rate=0.01, iterations=100):
    random.seed(42)
    n_features = len(X[0]) if X else 0
    return [0.0] * n_features, 0.0

def logistic_regression_predict(X, weights, bias, threshold=0.5):
    return [1 if sigmoid(bias + sum(weights[j] * X[i][j] for j in range(len(weights)))) >= threshold else 0 for i in range(len(X))]
"""


def logreg_broken_threshold():
    return """\"\"\"Broken logistic regression: threshold of 0.0 flips all predictions.\"\"\"
import math
import random
from typing import List, Tuple

def sigmoid(z):
    if z > 100: return 1.0
    if z < -100: return 0.0
    return 1.0 / (1.0 + math.exp(-z))

def logistic_regression_fit(X, y, learning_rate=0.01, iterations=100):
    random.seed(42)
    n_samples = len(X)
    n_features = len(X[0]) if X else 0
    weights = [0.0] * n_features
    bias = 0.0
    for _ in range(iterations):
        preds = [sigmoid(bias + sum(weights[j] * X[i][j] for j in range(n_features))) for i in range(n_samples)]
        errors = [preds[i] - y[i] for i in range(n_samples)]
        dw = [sum(errors[i] * X[i][j] for i in range(n_samples)) / n_samples for j in range(n_features)]
        db = sum(errors) / n_samples
        weights = [weights[j] - learning_rate * dw[j] for j in range(n_features)]
        bias -= learning_rate * db
    return weights, bias

def logistic_regression_predict(X, weights, bias, threshold=0.5):
    # BUG: threshold forced to 0.0
    return [1 if sigmoid(bias + sum(weights[j] * X[i][j] for j in range(len(weights)))) > 0.0 else 0 for i in range(len(X))]
"""


def logreg_cheat_all_ones():
    return """\"\"\"Cheat: predict all ones.\"\"\"
from typing import List, Tuple
def logistic_regression_fit(X, y, learning_rate=0.01, iterations=100):
    return [0.0, 0.0], 0.0
def logistic_regression_predict(X, weights, bias, threshold=0.5):
    return [1] * len(X)
"""


def logreg_edge_nan():
    return """\"\"\"Edge: returns NaN weights.\"\"\"
import math
from typing import List, Tuple
def logistic_regression_fit(X, y, learning_rate=0.01, iterations=100):
    return [float('nan'), float('nan')], float('nan')
def logistic_regression_predict(X, weights, bias, threshold=0.5):
    return [0] * len(X)
"""


def tree_alt_gini():
    return """\"\"\"Alternative decision tree: uses Gini impurity instead of entropy.\"\"\"
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

def _gini(counts: Dict[int, int]) -> float:
    total = sum(counts.values())
    if total == 0: return 0.0
    return 1.0 - sum((c / total) ** 2 for c in counts.values())

def _info_gain(parent, children):
    total = sum(c for c, _ in children)
    return parent - sum((c / total) * e for c, e in children)

def build_tree(X, y, max_depth=5, depth=0):
    node = TreeNode()
    counts = {}
    for label in y:
        counts[label] = counts.get(label, 0) + 1
    if len(counts) == 1 or depth >= max_depth or len(X) == 0:
        node.label = max(counts, key=counts.get)
        return node
    parent_gini = _gini(counts)
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
            gain = _info_gain(parent_gini, [(len(left), _gini(lc)), (len(right), _gini(rc))])
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


def tree_alt_depth3():
    return """\"\"\"Alternative decision tree: shallower depth but still separable.\"\"\"
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
    if len(counts) == 1 or depth >= min(max_depth, 3) or len(X) == 0:
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


def tree_broken_majority():
    return """\"\"\"Broken decision tree: always predicts majority class.\"\"\"
import random
from typing import List

class TreeNode:
    def __init__(self):
        self.feature = None
        self.threshold = None
        self.label = None
        self.left = None
        self.right = None

def build_tree(X, y, max_depth=5, depth=0):
    node = TreeNode()
    counts = {}
    for label in y: counts[label] = counts.get(label, 0) + 1
    node.label = max(counts, key=counts.get)
    return node

def predict_tree(x, node):
    return node.label
"""


def tree_broken_random_split():
    return """\"\"\"Broken decision tree: always splits on the irrelevant second feature.\"\"\"
import random
from typing import List

class TreeNode:
    def __init__(self):
        self.feature = None
        self.threshold = None
        self.label = None
        self.left = None
        self.right = None

def build_tree(X, y, max_depth=5, depth=0):
    node = TreeNode()
    counts = {}
    for label in y: counts[label] = counts.get(label, 0) + 1
    if len(counts) == 1 or depth >= max_depth or len(X) <= 2:
        node.label = max(counts, key=counts.get)
        return node
    # BUG: always split on feature 1, which is uncorrelated with the label.
    node.feature = 1
    vals = [X[i][node.feature] for i in range(len(X))]
    node.threshold = (min(vals) + max(vals)) / 2.0
    left = [i for i in range(len(X)) if X[i][node.feature] <= node.threshold]
    right = [i for i in range(len(X)) if X[i][node.feature] > node.threshold]
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


def tree_cheat_const():
    return """\"\"\"Cheat: always predict class 0.\"\"\"
class TreeNode:
    def __init__(self):
        self.feature = self.threshold = self.label = self.left = self.right = None

def build_tree(X, y, max_depth=5, depth=0):
    n = TreeNode(); n.label = 0; return n

def predict_tree(x, node):
    return 0
"""


def tree_edge_syntax():
    return """\"\"\"Edge: syntax error in tree module.\"\"\"
class TreeNode: pass
def build_tree(X, y, max_depth=5, depth=0)
    return None
"""


def gd_alt_nesterov():
    return """\"\"\"Alternative GD: Nesterov accelerated gradient.\"\"\"

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
        # Nesterov lookahead
        lx = x - momentum * vx
        ly = y - momentum * vy
        gx, gy = gradient_bowl(lx, ly)
        vx = momentum * vx + learning_rate * gx
        vy = momentum * vy + learning_rate * gy
        x -= vx
        y -= vy
    return max_iters, quadratic_bowl(x, y)
"""


def gd_alt_big_lr():
    return """\"\"\"Alternative GD: larger stable learning rate (0.008).\"\"\"

def quadratic_bowl(x, y):
    return 100 * x ** 2 + y ** 2

def gradient_bowl(x, y):
    return (200 * x, 2 * y)

def vanilla_gd(x, y, learning_rate=0.008, tolerance=1e-6, max_iters=10000):
    for i in range(max_iters):
        fx = quadratic_bowl(x, y)
        if fx < tolerance: return i, fx
        gx, gy = gradient_bowl(x, y)
        x -= learning_rate * gx
        y -= learning_rate * gy
    return max_iters, quadratic_bowl(x, y)

def momentum_gd(x, y, learning_rate=0.008, momentum=0.9, tolerance=1e-6, max_iters=10000):
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


def gd_broken_no_momentum():
    return """\"\"\"Broken GD: momentum set to zero, so momentum_gd equals vanilla_gd.\"\"\"

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
    # BUG: momentum ignored
    return vanilla_gd(x, y, learning_rate, tolerance, max_iters)
"""


def gd_broken_diverge():
    return """\"\"\"Broken GD: learning rate too large, diverges.\"\"\"

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
        vx = momentum * vx + 0.5 * gx  # huge step
        vy = momentum * vy + 0.5 * gy
        x -= vx
        y -= vy
    return max_iters, quadratic_bowl(x, y)
"""


def gd_cheat_zeros():
    return """\"\"\"Cheat: returns zero iterations and zero loss.\"\"\"
def vanilla_gd(x, y, learning_rate=0.005, tolerance=1e-6, max_iters=10000):
    return 0, 0.0
def momentum_gd(x, y, learning_rate=0.005, momentum=0.9, tolerance=1e-6, max_iters=10000):
    return 0, 0.0
"""


def gd_edge_timeout():
    return """\"\"\"Edge: high precision convergence under the timeout limit.\"\"\"

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

def momentum_gd(x, y, learning_rate=0.008, momentum=0.9, tolerance=1e-9, max_iters=20000):
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


def knn_alt_weighted():
    return """\"\"\"Alternative kNN: weighted by inverse distance.\"\"\"
import math
from typing import List

def euclidean(p1, p2):
    return math.sqrt(sum((p1[i] - p2[i]) ** 2 for i in range(len(p1))))

def knn_predict_batch(X_test, X_train, y_train, k=3):
    preds = []
    for x in X_test:
        dists = [(euclidean(x, X_train[i]), y_train[i]) for i in range(len(X_train))]
        dists.sort()
        votes = {}
        for d, lbl in dists[:k]:
            weight = 1.0 / max(d, 1e-9)
            votes[lbl] = votes.get(lbl, 0.0) + weight
        preds.append(max(votes, key=votes.get))
    return preds
"""


def knn_alt_k3():
    return """\"\"\"Alternative kNN: uses k=3 instead of k=5.\"\"\"
import math
from typing import List

def euclidean(p1, p2):
    return math.sqrt(sum((p1[i] - p2[i]) ** 2 for i in range(len(p1))))

def knn_predict_batch(X_test, X_train, y_train, k=3):
    preds = []
    for x in X_test:
        dists = sorted([(euclidean(x, X_train[i]), y_train[i]) for i in range(len(X_train))])
        top3 = [label for _, label in dists[:k]]
        counts = {}
        for lbl in top3:
            counts[lbl] = counts.get(lbl, 0) + 1
        preds.append(max(counts, key=counts.get))
    return preds
"""


def knn_broken_k1_noisy():
    return """\"\"\"Broken kNN: always predicts class 0.\"\"\"
import math
from typing import List

def euclidean(p1, p2):
    return math.sqrt(sum((p1[i] - p2[i]) ** 2 for i in range(len(p1))))

def knn_predict_batch(X_test, X_train, y_train, k=3):
    return [0] * len(X_test)
"""


def knn_broken_wrong_distance():
    return """\"\"\"Broken kNN: uses L1 distance but labels still may pass? Make it fail by using constant.\"\"\"
import math
from typing import List

def _dist(p1, p2):
    # BUG: city-block distance not the issue; we flip sign so far points look near.
    return -sum(abs(p1[i] - p2[i]) for i in range(len(p1)))

def knn_predict_batch(X_test, X_train, y_train, k=3):
    preds = []
    for x in X_test:
        dists = sorted((_dist(x, X_train[i]), y_train[i]) for i in range(len(X_train)))
        preds.append(dists[0][1])
    return preds
"""


def knn_broken_random():
    return """\"\"\"Broken kNN: random predictions.\"\"\"
import random
from typing import List

def knn_predict_batch(X_test, X_train, y_train, k=3):
    random.seed(42)
    return [random.choice(y_train) for _ in X_test]
"""


def knn_cheat_majority():
    return """\"\"\"Cheat: predict training majority.\"\"\"
from typing import List
from collections import Counter

def knn_predict_batch(X_test, X_train, y_train, k=3):
    majority = Counter(y_train).most_common(1)[0][0]
    return [majority] * len(X_test)
"""


def knn_edge_empty():
    return """\"\"\"Edge: empty training set.\"\"\"
from typing import List

def knn_predict_batch(X_test, X_train, y_train, k=3):
    return [0] * len(X_test)
"""


def nb_alt_bernoulli():
    return """\"\"\"Alternative Naive Bayes: Bernoulli-style presence features.\"\"\"
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
    vocab = set()
    for i in range(n):
        seen = set(texts[i])
        vocab.update(seen)
        for w in seen:
            word_counts[labels[i]][w] += 1
            total[labels[i]] += 1
    probs = {}
    for c in classes:
        probs[c] = {w: (word_counts[c][w] + 1) / (total[c] + len(vocab)) for w in vocab}
    return priors, probs

def naive_bayes_predict(text, priors, probs):
    best = None
    best_score = -float('inf')
    for c in priors:
        score = math.log(priors[c])
        seen = set(text)
        for w in seen:
            score += math.log(probs[c].get(w, 1e-9))
        if score > best_score:
            best_score = score
            best = c
    return best
"""


def nb_alt_laplace2():
    return """\"\"\"Alternative Naive Bayes: stronger Laplace smoothing alpha=2.\"\"\"
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
    alpha = 2
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


def nb_broken_no_smooth():
    return """\"\"\"Broken Naive Bayes: zero smoothing causes underflow / zero scores.\"\"\"
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
    for c in classes:
        # BUG: no smoothing
        probs[c] = {w: word_counts[c][w] / total[c] for w in vocab}
    return priors, probs

def naive_bayes_predict(text, priors, probs):
    best = None
    best_score = -float('inf')
    for c in priors:
        score = math.log(priors[c])
        for w in text:
            p = probs[c].get(w, 0.0)
            if p == 0:
                return max(priors, key=priors.get)
            score += math.log(p)
        if score > best_score:
            best_score = score
            best = c
    return best
"""


def nb_broken_ignore_words():
    return """\"\"\"Broken Naive Bayes: ignores word frequencies and uses only priors.\"\"\"
import math
import random
from typing import Dict, List, Tuple

def naive_bayes_train(texts, labels):
    n = len(texts)
    classes = set(labels)
    counts = {c: 0 for c in classes}
    for l in labels: counts[l] += 1
    priors = {c: counts[c] / n for c in classes}
    return priors, {}

def naive_bayes_predict(text, priors, probs):
    return max(priors, key=priors.get)
"""


def nb_cheat_always_0():
    return """\"\"\"Cheat: always predict 0.\"\"\"
from typing import List, Tuple
def naive_bayes_train(texts, labels):
    return {0: 0.5, 1: 0.5}, {}
def naive_bayes_predict(text, priors, probs):
    return 0
"""


def nb_edge_empty():
    return """\"\"\"Edge: empty training corpus.\"\"\"
from typing import List, Tuple
def naive_bayes_train(texts, labels):
    return {0: 0.5, 1: 0.5}, {}
def naive_bayes_predict(text, priors, probs):
    return 0
"""


def perceptron_alt_pocket():
    return """\"\"\"Alternative perceptron: pocket (keeps best weights seen).\"\"\"
import random
from typing import List, Tuple

def perceptron_fit(X, y, learning_rate=1.0, max_iterations=100):
    random.seed(42)
    n_samples = len(X)
    n_features = len(X[0]) if X else 0
    y_lbl = [2 * label - 1 if label in (0, 1) else label for label in y]
    weights = [0.0] * n_features
    bias = 0.0
    best_w = weights[:]
    best_b = bias
    best_err = n_samples
    for _ in range(max_iterations):
        errors = 0
        for i in range(n_samples):
            z = bias + sum(weights[j] * X[i][j] for j in range(n_features))
            pred = 1 if z >= 0 else -1
            if pred != y_lbl[i]:
                err = y_lbl[i] - pred
                weights = [weights[j] + learning_rate * err * X[i][j] / 2.0 for j in range(n_features)]
                bias += learning_rate * err / 2.0
                errors += 1
        if errors < best_err:
            best_err = errors
            best_w = weights[:]
            best_b = bias
        if errors == 0:
            break
    return best_w, best_b

def perceptron_predict(X, weights, bias):
    return [1 if bias + sum(weights[j] * X[i][j] for j in range(len(weights))) >= 0 else 0 for i in range(len(X))]
"""


def perceptron_alt_avg():
    return """\"\"\"Alternative perceptron: averaged weights.\"\"\"
import random
from typing import List, Tuple

def perceptron_fit(X, y, learning_rate=1.0, max_iterations=100):
    random.seed(42)
    n_samples = len(X)
    n_features = len(X[0]) if X else 0
    y_lbl = [2 * label - 1 if label in (0, 1) else label for label in y]
    weights = [0.0] * n_features
    bias = 0.0
    sum_w = [0.0] * n_features
    sum_b = 0.0
    count = 0
    for _ in range(max_iterations):
        err_count = 0
        for i in range(n_samples):
            z = bias + sum(weights[j] * X[i][j] for j in range(n_features))
            pred = 1 if z >= 0 else -1
            if pred != y_lbl[i]:
                err = y_lbl[i] - pred
                weights = [weights[j] + learning_rate * err * X[i][j] / 2.0 for j in range(n_features)]
                bias += learning_rate * err / 2.0
                err_count += 1
            for j in range(n_features):
                sum_w[j] += weights[j]
            sum_b += bias
            count += 1
        if err_count == 0:
            break
    avg_w = [sum_w[j] / max(count, 1) for j in range(n_features)]
    avg_b = sum_b / max(count, 1)
    return avg_w, avg_b

def perceptron_predict(X, weights, bias):
    return [1 if bias + sum(weights[j] * X[i][j] for j in range(len(weights))) >= 0 else 0 for i in range(len(X))]
"""


def perceptron_broken_subtract():
    return """\"\"\"Broken perceptron: subtracts update.\"\"\"
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
        for i in range(n_samples):
            z = bias + sum(weights[j] * X[i][j] for j in range(n_features))
            pred = 1 if z >= 0 else -1
            if pred != y_lbl[i]:
                err = y_lbl[i] - pred
                weights = [weights[j] - learning_rate * err * X[i][j] / 2.0 for j in range(n_features)]
                bias -= learning_rate * err / 2.0
    return weights, bias

def perceptron_predict(X, weights, bias):
    return [1 if bias + sum(weights[j] * X[i][j] for j in range(len(weights))) >= 0 else 0 for i in range(len(X))]
"""


def perceptron_broken_lr_tiny():
    return """\"\"\"Broken perceptron: always predicts class 0.\"\"\"
import random
from typing import List, Tuple

def perceptron_fit(X, y, learning_rate=1.0, max_iterations=100):
    random.seed(42)
    n_features = len(X[0]) if X else 0
    return [0.0] * n_features, -1.0

def perceptron_predict(X, weights, bias):
    return [1 if bias + sum(weights[j] * X[i][j] for j in range(len(weights))) >= 0 else 0 for i in range(len(X))]
"""


def perceptron_cheat_all_1():
    return """\"\"\"Cheat: always predict 1.\"\"\"
from typing import List, Tuple
def perceptron_fit(X, y, learning_rate=1.0, max_iterations=100):
    return [0.0, 0.0], 1.0
def perceptron_predict(X, weights, bias):
    return [1] * len(X)
"""


def perceptron_edge_random_weights():
    return """\"\"\"Edge: large random weights never update.\"\"\"
import random
from typing import List, Tuple

def perceptron_fit(X, y, learning_rate=1.0, max_iterations=100):
    random.seed(42)
    return [random.uniform(-100, 100) for _ in range(len(X[0]))], random.uniform(-100, 100)

def perceptron_predict(X, weights, bias):
    return [1 if bias + sum(weights[j] * X[i][j] for j in range(len(weights))) >= 0 else 0 for i in range(len(X))]
"""


def poly_alt_direct():
    return """\"\"\"Alternative polynomial: direct linear system via Vandermonde, no normal equations.\"\"\"
from typing import List

def fit_polynomial(x_vals, y_vals, degree):
    n = len(x_vals)
    V = [[x ** j for j in range(degree + 1)] for x in x_vals]
    # V w = y, solve via Gram-Schmidt-ish normal equations anyway
    VtV = [[sum(V[i][j] * V[i][k] for i in range(n)) for k in range(degree + 1)] for j in range(degree + 1)]
    Vty = [sum(V[i][j] * y_vals[i] for i in range(n)) for j in range(degree + 1)]
    # Gaussian elimination
    aug = [VtV[i][:] + [Vty[i]] for i in range(degree + 1)]
    for col in range(degree + 1):
        max_row = col
        for r in range(col + 1, degree + 1):
            if abs(aug[r][col]) > abs(aug[max_row][col]): max_row = r
        aug[col], aug[max_row] = aug[max_row], aug[col]
        for r in range(col + 1, degree + 1):
            factor = aug[r][col] / aug[col][col]
            for c in range(col, degree + 2):
                aug[r][c] -= factor * aug[col][c]
    coeffs = [0.0] * (degree + 1)
    for i in range(degree, -1, -1):
        coeffs[i] = aug[i][degree + 1]
        for j in range(i + 1, degree + 1):
            coeffs[i] -= aug[i][j] * coeffs[j]
        coeffs[i] /= aug[i][i]
    return coeffs

def eval_poly(coeffs, x):
    result = 0.0
    power = 1.0
    for c in coeffs:
        result += c * power
        power *= x
    return result
"""


def poly_alt_scaled():
    return """\"\"\"Alternative polynomial: fits on scaled x (still recovers coefficients).\"\"\"
from typing import List

def fit_polynomial(x_vals, y_vals, degree):
    s = max(abs(x) for x in x_vals) if x_vals else 1.0
    scaled = [x / s for x in x_vals]
    V = [[x ** j for j in range(degree + 1)] for x in scaled]
    n = len(x_vals)
    VtV = [[sum(V[i][j] * V[i][k] for i in range(n)) for k in range(degree + 1)] for j in range(degree + 1)]
    Vty = [sum(V[i][j] * y_vals[i] for i in range(n)) for j in range(degree + 1)]
    aug = [VtV[i][:] + [Vty[i]] for i in range(degree + 1)]
    for col in range(degree + 1):
        max_row = col
        for r in range(col + 1, degree + 1):
            if abs(aug[r][col]) > abs(aug[max_row][col]): max_row = r
        aug[col], aug[max_row] = aug[max_row], aug[col]
        for r in range(col + 1, degree + 1):
            factor = aug[r][col] / aug[col][col]
            for c in range(col, degree + 2):
                aug[r][c] -= factor * aug[col][c]
    c_scaled = [0.0] * (degree + 1)
    for i in range(degree, -1, -1):
        c_scaled[i] = aug[i][degree + 1]
        for j in range(i + 1, degree + 1):
            c_scaled[i] -= aug[i][j] * c_scaled[j]
        c_scaled[i] /= aug[i][i]
    # Convert back to original scale: y = sum c'_i (x/s)^i = sum (c'_i / s^i) x^i
    coeffs = [c_scaled[i] / (s ** i) for i in range(degree + 1)]
    return coeffs

def eval_poly(coeffs, x):
    result = 0.0
    power = 1.0
    for c in coeffs:
        result += c * power
        power *= x
    return result
"""


def poly_broken_degree1():
    return """\"\"\"Broken polynomial: fits degree 1.\"\"\"
from typing import List

def fit_polynomial(x_vals, y_vals, degree):
    n = len(x_vals)
    V = [[1.0, x] for x in x_vals]
    VtV = [[sum(V[i][j] * V[i][k] for i in range(n)) for k in range(2)] for j in range(2)]
    Vty = [sum(V[i][j] * y_vals[i] for i in range(n)) for j in range(2)]
    aug = [VtV[i][:] + [Vty[i]] for i in range(2)]
    for col in range(2):
        max_row = col
        for r in range(col + 1, 2):
            if abs(aug[r][col]) > abs(aug[max_row][col]): max_row = r
        aug[col], aug[max_row] = aug[max_row], aug[col]
        for r in range(col + 1, 2):
            factor = aug[r][col] / aug[col][col]
            for c in range(col, 3):
                aug[r][c] -= factor * aug[col][c]
    c = [0.0, 0.0]
    for i in range(1, -1, -1):
        c[i] = aug[i][2]
        for j in range(i + 1, 2):
            c[i] -= aug[i][j] * c[j]
        c[i] /= aug[i][i]
    return [c[0], c[1], 0.0]

def eval_poly(coeffs, x):
    return coeffs[0] + coeffs[1] * x + coeffs[2] * x * x
"""


def poly_broken_shuffle():
    return """\"\"\"Broken polynomial: returns shuffled/true-ish coefficients but not fitted.\"\"\"
import random
from typing import List

def fit_polynomial(x_vals, y_vals, degree):
    random.seed(42)
    coeffs = [2.0, 3.0, 0.5]
    random.shuffle(coeffs)
    return coeffs

def eval_poly(coeffs, x):
    return coeffs[0] + coeffs[1] * x + coeffs[2] * x * x
"""


def poly_cheat_true():
    return """\"\"\"Cheat: hardcodes true coefficients.\"\"\"
from typing import List
def fit_polynomial(x_vals, y_vals, degree):
    return [2.0, 3.0, 0.5]
def eval_poly(coeffs, x):
    return coeffs[0] + coeffs[1] * x + coeffs[2] * x * x
"""


def poly_edge_singular():
    return """\"\"\"Edge: duplicate x values cause singular matrix.\"\"\"
from typing import List

def fit_polynomial(x_vals, y_vals, degree):
    n = len(x_vals)
    x = [0.0] * n
    V = [[x[i] ** j for j in range(degree + 1)] for i in range(n)]
    VtV = [[sum(V[i][j] * V[i][k] for i in range(n)) for k in range(degree + 1)] for j in range(degree + 1)]
    Vty = [sum(V[i][j] * y_vals[i] for i in range(n)) for j in range(degree + 1)]
    aug = [VtV[i][:] + [Vty[i]] for i in range(degree + 1)]
    for col in range(degree + 1):
        max_row = col
        for r in range(col + 1, degree + 1):
            if abs(aug[r][col]) > abs(aug[max_row][col]): max_row = r
        aug[col], aug[max_row] = aug[max_row], aug[col]
        if abs(aug[col][col]) < 1e-12:
            raise ValueError("singular")
        for r in range(col + 1, degree + 1):
            factor = aug[r][col] / aug[col][col]
            for c in range(col, degree + 2):
                aug[r][c] -= factor * aug[col][c]
    coeffs = [0.0] * (degree + 1)
    for i in range(degree, -1, -1):
        coeffs[i] = aug[i][degree + 1]
        for j in range(i + 1, degree + 1):
            coeffs[i] -= aug[i][j] * coeffs[j]
        coeffs[i] /= aug[i][i]
    return coeffs

def eval_poly(coeffs, x):
    return coeffs[0] + coeffs[1] * x + coeffs[2] * x * x
"""


def ridge_alt_ols():
    return """\"\"\"Alternative ridge: OLS (lambda=0) on well-conditioned data.\"\"\"
from typing import List

def matrix_transpose(A):
    return [[A[i][j] for i in range(len(A))] for j in range(len(A[0]))]

def matrix_multiply(A, B):
    return [[sum(A[i][k] * B[k][j] for k in range(len(A[0]))) for j in range(len(B[0]))] for i in range(len(A))]

def matrix_vector_multiply(A, v):
    return [sum(A[i][j] * v[j] for j in range(len(v))) for i in range(len(A))]

def gaussian_elimination(A, b):
    n = len(A)
    A = [row[:] for row in A]
    b = b[:]
    for col in range(n):
        max_row = col
        for r in range(col + 1, n):
            if abs(A[r][col]) > abs(A[max_row][col]): max_row = r
        A[col], A[max_row] = A[max_row], A[col]
        b[col], b[max_row] = b[max_row], b[col]
        for r in range(col + 1, n):
            factor = A[r][col] / A[col][col]
            for c in range(col, n):
                A[r][c] -= factor * A[col][c]
            b[r] -= factor * b[col]
    x = [0.0] * n
    for i in range(n - 1, -1, -1):
        x[i] = b[i]
        for j in range(i + 1, n):
            x[i] -= A[i][j] * x[j]
        x[i] /= A[i][i]
    return x

def ridge_regression_normal_eq(X, y, lambda_reg=0.01):
    Xt = matrix_transpose(X)
    XTX = matrix_multiply(Xt, X)
    XTy = matrix_vector_multiply(Xt, y)
    return gaussian_elimination(XTX, XTy)

def ridge_regression_predict(X, weights):
    return matrix_vector_multiply(X, weights)
"""


def ridge_alt_svd_like():
    return """\"\"\"Alternative ridge: iterative Jacobi-ish refinement of normal equations.\"\"\"
from typing import List

def matrix_transpose(A):
    return [[A[i][j] for i in range(len(A))] for j in range(len(A[0]))]

def matrix_multiply(A, B):
    return [[sum(A[i][k] * B[k][j] for k in range(len(A[0]))) for j in range(len(B[0]))] for i in range(len(A))]

def matrix_vector_multiply(A, v):
    return [sum(A[i][j] * v[j] for j in range(len(v))) for i in range(len(A))]

def ridge_regression_normal_eq(X, y, lambda_reg=0.01):
    Xt = matrix_transpose(X)
    XTX = matrix_multiply(Xt, X)
    for i in range(len(XTX)):
        XTX[i][i] += lambda_reg
    XTy = matrix_vector_multiply(Xt, y)
    # Jacobi iteration (approximation)
    w = [0.0] * len(XTy)
    for _ in range(1000):
        new = []
        for i in range(len(w)):
            sigma = sum(XTX[i][j] * w[j] for j in range(len(w)) if j != i)
            new.append((XTy[i] - sigma) / XTX[i][i])
        w = new
    return w

def ridge_regression_predict(X, weights):
    return matrix_vector_multiply(X, weights)
"""


def ridge_broken_neg_rhs():
    return """\"\"\"Broken ridge: negates RHS.\"\"\"
from typing import List

def matrix_transpose(A):
    return [[A[i][j] for i in range(len(A))] for j in range(len(A[0]))]

def matrix_multiply(A, B):
    return [[sum(A[i][k] * B[k][j] for k in range(len(A[0]))) for j in range(len(B[0]))] for i in range(len(A))]

def matrix_vector_multiply(A, v):
    return [sum(A[i][j] * v[j] for j in range(len(v))) for i in range(len(A))]

def gaussian_elimination(A, b):
    n = len(A)
    A = [row[:] for row in A]
    b = b[:]
    for col in range(n):
        max_row = col
        for r in range(col + 1, n):
            if abs(A[r][col]) > abs(A[max_row][col]): max_row = r
        A[col], A[max_row] = A[max_row], A[col]
        b[col], b[max_row] = b[max_row], b[col]
        for r in range(col + 1, n):
            factor = A[r][col] / A[col][col]
            for c in range(col, n):
                A[r][c] -= factor * A[col][c]
            b[r] -= factor * b[col]
    x = [0.0] * n
    for i in range(n - 1, -1, -1):
        x[i] = b[i]
        for j in range(i + 1, n):
            x[i] -= A[i][j] * x[j]
        x[i] /= A[i][i]
    return x

def ridge_regression_normal_eq(X, y, lambda_reg=0.01):
    Xt = matrix_transpose(X)
    XTX = matrix_multiply(Xt, X)
    for i in range(len(XTX)):
        XTX[i][i] += lambda_reg
    XTy = matrix_vector_multiply(Xt, y)
    return gaussian_elimination(XTX, [-v for v in XTy])

def ridge_regression_predict(X, weights):
    return matrix_vector_multiply(X, weights)
"""


def ridge_broken_huge_lambda():
    return """\"\"\"Broken ridge: enormous lambda shrinks weights to zero.\"\"\"
from typing import List

def matrix_transpose(A):
    return [[A[i][j] for i in range(len(A))] for j in range(len(A[0]))]

def matrix_multiply(A, B):
    return [[sum(A[i][k] * B[k][j] for k in range(len(A[0]))) for j in range(len(B[0]))] for i in range(len(A))]

def matrix_vector_multiply(A, v):
    return [sum(A[i][j] * v[j] for j in range(len(v))) for i in range(len(A))]

def gaussian_elimination(A, b):
    n = len(A)
    A = [row[:] for row in A]
    b = b[:]
    for col in range(n):
        max_row = col
        for r in range(col + 1, n):
            if abs(A[r][col]) > abs(A[max_row][col]): max_row = r
        A[col], A[max_row] = A[max_row], A[col]
        b[col], b[max_row] = b[max_row], b[col]
        for r in range(col + 1, n):
            factor = A[r][col] / A[col][col]
            for c in range(col, n):
                A[r][c] -= factor * A[col][c]
            b[r] -= factor * b[col]
    x = [0.0] * n
    for i in range(n - 1, -1, -1):
        x[i] = b[i]
        for j in range(i + 1, n):
            x[i] -= A[i][j] * x[j]
        x[i] /= A[i][i]
    return x

def ridge_regression_normal_eq(X, y, lambda_reg=0.01):
    Xt = matrix_transpose(X)
    XTX = matrix_multiply(Xt, X)
    for i in range(len(XTX)):
        XTX[i][i] += 100000.0
    XTy = matrix_vector_multiply(Xt, y)
    return gaussian_elimination(XTX, XTy)

def ridge_regression_predict(X, weights):
    return matrix_vector_multiply(X, weights)
"""


def ridge_cheat_true_weights():
    return """\"\"\"Cheat: hardcodes true weights.\"\"\"
from typing import List
def ridge_regression_normal_eq(X, y, lambda_reg=0.01):
    return [2.0, 3.0, -1.0]
def ridge_regression_predict(X, weights):
    return [sum(weights[j] * X[i][j] for j in range(len(weights))) for i in range(len(X))]
"""


def ridge_edge_singular():
    return """\"\"\"Edge: returns NaN weights.\"\"\"
from typing import List

def ridge_regression_normal_eq(X, y, lambda_reg=0.01):
    return [float('nan')] * 3

def ridge_regression_predict(X, weights):
    return [sum(weights[j] * X[i][j] for j in range(len(weights))) for i in range(len(X))]
"""


# Build manifest
items = []


def add(pattern, kind, name, source, expect, note=""):
    path = write(name, source)
    items.append({
        "file": str(path.relative_to(path.parents[2])),
        "pattern": pattern,
        "kind": kind,
        "expect": expect,
        "note": note,
    })


# Canonical correct (10)
for pattern in [
    "linear_regression_gd", "kmeans_clustering", "logistic_regression",
    "decision_tree_id3", "gradient_descent_momentum", "knn_classifier",
    "naive_bayes_text", "perceptron", "polynomial_regression",
    "ridge_regression_normal_eq",
]:
    src = (CORPUS_DIR.parents[1] / "examples" / f"{pattern}_good.py").read_text()
    add(pattern, "canonical_correct", f"{pattern}_canonical_good.py", src, "PASS", "copied from examples/")

# Alternative correct (35)
add("linear_regression_gd", "alternative_correct", "linreg_normal_eq.py", linreg_normal_eq(), "PASS", "closed-form via normal equations")
add("linear_regression_gd", "alternative_correct", "linreg_sgd.py", linreg_sgd(), "PASS", "stochastic gradient descent")
add("linear_regression_gd", "alternative_correct", "linreg_higher_lr.py", linreg_higher_lr(), "PASS", "batch GD with higher lr")
add("kmeans_clustering", "alternative_correct", "kmeans_kmeanspp.py", kmeans_alt_init(), "PASS", "farthest-first initialization")
add("kmeans_clustering", "alternative_correct", "kmeans_median.py", kmeans_alt_median(), "PASS", "median centroid update")
add("logistic_regression", "alternative_correct", "logreg_newtonish.py", logreg_alt_newtonish(), "PASS", "Newton-like larger step")
add("logistic_regression", "alternative_correct", "logreg_high_lr.py", logreg_alt_high_lr(), "PASS", "higher learning rate")
add("decision_tree_id3", "alternative_correct", "tree_gini.py", tree_alt_gini(), "PASS", "Gini impurity split")
add("decision_tree_id3", "alternative_correct", "tree_depth3.py", tree_alt_depth3(), "PASS", "shallower depth=3")
add("gradient_descent_momentum", "alternative_correct", "gd_nesterov.py", gd_alt_nesterov(), "PASS", "Nesterov acceleration")
add("gradient_descent_momentum", "alternative_correct", "gd_big_lr.py", gd_alt_big_lr(), "PASS", "larger learning rate")
add("knn_classifier", "alternative_correct", "knn_weighted.py", knn_alt_weighted(), "PASS", "inverse-distance weighted kNN")
add("knn_classifier", "alternative_correct", "knn_k3.py", knn_alt_k3(), "PASS", "k=3 instead of k=5")
add("naive_bayes_text", "alternative_correct", "nb_bernoulli.py", nb_alt_bernoulli(), "PASS", "Bernoulli presence features")
add("naive_bayes_text", "alternative_correct", "nb_laplace2.py", nb_alt_laplace2(), "PASS", "alpha=2 smoothing")
add("perceptron", "alternative_correct", "perceptron_pocket.py", perceptron_alt_pocket(), "PASS", "pocket perceptron")
add("perceptron", "alternative_correct", "perceptron_avg.py", perceptron_alt_avg(), "PASS", "averaged perceptron")
add("polynomial_regression", "alternative_correct", "poly_direct.py", poly_alt_direct(), "PASS", "direct linear system")
add("polynomial_regression", "alternative_correct", "poly_scaled.py", poly_alt_scaled(), "PASS", "scaled Vandermonde")
add("ridge_regression_normal_eq", "alternative_correct", "ridge_ols.py", ridge_alt_ols(), "PASS", "OLS lambda=0")
add("ridge_regression_normal_eq", "alternative_correct", "ridge_jacobi.py", ridge_alt_svd_like(), "PASS", "iterative Jacobi refinement")

# Broken (35)
add("linear_regression_gd", "broken", "linreg_broken_sign.py", linreg_broken_sign(), "FAIL", "gradient ascent")
add("linear_regression_gd", "broken", "linreg_broken_one_iter.py", linreg_broken_one_iter(), "FAIL", "one iteration only")
add("linear_regression_gd", "broken", "linreg_broken_zero_lr.py", linreg_broken_zero_lr(), "FAIL", "zero learning rate")
add("kmeans_clustering", "broken", "kmeans_broken_farthest.py", kmeans_broken_farthest(), "FAIL", "assign farthest")
add("kmeans_clustering", "broken", "kmeans_broken_no_update.py", kmeans_broken_no_update(), "FAIL", "never updates centroids")
add("kmeans_clustering", "broken", "kmeans_broken_k2.py", kmeans_broken_k2(), "FAIL", "uses k=2 instead of k=3")
add("logistic_regression", "broken", "logreg_broken_sign.py", logreg_broken_sign(), "FAIL", "gradient sign flipped")
add("logistic_regression", "broken", "logreg_broken_one_iter.py", logreg_broken_one_iter(), "FAIL", "one iteration only")
add("logistic_regression", "broken", "logreg_broken_threshold.py", logreg_broken_threshold(), "FAIL", "threshold=0.0")
add("decision_tree_id3", "broken", "tree_broken_majority.py", tree_broken_majority(), "FAIL", "always majority class")
add("decision_tree_id3", "broken", "tree_broken_random_split.py", tree_broken_random_split(), "FAIL", "random splits")
add("gradient_descent_momentum", "broken", "gd_broken_no_momentum.py", gd_broken_no_momentum(), "FAIL", "momentum_gd equals vanilla")
add("gradient_descent_momentum", "broken", "gd_broken_diverge.py", gd_broken_diverge(), "FAIL", "diverging step size")
add("knn_classifier", "broken", "knn_broken_k1_noisy.py", knn_broken_k1_noisy(), "FAIL", "k=1 overfits noise")
add("knn_classifier", "broken", "knn_broken_wrong_distance.py", knn_broken_wrong_distance(), "FAIL", "negative distance ranking")
add("knn_classifier", "broken", "knn_broken_random.py", knn_broken_random(), "FAIL", "random predictions")
add("naive_bayes_text", "broken", "nb_broken_no_smooth.py", nb_broken_no_smooth(), "FAIL", "no Laplace smoothing")
add("naive_bayes_text", "broken", "nb_broken_ignore_words.py", nb_broken_ignore_words(), "FAIL", "ignores words")
add("perceptron", "broken", "perceptron_broken_subtract.py", perceptron_broken_subtract(), "FAIL", "subtracts update")
add("perceptron", "broken", "perceptron_broken_lr_tiny.py", perceptron_broken_lr_tiny(), "FAIL", "always predicts class 0")
add("polynomial_regression", "broken", "poly_broken_degree1.py", poly_broken_degree1(), "FAIL", "fits degree 1")
add("polynomial_regression", "broken", "poly_broken_shuffle.py", poly_broken_shuffle(), "FAIL", "shuffles true coeffs")
add("ridge_regression_normal_eq", "broken", "ridge_broken_neg_rhs.py", ridge_broken_neg_rhs(), "FAIL", "negates RHS")
add("ridge_regression_normal_eq", "broken", "ridge_broken_huge_lambda.py", ridge_broken_huge_lambda(), "FAIL", "lambda too large")

# Cheats (10)
add("linear_regression_gd", "cheat", "linreg_cheat_const.py", linreg_cheat_const(), "FAIL", "constant unrelated values")
add("linear_regression_gd", "cheat", "linreg_cheat_hardcoded_oracle.py", linreg_cheat_hardcoded_oracle(), "PASS", "disclosed exception: hardcoded oracle output; randomized fixtures roadmap")
add("kmeans_clustering", "cheat", "kmeans_cheat_random_assign.py", kmeans_cheat_random_assign(), "FAIL", "random assignments")
add("logistic_regression", "cheat", "logreg_cheat_all_ones.py", logreg_cheat_all_ones(), "FAIL", "predict all ones")
add("decision_tree_id3", "cheat", "tree_cheat_const.py", tree_cheat_const(), "FAIL", "always class 0")
add("gradient_descent_momentum", "cheat", "gd_cheat_zeros.py", gd_cheat_zeros(), "FAIL", "returns zero loss zero iters")
add("knn_classifier", "cheat", "knn_cheat_majority.py", knn_cheat_majority(), "FAIL", "training majority")
add("naive_bayes_text", "cheat", "nb_cheat_always_0.py", nb_cheat_always_0(), "FAIL", "always predict 0")
add("perceptron", "cheat", "perceptron_cheat_all_1.py", perceptron_cheat_all_1(), "FAIL", "always predict 1")
add("polynomial_regression", "cheat", "poly_cheat_true.py", poly_cheat_true(), "FAIL", "hardcoded true coeffs (oracle differs due to noise)")
add("ridge_regression_normal_eq", "cheat", "ridge_cheat_true_weights.py", ridge_cheat_true_weights(), "FAIL", "hardcoded true weights (oracle differs due to noise)")

# Edge / robustness (10)
add("linear_regression_gd", "edge", "linreg_edge_syntax.py", linreg_edge_syntax(), "FAIL", "syntax error")
add("linear_regression_gd", "edge", "linreg_edge_nan.py", linreg_edge_nan(), "FAIL", "NaN weights")
add("kmeans_clustering", "edge", "kmeans_edge_empty.py", kmeans_edge_empty(), "FAIL", "empty centroids")
add("logistic_regression", "edge", "logreg_edge_nan.py", logreg_edge_nan(), "FAIL", "NaN weights")
add("decision_tree_id3", "edge", "tree_edge_syntax.py", tree_edge_syntax(), "FAIL", "syntax error")
add("gradient_descent_momentum", "edge", "gd_edge_timeout.py", gd_edge_timeout(), "PASS", "high precision within timeout")
add("knn_classifier", "edge", "knn_edge_empty.py", knn_edge_empty(), "FAIL", "empty training set")
add("naive_bayes_text", "edge", "nb_edge_empty.py", nb_edge_empty(), "FAIL", "empty training corpus")
add("perceptron", "edge", "perceptron_edge_random_weights.py", perceptron_edge_random_weights(), "FAIL", "random large weights")
add("polynomial_regression", "edge", "poly_edge_singular.py", poly_edge_singular(), "FAIL", "singular matrix")
add("ridge_regression_normal_eq", "edge", "ridge_edge_singular.py", ridge_edge_singular(), "FAIL", "NaN weights")

# Ensure unique filenames (allow extending later)
names = [i["file"] for i in items]
assert len(names) == len(set(names)), "duplicate filenames"

# Write manifest
MANIFEST_PATH.write_text(json.dumps({"count": len(items), "items": items}, indent=2), encoding="utf-8")
print(f"Wrote {len(items)} corpus entries to {CORPUS_DIR}")
print(f"Manifest: {MANIFEST_PATH}")
