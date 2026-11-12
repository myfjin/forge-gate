"""Alternative k-means: deterministic k-means++ style init."""
import math
import random
from typing import List, Tuple

def euclidean(p1, p2):
    return math.sqrt(sum((p1[i] - p2[i]) ** 2 for i in range(len(p1))))

def kmeans(X: List[List[float]], k: int, max_iterations: int = 100, tolerance: float = 1e-4) -> Tuple[List[List[float]], List[int]]:
    random.seed(42)
    n_samples = len(X)
    n_features = len(X[0]) if X else 0
    # Pick first center randomly deterministically, then farthest remaining point as rest.
    indices = list(range(n_samples))
    centroids = [X[indices[0]][:]]
    while len(centroids) < k:
        dists = [min(euclidean(x, c) for c in centroids) for x in X]
        idx = dists.index(max(dists))
        centroids.append(X[idx][:])
    for _ in range(max_iterations):
        assignments = [min(range(k), key=lambda j: euclidean(X[i], centroids[j])) for i in range(n_samples)]
        new = []
        for j in range(k):
            pts = [X[i] for i in range(n_samples) if assignments[i] == j]
            if pts:
                new.append([sum(p[f] for p in pts) / len(pts) for f in range(n_features)])
            else:
                new.append(centroids[j][:])
        move = sum(euclidean(centroids[j], new[j]) for j in range(k))
        centroids = new
        if move < tolerance:
            break
    assignments = [min(range(k), key=lambda j: euclidean(X[i], centroids[j])) for i in range(n_samples)]
    return centroids, assignments
