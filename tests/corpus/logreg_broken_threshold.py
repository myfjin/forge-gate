"""Broken logistic regression: threshold of 0.0 flips all predictions."""
import math
import random
from typing import List, Tuple

def sigmoid(z):
    if z > 100: return 1.0
    if z < -100: return 0.0
    return 1.0 / (1.0 + math.exp(-z))

def logistic_regression_fit(X, y, learning_rate=0.01, iterations=100):
    random.seed(42)
    n_samples = len(X)
    n_features = len(X[0]) if X else 0
    weights = [0.0] * n_features
    bias = 0.0
    for _ in range(iterations):
        preds = [sigmoid(bias + sum(weights[j] * X[i][j] for j in range(n_features))) for i in range(n_samples)]
        errors = [preds[i] - y[i] for i in range(n_samples)]
        dw = [sum(errors[i] * X[i][j] for i in range(n_samples)) / n_samples for j in range(n_features)]
        db = sum(errors) / n_samples
        weights = [weights[j] - learning_rate * dw[j] for j in range(n_features)]
        bias -= learning_rate * db
    return weights, bias

def logistic_regression_predict(X, weights, bias, threshold=0.5):
    # BUG: threshold forced to 0.0
    return [1 if sigmoid(bias + sum(weights[j] * X[i][j] for j in range(len(weights)))) > 0.0 else 0 for i in range(len(X))]
