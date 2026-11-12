"""Self-contained correct implementation of binary logistic regression.

This file can be run standalone (`python examples/logistic_regression_good.py`)
or verified against the Forge Gate oracle:

    forge-gate verify examples/logistic_regression_good.py --pattern logistic_regression
"""
import math
import random
from typing import List, Tuple


def sigmoid(z: float) -> float:
    """Compute sigmoid(z) = 1 / (1 + exp(-z))."""
    if z > 100:
        return 1.0
    if z < -100:
        return 0.0
    return 1.0 / (1.0 + math.exp(-z))


def logistic_regression_fit(
    X: List[List[float]],
    y: List[int],
    learning_rate: float = 0.01,
    iterations: int = 100,
) -> Tuple[List[float], float]:
    """Fit a binary logistic regression model using gradient descent."""
    if learning_rate <= 0:
        raise ValueError(f"learning_rate must be positive, got {learning_rate}")
    if iterations < 0:
        raise ValueError(f"iterations must be non-negative, got {iterations}")

    n_samples = len(X)
    n_features = len(X[0]) if X else 0

    weights = [0.0] * n_features
    bias = 0.0

    for _ in range(iterations):
        predictions = []
        for i in range(n_samples):
            z = bias + sum(weights[j] * X[i][j] for j in range(n_features))
            predictions.append(sigmoid(z))

        errors = [predictions[i] - y[i] for i in range(n_samples)]
        dw = [
            sum(errors[i] * X[i][j] for i in range(n_samples)) / n_samples
            for j in range(n_features)
        ]
        db = sum(errors) / n_samples

        weights = [weights[j] - learning_rate * dw[j] for j in range(n_features)]
        bias = bias - learning_rate * db

    return weights, bias


def logistic_regression_predict(
    X: List[List[float]],
    weights: List[float],
    bias: float,
    threshold: float = 0.5,
) -> List[int]:
    """Predict binary labels for samples X."""
    predictions = []
    for i in range(len(X)):
        z = bias + sum(weights[j] * X[i][j] for j in range(len(weights)))
        prob = sigmoid(z)
        predictions.append(1 if prob >= threshold else 0)
    return predictions


if __name__ == "__main__":
    random.seed(42)

    # Same seed-locked synthetic data the Forge Gate oracle uses.
    n_samples = 40
    X, y = [], []
    for _ in range(n_samples // 2):
        X.append([random.gauss(-2, 1), random.gauss(-2, 1)])
        y.append(0)
    for _ in range(n_samples // 2):
        X.append([random.gauss(2, 1), random.gauss(2, 1)])
        y.append(1)

    weights, bias = logistic_regression_fit(X, y, learning_rate=0.1, iterations=100)
    preds = logistic_regression_predict(X, weights, bias)
    accuracy = sum(preds[i] == y[i] for i in range(n_samples)) / n_samples

    assert accuracy >= 0.85, f"Accuracy = {accuracy} is too low"

    print(
        f"LOGISTIC_REGRESSION PASS: accuracy = {accuracy:.3f}, "
        f"weights = {[f'{w:.2f}' for w in weights]}"
    )
