"""Broken Naive Bayes: flips training labels."""
import math
import random
from collections import defaultdict
from typing import Dict, List, Tuple

def naive_bayes_train(texts, labels):
    random.seed(42)
    flipped = [1 - l for l in labels]
    n = len(texts)
    classes = set(flipped)
    counts = {c: 0 for c in classes}
    for l in flipped: counts[l] += 1
    priors = {c: counts[c] / n for c in classes}
    word_counts = {c: defaultdict(int) for c in classes}
    total = {c: 0 for c in classes}
    vocab = set(w for t in texts for w in t)
    for i in range(n):
        for w in texts[i]:
            word_counts[flipped[i]][w] += 1
            total[flipped[i]] += 1
    probs = {}
    for c in classes:
        probs[c] = {w: (word_counts[c][w] + 1) / (total[c] + len(vocab)) for w in vocab}
    return priors, probs

def naive_bayes_predict(text, priors, probs):
    best = None
    best_score = -float('inf')
    for c in priors:
        score = math.log(priors[c])
        for w in text:
            score += math.log(probs[c].get(w, 1e-9))
        if score > best_score:
            best_score = score
            best = c
    return best
