"""Self-contained correct implementation of linear regression via batch gradient descent.

This file can be run standalone (`python examples/linear_regression_gd_good.py`)
or verified against the Forge Gate oracle:

    forge-gate verify examples/linear_regression_gd_good.py --pattern linear_regression_gd
"""
import random
from typing import List, Tuple


def mean(values: List[float]) -> float:
    """Compute arithmetic mean."""
    return sum(values) / len(values) if values else 0.0


def linear_regression_gd(
    X: List[List[float]],
    y: List[float],
    learning_rate: float = 0.01,
    iterations: int = 100,
) -> Tuple[List[float], float]:
    """Fit a linear regression model using batch gradient descent.

    Returns:
        (weights, bias): fitted parameters.
    """
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

        weights = [weights[j] - learning_rate * dw[j] for j in range(n_features)]
        bias = bias - learning_rate * db

    return weights, bias


if __name__ == "__main__":
    random.seed(42)

    # Same seed-locked synthetic data the Forge Gate oracle uses.
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

    assert r2 > 0.8, f"R² = {r2} is too low"
    assert abs(weights[0] - 2.0) < 0.5, f"w[0] = {weights[0]}, expected ~2.0"
    assert abs(weights[1] - 3.0) < 0.5, f"w[1] = {weights[1]}, expected ~3.0"

    print(
        f"LINEAR_REGRESSION_GD PASS: R² = {r2:.3f}, "
        f"weights = {[f'{w:.2f}' for w in weights]}"
    )
