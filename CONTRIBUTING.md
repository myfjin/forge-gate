# Contributing to Forge Gate

Thank you for looking. Issues and pull requests are welcome at
[myfjin/forge-gate](https://github.com/myfjin/forge-gate/issues).

## How this repository is made

Forge Gate is developed in a private working repository. Most of this tree, everything
except the files listed below, is **published** from it by a script, and is never edited
by hand here. A pull request that changes published code is welcome: the maintainers
carry the change into the working repository by hand, and the next publish brings it
back here under your authorship credit in the commit message.

Changed here directly, by pull request: `LICENSE`, `NOTICE`, `README.md`,
`CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, `pyproject.toml`, `.gitignore`,
`.github/`.

## Sign your commits (DCO)

Every commit needs a `Signed-off-by:` line, which certifies the
[Developer Certificate of Origin](https://developercertificate.org/):

    git commit -s

CI refuses a pull request with an unsigned commit. Do not use GitHub's "Update branch"
button: its merge commit carries no sign-off. Rebase instead.

## Running the tests

    python3 -m venv .venv && .venv/bin/pip install . pytest pytest-timeout
    PYTHONPATH=src .venv/bin/python -m pytest -q

The default tier uses fixtures only: no network, no other project. Tests that need the
AURA pattern library (`pytest -m live`) fail naming the setting they need.
