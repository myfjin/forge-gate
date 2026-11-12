# Known debt

The list of known issues in Forge Gate that are not fixed yet. Each item says what is
wrong, where, what "done" looks like, who owns it and the date it must be decided by.
Rules (AURA project standard v0.3 §6): the count of unwired self-tests and `violation`
routes never grows; an item past its decide-by date fails the standard; a `deps.lock`
pin that no longer matches what a host uses is listed here or fixed, never only printed.
Host-specific notes live in the private `ops/` folder.

Opened 2026-11-11 (build step 1). Owner "Claude" = the builder seat; the fix of each item
is reviewed by a seat that did not build it.

## KD-1 · twin-consensus lens takes non-canonical files as "twins"

- **What:** `tools/twin_consensus.py` `_resolve_twins()` searches the whole library with
  `rglob(f"{pattern}{ext}")`. A library checkout that holds draft or playground copies of a
  pattern (e.g. under `tools/pattern-scoring/`) yields several same-language "twins"; when
  one draft fails the fixture the lens reports `UNSTABLE: reference twins disagree` and the
  verdict for a correct submission becomes FAIL.
- **Seen:** with `knn_classifier` and `gradient_descent_momentum` against an older library
  checkout that still had those folders; not against the pinned commit, which has none.
- **Reproduce:** set `FORGE_GATE_LIBRARY_DIR` to a library checkout with a second
  `knn_classifier.py` outside `python/`, then
  `forge-gate verify src/forge_gate/reference/knn_classifier.py --pattern knn_classifier`.
- **Done looks like:** twins resolved only from the library's canonical language trees
  (or from its catalog paths), a test with a planted draft copy that goes RED on today's
  code and GREEN after, and the red arm run by the reviewer.
- **Owner:** Claude. **Decide by:** 2026-11-25.

## KD-2 · an installed Forge Gate needs a source checkout for five lenses

- **What:** the lens tools (`tools/`: metamorphic, invariant, twin, guard, JS oracle) are
  not in the installed package. `pip install .` alone runs property-primary only; the
  others report ERROR naming `FORGE_GATE_HOME` (they no longer crash or point into
  site-packages). The test suite sets `FORGE_GATE_HOME` to its own checkout
  (`tests/conftest.py`).
- **Done looks like:** `tools/` moved into the package (or declared as package data with
  imports fixed), `pip install .` from a clean clone with an empty `$HOME` runs every lens
  without `FORGE_GATE_HOME`, and the route test is updated to match.
- **Owner:** Claude. **Decide by:** before the first public release of Forge Gate (§7b
  publish step); 2026-12-15 at the latest.

## KD-3 · the deploy host's library checkout is not at the pinned commit

- **What:** `deps.lock` pins aura-pattern-library at `d7462d3` (the commit the shipped
  references were copied from). The deploy host's library checkout is at an older commit,
  so `pytest -m live` there goes RED in `test_library_checkout_is_at_the_pinned_commit`.
  The 10 reference files themselves are byte-identical on both (measured), so `verify` is
  unaffected; the twin lens is (KD-1).
- **Done looks like:** the deploy host's library at the pinned commit (or the pin moved,
  with the references re-copied and ORIGIN.md updated in the same change), and
  `pytest -m live` GREEN there. Updating that checkout is library work and follows the
  library's own procedure.
- **Owner:** Claude, with the maintainer's go for the library. **Decide by:** 2026-11-25.
- **Status 2026-11-11: fixed, awaiting review.** The old pin `d7462d3` was a dev-machine
  commit never pushed. The maintainer chose option (b): pin moved to the library's GitHub master
  `c39c1dc` (all 10 references byte-identical, no re-copy), and the deploy host's checkout
  moved there from `d11b609`, a pre-squash line GitHub no longer has. Old line kept as branch
  `archive/pre-repin-d11b609` plus a tarball of the 2,238 files only it tracked
  (`tools/pattern-scoring/`); record in the deploy host's `archive/2026-11-library-repin/`.
  Closes when the reviewer has run `pytest -m live` there, with its red arm.

## KD-4 · guard-lens isolation probe failed once, not reproduced

- **What:** `tests/verify_guard_lens_isolation.sh` (tier `gates`) left one `guard_lens`
  process alive after its orphan-child probe in one run on the deploy host
  (`survivors: 1 python3 guard_lens process(es) still running`), on unchanged `master`
  code. The same probe passed on the build-step-1 branch and in the `gates` tier run. The
  branch did not change `tools/guard_lens.py` beyond a comment, so this is treated as
  flaky, not fixed.
- **Done looks like:** the probe run 10 times in a row on the deploy host with 0 survivors,
  or the race named and closed in `tools/guard_lens.py` with a test that reproduces it.
- **Owner:** Claude. **Decide by:** 2026-11-25.

## KD-5 · the slow gates are measured green only on Linux

- **What:** tier `gates` passes on the deploy host (Linux). On the dev machine (macOS)
  `verify_guard_lens.sh` and `verify_guard_lens_isolation.sh` failed and
  `verify_determinism_lens.sh` did not finish in 600 s (it takes ~15 min on the deploy
  host). Not investigated: the isolation probe reads `/proc`, which macOS does not have,
  but that is a guess for the other two.
- **Done looks like:** each gate either passes on macOS, or skips there with a stated
  reason that a test checks, so "red on macOS" is never ambiguous.
- **Owner:** Claude. **Decide by:** before the public release (KD-2's date).

## KD-6 · a quarantined lens result prints as a bare FAIL inside a PASS

- **What:** `verify src/forge_gate/reference/kmeans_clustering.py` prints
  `determinism FAIL non-deterministic output …` with an overall `PASS`. The FAIL is
  deliberately quarantined (`DETERMINISM_ADVISORY_PATTERNS`, `src/forge_gate/lenses.py`, measured
  2026-08-01: k-means lands in a different valid local optimum), but the printed line does not
  say so, so a reader sees a FAIL inside a PASS with no reason. Found by 6E (review B1-2).
- **Done looks like:** the line reads `determinism FAIL (quarantined: …)`, rendered from the same
  list that does the quarantining; a test that goes RED if the label is missing or appears on a
  non-quarantined pattern; verdict JSON byte-identical (criterion 1); reviewer runs the red arm.
- **Owner:** Claude. **Decide by:** 2026-11-25.

## KD-7 · scout slice 1 leaves three parts of §12.4 unbuilt

- **What:** `forge-gate scout` (build step 5) reads the corpus export and runs the shape
  lens, but (a) writes no `.intake-health` signal and no phase-1 staging queue (both need
  `FORGE_GATE_SCOUT_DIR`, and the queue only matters once `learn` exists, step 6b);
  (b) the shape lens has no score band yet, so trust never breaks ties
  (`[NEED: band width per lens]`, §4a.4 rule 3); (c) exports reach the deploy host only by a
  one-off copy (2026-11-11): the corpus project's daily `ops/sync-to-13.sh` ships its lookup
  index, not exports.
- **Done looks like:** (a) the health file written each run, with a test; (b) a band width
  measured from the Rule-2 set and the real export, written next to `TAU`; (c) a decided
  route for new exports (extend the corpus sync, or a scout-side pull), with a sha check on
  arrival and its red arm.
- **Owner:** Claude. **Decide by:** (a) with step 6b; (b) before the trust tie-break is
  used, at the latest step 6a; (c) 2026-11-25.

## KD-8 · the isolation box has run mode only

- **What:** (a) FETCH mode (network only to an index's named hosts, §4a.6 V42-1/V43-1) is not
  built; `box.run(mode="fetch")` refuses with BLIND(fetch-mode-not-built). (b) The separate
  box user (defence in depth if bubblewrap is escaped) is not created: it is a root change on
  the deploy host, asked of the maintainer 2026-11-11. (c) How crew-corpus items get their
  dependencies is still `[NEED]` (§4a.6); until decided, a corpus item needing a non-stdlib
  import will be BLIND(missing-dependency) in step 6a.
- **Done looks like:** (a) fetch mode with its red arm (a host outside the set unreachable,
  a host inside reachable), built before step 7's first fetch; (b) DONE 2026-11-11 (the maintainer's yes): user `fgbox` + root
  `/var/lib/forge-gate-box` on the deploy host, `box-selftest` PASS through it; (c) a decision recorded in the
  design, with its test.
- **Owner:** Claude, (a)/(b) with the maintainer's go for root changes. **Decide by:** (a) before step 7;
  (b) 2026-11-25; (c) with step 6a.

## KD-9 · behaviour lens (step 6a): what it does not do yet

- **What:** (a) The §5 walked example (a captured run of a pinned cpython file) is not the 6a
  integration test: the design's candidate needs step 7's signed list and pin check. 6a's
  integration test is the Rule-2 set plus a real run on the corpus export. (b) A candidate runs in
  the same interpreter as its pair's harness, so it can forge ITS OWN pair's result line (not
  another pair's: each pair is a fresh process). (c) Pairs over 80 per query are reported
  (`over-cap N`), not run. (d) Inputs come from the ported executor (type hints + names) plus
  seeded variants; a function whose arguments it cannot build reports `no-baseline`.
- **Done looks like:** (a) the captured example as a test in step 7; (b) the result line carried
  on a channel the candidate cannot write (e.g. a pipe fd closed before loading the candidate),
  with a test that a forging candidate fails; (c) the cap measured against run time on the real
  export; (d) a coverage count of `no-baseline` on the real export.
- **Owner:** Claude. **Decide by:** (a) step 7; (b)-(d) 2026-11-25.
