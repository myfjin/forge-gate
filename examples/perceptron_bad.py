"""Self-contained BROKEN implementation of the Perceptron learning algorithm.

The bug: weight update is subtracted instead of added (w -= error * x instead of w +=).
The perceptron never converges and accuracy stays around 0.5. Verify with Forge Gate
for FAIL.

    forge-gate verify examples/perceptron_bad.py --pattern perceptron
"""
import random
from typing import List, Tuple


def perceptron_fit(
    X: List[List[float]],
    y: List[int],
    learning_rate: float = 1.0,
    max_iterations: int = 100,
) -> Tuple[List[float], float]:
    """BUG: subtracts the weight update instead of adding it."""
    n_samples = len(X)
    n_features = len(X[0]) if X else 0

    y_labels = [2 * label - 1 if label in (0, 1) else label for label in y]

    weights = [0.0] * n_features
    bias = 0.0

    for _ in range(max_iterations):
        num_errors = 0
        for i in range(n_samples):
            z = bias + sum(weights[j] * X[i][j] for j in range(n_features))
            pred = 1 if z >= 0 else -1

            if pred != y_labels[i]:
                error = y_labels[i] - pred
                # BUG: should be + learning_rate * error * X[i][j] / 2.0
                weights = [
                    weights[j] - learning_rate * error * X[i][j] / 2.0
                    for j in range(n_features)
                ]
                bias = bias - learning_rate * error / 2.0
                num_errors += 1

        if num_errors == 0:
            break

    return weights, bias


def perceptron_predict(
    X: List[List[float]],
    weights: List[float],
    bias: float,
) -> List[int]:
    predictions = []
    for i in range(len(X)):
        z = bias + sum(weights[j] * X[i][j] for j in range(len(weights)))
        predictions.append(1 if z >= 0 else 0)
    return predictions


if __name__ == "__main__":
    random.seed(42)

    n_samples = 40
    X: List[List[float]] = []
    y: List[int] = []

    for _ in range(n_samples // 2):
        x1 = random.uniform(-3, 3)
        x2 = x1 - random.uniform(0.5, 2.5)
        X.append([x1, x2])
        y.append(0)
    for _ in range(n_samples // 2):
        x1 = random.uniform(-3, 3)
        x2 = x1 + random.uniform(0.5, 2.5)
        X.append([x1, x2])
        y.append(1)

    weights, bias = perceptron_fit(X, y, learning_rate=1.0, max_iterations=50)
    preds = perceptron_predict(X, weights, bias)
    accuracy = sum(preds[i] == y[i] for i in range(n_samples)) / n_samples

    print(f"PERCEPTRON BAD: accuracy = {accuracy:.3f}, weights = {[f'{w:.2f}' for w in weights]}")
