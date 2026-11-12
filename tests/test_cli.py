"""Smoke tests for Forge Gate CLI.

These are basic happy-path and failure-path tests. 4Q owns the dedicated
disaster + contract suite; coordinate before expanding this file.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from forge_gate.catalog import reference_path

FIXTURES = [
    "kmeans_clustering",
    "linear_regression_gd",
    "knn_classifier",
    "logistic_regression",
    "naive_bayes_text",
    "polynomial_regression",
    "decision_tree_id3",
    "perceptron",
    "ridge_regression_normal_eq",
    "gradient_descent_momentum",
]


def _aura_file(pattern: str) -> Path:
    """The shipped reference implementation (byte copy of the library pattern)."""
    return reference_path(pattern)


def _copy_correct(tmp_path: Path, pattern: str) -> Path:
    src = _aura_file(pattern)
    dst = tmp_path / f"{pattern}_correct.py"
    dst.write_text(src.read_text(), encoding="utf-8")
    return dst


@pytest.mark.parametrize("pattern", FIXTURES)
def test_all_patterns_pass_on_aura_copy(tmp_path: Path, pattern: str):
    user_file = _copy_correct(tmp_path, pattern)
    result = subprocess.run(
        [sys.executable, "-m", "forge_gate.cli", "verify", str(user_file), "--pattern", pattern],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert f"PASS: {pattern}" in result.stdout


def test_broken_linear_regression_fails(tmp_path: Path):
    src = _aura_file("linear_regression_gd")
    code = src.read_text(encoding="utf-8")
    # Remove the / n_samples normalization from the gradient to make weights explode.
    broken = code.replace(
        "dw = [sum(errors[i] * X[i][j] for i in range(n_samples)) / n_samples\n              for j in range(n_features)]",
        "dw = [sum(errors[i] * X[i][j] for i in range(n_samples))\n              for j in range(n_features)]",
    ).replace(
        "db = sum(errors) / n_samples",
        "db = sum(errors)",
    )
    user_file = tmp_path / "linear_regression_gd_broken.py"
    user_file.write_text(broken, encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "-m", "forge_gate.cli", "verify", str(user_file), "--pattern", "linear_regression_gd"],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "FAIL: linear_regression_gd" in result.stdout


def test_missing_function_fails(tmp_path: Path):
    user_file = tmp_path / "wrong_fn.py"
    user_file.write_text("def not_the_function(X, y):\n    return None\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "-m", "forge_gate.cli", "verify", str(user_file), "--pattern", "linear_regression_gd"],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "FAIL: linear_regression_gd" in result.stdout
