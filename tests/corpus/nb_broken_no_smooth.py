"""Broken Naive Bayes: zero smoothing causes underflow / zero scores."""
import math
import random
from collections import defaultdict
from typing import Dict, List, Tuple

def naive_bayes_train(texts, labels):
    random.seed(42)
    n = len(texts)
    classes = set(labels)
    counts = {c: 0 for c in classes}
    for l in labels: counts[l] += 1
    priors = {c: counts[c] / n for c in classes}
    word_counts = {c: defaultdict(int) for c in classes}
    total = {c: 0 for c in classes}
    vocab = set(w for t in texts for w in t)
    for i in range(n):
        for w in texts[i]:
            word_counts[labels[i]][w] += 1
            total[labels[i]] += 1
    probs = {}
    for c in classes:
        # BUG: no smoothing
        probs[c] = {w: word_counts[c][w] / total[c] for w in vocab}
    return priors, probs

def naive_bayes_predict(text, priors, probs):
    best = None
    best_score = -float('inf')
    for c in priors:
        score = math.log(priors[c])
        for w in text:
            p = probs[c].get(w, 0.0)
            if p == 0:
                return max(priors, key=priors.get)
            score += math.log(p)
        if score > best_score:
            best_score = score
            best = c
    return best
