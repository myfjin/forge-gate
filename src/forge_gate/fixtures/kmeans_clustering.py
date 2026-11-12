#!/usr/bin/env python3
"""Gate fixture for kmeans_clustering.

PASS is decided by clustering quality properties, not by matching the AURA
oracle's exact centroid coordinates.
"""
import random
import sys
from pathlib import Path

_pkg = Path(__file__).resolve().parents[2]
if str(_pkg) not in sys.path:
    sys.path.insert(0, str(_pkg))

from forge_gate.catalog import reference_path
from forge_gate.fixture_engine import approx_eq, emit, get_fn, load_module


def euclidean_distance(p1, p2):
    return sum((p1[i] - p2[i]) ** 2 for i in range(len(p1))) ** 0.5


def intra_cluster_distance(X, centroids, assignments):
    total = 0.0
    for i in range(len(X)):
        c = assignments[i]
        total += euclidean_distance(X[i], centroids[c])
    return total / len(X) if X else 0.0


def main():
    if len(sys.argv) < 2:
        print("usage: kmeans_clustering.py <user_file.py>", file=sys.stderr)
        sys.exit(2)

    user_path = sys.argv[1]
    aura_path = str(reference_path("kmeans_clustering"))

    user_mod = load_module(user_path, "user_impl")
    aura_mod = load_module(aura_path, "aura_impl")

    user_kmeans = get_fn(user_mod, "kmeans")
    aura_kmeans = get_fn(aura_mod, "kmeans")

    random.seed(42)
    n_per_cluster = 20
    X = []
    for _ in range(n_per_cluster):
        X.append([random.gauss(0, 0.5), random.gauss(0, 0.5)])
    for _ in range(n_per_cluster):
        X.append([random.gauss(5, 0.5), random.gauss(0, 0.5)])
    for _ in range(n_per_cluster):
        X.append([random.gauss(2.5, 0.5), random.gauss(4, 0.5)])

    user_centroids, user_assignments = user_kmeans(X, k=3, max_iterations=100)
    aura_centroids, aura_assignments = aura_kmeans(X, k=3, max_iterations=100)

    user_intra = intra_cluster_distance(X, user_centroids, user_assignments)
    aura_intra = intra_cluster_distance(X, aura_centroids, aura_assignments)

    user_centroid_dists = []
    for i in range(3):
        for j in range(i + 1, 3):
            user_centroid_dists.append(euclidean_distance(user_centroids[i], user_centroids[j]))
    aura_centroid_dists = []
    for i in range(3):
        for j in range(i + 1, 3):
            aura_centroid_dists.append(euclidean_distance(aura_centroids[i], aura_centroids[j]))

    canonical_match = (
        approx_eq(user_intra, aura_intra, rel_tol=0.1)
        and approx_eq(min(user_centroid_dists), min(aura_centroid_dists), rel_tol=0.1)
    )

    metrics = {
        "intra_cluster_distance": round(user_intra, 6),
        "min_centroid_separation": round(min(user_centroid_dists), 6),
        "canonical_match": canonical_match,
    }

    ok = (
        user_intra < 2.0
        and min(user_centroid_dists) > 2.0
    )

    status = "PASS" if ok else "FAIL"
    reason = "k-means quality properties hold" if ok else "k-means quality properties fail"

    emit({
        "status": status,
        "pattern": "kmeans_clustering",
        "reason": reason,
        "metrics": metrics,
    })


if __name__ == "__main__":
    main()
