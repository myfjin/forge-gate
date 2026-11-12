"""Broken kNN: random predictions."""
import random
from typing import List

def knn_predict_batch(X_test, X_train, y_train, k=3):
    random.seed(42)
    return [random.choice(y_train) for _ in X_test]
