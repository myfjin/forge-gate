# ORIGIN — reference implementations (copied, not written here)

These 10 files are **byte-for-byte copies** of patterns from the AURA Pattern Library.
`forge-gate verify` runs each one as the ground-truth oracle for its pattern, so the
shipped tool no longer reads the library at run time (build step 1, Q1, 2026-11-11).

- **Source:** aura-pattern-library, commit `c39c1dcc3adb0aa9bd04eb30f1f00045c14c88ce` (GitHub master, PR #7).
  Copied 2026-11-11 from `d7462d320c263f9870e80321e1edbb0d112fe6e6`, a dev-machine commit never pushed; re-pinned the same day
  to the published commit, where all 10 files are byte-identical (KNOWN-DEBT KD-3, the maintainer's
  option b). The sha256s below did not change.
- **Licence:** Apache-2.0, Copyright 2026 Illia Hladkyi (Reality Optimizer), the same
  author and licence as Forge Gate. The library's NOTICE applies to these files.
- **Changed here:** nothing. Do not edit them in this repo. Fix the pattern in the
  library, then re-copy, and update the commit and every sha256 below in the same change.
- **Drift check:** `tests/test_live_library.py` (tier `live`, needs
  `FORGE_GATE_LIBRARY_DIR`) compares each copy against the library checkout.

| file here | path in the library | sha256 |
|---|---|---|
| `kmeans_clustering.py` | `python/data-science/core_ml/kmeans_clustering.py` | `55d6e53c91e646eedc6157d86ed4343d30db62f7b44ecc9c06d8c65f8193bf8a` |
| `linear_regression_gd.py` | `python/data-science/core_ml/linear_regression_gd.py` | `bbe705e724d0c99a93932cf7297c4bb7ebc9ce2ae1683cc18b94ad7ad2b09e10` |
| `knn_classifier.py` | `python/data-science/core_ml/knn_classifier.py` | `54338b4c40e5677c35bc0c840467b442c628830cc368639c8d241339b258d269` |
| `logistic_regression.py` | `python/data-science/core_ml/logistic_regression.py` | `75b9aa0ced8513806b4f8d12d9742d2894be8574dcfe6885274e61ca71239dbd` |
| `naive_bayes_text.py` | `python/data-science/core_ml/naive_bayes_text.py` | `ae5ab902788a16dfc1d9cbdd46dbc5f01014b158d034443a7b10d705f357008b` |
| `polynomial_regression.py` | `python/data-science/statistical_modeling/polynomial_regression.py` | `24ca3b6a78bbbb8dd29f3887a32373c1eadec92e101709ede5db6aa1b291a4c4` |
| `decision_tree_id3.py` | `python/data-science/core_ml/decision_tree_id3.py` | `4e2d16f0d9766fdab3c7c04430628da97e994068017dbef8663277e5c0f6d986` |
| `perceptron.py` | `python/data-science/core_ml/perceptron.py` | `033bf1abc5ab1ed0e1476e18c1866c347ab838802b256a68e44769879e4c3611` |
| `ridge_regression_normal_eq.py` | `python/data-science/core_ml/ridge_regression_normal_eq.py` | `d93d8ede8a412ff3d4f958cbaeec1a61366c29c41fea0b50497fb6624d9d1bfd` |
| `gradient_descent_momentum.py` | `python/data-science/optimization_and_distances/gradient_descent_momentum.py` | `9d55286a77733865875b53abf1d7565bc96a2f40183f38ce595f7cf36917b15b` |
