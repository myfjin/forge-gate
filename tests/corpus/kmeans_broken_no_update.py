"""Broken k-means: never updates centroids after init."""
import math
import random
from typing import List, Tuple

def euclidean(p1, p2):
    return math.sqrt(sum((p1[i] - p2[i]) ** 2 for i in range(len(p1))))

def kmeans(X: List[List[float]], k: int, max_iterations: int = 100, tolerance: float = 1e-4) -> Tuple[List[List[float]], List[int]]:
    random.seed(42)
    n_samples = len(X)
    indices = random.sample(range(n_samples), k)
    centroids = [X[i][:] for i in indices]
    assignments = [min(range(k), key=lambda j: euclidean(X[i], centroids[j])) for i in range(n_samples)]
    return centroids, assignments
