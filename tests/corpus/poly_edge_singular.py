"""Edge: duplicate x values cause singular matrix."""
from typing import List

def fit_polynomial(x_vals, y_vals, degree):
    n = len(x_vals)
    x = [0.0] * n
    V = [[x[i] ** j for j in range(degree + 1)] for i in range(n)]
    VtV = [[sum(V[i][j] * V[i][k] for i in range(n)) for k in range(degree + 1)] for j in range(degree + 1)]
    Vty = [sum(V[i][j] * y_vals[i] for i in range(n)) for j in range(degree + 1)]
    aug = [VtV[i][:] + [Vty[i]] for i in range(degree + 1)]
    for col in range(degree + 1):
        max_row = col
        for r in range(col + 1, degree + 1):
            if abs(aug[r][col]) > abs(aug[max_row][col]): max_row = r
        aug[col], aug[max_row] = aug[max_row], aug[col]
        if abs(aug[col][col]) < 1e-12:
            raise ValueError("singular")
        for r in range(col + 1, degree + 1):
            factor = aug[r][col] / aug[col][col]
            for c in range(col, degree + 2):
                aug[r][c] -= factor * aug[col][c]
    coeffs = [0.0] * (degree + 1)
    for i in range(degree, -1, -1):
        coeffs[i] = aug[i][degree + 1]
        for j in range(i + 1, degree + 1):
            coeffs[i] -= aug[i][j] * coeffs[j]
        coeffs[i] /= aug[i][i]
    return coeffs

def eval_poly(coeffs, x):
    return coeffs[0] + coeffs[1] * x + coeffs[2] * x * x
