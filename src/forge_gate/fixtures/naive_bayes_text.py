#!/usr/bin/env python3
"""Gate fixture for naive_bayes_text.

PASS is decided by text-classification quality properties, not by matching the
AURA oracle's exact probability tables.
"""
import random
import sys
from pathlib import Path

_pkg = Path(__file__).resolve().parents[2]
if str(_pkg) not in sys.path:
    sys.path.insert(0, str(_pkg))

from forge_gate.catalog import reference_path
from forge_gate.fixture_engine import approx_eq, emit, get_fn, load_module


def main():
    if len(sys.argv) < 2:
        print("usage: naive_bayes_text.py <user_file.py>", file=sys.stderr)
        sys.exit(2)

    user_path = sys.argv[1]
    aura_path = str(reference_path("naive_bayes_text"))

    user_mod = load_module(user_path, "user_impl")
    aura_mod = load_module(aura_path, "aura_impl")

    user_train = get_fn(user_mod, "naive_bayes_train")
    user_predict = get_fn(user_mod, "naive_bayes_predict")
    aura_train = get_fn(aura_mod, "naive_bayes_train")
    aura_predict = get_fn(aura_mod, "naive_bayes_predict")

    random.seed(42)
    spam_words = ["cheap", "free", "buy", "offer", "click", "win", "prize"]
    ham_words = ["meeting", "report", "data", "project", "deadline", "team", "review"]
    shared_words = ["the", "and", "of", "to", "a", "for"]

    texts, labels = [], []
    for _ in range(12):
        label = 1
        words = []
        for _ in range(random.randint(4, 6)):
            r = random.random()
            if r < 0.55:
                words.append(random.choice(spam_words))
            elif r < 0.80:
                words.append(random.choice(shared_words))
            else:
                # Cross-class noise: ham-looking word in a spam doc.
                words.append(random.choice(ham_words))
        texts.append(words)
        labels.append(label)
    for _ in range(12):
        label = 0
        words = []
        for _ in range(random.randint(4, 6)):
            r = random.random()
            if r < 0.55:
                words.append(random.choice(ham_words))
            elif r < 0.80:
                words.append(random.choice(shared_words))
            else:
                # Cross-class noise: spam-looking word in a ham doc.
                words.append(random.choice(spam_words))
        texts.append(words)
        labels.append(label)

    user_priors, user_probs = user_train(texts, labels)
    aura_priors, aura_probs = aura_train(texts, labels)

    correct = 0
    for i in range(len(texts)):
        if user_predict(texts[i], user_priors, user_probs) == labels[i]:
            correct += 1
    user_accuracy = correct / len(texts)

    aura_correct = 0
    for i in range(len(texts)):
        if aura_predict(texts[i], aura_priors, aura_probs) == labels[i]:
            aura_correct += 1
    aura_accuracy = aura_correct / len(texts)

    pred_spam = user_predict(["cheap", "free", "buy"], user_priors, user_probs)
    pred_ham = user_predict(["meeting", "data", "report"], user_priors, user_probs)

    priors_match = approx_eq(user_priors, aura_priors, rel_tol=1e-4)
    probs_match = approx_eq(user_probs, aura_probs, rel_tol=1e-4)

    metrics = {
        "accuracy": round(user_accuracy, 6),
        "oracle_accuracy": round(aura_accuracy, 6),
        "pred_spam": pred_spam,
        "pred_ham": pred_ham,
        "canonical_match": priors_match and probs_match,
    }

    ok = (
        user_accuracy >= 0.75
        and pred_spam == 1
        and pred_ham == 0
    )

    emit({
        "status": "PASS" if ok else "FAIL",
        "pattern": "naive_bayes_text",
        "reason": "Naive Bayes text classification properties hold" if ok else "Naive Bayes text classification properties fail",
        "metrics": metrics,
    })


if __name__ == "__main__":
    main()
