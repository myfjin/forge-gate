"""Self-contained BROKEN implementation of linear regression.

The bug is a single flipped sign: gradient *ascent* is performed instead of
gradient descent, so weights diverge from the oracle. Run standalone to see the
divergence, or verify with Forge Gate to see FAIL.

    forge-gate verify examples/linear_regression_gd_bad.py --pattern linear_regression_gd
"""
import random
from typing import List, Tuple


def mean(values: List[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def linear_regression_gd(
    X: List[List[float]],
    y: List[float],
    learning_rate: float = 0.01,
    iterations: int = 100,
) -> Tuple[List[float], float]:
    """BUG: performs gradient ascent instead of gradient descent."""
    n_samples = len(X)
    n_features = len(X[0]) if X else 0

    weights = [0.0] * n_features
    bias = 0.0

    for _ in range(iterations):
        predictions = [
            bias + sum(weights[j] * X[i][j] for j in range(n_features))
            for i in range(n_samples)
        ]
        errors = [predictions[i] - y[i] for i in range(n_samples)]

        dw = [
            sum(errors[i] * X[i][j] for i in range(n_samples)) / n_samples
            for j in range(n_features)
        ]
        db = sum(errors) / n_samples

        # BUG: should subtract the gradient. Adding it makes loss grow.
        weights = [weights[j] + learning_rate * dw[j] for j in range(n_features)]
        bias = bias + learning_rate * db

    return weights, bias


if __name__ == "__main__":
    random.seed(42)

    n_samples = 30
    X = [[random.uniform(-5, 5), random.uniform(-5, 5)] for _ in range(n_samples)]
    y = [2.0 * X[i][0] + 3.0 * X[i][1] + random.gauss(0, 0.5) for i in range(n_samples)]

    weights, bias = linear_regression_gd(X, y, learning_rate=0.01, iterations=200)

    predictions = [
        bias + sum(weights[j] * X[i][j] for j in range(2)) for i in range(n_samples)
    ]
    mean_y = sum(y) / len(y)
    ss_res = sum((predictions[i] - y[i]) ** 2 for i in range(n_samples))
    ss_tot = sum((y[i] - mean_y) ** 2 for i in range(n_samples))
    r2 = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else 0.0

    print(
        f"LINEAR_REGRESSION_GD BAD: R² = {r2:.3f}, "
        f"weights = {[f'{w:.2f}' for w in weights]}"
    )
