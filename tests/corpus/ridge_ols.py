"""Alternative ridge: OLS (lambda=0) on well-conditioned data."""
from typing import List

def matrix_transpose(A):
    return [[A[i][j] for i in range(len(A))] for j in range(len(A[0]))]

def matrix_multiply(A, B):
    return [[sum(A[i][k] * B[k][j] for k in range(len(A[0]))) for j in range(len(B[0]))] for i in range(len(A))]

def matrix_vector_multiply(A, v):
    return [sum(A[i][j] * v[j] for j in range(len(v))) for i in range(len(A))]

def gaussian_elimination(A, b):
    n = len(A)
    A = [row[:] for row in A]
    b = b[:]
    for col in range(n):
        max_row = col
        for r in range(col + 1, n):
            if abs(A[r][col]) > abs(A[max_row][col]): max_row = r
        A[col], A[max_row] = A[max_row], A[col]
        b[col], b[max_row] = b[max_row], b[col]
        for r in range(col + 1, n):
            factor = A[r][col] / A[col][col]
            for c in range(col, n):
                A[r][c] -= factor * A[col][c]
            b[r] -= factor * b[col]
    x = [0.0] * n
    for i in range(n - 1, -1, -1):
        x[i] = b[i]
        for j in range(i + 1, n):
            x[i] -= A[i][j] * x[j]
        x[i] /= A[i][i]
    return x

def ridge_regression_normal_eq(X, y, lambda_reg=0.01):
    Xt = matrix_transpose(X)
    XTX = matrix_multiply(Xt, X)
    XTy = matrix_vector_multiply(Xt, y)
    return gaussian_elimination(XTX, XTy)

def ridge_regression_predict(X, weights):
    return matrix_vector_multiply(X, weights)
