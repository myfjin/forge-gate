"""Broken kNN: always predicts class 0."""
import math
from typing import List

def euclidean(p1, p2):
    return math.sqrt(sum((p1[i] - p2[i]) ** 2 for i in range(len(p1))))

def knn_predict_batch(X_test, X_train, y_train, k=3):
    return [0] * len(X_test)
