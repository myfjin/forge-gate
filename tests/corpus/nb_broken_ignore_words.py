"""Broken Naive Bayes: ignores word frequencies and uses only priors."""
import math
import random
from typing import Dict, List, Tuple

def naive_bayes_train(texts, labels):
    n = len(texts)
    classes = set(labels)
    counts = {c: 0 for c in classes}
    for l in labels: counts[l] += 1
    priors = {c: counts[c] / n for c in classes}
    return priors, {}

def naive_bayes_predict(text, priors, probs):
    return max(priors, key=priors.get)
