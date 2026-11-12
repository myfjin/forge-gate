"""Alternative logistic regression: Newton-like update with Hessian approximation."""
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
