# ForgeProof

> **Renamed.** Forge Gate is now **ForgeProof** (2026-11-25). The name `forgegate` was already
> taken on PyPI and npm by another tool for AI agents, so we changed ours before the first release.
> For one release the old `forge-gate` command and `forge_gate` import still work and print a notice
> (see [`CHANGELOG.md`](CHANGELOG.md)). Links to the old repository address redirect here.
>
> **What the name means.** Read it like *waterproof* or *foolproof*: forged code does not get
> through. "Forge" keeps the project's history. "Proof" comes from the gunsmiths' proof house,
> where every barrel is test-fired with an extra-strong charge before it may be sold. ForgeProof
> does the same with code: it runs it, plants known faults as controls, and stamps a receipt.
> Other projects also use the name ForgeProof; this one checks AI-written code **by running it**.

ForgeProof checks code written by AI by **running it** next to a known-correct version on the
same inputs, and reports whether it behaves correctly. The known-correct versions come from the
signed AURA pattern library and ship inside the package. No language model takes part in any
verdict.

**New here? Start with [`docs/getting-started.md`](docs/getting-started.md):** from a clean clone
to a real PASS and a real FAIL in a few minutes.

## Why it exists

An AI agent can write code, write the tests for it, and report that the tests are green. All three
come from the same author, so a green run proves less than it seems. A test that cannot fail
passes, and a suite that never looks at the broken line passes too.

ForgeProof asks two questions that do not depend on trusting the author:

1. **Does this code behave like a correct implementation?** It runs your file and a reference
   implementation on the same seed-locked input, and judges the result through several
   independent lenses (below).
2. **Would the tests notice if the code were wrong?** The shared tools in `tools/` plant small
   deliberate bugs ("mutants"), re-run the tests, and report which bugs the suite missed.

## Status

Alpha (0.2.0). Not on PyPI yet: install from a checkout. On PyPI the package will be called
`forgeproof-cli` (`forgeproof` is too close to an existing project there); the command and the
import stay `forgeproof`. The verify gate covers 10 Python
patterns. Several commands (`scout`, `learn`, `ledger`, `chaos-calibrate`) are advisory or
plumbing today. **`KNOWN-DEBT.md` lists every known gap**: what it
is, where, what "fixed" will look like, and who owns it.

## Install

```bash
git clone https://github.com/myfjin/forgeproof.git && cd forgeproof
python3 -m venv .venv && .venv/bin/pip install . pytest pytest-timeout "setuptools>=61"
PYTHONPATH=src .venv/bin/python -m pytest -q   # default tier: fixtures only, no network, no library
npm ci                                          # only for the JS oracle (TypeScript type gate)
```

An install runs every lens on its own: the lens tools ship inside the package (`KNOWN-DEBT.md`,
KD-2, fixed 2026-11-25), so no `FORGE_GATE_HOME` is needed for a verdict. The clone is needed for
the tests, the shared tools in `tools/` and the JS oracle.

Python 3.10 or newer. Without `npm ci` the JS oracle's type gate reports `unavailable`. The JS
oracle is a checkout gate (`pytest -m js`), not a `verify` lens, so a pip install does not have it.

## Configuration

ForgeProof reads no other project unless you tell it where. Each setting is an
environment variable, or a key in `config.toml` `[paths]` (per host, not tracked; copy
`config.example.toml`). The environment wins.

| key | needed for | default |
|---|---|---|
| `FORGE_GATE_LIBRARY_DIR` | the twin-consensus lens, `pytest -m live`, library tools | none: unset, those fail naming the key |
| `FORGE_GATE_HOME` | where `config.toml` is read, and `github-facts`' test runner (a checkout tool); no lens needs it | the checkout |
| `FORGE_GATE_PYTHON` | child runs (oracles, fixtures, lenses) | the interpreter running ForgeProof |
| `FORGE_GATE_NODE`, `_BUN`, `_QJS` | the JS oracle's three engines | `PATH` |
| `FORGE_GATE_SCRATCH` | short-lived lens scripts | `<checkout>/tmp` |

`deps.lock` pins what each outside route was checked against.

## Usage

```bash
# List gate-ready patterns
forgeproof list

# Verify a user implementation against a seed-locked oracle
forgeproof verify myfile.py --pattern linear_regression_gd
```

A verdict is `PASS`, `FAIL`, `UNSTABLE` or `UNSUPPORTED`, with one line per lens. `UNSUPPORTED`
means a lens could not measure here and says why; it is never a pass in disguise. `UNSTABLE`
means a lens errored or the references disagreed, so no verdict could be trusted.

## The lenses

| lens | the question it asks |
|---|---|
| property-primary | do the results have the properties a correct implementation must have? |
| metamorphic | if the input changes in a known way, does the output change the way it must? |
| invariant | do the rules that must always hold, hold? |
| twin-consensus | do implementations of the same pattern in other languages agree? |
| determinism | do repeated runs with a fixed seed give the same answer? |
| guard | given NaN (not-a-number) as input, does the code pass it on honestly, or quietly return a plausible number? Infinity is tried too, but only recorded. Needs no reference at all (`docs/GUARD_LENS.md`) |

## Examples

The `examples/` directory contains self-contained reference implementations for
gate-ready patterns. Run them standalone, or pass them to the gate to see a live
`PASS`/`FAIL` verdict:

```bash
python examples/linear_regression_gd_good.py
forgeproof verify examples/linear_regression_gd_good.py --pattern linear_regression_gd
forgeproof verify examples/linear_regression_gd_bad.py  --pattern linear_regression_gd
```

Every one of the 10 catalog patterns has a pair: `<pattern>_good.py` and `<pattern>_bad.py`.
The `_good` files implement the algorithm correctly and print `PASS` via the gate. The `_bad`
files each contain one deliberate bug and print `FAIL`.

Known oddity: `kmeans_clustering_good.py` shows `determinism FAIL` inside a `PASS`. k-means can
settle on different valid answers from run to run, so that lens is advisory for this one pattern;
the line does not say so yet (`KNOWN-DEBT.md`, KD-6).

## What `verify` does

1. Resolves the pattern name in the shipped catalog (`forgeproof/data/gate_catalog.json`).
2. Loads the shipped reference implementation (`forgeproof/reference/`, byte copies of
   AURA library patterns; commit and sha256 in `reference/ORIGIN.md`).
3. Runs the user's file in a separate process: fixed seed, 60 s timeout; network cut off where
   the system supports it (Linux `unshare`), not on macOS.
4. Runs the AURA oracle on the same fixture.
5. Compares exit codes and numeric outputs within tolerance, and runs the lenses.
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

## Shared tools: does your test suite test anything?

Two tools in `tools/` work on any Python project kept in git and tested with pytest, not only
ForgeProof (`docs/shared-tools.md`):

- **`tools/fg_gate.py`** runs the tests and calls a run green only when it can prove what ran:
  counts from the run's own fresh result file, the tree unchanged by the tests, and any tests you
  name actually run. Stopping it with Ctrl-C stops the tests too.
- **`tools/fg_mutrun.py`** plants the small bugs you describe, one at a time, runs the tests,
  restores the code, and reports which bugs the tests caught and which survived.

ForgeProof's own recent changes were gated this way: each step was reviewed by a separate AI
agent session, not the one that built it, before it was merged.

## Where the code to check comes from

ForgeProof doesn't care who wrote a file. It runs it and measures what comes out. In practice,
code reaches it from four kinds of source, and each one is trusted differently.

- **Your own file, or what your AI assistant just wrote.** This is the main case: a script from
  Claude Code, Copilot, Cursor or any other agent, or one you wrote yourself. If it implements one
  of the 10 catalog patterns, `forgeproof verify yourfile.py --pattern <name>` runs it against the
  shipped reference and gives a verdict. Code that matches no catalog pattern gets no verdict; the
  shared tools above can still tell you whether its own tests would notice a bug.
- **The examples in `examples/`.** A known-good and a known-bad file for every pattern. Use them
  to see what a real `PASS` and a real `FAIL` look like before trusting a verdict on your own code.
- **The signed reference implementations** in `src/forgeproof/reference/`. These are byte copies
  from the AURA pattern library, a signed collection of self-testing code patterns. They are the
  oracles: ForgeProof compares your code with them and does not judge them.
- **Pools that `scout` and `learn` read** (advisory today, see Status). A pool is a body of
  existing code that scout searches to answer "what is this code related to?". There are two so
  far. One is a **GitHub pool**: public repositories on a list signed with two keys, each pinned
  to one commit and read from a private cache (KD-13). Today only python/cpython at v3.14.10 is
  on it. The other is the **crew corpus**, described next. Scout reads pool code as
  text and never runs it while searching.

### Crew code collector: how we gather the code our own agents write

AURA is built by a small crew of AI agents working with one person. Over months they have written
thousands of scripts: backups, monitors, test harnesses, data tools. Most were written once,
worked, and were forgotten, and the next agent wrote the same thing again. The **code collector**
gathers that code so that it can be found, measured, and in the end proven or thrown away.

How it works:

1. **It harvests after the fact.** Agents don't change how they work. The collector reads the
   session records they already leave behind, and takes out every piece of code they produced:
   files written or edited, scripts passed inline to a shell, and longer shell commands.
2. **Exclude, scrub, then store, always in that order.** Work done for outside clients is dropped
   first. Secrets, keys, host names, addresses, user names and e-mail addresses are then removed in
   memory, and a second pass checks the scrubbing. Only then is anything written to disk. The store
   refuses any write a secret detector still matches. A separate scanner, sharing no code with the
   scrubber, sweeps the result.
3. **Every run proves it can fail.** Each run starts by planting fake secrets, fake client files
   and a corrupt record. If they don't land exactly as expected, the run is marked invalid.
4. **Each item keeps its evidence.** Who wrote it, when, and in which session. Did it **run**, and
   with what exit code? Was it ever **proven red**, meaning run with a deliberately planted bug and
   seen to fail? Is it still **scheduled**, still on disk, or gone? Every answer keeps the evidence
   string it came from. "Not seen running" is recorded as *unknown*, never as *no*, and words alone
   never upgrade an item to proven.
5. **Near-copies are grouped.** Items with the same shape fall into one cluster. A cluster that
   turns up in two or more projects is a sign the code is worth keeping. A cluster whose members
   drifted apart is kept as a before/after pair. When an agent is about to write a new script, a
   hook can show it the closest existing shapes first: *this already exists, here*.
6. **Collected is not verified.** The corpus is raw input with the lowest trust. Scout reads it
   only from an export whose manifest and checksums all match; anything else counts as blind, never
   as clean. `forgeproof learn` can test a corpus item, but today only the chaos engine can give it
   a `PASS` (KD-11, KD-12). Code reaches the signed pattern library only through that battery and a
   reviewed harvest, never straight from the corpus.

The corpus itself is **private**. It is built from private working conversations, so it is not
published, and neither is the collector. What this repository ships is the reading side: scout's
intake checks, and the ledgers that record whether reused code later held up (`KNOWN-DEBT.md`,
KD-7, KD-11, KD-14).

## Where ForgeProof is going

ForgeProof is alpha. This is the order we plan to work in, and each step lands only with its
tests and its known-debt entry.

1. **Learn from what scout measures.** Scout already records facts: how well a GitHub project's
   own tests catch planted bugs (`forgeproof github-facts`), and whether code from the crew corpus
   was ever seen running. `learn` reads only the crew corpus today. Next it also reads the GitHub
   facts, first by hand on a copy, and only then on a schedule (`KNOWN-DEBT.md`, KD-17 (a)).
2. **More reviewed GitHub projects.** About one project every one to two weeks, chosen for a long
   history and real tests rather than for stars. Each one is pinned to one commit, licence-checked
   file by file, and signed before scout reads it.
3. **All six languages.** The pattern library holds Python, R, Rust, Go, C++ and JavaScript. The
   scores scout reads exist for Python, R and Rust today; Go, C++ and JavaScript need their own
   extractors first.
4. **A first release on PyPI**, as `forgeproof-cli`. Its condition is met: since KD-2 was fixed
   (2026-11-25), an installed copy runs every lens without a source checkout.
5. **A free web service.** Paste code and have it checked in an isolated sandbox, with no account.
   You choose whether we may keep your code to improve the checks, and we never show one person's
   code to anyone else. Prefer to keep everything private? Run this repository on your own
   machine. This is a direction, not yet a design: it starts after the steps above, with a legal
   review first.

ForgeProof is directed by one person and largely written with AI agents. Every commit says so.

## How this repository is made

Most of this tree is published from a private working repository by a script that
scrubs it and refuses to publish when any of its checks fails. `CONTRIBUTING.md` says
which files are edited here directly, and how a pull request to published code is
carried back.

## Licence

Apache-2.0. Copyright 2026 Illia Hladkyi. See `LICENSE` and `NOTICE`.
