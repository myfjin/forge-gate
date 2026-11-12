"""Broken kNN: inverts the correct kNN predictions."""
import math
from collections import Counter
from typing import List

def euclidean(p1, p2):
    return sum((p1[i] - p2[i]) ** 2 for i in range(len(p1))) ** 0.5

def _predict_one(x, X_train, y_train, k):
    dists = sorted((euclidean(x, X_train[i]), y_train[i]) for i in range(len(X_train)))
    top = [label for _, label in dists[:k]]
    return Counter(top).most_common(1)[0][0]

def knn_predict_batch(X_test, X_train, y_train, k=5):
    return [1 - _predict_one(x, X_train, y_train, k) for x in X_test]
