"""Alternative polynomial: direct linear system via Vandermonde, no normal equations."""
from typing import List

def fit_polynomial(x_vals, y_vals, degree):
    n = len(x_vals)
    V = [[x ** j for j in range(degree + 1)] for x in x_vals]
    # V w = y, solve via Gram-Schmidt-ish normal equations anyway
    VtV = [[sum(V[i][j] * V[i][k] for i in range(n)) for k in range(degree + 1)] for j in range(degree + 1)]
    Vty = [sum(V[i][j] * y_vals[i] for i in range(n)) for j in range(degree + 1)]
    # Gaussian elimination
    aug = [VtV[i][:] + [Vty[i]] for i in range(degree + 1)]
    for col in range(degree + 1):
        max_row = col
        for r in range(col + 1, degree + 1):
            if abs(aug[r][col]) > abs(aug[max_row][col]): max_row = r
        aug[col], aug[max_row] = aug[max_row], aug[col]
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
    result = 0.0
    power = 1.0
    for c in coeffs:
        result += c * power
        power *= x
    return result
