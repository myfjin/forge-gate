# Getting started with ForgeProof

ForgeProof is **install-from-git** while it is alpha (0.1.0). There is no PyPI package yet.

This guide takes you from nothing to a real verdict on a real file, then shows you how to tell
whether a test suite actually tests anything. It also says plainly what ForgeProof does not do
yet.

---

## What you need

- **Python 3.10 or newer** (`requires-python = ">=3.10"`)
- **git**
- **Node.js and npm**, only if you want the JavaScript oracle's type gate. Without them that
  gate reports `unavailable`, and everything else still works.

## Install

    git clone https://github.com/myfjin/forgeproof.git
    cd forgeproof
    python3 -m venv .venv
    .venv/bin/pip install . pytest pytest-timeout
    export FORGE_GATE_HOME="$PWD"

**Do not skip the last line.** Several lenses still read files from the checkout rather than from
the installed package (`KNOWN-DEBT.md`, KD-2). Without `FORGE_GATE_HOME` those lenses cannot run,
and a correct file comes out `UNSTABLE` instead of `PASS`. Run the `export` inside the clone, in
every new shell, until KD-2 is fixed.

## Run the tests

    PYTHONPATH=src .venv/bin/python -m pytest -q

That is the default set: test data shipped in the repo only, no network, no pattern library. It
proves the machinery works on this machine. Five slower groups (`live`, `gates`, `js`, `brain`,
`network`) are switched off by default; the markers in `pyproject.toml` say what each one needs.

## Your first verdict: one good file, one broken file

`examples/` holds a pair of small programs for each of the 10 patterns the gate knows. The
`_good` file implements the algorithm correctly; the `_bad` file has one deliberate bug.

    .venv/bin/forgeproof list
    .venv/bin/forgeproof verify examples/linear_regression_gd_good.py --pattern linear_regression_gd
    .venv/bin/forgeproof verify examples/linear_regression_gd_bad.py  --pattern linear_regression_gd

`list` prints the 10 patterns. The good file ends in `PASS` and exit code 0; the bad one ends in
`FAIL` and exit code 1. A verdict shows its reason, the numbers it measured, and one line per lens
that ran (a `FAIL` stops at the first lens that objects):

    PASS: linear_regression_gd
      reason: Linear regression quality properties hold (unsupported here: twin-consensus)
      lenses:
        property-primary   PASS   Linear regression quality properties hold
        metamorphic        PASS   All 3 gating relations passed
        invariant          PASS   all ratified invariants hold
        twin-consensus     UNSUPPORTED   ... FORGE_GATE_LIBRARY_DIR is not set ...
        determinism        PASS   3 runs identical at tolerance 1e-12
        guard              PASS   all NaN inputs handled ...

**Read every lens line, not only the first word.**

- `UNSUPPORTED` means "this lens could not measure here", and it says why. It never counts as a
  pass in disguise. Twin-consensus compares your code with the same pattern written in other
  languages, so it needs a checkout of the AURA pattern library; until `FORGE_GATE_LIBRARY_DIR`
  points at one, it says so and steps aside.
- `UNSTABLE` means a lens errored or the references disagreed, so no verdict could be trusted.
- **Known oddity:** `kmeans_clustering_good.py` shows `determinism FAIL` inside a `PASS`. k-means
  can settle on different valid answers from run to run, so that lens is advisory for this one
  pattern; the line does not say so yet (`KNOWN-DEBT.md`, KD-6).

No language model takes part in any verdict. Every lens runs code and measures what came out.

## What the lenses check

| lens | the question it asks |
|---|---|
| property-primary | do the results have the properties a correct implementation must have? |
| metamorphic | if the input changes in a known way, does the output change the way it must? |
| invariant | do the rules that must always hold, hold? |
| twin-consensus | do implementations of the same pattern in other languages agree with yours? |
| determinism | do repeated runs with a fixed seed give the same answer? |
| guard | given NaN (not-a-number) as input, does the code pass it on honestly, or quietly return a plausible number? Infinity is tried too, but only recorded, because many correct functions have finite answers at infinity. Needs no reference at all (`docs/GUARD_LENS.md`) |

## Does your test suite test anything? The shared tools

A green test run is a claim about the tests, not about the code. Two small tools in `tools/`
make that claim checkable for any Python project kept in git and tested with pytest:

- **`tools/fg_gate.py`** runs the tests and calls a run green only when it can prove what ran: the
  counts come from the run's own fresh result file, the tests must not have changed the tree they
  were testing, and any tests you name must actually have run.
- **`tools/fg_mutrun.py`** plants a small bug you describe (a "mutant"), runs the tests, puts the
  code back, and reports whether the tests noticed. A mutant the tests do not notice shows you
  exactly where the suite is blind.

Both are described, with every option and what they do not do, in `docs/shared-tools.md`.

## What works today, honestly

- The verify gate covers **10 Python patterns**, each with a reference implementation that ships
  inside the package (byte copies from the AURA pattern library; where each came from is in
  `src/forgeproof/reference/ORIGIN.md`).
- The `scout`, `learn`, `ledger` and `chaos-calibrate` commands are **advisory or plumbing**
  today. `KNOWN-DEBT.md` says what each one does not do yet.
- **`KNOWN-DEBT.md` is the honest list.** Every known gap has its own entry: what it is, where it
  is, what "fixed" will look like, and who owns it. If something surprises you, look there first.

## Where to read next

- [`README.md`](https://github.com/myfjin/forgeproof/blob/main/README.md): what ForgeProof is and why
- [`docs/shared-tools.md`](https://github.com/myfjin/forgeproof/blob/main/docs/shared-tools.md): the gate and the mutant runner in full
- [`docs/GUARD_LENS.md`](https://github.com/myfjin/forgeproof/blob/main/docs/GUARD_LENS.md): the lens that needs no reference
- [`KNOWN-DEBT.md`](https://github.com/myfjin/forgeproof/blob/main/KNOWN-DEBT.md): what is not done, said plainly
- [`CONTRIBUTING.md`](https://github.com/myfjin/forgeproof/blob/main/CONTRIBUTING.md): how to send a change

## Contributing

Pull requests with **DCO sign-off** (`git commit -s`). No CLA. If an idea is bigger than one pull
request, open an issue first and talk it through.
