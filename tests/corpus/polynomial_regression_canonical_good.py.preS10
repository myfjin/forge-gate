"""Self-contained correct implementation of polynomial regression via least squares.

This file can be run standalone or verified against the Forge Gate oracle:

    forge-gate verify examples/polynomial_regression_good.py --pattern polynomial_regression
"""
import math
import random
from typing import List


def gaussian_elimination(A: List[List[float]], b: List[float]) -> List[float]:
    """Solve Ax=b via Gaussian elimination with partial pivoting."""
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
    """Build Vandermonde design matrix."""
    n = len(x_vals)
    V = [[0.0] * (degree + 1) for _ in range(n)]
    for i in range(n):
        for j in range(degree + 1):
            V[i][j] = x_vals[i] ** j
    return V


def fit_polynomial(x_vals: List[float], y_vals: List[float], degree: int) -> List[float]:
    """Fit polynomial of given degree via normal equations."""
    V = vandermonde_matrix(x_vals, degree)
    n_cols = degree + 1
    VtV = [[0.0] * n_cols for _ in range(n_cols)]
    Vty = [0.0] * n_cols

    for j in range(n_cols):
        for k in range(n_cols):
            for i in range(len(x_vals)):
                VtV[j][k] += V[i][j] * V[i][k]
        for i in range(len(x_vals)):
            Vty[j] += V[i][j] * y_vals[i]

    return gaussian_elimination(VtV, Vty)


def eval_poly(coeffs: List[float], x: float) -> float:
    """Evaluate polynomial at x given coefficients."""
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

    for i, (true_c, fit_c) in enumerate(zip(true_coeffs, fitted_coeffs)):
        assert abs(true_c - fit_c) < 0.2, f"Coeff {i}: true={true_c}, fit={fit_c}"

    print(f"polynomial_regression.py: fitted_coeffs={fitted_coeffs}, mse={mse:.4f} PASS")
