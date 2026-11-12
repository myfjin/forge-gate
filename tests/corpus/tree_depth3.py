"""Alternative decision tree: shallower depth but still separable."""
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
    if len(counts) == 1 or depth >= min(max_depth, 3) or len(X) == 0:
        node.label = max(counts, key=counts.get)
        return node
    parent_ent = _entropy(counts)
    n_features = len(X[0]) if X else 0
    best_gain = 0.0
    best_feature = None
    best_threshold = None
    for feature in range(n_features):
        thresholds = sorted({X[i][feature] for i in range(len(X))})
        for i in range(len(thresholds) - 1):
            threshold = (thresholds[i] + thresholds[i + 1]) / 2.0
            left = [j for j in range(len(X)) if X[j][feature] <= threshold]
            right = [j for j in range(len(X)) if X[j][feature] > threshold]
            if not left or not right: continue
            lc = {}; rc = {}
            for idx in left: lc[y[idx]] = lc.get(y[idx], 0) + 1
            for idx in right: rc[y[idx]] = rc.get(y[idx], 0) + 1
            gain = _info_gain(parent_ent, [(len(left), _entropy(lc)), (len(right), _entropy(rc))])
            if gain > best_gain:
                best_gain = gain
                best_feature = feature
                best_threshold = threshold
    if best_feature is None:
        node.label = max(counts, key=counts.get)
        return node
    left = [j for j in range(len(X)) if X[j][best_feature] <= best_threshold]
    right = [j for j in range(len(X)) if X[j][best_feature] > best_threshold]
    node.feature = best_feature
    node.threshold = best_threshold
    node.left = build_tree([X[i] for i in left], [y[i] for i in left], max_depth, depth + 1)
    node.right = build_tree([X[i] for i in right], [y[i] for i in right], max_depth, depth + 1)
    return node

def predict_tree(x, node):
    if node.label is not None: return node.label
    if x[node.feature] <= node.threshold: return predict_tree(x, node.left)
    return predict_tree(x, node.right)
