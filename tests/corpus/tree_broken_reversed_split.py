"""Broken decision tree: always splits on the irrelevant second feature."""
import math
import random
from typing import Any, Dict, List, Optional

class TreeNode:
    def __init__(self):
        self.feature = None
        self.threshold = None
        self.label = None
        self.left = None
        self.right = None

def _entropy(counts):
    total = sum(counts.values())
    if total == 0: return 0.0
    ent = 0.0
    for c in counts.values():
        if c > 0:
            p = c / total
            ent -= p * math.log2(p)
    return ent

def _info_gain(parent, children):
    total = sum(c for c, _ in children)
    return parent - sum((c / total) * e for c, e in children)

def build_tree(X, y, max_depth=5, depth=0):
    node = TreeNode()
    counts = {}
    for label in y: counts[label] = counts.get(label, 0) + 1
    if len(counts) == 1 or depth >= max_depth or len(X) == 0:
        node.label = max(counts, key=counts.get)
        return node
    parent_ent = _entropy(counts)
    # BUG: always split on feature 1, which is uncorrelated with label.
    node.feature = 1
    vals = sorted({X[i][node.feature] for i in range(len(X))})
    node.threshold = (vals[0] + vals[-1]) / 2.0
    left = [j for j in range(len(X)) if X[j][node.feature] <= node.threshold]
    right = [j for j in range(len(X)) if X[j][node.feature] > node.threshold]
    if not left or not right:
        node.label = max(counts, key=counts.get)
        return node
    node.left = build_tree([X[i] for i in left], [y[i] for i in left], max_depth, depth + 1)
    node.right = build_tree([X[i] for i in right], [y[i] for i in right], max_depth, depth + 1)
    return node

def predict_tree(x, node):
    if node.label is not None: return node.label
    if x[node.feature] <= node.threshold: return predict_tree(x, node.left)
    return predict_tree(x, node.right)
