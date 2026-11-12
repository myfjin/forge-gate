"""Alternative ridge: iterative Jacobi-ish refinement of normal equations."""
from typing import List

def matrix_transpose(A):
    return [[A[i][j] for i in range(len(A))] for j in range(len(A[0]))]

def matrix_multiply(A, B):
    return [[sum(A[i][k] * B[k][j] for k in range(len(A[0]))) for j in range(len(B[0]))] for i in range(len(A))]

def matrix_vector_multiply(A, v):
    return [sum(A[i][j] * v[j] for j in range(len(v))) for i in range(len(A))]

def ridge_regression_normal_eq(X, y, lambda_reg=0.01):
    Xt = matrix_transpose(X)
    XTX = matrix_multiply(Xt, X)
    for i in range(len(XTX)):
        XTX[i][i] += lambda_reg
    XTy = matrix_vector_multiply(Xt, y)
    # Jacobi iteration (approximation)
    w = [0.0] * len(XTy)
    for _ in range(1000):
        new = []
        for i in range(len(w)):
            sigma = sum(XTX[i][j] * w[j] for j in range(len(w)) if j != i)
            new.append((XTy[i] - sigma) / XTX[i][i])
        w = new
    return w

def ridge_regression_predict(X, weights):
    return matrix_vector_multiply(X, weights)
