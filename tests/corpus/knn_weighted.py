"""Alternative kNN: weighted by inverse distance."""
import math
from typing import List

def euclidean(p1, p2):
    return math.sqrt(sum((p1[i] - p2[i]) ** 2 for i in range(len(p1))))

def knn_predict_batch(X_test, X_train, y_train, k=3):
    preds = []
    for x in X_test:
        dists = [(euclidean(x, X_train[i]), y_train[i]) for i in range(len(X_train))]
        dists.sort()
        votes = {}
        for d, lbl in dists[:k]:
            weight = 1.0 / max(d, 1e-9)
            votes[lbl] = votes.get(lbl, 0.0) + weight
        preds.append(max(votes, key=votes.get))
    return preds
