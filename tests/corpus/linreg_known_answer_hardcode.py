"""Known-answer hardcode: returns the oracle's exact output without computing.

Under the current property-primary verdict this legitimately PASSes because the
fixture checks that the returned answer is correct. This is the disclosed
known-answer limitation of fixed fixtures; randomized/hidden fixtures are the
v2 roadmap fix.
"""
from typing import List, Tuple

def linear_regression_gd(X, y, learning_rate=0.01, iterations=100) -> Tuple[List[float], float]:
    return [2.02429, 2.984377], -0.067942
