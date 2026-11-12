"""Self-contained BROKEN implementation of Multinomial Naive Bayes for text.

The bug: training ignores the labels and pools all documents into a single class,
so both class priors are 0.5 and word probabilities are identical. Predictions
become random/majority-class. Verify with Forge Gate to see FAIL.

    forge-gate verify examples/naive_bayes_text_bad.py --pattern naive_bayes_text
"""
import math
import random
from collections import defaultdict
from typing import Dict, List, Tuple


def naive_bayes_train(
    texts: List[List[str]],
    labels: List[int],
) -> Tuple[Dict[int, float], Dict[int, Dict[str, float]]]:
    """BUG: ignores labels and trains one shared model for all classes."""
    n_docs = len(texts)
    classes = set(labels)

    # BUG: priors forced equal regardless of class distribution.
    class_priors = {c: 0.5 for c in classes}

    # BUG: word counts pooled across ALL documents, not per-class.
    pooled_counts: Dict[str, int] = defaultdict(int)
    total_words = 0
    for doc in texts:
        for word in doc:
            pooled_counts[word] += 1
            total_words += 1

    vocab_size = len(set(w for text in texts for w in text))
    word_probs: Dict[int, Dict[str, float]] = {}
    for c in classes:
        word_probs[c] = {}
        for word in set(w for text in texts for w in text):
            word_probs[c][word] = (pooled_counts[word] + 1) / (total_words + vocab_size)

    return class_priors, word_probs


def naive_bayes_predict(
    text: List[str],
    class_priors: Dict[int, float],
    word_probs: Dict[int, Dict[str, float]],
) -> int:
    """Correct log-space prediction."""
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

    print(
        f"NAIVE_BAYES_TEXT BAD: accuracy = {accuracy:.3f}, "
        f"test_spam → {pred_spam}, test_ham → {pred_ham}"
    )
