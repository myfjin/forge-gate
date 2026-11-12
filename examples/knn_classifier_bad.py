"""Self-contained BROKEN implementation of k-nearest neighbors classification.

The bug: selects the k *farthest* neighbors instead of the k nearest.
Accuracy collapses. Verify with Forge Gate to see FAIL.

    forge-gate verify examples/knn_classifier_bad.py --pattern knn_classifier
"""
import math
import random
from typing import List


def euclidean_distance(p1: List[float], p2: List[float]) -> float:
    return math.sqrt(sum((p1[i] - p2[i]) ** 2 for i in range(len(p1))))


def knn_predict(
    x_test: List[float],
    X_train: List[List[float]],
    y_train: List[int],
    k: int = 3,
) -> int:
    """BUG: uses farthest neighbors instead of nearest."""
    distances = []
    for i in range(len(X_train)):
        dist = euclidean_distance(x_test, X_train[i])
        distances.append((dist, y_train[i]))
    distances.sort(reverse=True)
    # BUG: should be distances[:k]
    k_nearest = distances[:k]

    votes: dict[int, int] = {}
    for _, label in k_nearest:
        votes[label] = votes.get(label, 0) + 1
    return max(votes, key=votes.get)


def knn_predict_batch(
    X_test: List[List[float]],
    X_train: List[List[float]],
    y_train: List[int],
    k: int = 3,
) -> List[int]:
    return [knn_predict(X_test[i], X_train, y_train, k) for i in range(len(X_test))]


if __name__ == "__main__":
    random.seed(42)

    n_train = 50
    X_train: List[List[float]] = []
    y_train: List[int] = []
    for _ in range(n_train // 2):
        X_train.append([random.gauss(0, 1), random.gauss(0, 1)])
        y_train.append(0)
    for _ in range(n_train // 2):
        X_train.append([random.gauss(5, 1), random.gauss(5, 1)])
        y_train.append(1)

    n_test = 20
    X_test: List[List[float]] = []
    y_test: List[int] = []
    for _ in range(n_test // 2):
        X_test.append([random.gauss(0, 0.8), random.gauss(0, 0.8)])
        y_test.append(0)
    for _ in range(n_test // 2):
        X_test.append([random.gauss(5, 0.8), random.gauss(5, 0.8)])
        y_test.append(1)

    preds = knn_predict_batch(X_test, X_train, y_train, k=5)
    accuracy = sum(preds[i] == y_test[i] for i in range(n_test)) / n_test

    print(f"KNN_CLASSIFIER BAD: accuracy = {accuracy:.3f}, k = 5")
