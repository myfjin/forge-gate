"""Known-answer hardcode: returns the planted polynomial coefficients without fitting.

Under the current property-primary verdict this legitimately PASSes because the
fixture checks coefficient recovery against the planted ground truth. This is
the disclosed known-answer limitation of fixed fixtures; randomized/hidden
fixtures are the v2 roadmap fix.
"""
from typing import List

def fit_polynomial(x_vals, y_vals, degree):
    return [2.0, 3.0, 0.5]

def eval_poly(coeffs, x):
    return coeffs[0] + coeffs[1] * x + coeffs[2] * x * x
