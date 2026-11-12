"""Alternative perceptron: pocket (keeps best weights seen)."""
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
