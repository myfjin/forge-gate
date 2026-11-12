"""Edge: returns NaN weights."""
from typing import List

def ridge_regression_normal_eq(X, y, lambda_reg=0.01):
    return [float('nan')] * 3

def ridge_regression_predict(X, weights):
    return [sum(weights[j] * X[i][j] for j in range(len(weights))) for i in range(len(X))]
