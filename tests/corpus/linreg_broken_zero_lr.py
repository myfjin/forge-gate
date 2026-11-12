"""Broken linreg: zero learning rate leaves weights at zero."""
import random
from typing import List, Tuple

def linear_regression_gd(
    X: List[List[float]], y: List[float], learning_rate: float = 0.01, iterations: int = 100
) -> Tuple[List[float], float]:
    random.seed(42)
    n_features = len(X[0]) if X else 0
    return [0.0] * n_features, 0.0
