"""Broken decision tree: always predicts majority class."""
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
    node.label = max(counts, key=counts.get)
    return node

def predict_tree(x, node):
    return node.label
