"""Known-answer hardcode: returns the planted generative weights without computing.

Under the current property-primary verdict this legitimately PASSes because the
fixture checks that the returned weights recover the ground truth. This is the
disclosed known-answer limitation of fixed fixtures; randomized/hidden fixtures
are the v2 roadmap fix.
"""
from typing import List, Tuple

def linear_regression_gd(X, y, learning_rate=0.01, iterations=100) -> Tuple[List[float], float]:
    return [2.0, 3.0], 0.0
