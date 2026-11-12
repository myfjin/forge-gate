"""Broken linreg: only one iteration, no convergence."""
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
