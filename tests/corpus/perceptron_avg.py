"""Alternative perceptron: averaged weights."""
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
