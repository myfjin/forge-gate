"""Correct linreg: batch GD honouring its parameters, with gradient-norm early stopping.

A genuine alternative implementation: single-pass gradient accumulation (rather than
list comprehensions) plus an early-exit once the gradient is numerically flat. It
respects the caller's learning_rate and iterations, so a harness can drive it to
convergence -- unlike the fixture this replaces, which silently hardcoded lr=0.05 and
ran iterations//4 steps, then under-converged and failed metamorphic shift-target.
"""
import math
from typing import List, Tuple


def linear_regression_gd(
    X: List[List[float]], y: List[float], learning_rate: float = 0.01, iterations: int = 100
) -> Tuple[List[float], float]:
    n_samples = len(X)
    n_features = len(X[0]) if X else 0
    weights = [0.0] * n_features
    bias = 0.0
    for _ in range(iterations):
        dw = [0.0] * n_features
        db = 0.0
        for i in range(n_samples):
            pred = bias
            for j in range(n_features):
                pred += weights[j] * X[i][j]
            err = pred - y[i]
            for j in range(n_features):
                dw[j] += err * X[i][j]
            db += err
        dw = [g / n_samples for g in dw]
        db /= n_samples
        if math.sqrt(sum(g * g for g in dw) + db * db) < 1e-12:
            break
        for j in range(n_features):
            weights[j] -= learning_rate * dw[j]
        bias -= learning_rate * db
    return weights, bias
