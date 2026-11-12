"""Broken kNN: uses L1 distance but labels still may pass? Make it fail by using constant."""
import math
from typing import List

def _dist(p1, p2):
    # BUG: city-block distance not the issue; we flip sign so far points look near.
    return -sum(abs(p1[i] - p2[i]) for i in range(len(p1)))

def knn_predict_batch(X_test, X_train, y_train, k=3):
    preds = []
    for x in X_test:
        dists = sorted((_dist(x, X_train[i]), y_train[i]) for i in range(len(X_train)))
        preds.append(dists[0][1])
    return preds
