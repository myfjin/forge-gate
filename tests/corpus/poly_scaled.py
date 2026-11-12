"""Alternative polynomial: fits on scaled x (still recovers coefficients)."""
from typing import List

def fit_polynomial(x_vals, y_vals, degree):
    s = max(abs(x) for x in x_vals) if x_vals else 1.0
    scaled = [x / s for x in x_vals]
    V = [[x ** j for j in range(degree + 1)] for x in scaled]
    n = len(x_vals)
    VtV = [[sum(V[i][j] * V[i][k] for i in range(n)) for k in range(degree + 1)] for j in range(degree + 1)]
    Vty = [sum(V[i][j] * y_vals[i] for i in range(n)) for j in range(degree + 1)]
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
    c_scaled = [0.0] * (degree + 1)
    for i in range(degree, -1, -1):
        c_scaled[i] = aug[i][degree + 1]
        for j in range(i + 1, degree + 1):
            c_scaled[i] -= aug[i][j] * c_scaled[j]
        c_scaled[i] /= aug[i][i]
    # Convert back to original scale: y = sum c'_i (x/s)^i = sum (c'_i / s^i) x^i
    coeffs = [c_scaled[i] / (s ** i) for i in range(degree + 1)]
    return coeffs

def eval_poly(coeffs, x):
    result = 0.0
    power = 1.0
    for c in coeffs:
        result += c * power
        power *= x
    return result
