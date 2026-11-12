# Forge Gate

Deterministic regression gate for code-generating agents. Its reference implementations
come from the signed AURA pattern library and ship inside the package.

## Status

Alpha (0.1.0). Not on PyPI yet: install from a checkout.

## Install

```bash
git clone https://github.com/myfjin/forge-gate.git && cd forge-gate
python3 -m venv .venv && .venv/bin/pip install . pytest pytest-timeout
PYTHONPATH=src .venv/bin/python -m pytest -q   # default tier: fixtures only, no network, no library
npm ci                                          # only for the JS oracle (TypeScript type gate)
```

Python 3.10 or newer. Without `npm ci` the JS oracle's type gate reports `unavailable`.
Several lenses still need the checkout rather than an installed package (`KNOWN-DEBT.md`, KD-2).

## Configuration

Forge Gate reads no other project unless you tell it where. Each setting is an
environment variable, or a key in `config.toml` `[paths]` (per host, not tracked; copy
`config.example.toml`). The environment wins.

| key | needed for | default |
|---|---|---|
| `FORGE_GATE_LIBRARY_DIR` | the twin-consensus lens, `pytest -m live`, library tools | none: unset, those fail naming the key |
| `FORGE_GATE_HOME` | the lens tools when Forge Gate is installed outside a checkout | the checkout |
| `FORGE_GATE_PYTHON` | child runs (oracles, fixtures, lenses) | the interpreter running Forge Gate |
| `FORGE_GATE_NODE`, `_BUN`, `_QJS` | the JS oracle's three engines | `PATH` |
| `FORGE_GATE_SCRATCH` | short-lived lens scripts | `<checkout>/tmp` |

`deps.lock` pins what each outside route was checked against.

## Usage

```bash
# List gate-ready patterns
forge-gate list

# Verify a user implementation against a seed-locked oracle
forge-gate verify myfile.py --pattern linear_regression_gd
```

## Examples

The `examples/` directory contains self-contained reference implementations for
gate-ready patterns. Run them standalone, or pass them to the gate to see a live
`PASS`/`FAIL` verdict:

```bash
python examples/linear_regression_gd_good.py
forge-gate verify examples/linear_regression_gd_good.py --pattern linear_regression_gd
forge-gate verify examples/linear_regression_gd_bad.py  --pattern linear_regression_gd
```

Three patterns are covered:

- `linear_regression_gd_good.py` / `linear_regression_gd_bad.py`
- `kmeans_clustering_good.py` / `kmeans_clustering_bad.py`
- `logistic_regression_good.py` / `logistic_regression_bad.py`

The `_good` files implement the algorithm correctly and print `PASS` via the gate.
The `_bad` files each contain exactly one real bug (wrong gradient sign, nearest-
farthest centroid swap) and print `FAIL`.

## What it does

1. Resolves the pattern name in the shipped catalog (`forge_gate/data/gate_catalog.json`).
2. Loads the shipped reference implementation (`forge_gate/reference/`, byte copies of
   AURA library patterns; commit and sha256 in `reference/ORIGIN.md`).
3. Runs the user's file in a subprocess: fixed seed, 60s timeout, no network.
4. Runs the AURA oracle on the same fixture.
5. Compares exit codes and numeric outputs within tolerance.
6. Returns PASS / FAIL / UNSTABLE / UNSUPPORTED.

No LLM judgment is used in the verdict path. The gate is execution-backed.

## Catalog

Python-only v1 ships with 10 curated seed-locked oracles:

- `kmeans_clustering`
- `linear_regression_gd`
- `knn_classifier`
- `logistic_regression`
- `naive_bayes_text`
- `polynomial_regression`
- `decision_tree_id3`
- `perceptron`
- `ridge_regression_normal_eq`
- `gradient_descent_momentum`


## How this repository is made

Most of this tree is published from a private working repository by a script that
scrubs it and refuses to publish when any of its checks fails. `CONTRIBUTING.md` says
which files are edited here directly, and how a pull request to published code is
carried back.

## Licence

Apache-2.0. Copyright 2026 Illia Hladkyi. See `LICENSE` and `NOTICE`.
