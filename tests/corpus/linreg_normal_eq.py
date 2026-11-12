"""Alternative correct linear regression via normal equations."""
import random
from typing import List, Tuple

def _mean(v: List[float]) -> float:
    return sum(v) / len(v) if v else 0.0

def linear_regression_gd(
    X: List[List[float]], y: List[float], learning_rate: float = 0.01, iterations: int = 100
) -> Tuple[List[float], float]:
    random.seed(42)
    n_samples = len(X)
    n_features = len(X[0]) if X else 0
    X_b = [[1.0] + row for row in X]
    XtX = [[sum(X_b[i][j] * X_b[i][k] for i in range(n_samples)) for k in range(n_features + 1)] for j in range(n_features + 1)]
    Xty = [sum(X_b[i][j] * y[i] for i in range(n_samples)) for j in range(n_features + 1)]
    # Gaussian elimination
    n = len(XtX)
    aug = [XtX[i][:] + [Xty[i]] for i in range(n)]
    for col in range(n):
        max_row = col
        for r in range(col + 1, n):
            if abs(aug[r][col]) > abs(aug[max_row][col]):
                max_row = r
        aug[col], aug[max_row] = aug[max_row], aug[col]
        for r in range(col + 1, n):
            factor = aug[r][col] / aug[col][col]
            for c in range(col, n + 1):
                aug[r][c] -= factor * aug[col][c]
    beta = [0.0] * n
    for i in range(n - 1, -1, -1):
        beta[i] = aug[i][n]
        for j in range(i + 1, n):
            beta[i] -= aug[i][j] * beta[j]
        beta[i] /= aug[i][i]
    return beta[1:], beta[0]
