"""Alternative kNN: uses k=3 instead of k=5."""
import math
from typing import List

def euclidean(p1, p2):
    return math.sqrt(sum((p1[i] - p2[i]) ** 2 for i in range(len(p1))))

def knn_predict_batch(X_test, X_train, y_train, k=3):
    preds = []
    for x in X_test:
        dists = sorted([(euclidean(x, X_train[i]), y_train[i]) for i in range(len(X_train))])
        top3 = [label for _, label in dists[:k]]
        counts = {}
        for lbl in top3:
            counts[lbl] = counts.get(lbl, 0) + 1
        preds.append(max(counts, key=counts.get))
    return preds
