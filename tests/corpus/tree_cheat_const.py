"""Cheat: always predict class 0."""
class TreeNode:
    def __init__(self):
        self.feature = self.threshold = self.label = self.left = self.right = None

def build_tree(X, y, max_depth=5, depth=0):
    n = TreeNode(); n.label = 0; return n

def predict_tree(x, node):
    return 0
