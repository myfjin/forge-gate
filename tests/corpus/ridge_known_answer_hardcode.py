"""Known-answer hardcode: returns the planted ridge weights without computing.

Under the current property-primary verdict this legitimately PASSes because the
fixture checks weight recovery against the planted ground truth. This is the
disclosed known-answer limitation of fixed fixtures; randomized/hidden fixtures
are the v2 roadmap fix.
"""
from typing import List

def ridge_regression_normal_eq(X, y, lambda_reg=0.01):
    return [2.0, 3.0, -1.0]

def ridge_regression_predict(X, weights):
    return [sum(weights[j] * X[i][j] for j in range(len(weights))) for i in range(len(X))]
