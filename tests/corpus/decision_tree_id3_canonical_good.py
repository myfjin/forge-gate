"""Self-contained correct implementation of an ID3 decision tree.

This file can be run standalone or verified against the Forge Gate oracle:

    forge-gate verify examples/decision_tree_id3_good.py --pattern decision_tree_id3
"""
import math
import random
from typing import Any, Dict, List, Optional


class TreeNode:
    """A node in the decision tree."""

    def __init__(self) -> None:
        self.feature: Optional[int] = None
        self.threshold: Optional[float] = None
        self.label: Optional[int] = None
        self.left: Optional["TreeNode"] = None
        self.right: Optional["TreeNode"] = None


def _entropy(class_counts: Dict[int, int]) -> float:
    total = sum(class_counts.values())
    if total == 0:
        return 0.0
    ent = 0.0
    for count in class_counts.values():
        if count > 0:
            p = count / total
            ent -= p * math.log2(p)
    return ent


def _information_gain(
    parent_entropy: float, child_entropies: List[tuple[int, float]]
) -> float:
    total = sum(count for count, _ in child_entropies)
    weighted = sum((count / total) * ent for count, ent in child_entropies)
    return parent_entropy - weighted


def build_tree(
    X: List[List[float]],
    y: List[int],
    max_depth: int = 5,
    depth: int = 0,
) -> TreeNode:
    """Build a decision tree using ID3 (information gain)."""
    node = TreeNode()

    class_counts: Dict[int, int] = {}
    for label in y:
        class_counts[label] = class_counts.get(label, 0) + 1

    if len(class_counts) == 1 or depth >= max_depth or len(X) == 0:
        node.label = max(class_counts, key=class_counts.get)
        return node

    parent_entropy = _entropy(class_counts)
    n_features = len(X[0]) if X else 0
    best_gain = 0.0
    best_feature: Optional[int] = None
    best_threshold: Optional[float] = None

    for feature in range(n_features):
        thresholds = sorted({X[i][feature] for i in range(len(X))})
        for i in range(len(thresholds) - 1):
            threshold = (thresholds[i] + thresholds[i + 1]) / 2.0
            left_indices = [j for j in range(len(X)) if X[j][feature] <= threshold]
            right_indices = [j for j in range(len(X)) if X[j][feature] > threshold]
            if not left_indices or not right_indices:
                continue

            left_counts: Dict[int, int] = {}
            right_counts: Dict[int, int] = {}
            for idx in left_indices:
                left_counts[y[idx]] = left_counts.get(y[idx], 0) + 1
            for idx in right_indices:
                right_counts[y[idx]] = right_counts.get(y[idx], 0) + 1

            gain = _information_gain(
                parent_entropy,
                [(len(left_indices), _entropy(left_counts)),
                 (len(right_indices), _entropy(right_counts))],
            )
            if gain > best_gain:
                best_gain = gain
                best_feature = feature
                best_threshold = threshold

    if best_feature is None:
        node.label = max(class_counts, key=class_counts.get)
        return node

    left_indices = [j for j in range(len(X)) if X[j][best_feature] <= best_threshold]
    right_indices = [j for j in range(len(X)) if X[j][best_feature] > best_threshold]

    X_left = [X[i] for i in left_indices]
    y_left = [y[i] for i in left_indices]
    X_right = [X[i] for i in right_indices]
    y_right = [y[i] for i in right_indices]

    node.feature = best_feature
    node.threshold = best_threshold
    node.left = build_tree(X_left, y_left, max_depth, depth + 1)
    node.right = build_tree(X_right, y_right, max_depth, depth + 1)
    return node


def predict_tree(x: List[float], node: TreeNode) -> int:
    """Predict class for a single sample."""
    if node.label is not None:
        return node.label
    if x[node.feature] <= node.threshold:
        return predict_tree(x, node.left)
    return predict_tree(x, node.right)


if __name__ == "__main__":
    random.seed(42)

    n_samples = 50
    X: List[List[float]] = []
    y: List[int] = []
    for _ in range(n_samples // 2):
        X.append([random.uniform(0, 2.5), random.uniform(0, 5)])
        y.append(0)
    for _ in range(n_samples // 2):
        X.append([random.uniform(2.5, 5), random.uniform(0, 5)])
        y.append(1)

    tree = build_tree(X, y, max_depth=5)
    preds = [predict_tree(X[i], tree) for i in range(len(X))]
    accuracy = sum(preds[i] == y[i] for i in range(len(X))) / len(X)

    assert accuracy >= 0.95, f"Accuracy = {accuracy} is too low"

    print(f"DECISION_TREE_ID3 PASS: accuracy = {accuracy:.3f}")
