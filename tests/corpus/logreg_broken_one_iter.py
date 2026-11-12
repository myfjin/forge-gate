"""Broken logistic regression: zero iterations, weights stay zero."""
import math
import random
from typing import List, Tuple

def sigmoid(z):
    if z > 100: return 1.0
    if z < -100: return 0.0
    return 1.0 / (1.0 + math.exp(-z))

def logistic_regression_fit(X, y, learning_rate=0.01, iterations=100):
    random.seed(42)
    n_features = len(X[0]) if X else 0
    return [0.0] * n_features, 0.0

def logistic_regression_predict(X, weights, bias, threshold=0.5):
    return [1 if sigmoid(bias + sum(weights[j] * X[i][j] for j in range(len(weights)))) >= threshold else 0 for i in range(len(X))]
