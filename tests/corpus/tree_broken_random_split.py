"""Broken decision tree: always splits on the irrelevant second feature."""
import random
from typing import List

class TreeNode:
    def __init__(self):
        self.feature = None
        self.threshold = None
        self.label = None
        self.left = None
        self.right = None

def build_tree(X, y, max_depth=5, depth=0):
    node = TreeNode()
    counts = {}
    for label in y: counts[label] = counts.get(label, 0) + 1
    if len(counts) == 1 or depth >= max_depth or len(X) <= 2:
        node.label = max(counts, key=counts.get)
        return node
    # BUG: always split on feature 1, which is uncorrelated with the label.
    node.feature = 1
    vals = [X[i][node.feature] for i in range(len(X))]
    node.threshold = (min(vals) + max(vals)) / 2.0
    left = [i for i in range(len(X)) if X[i][node.feature] <= node.threshold]
    right = [i for i in range(len(X)) if X[i][node.feature] > node.threshold]
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
