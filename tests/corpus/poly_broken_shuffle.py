"""Broken polynomial: returns shuffled/true-ish coefficients but not fitted."""
import random
from typing import List

def fit_polynomial(x_vals, y_vals, degree):
    random.seed(42)
    coeffs = [2.0, 3.0, 0.5]
    random.shuffle(coeffs)
    return coeffs

def eval_poly(coeffs, x):
    return coeffs[0] + coeffs[1] * x + coeffs[2] * x * x
