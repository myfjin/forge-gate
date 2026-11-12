"""Alternative correct linear regression via stochastic gradient descent."""
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
