"""Self-contained BROKEN implementation of binary logistic regression.

The bug is a single flipped sign: gradient *ascent* is performed during fitting
instead of gradient descent, so weights diverge and accuracy collapses. Verify
with Forge Gate to see FAIL.

    forge-gate verify examples/logistic_regression_bad.py --pattern logistic_regression
"""
import math
import random
from typing import List, Tuple


def sigmoid(z: float) -> float:
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
    """BUG: performs gradient ascent instead of gradient descent."""
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

        # BUG: should subtract the gradient. Adding it makes loss grow.
        weights = [weights[j] + learning_rate * dw[j] for j in range(n_features)]
        bias = bias + learning_rate * db

    return weights, bias


def logistic_regression_predict(
    X: List[List[float]],
    weights: List[float],
    bias: float,
    threshold: float = 0.5,
) -> List[int]:
    predictions = []
    for i in range(len(X)):
        z = bias + sum(weights[j] * X[i][j] for j in range(len(weights)))
        prob = sigmoid(z)
        predictions.append(1 if prob >= threshold else 0)
    return predictions


if __name__ == "__main__":
    random.seed(42)

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

    print(
        f"LOGISTIC_REGRESSION BAD: accuracy = {accuracy:.3f}, "
        f"weights = {[f'{w:.2f}' for w in weights]}"
    )
