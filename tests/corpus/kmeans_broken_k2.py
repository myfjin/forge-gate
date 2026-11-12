"""Broken k-means: requests k=2 instead of k=3."""
import math
import random
from typing import List, Tuple

def euclidean(p1, p2):
    return math.sqrt(sum((p1[i] - p2[i]) ** 2 for i in range(len(p1))))

def kmeans(X: List[List[float]], k: int, max_iterations: int = 100, tolerance: float = 1e-4) -> Tuple[List[List[float]], List[int]]:
    random.seed(42)
    actual_k = 2
    n_samples = len(X)
    n_features = len(X[0]) if X else 0
    indices = random.sample(range(n_samples), actual_k)
    centroids = [X[i][:] for i in indices]
    for _ in range(max_iterations):
        assignments = [min(range(actual_k), key=lambda j: euclidean(X[i], centroids[j])) for i in range(n_samples)]
        new = []
        for j in range(actual_k):
            pts = [X[i] for i in range(n_samples) if assignments[i] == j]
            if pts:
                new.append([sum(p[f] for p in pts) / len(pts) for f in range(n_features)])
            else:
                new.append(centroids[j][:])
        centroids = new
    assignments = [min(range(actual_k), key=lambda j: euclidean(X[i], centroids[j])) for i in range(n_samples)]
    return centroids, assignments
