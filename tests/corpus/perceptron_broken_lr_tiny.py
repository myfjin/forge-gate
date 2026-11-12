"""Broken perceptron: always predicts class 0."""
import random
from typing import List, Tuple

def perceptron_fit(X, y, learning_rate=1.0, max_iterations=100):
    random.seed(42)
    n_features = len(X[0]) if X else 0
    return [0.0] * n_features, -1.0

def perceptron_predict(X, weights, bias):
    return [1 if bias + sum(weights[j] * X[i][j] for j in range(len(weights))) >= 0 else 0 for i in range(len(X))]
