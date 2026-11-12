"""Broken polynomial: fits degree 1."""
from typing import List

def fit_polynomial(x_vals, y_vals, degree):
    n = len(x_vals)
    V = [[1.0, x] for x in x_vals]
    VtV = [[sum(V[i][j] * V[i][k] for i in range(n)) for k in range(2)] for j in range(2)]
    Vty = [sum(V[i][j] * y_vals[i] for i in range(n)) for j in range(2)]
    aug = [VtV[i][:] + [Vty[i]] for i in range(2)]
    for col in range(2):
        max_row = col
        for r in range(col + 1, 2):
            if abs(aug[r][col]) > abs(aug[max_row][col]): max_row = r
        aug[col], aug[max_row] = aug[max_row], aug[col]
        for r in range(col + 1, 2):
            factor = aug[r][col] / aug[col][col]
            for c in range(col, 3):
                aug[r][c] -= factor * aug[col][c]
    c = [0.0, 0.0]
    for i in range(1, -1, -1):
        c[i] = aug[i][2]
        for j in range(i + 1, 2):
            c[i] -= aug[i][j] * c[j]
        c[i] /= aug[i][i]
    return [c[0], c[1], 0.0]

def eval_poly(coeffs, x):
    return coeffs[0] + coeffs[1] * x + coeffs[2] * x * x
