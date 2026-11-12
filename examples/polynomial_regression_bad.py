"""Self-contained BROKEN implementation of polynomial regression.

The bug: fits degree 1 instead of the requested degree (ignores the degree
argument), so quadratic coefficients are wrong. Verify with Forge Gate to see FAIL.

    forge-gate verify examples/polynomial_regression_bad.py --pattern polynomial_regression
"""
import math
import random
from typing import List


def gaussian_elimination(A: List[List[float]], b: List[float]) -> List[float]:
    n = len(A)
    aug = [A[i][:] + [b[i]] for i in range(n)]

    for col in range(n):
        max_row = col
        for row in range(col + 1, n):
            if abs(aug[row][col]) > abs(aug[max_row][col]):
                max_row = row
        aug[col], aug[max_row] = aug[max_row], aug[col]

        if abs(aug[col][col]) < 1e-12:
            raise ValueError("Matrix is singular")

        for row in range(col + 1, n):
            factor = aug[row][col] / aug[col][col]
            for j in range(col, n + 1):
                aug[row][j] -= factor * aug[col][j]

    x = [0.0] * n
    for i in range(n - 1, -1, -1):
        x[i] = aug[i][n]
        for j in range(i + 1, n):
            x[i] -= aug[i][j] * x[j]
        x[i] /= aug[i][i]

    return x


def vandermonde_matrix(x_vals: List[float], degree: int) -> List[List[float]]:
    """BUG: always builds degree-1 Vandermonde matrix."""
    n = len(x_vals)
    V = [[0.0] * 2 for _ in range(n)]
    for i in range(n):
        V[i][0] = 1.0
        V[i][1] = x_vals[i]
    return V


def fit_polynomial(x_vals: List[float], y_vals: List[float], degree: int) -> List[float]:
    """BUG: ignores degree and always fits a line."""
    V = vandermonde_matrix(x_vals, degree)
    VtV = [[0.0] * 2 for _ in range(2)]
    Vty = [0.0] * 2

    for j in range(2):
        for k in range(2):
            for i in range(len(x_vals)):
                VtV[j][k] += V[i][j] * V[i][k]
        for i in range(len(x_vals)):
            Vty[j] += V[i][j] * y_vals[i]

    coeffs_1 = gaussian_elimination(VtV, Vty)
    # BUG: silently returns degree-1 coefficients padded to length 3.
    return [coeffs_1[0], coeffs_1[1], 0.0]


def eval_poly(coeffs: List[float], x: float) -> float:
    result = 0.0
    power = 1.0
    for c in coeffs:
        result += c * power
        power *= x
    return result


if __name__ == "__main__":
    random.seed(42)

    true_coeffs = [2.0, 3.0, 0.5]
    x_vals = [float(i) * 0.5 for i in range(20)]
    y_vals = [eval_poly(true_coeffs, x) + random.gauss(0, 0.1) for x in x_vals]

    fitted_coeffs = fit_polynomial(x_vals, y_vals, 2)
    y_pred = [eval_poly(fitted_coeffs, x) for x in x_vals]
    mse = sum((y_pred[i] - y_vals[i]) ** 2 for i in range(len(y_vals))) / len(y_vals)

    print(f"polynomial_regression.py BAD: fitted_coeffs={fitted_coeffs}, mse={mse:.4f}")
