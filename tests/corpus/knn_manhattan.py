"""Alternative kNN: uses Manhattan (L1) distance with k=5."""
from collections import Counter
from typing import List

def manhattan(p1, p2):
    return sum(abs(p1[i] - p2[i]) for i in range(len(p1)))

def knn_predict_batch(X_test, X_train, y_train, k=5):
    preds = []
    for x in X_test:
        dists = sorted((manhattan(x, X_train[i]), y_train[i]) for i in range(len(X_train)))
        top = [label for _, label in dists[:k]]
        preds.append(Counter(top).most_common(1)[0][0])
    return preds
