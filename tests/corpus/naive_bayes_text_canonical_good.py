"""Self-contained correct implementation of Multinomial Naive Bayes for text.

This file can be run standalone or verified against the Forge Gate oracle:

    forge-gate verify examples/naive_bayes_text_good.py --pattern naive_bayes_text
"""
import math
import random
from collections import defaultdict
from typing import Dict, List, Tuple


def naive_bayes_train(
    texts: List[List[str]],
    labels: List[int],
) -> Tuple[Dict[int, float], Dict[int, Dict[str, float]]]:
    """Train a Multinomial Naive Bayes classifier on tokenized text."""
    n_docs = len(texts)
    classes = set(labels)

    class_counts = {c: 0 for c in classes}
    for label in labels:
        class_counts[label] += 1

    class_priors = {c: class_counts[c] / n_docs for c in classes}

    word_counts = {c: defaultdict(int) for c in classes}
    total_words = {c: 0 for c in classes}

    for i in range(n_docs):
        label = labels[i]
        for word in texts[i]:
            word_counts[label][word] += 1
            total_words[label] += 1

    vocab_size = len(set(w for text in texts for w in text))
    word_probs: Dict[int, Dict[str, float]] = {}
    for c in classes:
        word_probs[c] = {}
        for word in set(w for text in texts for w in text):
            word_probs[c][word] = (word_counts[c][word] + 1) / (
                total_words[c] + vocab_size
            )

    return class_priors, word_probs


def naive_bayes_predict(
    text: List[str],
    class_priors: Dict[int, float],
    word_probs: Dict[int, Dict[str, float]],
) -> int:
    """Predict the class of a document."""
    best_class = None
    best_score = -float("inf")

    for c in class_priors:
        score = math.log(class_priors[c])
        for word in text:
            if word in word_probs[c]:
                score += math.log(word_probs[c][word])
            else:
                vocab_size = len(word_probs[c])
                score += math.log(
                    1.0 / (sum(len(wp) for wp in word_probs.values()) + vocab_size)
                )
        if score > best_score:
            best_score = score
            best_class = c

    return best_class


if __name__ == "__main__":
    random.seed(42)

    spam_words = ["cheap", "free", "buy", "offer", "click"]
    ham_words = ["meeting", "report", "data", "project", "deadline"]

    texts: List[List[str]] = []
    labels: List[int] = []

    for _ in range(10):
        texts.append([random.choice(spam_words) for _ in range(random.randint(3, 5))])
        labels.append(1)
    for _ in range(10):
        texts.append([random.choice(ham_words) for _ in range(random.randint(3, 5))])
        labels.append(0)

    class_priors, word_probs = naive_bayes_train(texts, labels)

    correct = 0
    for i in range(len(texts)):
        if naive_bayes_predict(texts[i], class_priors, word_probs) == labels[i]:
            correct += 1
    accuracy = correct / len(texts)

    pred_spam = naive_bayes_predict(["cheap", "free", "buy"], class_priors, word_probs)
    pred_ham = naive_bayes_predict(
        ["meeting", "data", "report"], class_priors, word_probs
    )

    assert accuracy >= 0.9, f"Accuracy = {accuracy} is too low"
    assert pred_spam == 1, "Failed to classify spam"
    assert pred_ham == 0, "Failed to classify ham"

    print(
        f"NAIVE_BAYES_TEXT PASS: accuracy = {accuracy:.3f}, "
        f"test_spam → {pred_spam}, test_ham → {pred_ham}"
    )
