"""Self-contained correct implementation of k-Means clustering.

This file can be run standalone (`python examples/kmeans_clustering_good.py`)
or verified against the Forge Gate oracle:

    forge-gate verify examples/kmeans_clustering_good.py --pattern kmeans_clustering
"""
import math
import random
from typing import List, Tuple


def euclidean_distance(p1: List[float], p2: List[float]) -> float:
    """Compute Euclidean distance between two points."""
    return math.sqrt(sum((p1[i] - p2[i]) ** 2 for i in range(len(p1))))


def kmeans(
    X: List[List[float]],
    k: int,
    max_iterations: int = 100,
    tolerance: float = 1e-4,
) -> Tuple[List[List[float]], List[int]]:
    """Fit k-means clustering.

    Returns:
        (centroids, assignments): final centroids and cluster index per sample.
    """
    n_samples = len(X)
    n_features = len(X[0]) if X else 0

    # Initialize centroids by randomly selecting k samples.
    indices = random.sample(range(n_samples), k)
    centroids = [X[i][:] for i in indices]

    for _ in range(max_iterations):
        # Assign each sample to the nearest centroid.
        assignments = []
        for i in range(n_samples):
            distances = [euclidean_distance(X[i], centroids[j]) for j in range(k)]
            assignments.append(distances.index(min(distances)))

        # Recompute centroids as the mean of assigned points.
        new_centroids = []
        for j in range(k):
            cluster_points = [X[i] for i in range(n_samples) if assignments[i] == j]
            if cluster_points:
                centroid = [
                    sum(p[f] for p in cluster_points) / len(cluster_points)
                    for f in range(n_features)
                ]
            else:
                centroid = centroids[j][:]
            new_centroids.append(centroid)

        movement = sum(euclidean_distance(centroids[j], new_centroids[j]) for j in range(k))
        centroids = new_centroids

        if movement < tolerance:
            break

    # Final assignment.
    assignments = []
    for i in range(n_samples):
        distances = [euclidean_distance(X[i], centroids[j]) for j in range(k)]
        assignments.append(distances.index(min(distances)))

    return centroids, assignments


def intra_cluster_distance(
    X: List[List[float]], centroids: List[List[float]], assignments: List[int]
) -> float:
    """Average distance from each point to its assigned centroid."""
    total = 0.0
    for i in range(len(X)):
        c = assignments[i]
        total += euclidean_distance(X[i], centroids[c])
    return total / len(X) if X else 0.0


if __name__ == "__main__":
    random.seed(42)

    # Same seed-locked synthetic data the Forge Gate oracle uses.
    n_per_cluster = 20
    X = []
    for _ in range(n_per_cluster):
        X.append([random.gauss(0, 0.5), random.gauss(0, 0.5)])
    for _ in range(n_per_cluster):
        X.append([random.gauss(5, 0.5), random.gauss(0, 0.5)])
    for _ in range(n_per_cluster):
        X.append([random.gauss(2.5, 0.5), random.gauss(4, 0.5)])

    centroids, assignments = kmeans(X, k=3, max_iterations=100)

    intra_dist = intra_cluster_distance(X, centroids, assignments)

    centroid_dists = []
    for i in range(3):
        for j in range(i + 1, 3):
            centroid_dists.append(euclidean_distance(centroids[i], centroids[j]))

    assert intra_dist < 2.0, f"Intra-cluster distance = {intra_dist} is too high"
    assert min(centroid_dists) > 2.0, "Centroids are too close together"

    print(f"KMEANS_CLUSTERING PASS: intra_dist = {intra_dist:.3f}, k = 3")
