"""Self-contained BROKEN implementation of ridge regression via normal equations.

The bug: forgets to add the regularization term at all, and then the bad code
also *subtracts* X^T*y from the bias vector before solving, producing weights
that are the negative of the OLS solution and completely wrong. Verify with Forge
Gate to see FAIL.

    forge-gate verify examples/ridge_regression_normal_eq_bad.py --pattern ridge_regression_normal_eq
"""
import random
from typing import List


def matrix_transpose(A: List[List[float]]) -> List[List[float]]:
    if not A:
        return []
    m = len(A)
    n = len(A[0])
    return [[A[i][j] for i in range(m)] for j in range(n)]


def matrix_multiply(A: List[List[float]], B: List[List[float]]) -> List[List[float]]:
    m = len(A)
    n = len(A[0]) if A else 0
    p = len(B[0]) if B else 0
    result = [[0.0] * p for _ in range(m)]
    for i in range(m):
        for j in range(p):
            for k in range(n):
                result[i][j] += A[i][k] * B[k][j]
    return result


def matrix_vector_multiply(A: List[List[float]], v: List[float]) -> List[float]:
    result = [0.0] * len(A)
    for i in range(len(A)):
        for j in range(len(v)):
            result[i] += A[i][j] * v[j]
    return result


def gaussian_elimination(A: List[List[float]], b: List[float]) -> List[float]:
    """Solve Ax=b using Gaussian elimination with partial pivoting."""
    n = len(A)
    A = [row[:] for row in A]
    b = b[:]

    for col in range(n):
        max_row = col
        for row in range(col + 1, n):
            if abs(A[row][col]) > abs(A[max_row][col]):
                max_row = row
        A[col], A[max_row] = A[max_row], A[col]
        b[col], b[max_row] = b[max_row], b[col]

        for row in range(col + 1, n):
            if A[col][col] != 0:
                factor = A[row][col] / A[col][col]
                for j in range(col, n):
                    A[row][j] -= factor * A[col][j]
                b[row] -= factor * b[col]

    x = [0.0] * n
    for i in range(n - 1, -1, -1):
        x[i] = b[i]
        for j in range(i + 1, n):
            x[i] -= A[i][j] * x[j]
        if A[i][i] != 0:
            x[i] /= A[i][i]
    return x


def ridge_regression_normal_eq(
    X: List[List[float]],
    y: List[float],
    lambda_reg: float = 0.01,
) -> List[float]:
    """BUG: no regularization AND negates X^T*y, solving for -w instead of w."""
    X_T = matrix_transpose(X)
    XTX = matrix_multiply(X_T, X)
    XTy = matrix_vector_multiply(X_T, y)
    # BUG: should add lambda to XTX diagonal; instead we negate the RHS.
    neg_XTy = [-v for v in XTy]
    return gaussian_elimination(XTX, neg_XTy)


def ridge_regression_predict(X: List[List[float]], weights: List[float]) -> List[float]:
    return matrix_vector_multiply(X, weights)


if __name__ == "__main__":
    random.seed(42)

    n_samples = 30
    n_features = 3
    X = [[random.uniform(-5, 5) for _ in range(n_features)] for _ in range(n_samples)]
    true_weights = [2.0, 3.0, -1.0]
    y = [
        sum(true_weights[j] * X[i][j] for j in range(n_features)) + random.gauss(0, 0.5)
        for i in range(n_samples)
    ]

    weights = ridge_regression_normal_eq(X, y, lambda_reg=0.01)
    predictions = ridge_regression_predict(X, weights)
    ss_res = sum((predictions[i] - y[i]) ** 2 for i in range(n_samples))
    ss_tot = sum((y[i] - sum(y) / len(y)) ** 2 for i in range(n_samples))
    r2 = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else 0.0

    print(f"RIDGE_REGRESSION_NORMAL_EQ BAD: R² = {r2:.3f}, weights = {[f'{w:.2f}' for w in weights]}")
