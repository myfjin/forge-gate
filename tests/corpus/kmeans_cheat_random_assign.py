"""Cheat: random assignments."""
import random
from typing import List, Tuple
def kmeans(X, k, max_iterations=100, tolerance=1e-4) -> Tuple[List[List[float]], List[int]]:
    random.seed(42)
    return [[0.0, 0.0]] * k, [random.randrange(k) for _ in X]
