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
- **Done looks like:** (a) **DONE 2026-11-14** (step 7 slice 1, 763e332 + fdcf6c1 + d5d5d6a): fetch mode with
  its red arm (5 hosts outside the set blocked, the inside host reached), passed live on the deploy host
  before step 7's only fetch; the `pypi` set is written but used by no fetch (KD-13 keeps what is open); (b) DONE 2026-11-11 (the maintainer's yes): user `fgbox` + root
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

## KD-10 · the publish gates have known blind spots
- **What:** (a) The personal-data scan matches a fixed list of exact patterns. Spelling
  variants of a name, places beyond the listed ones, phone numbers, ssh host aliases and chat
  handles pass it. (b) The quote gate finds a maintainer reference with a quotation or speech
  verb on the SAME line only. A colon without quotation marks, an attribution after a dash,
  a blockquote, a quote split over two lines and curly single quotes pass it. Found by the
  independent review of the first publish (2026-11-12); none of these shapes was in it (checked).
- **Done looks like:** each shape planted in a red arm and refused, without new false hits
  on the current export (the allowed hits stay credit and sign-off rows only, checked by hand).
- **Owner:** Claude. **Decide by:** 2026-11-25. Until then every publish's changed lines are read
  by hand before it is proposed (done for the second publish, 2026-11-12: three files).

## KD-11 · `learn` (step 6b-1) is plumbing: corpus-only, no engine wired

- **What:** (a) **For now** `forge-gate learn` accepts only a file whose bytes are an item of the
  readable corpus export (the maintainer L2, 2026-11-13: a temporary limit, not the design). A local author
  file gets `BLIND(not in a pool)`. **Lifted for GitHub only (step 7, 2026-11-14):** `learn --github
  org/repo@<commit> <path>` reads a file of a ratified repo from the private pinned cache (ghpool; KD-13).
  Before step 7 a GitHub file also got `BLIND(not in a pool)`: each needs its own pool and an id
  decision. (b) **Done in 6b-2 (KD-12):** chaos is wired with a parity test; every other
  engine is `BLIND(not wired)` and may carry `FAIL` only (`CARD_VERDICTS`). On the real export
  learn still appends nothing (no item is byte-identical to a probed library file). (c) The id's commit part is
  the export's run id, so the same item learned again from a LATER export gets a new id and a
  second row; the duplicate check only catches the same id. (d) The survival gate is "at least one
  engine verdict" (the 6b-1 brief); the design's §3 says "measured-passing". Whether a FAIL-only
  function gets a row at all is open for 6b-2. (e) Not built from §4.5: `.session-baseline`
  (read-and-store of the canonical's tier counts), `FORGE_GATE_LOGIC_REGISTRY_CANONICAL`, the
  category classifier (rows say `category: "scout"`), `strength` refinement (`"none"` unless a
  PASS card exists), and `verified_run` (written `false`: nothing ran). (f) The deployed reader
  accepts verdicts `PASS FAIL FLAG RATIFIED`; scout's card vocabulary also has `UNSTABLE
  UNSUPPORTED PROPOSAL`. A scout card with one of those would make the hourly build raise. learn
  writes only `FAIL` today; 6b-2 must keep every card inside the reader's set (test against the
  reader, not a copy of its constants). (g) While learn holds the build lock at :00, that hour's
  build is skipped (`flock -n`) and the next hour runs (L1, accepted). (h) **Decided 2026-11-13 (6E, decision A; review F3).** The Brain's
  extractors (`logic_extract{,_r,_rust,_sysadmin}.py build`) rebuild their ladder file from
  their own source, so a learned row in a real ladder would be deleted by the next re-extract;
  `logic_reconcile.py` and a hand-run `regate_registry.py` keep rows but take no lock. learn
  therefore appends ONLY to scout's own files `scout_registry{,_r,_rust,_sysadmin}.jsonl` in the
  Brain's data folder (6b-1 commit 3). The Brain's scout reader (aura-brain 3fddd78, accepted
  by 6E) reads them after the real ladders, so a real row wins a duplicate id. It is live on the deploy host
  since 2026-11-13 18:36 (the 19:00 build printed `scout source: 4 file(s), 0 row(s)`) and lands
  on the dev machine after its park. An older Brain (6be79ec) ignores the files: learned cards come out `unmatched`. (i) learn takes the Brain's data
  folder (`FORGE_GATE_BRAIN_DATA`) and refuses one without `logic_registry.jsonl`; it must equal
  the Brain's `paths.DATA` on each host (confirmed on the deploy host 2026-11-13; the dev machine after its park).
  (j) A crash (kill -9, power) between the append and the report swap leaves rows
  without their cards; the next run skips them as duplicates, so those cards are never written.
  A failure that raises is rolled back (the registry is cut back to its old size); a crash is not.
  (k) Switch-on order: the four scout files must exist (empty) before the Brain's scout reader
  is deployed (it reports a missing one), and the report file must exist before `AURA_SCOUT_REPORT_CARDS` names it (the
  reader opens it unguarded, so a missing file stops the hourly build), and it is written 0644 so
  the build's user can read it. Status 2026-11-13: on the deploy host the four files exist (0664, with a named-user
  ACL for the builder) and the reader is deployed; the report file and the dev machine are still open.
- **Done looks like:** (a) a pool and id decision per new source, with a red arm that a
  non-member still writes nothing; (b) chaos wired in 6b-2 with a parity test against the
  validator's `chaos_scan.py` (same files, same verdicts), then metamorphic the same way; (c) an
  id rule that is stable across exports (e.g. the item id plus the body's sha256) or a dedupe on
  `extraction.sha256` + function, decided before the live switch-on; (d) decided in 6b-2;
  (e) each built or dropped from the design; (f) a test that runs learn's card writer against the
  deployed reader for every verdict an engine can return; (g) logged in the switch-on deploy plan;
  (h) the scout reader on the dev machine too (the deploy host done 2026-11-13, 3fddd78; forge-gate's brain
  arm shows 6be79ec leaves learned cards `unmatched`), and the by-hand Brain writers take
  the build lock at CLI entry (6E: yes); (i) a switch-on check on each host that
  `FORGE_GATE_BRAIN_DATA` resolves to the Brain's `paths.DATA`; (j) a recovery step that rebuilds a
  missing card set from rows whose `extraction.stage` says learn wrote them, or cards written first
  under a journal; (k) in the switch-on plan, with a check on both hosts.
- **Owner:** Claude, except the Brain side of (h): 6E (the reader on the dev machine after its park; the build
  lock at CLI entry of the by-hand writers). **Decide by:** (b), (d), (f), (j) with step 6b-2;
  (c) the id rule and (e) build-or-drop each item are switch-on plan decisions (Claude proposes,
  the maintainer decides); (a), (c), (e), (h), (i), (k) before the live switch-on (L4, both hosts),
  2026-11-25 at the latest. Restated 2026-11-13 at the 6b-1 close-out.

## KD-12 · chaos (step 6b-2) is wired, but only for the validator's own probe set

- **What:** scout's chaos engine (`scout/engines/chaos.py`, port `chaos_port.py`, child
  `chaos_runner.py`) gives a verdict only where the validator's `chaos_scan.py` (library 4b65044b,
  blob fab0bdd2) would give one on the same bytes. Parity is tested in
  `tests/test_scout_chaos_parity.py` (`-m live`). Limits, all deliberate:
  - (a) **Domain = byte-identical.** Only a corpus item whose bytes equal one of the 19 probed
    library files (sha256 pinned) is probed. The validator's chaos is a closed list of hand-written
    probes, and its PASS rests on a person choosing which file faces which probe. Probing crew code
    by API shape would be more complete but would make a PASS easier to get (named conflict,
    precedence: never easier). On the real export 20261111T130229Z, 0 of 1256 items are
    probeable.
  - (b) **Race/timing probes are BLIND:** idempotent_once_guard, transaction_rollback,
    migration_with_rollback, seqlock_snapshot, double_buffer. Their verdict depends on scheduling.
  - (c) **The gate copies the validator's ABORT, three times over.** All 19 negative controls
    run first, 3 times in a row (`chaos_runner.GATE_RUNS`); one survival in any run makes every
    verdict BLIND. Why 3: on the deploy host the `seqlock_snapshot` NC is a race. It died 0/40 in
    one measurement and 1 of 14 child runs in the first the deploy host parity run, which made scout PASS
    where that host's validator run had ABORTed (parity r1 red, 2026-11-13). Parity through a
    race NC can only hold on average (named conflict). Scout takes the stricter side: P(PASS)
    = p^3 <= p, a per-run bound. **Host history (parent review):** every gate run is recorded
    per host in `$FORGE_GATE_SCOUT_DIR/chaos_gate_history.json`. Once any NC has survived on a
    host, chaos is BLIND for every probe there until an operator archives the file, so on the deploy host
    chaos is BLIND by rule, not by odds. **Calibration floor (KD-12 m, built 2026-11-14):** a
    machine gives no chaos verdict until it has CALIBRATION_N = 1200 clean gate runs recorded
    under its current configuration (`forge-gate chaos-calibrate`). N is measured, not guessed:
    `chaos_runner._gate`, 400 runs per host in 10 fresh processes, 2026-11-14: the dev machine
    1/400 seqlock survivals (M1, venv py3.11, rustc 1.99.1), the deploy host 399/400 (i7-4770HQ,
    py3.10, rustc 1.75). For a 1-in-391 survival rate, ln(0.05)/ln(1-1/391) ~ 1170 runs miss
    every survival with 5% probability. Data: dev machine `~/ai-lab/fg6b2/calib/gate-18.jsonl`,
    deploy host `/home/me/ai-lab/fg6b2/calib/gate-13.jsonl`. An unreadable history is BLIND. On the deploy host the validator itself ABORTs every run
    (`seqlock_snapshot`'s NC sees 0 torn reads, 6/6, 2026-11-13), so **chaos can PASS nothing there
    today**. On the dev machine both pass 19/19.
  - (d) **Stricter than the validator:** an NC whose "death" was a raise, a failed Rust compile, a
    missing rustc or a timeout counts as unclean, and gives BLIND. The validator counts it as died
    (named conflict; parity is exact wherever the NCs really run).
  - (e) The 6 Rust probes are parity-tested but unreachable through learn (no Rust signature
    extractor), and the probe drives classes, not top-level functions. A chaos card backs only
    a probe's SUBJECT function (hand-read per probe, `chaos.SUBJECT`), and only if the run
    recorded the probe reading it. A function the probe reads only to measure (PITR `vals`) or
    to inject the fault (ETL `stable_checksum`) is never backed (6b-2 review F1).
  - (f) Python probes have no timeout in the validator; scout caps the whole child at 900 s and
    says BLIND.
  - (g) KD-11 (d) is kept for 6b-2: a FAIL-only function still gets a candidate row (strength
    `none`) carrying its FAIL card. The design's §3 says "measured-passing" (named conflict; the
    row cannot promote, so this is not the unsafe side). The maintainer decides at the switch-on plan.
  - (i) Parity is shown per host and per interpreter pair. Scout's child uses forge-gate's
    Python (3.11); the parity test runs the validator under `FG_TEST_VALIDATOR_PYTHON`, which
    is the host's own python3 (3.9 on the dev machine, 3.10 on the deploy host) in the 6b-2 runs (review F3).
  - (j) **DECIDED 2026-11-14 (the maintainer): library copies are not learned.** An exact copy adds no
    evidence: its PASS is the library's own, already in the Brain. `chaos.LEARN_LIBRARY_COPIES =
    False` is a code constant, not config: learn gives BLIND("library copy ...") before any child
    runs. Only tests flip it, so the parity machinery stays proven. Consequence (stated, not
    hidden): with (a) byte identity, **chaos in learn is BLIND on all real input**. Human curation
    of 1550 patterns is not feasible; the scalable path is generated probes, a separate engine
    and not 6b-2.
  - (h) KD-11 (j) is still open. A crash between append and report swap leaves a row without its
    PASS card, which errs to candidate, the safe side.
  - Crew review 2026-11-14 (5S + 6E; adjudication `aura-docs/Forge Gate/ADJUDICATION-6b2-crew-20261114.md`).
    5S F1 (a symlinked gate history read as clean) is FIXED in 9ba46af. Open from the review:
  - (k) 6E D1: `probe_for` hashes `item["body"].encode("utf-8")`, the export's JSON string, not the
    original on-disk bytes. Upstream newline or encoding normalisation would let a file that is not
    byte-identical match a pin. Latent (0 matches today); the child always runs the library's own
    pinned file. Close: hash the original bytes, or record the normalisation step in the export.
  - (l) 6E B4: `killpg` runs after the normal-path `wait()` has reaped the child, so a pgid could in
    principle be reused once the group is empty; a grandchild that calls `setsid()` escapes the
    group. Low probability: only pinned library code and the validator's NC code run in the child.
  - (m) **BUILT 2026-11-14** (5S F2 + 6E D3, found independently: the history was keyed on
    `socket.gethostname()`; the dev machine's hostname is `laptop.local` and changes with the
    network). History v2 is keyed on the machine id (`/etc/machine-id`; macOS IOPlatformUUID);
    an unknown id is BLIND. Under it, each configuration (python, rustc, port md5, cpu, os) keeps
    gate_runs and clean_runs. Rules: any survival on the machine, under any configuration, latches
    it BLIND; fewer than CALIBRATION_N clean runs under the current configuration is BLIND. v1
    history reads as unreadable, so BLIND (fail closed). Open: (p) a cloned OS image can carry a
    baked machine id; the two machines in use have distinct ids (checked 2026-11-14). (q) `cpu` is
    machine type plus core count, not the model; every OS or toolchain update costs a
    recalibration (~400 children, ~27-67 min).
  - (n) **FIXED 2026-11-14** (6E E(f), and adversarial review F2, which found the wider case: a
    probe `scan` that raised after a survival also dropped the gate). The child rewrites its result
    file atomically after EVERY gate run and keeps the gate in its error result; `run_child` hands a
    partial gate up through Blind; learn and calibrate record it as not clean before anything
    raises.
  - (o) 5S F11: the staged library is sha-checked, then loaded by the child (check-then-use). Same
    uid, 0700 work dir, so this is not a privilege boundary, but "only pinned bytes execute" is not
    enforced end to end. Close: the child re-hashes each file it loads against the pins.
  - Switch-on plan additions (6E E): (a) and (j) are ONE decision (under byte identity, a verbatim
    copy is the only reachable input), decided as (j) above; the calibration N is measured
    as 1200 (the earlier ">= 150" was a guess and was wrong); chaos BLIND reasons counted and shown per reason; the child run under the host
    validator's interpreter (KD-12 i).
- **Done looks like:** (a) decided 2026-11-14 with (j): no probing beyond byte identity in 6b-2;
  generated probes are a separate future engine; (b) a race probe repeated N times with a stated confidence, or
  kept BLIND; (c) the validator's seqlock NC fixed or ruled host-dependent by its owner
  (SIDE-QUEUE 2026-11-13); metamorphic wired the same way.
- **Owner:** Claude (scout side); the validator's gate is outside forge-gate (the maintainer/6E). **Decide
  by:** before the live switch-on (L4), 2026-11-25 at the latest.

## KD-13 · the GitHub lane (step 7): one repo, and what its first run left open

- **What:** step 7 built the signed list (`scout/trusted_repos.jsonl` + `scout/allowed_signers`,
  ssh-keygen -Y, 2 distinct keys incl. operator), the trust pin outside the repo
  (`FORGE_GATE_RATIFY_PIN`), the private pinned cache and `learn --github`. python/cpython at
  v3.14.10 (b703b2c5) was ratified first; more-itertools/more-itertools at v10.8.0 (8c1a6ef) was
  ratified on 2026-11-22 by the GitHub-lane loop (`docs/github-lane.md`). Open:
  (a) the fetch record (every CONNECT, the dial list) is returned in memory; only the caller writes
  it. The caller's script crashed after the one fetch, so that run's record is lost.
  **DONE 2026-11-22 (GitHub-lane loop S1):** `box.run(..., record_dir=DIR)` writes the record
  itself to `DIR/fetch-*.json`: `started` before any socket opens, then atomically (fsync, rename)
  `done` before it returns or `error` when it raises; an unwritable record is BLIND. Tests
  `tests/test_github_lane_s1.py`: a caller killed (`os._exit`) right after the run keeps the record
  with its CONNECT and dial; a run that raises after a CONNECT keeps it too. Still open: a fetch
  called WITHOUT `record_dir` keeps the record in memory only (the in-box tests and the reach check
  do); every real fetch must pass one (`docs/github-lane.md`). **Proof:** the GitHub-lane loop ledger (`loops/github-lane-2026-11/LEDGER.md`, in the private docs), row
  `2026-11-22T09:24Z` VERIFIED S1 (parent mutants: no record written in run mode, missing folder,
  final write unwritable, dials emptied: each KILLED rc 1), and row `2026-11-22 17:28Z` (P4: the
  live ls-remote and the one live fetch each left their record on disk, `state: done`).
  (b) "only the set's hosts were dialled" rests on box.py's own counter and the proxy's own log;
  there is no witness outside the code under test.
  (c) the second signer is not an independent custodian: the 5S seat key lives under `au` on the
  deploy host, where the builder's account has passwordless sudo. The two-signature rule proves two
  signing acts, not two people holding keys apart. ACCEPTED 2026-11-15: the maintainer, "option 1, record my
  acceptance". The trust root is the maintainer's passphrase-protected key, pinned in each host's config
  (`FORGE_GATE_RATIFY_PIN`); the 5S signature records that the verifier seat checked the line. It is
  not independent custody, and nothing may claim it is.
  (d) licence ids: the line says `Python-2.0` (the LICENSE file's PSF/BeOpen/CNRI stack); GitHub's
  API says `NOASSERTION`. Three cpython files carry `SPDX: PSF-2.0 AND ...`; PSF-2.0 is not on the
  closed allowlist, so they are refused (the safe direction). DECIDED 2026-11-15: the maintainer, "yes PSF-2.0,
  go ahead brother"; PSF-2.0 is on the list now (a file is still refused if another branch of its
  AND is not).
  (e) trust signals: closed issues and dependents are unknown (no `api.github.com`); first-commit
  age and contributors are relayed from 5S's 2026-10-31 report and signed as such. For
  more-itertools (2026-11-22) the signed line carries `last_commit_age_days` 446, read from the
  fetched commit, and the other four signals as `null` (unknown) (ledger row `2026-11-22 17:28Z`).
  They stay unknown until there is a source.
  (f) the list and signer files are found through the source tree (`parents[3]`), so an installed
  Forge Gate cannot find them (same shape as KD-2). **DONE 2026-11-22 (S1):** config key
  `FORGE_GATE_SCOUT_LIST_DIR`, default `<checkout>/scout`; unset outside a checkout (or not a
  folder) fails naming the key, and `learn --github` says `BLIND(unsigned: no signed list: ...)`
  without reading anything. Tests in `tests/test_github_lane_s1.py`. The trust pin is unaffected.
  **Proof:** ledger row `2026-11-22T09:24Z` VERIFIED S1 (parent mutants "list dir not a dir" and
  "wrong list name" KILLED rc 1).
  (g) DECISIONS-7 G2(c) shows `signatures: [{key_id, sig}]`; the reviewed verifier uses
  `{signer, sig}` (the code is right; the doc is stale). **DONE 2026-11-22 (S1):** a dated note
  appended under G2(c) of the decision record (outside this repo), naming `{signer, sig}`; the
  original text is kept. **Proof:** ledger row `2026-11-22T09:13Z` [S1] READY and its handoff
  (`handoff/S1.md`: the record's md5 before and after the note, and the signed line's real shape
  `[sig, signer]` read at `ratify.py:161`); accepted in row `2026-11-22T09:24Z` VERIFIED S1.
  (h) on the deploy host a config folder carries a default ACL for a second local user, so a new private key
  there comes out group-readable and ssh-keygen refuses it (fails closed; 5S fixed its own folder).
  **DONE 2026-11-22 (S1):** ops note in `docs/github-lane.md` (the check, the per-folder fix,
  and why the home folder's own ACL is left alone), from a `getfacl` read on the deploy host. **Proof:** ledger row `2026-11-22T09:13Z` [S1] READY
  (`handoff/S1.md`, the `getfacl` facts) and row `2026-11-22T09:24Z` VERIFIED S1; the P4 fetch's
  record folder is named in row `2026-11-22T09:24Z` DECISION, item 3, with a `getfacl` check before use.
  (i) a ratify key without a passphrase lets any process running as the key's owner sign, so its
  signature does not prove its owner was present. CLOSED for the maintainer's key 2026-11-15: passphrase set
  (empty and wrong passphrases refused; key id still dad6972af38242fc; not in ssh-agent). The
  crew seat keys are part of (c).
- **Done looks like:** (a) box.run (or the fetch wrapper) writes the fetch record to a durable file
  before it returns, with a test that a crash after the run keeps it; (b) an outside witness in the
  red arm (e.g. a host-side capture of the box's traffic) or a recorded decision that the in-code
  counter is enough; (c) a key holder the builder cannot reach (a hardware key, or a host without
  the builder's ssh), or the maintainer's recorded acceptance (accepted 2026-11-15); (d) the maintainer confirms `Python-2.0` for cpython
  and decides on PSF-2.0; (e) the two unknowns stay unknown or get a source; (f) a configured path,
  or the files shipped as package data; (g) the doc corrected; (h) an ops note, or the ACL narrowed; (i) done 2026-11-15.
  (a), (f), (g), (h) done 2026-11-22 (S1; (a) also held in the live P4 fetch), proofs above;
  (b) parked for the post-11-28 crew review; (e) stays unknown. What the new fact tool does not
  do is KD-17.
- **Owner:** Claude, except (c), (d) and (i): the maintainer. **Decide by:** (c), (d) at the end-of-loop crew
  review; (a), (b), (f), (g), (h) before the publish gate, 2026-11-25 at the latest.

## KD-14 · the outcome ledgers (step 8a): what they measure, and what they cannot

- **What:** step 8a built the exposure and outcome ledgers (`src/forge_gate/ledger.py`,
  `FORGE_GATE_LEDGER_DIR`, off when unset): scout records each result it shows (`scout-search`),
  verify records an oracle outcome for reused scout code (`oracle-run`), and
  `forge-gate ledger mark|event|show` records a person's mark and the build/test run that scores it
  (`person-mark`). Named writers are pinned by an AST route test. Open:
  (a) **same account.** Every writer runs as the calling user, and the ledger files are that user's
  files. Append-only, the shared lock and the row check are this code's discipline, not the OS's:
  any process running as that user can append a well-formed row by hand, and the read-time row check
  cannot tell it from a real one. `--who` is free text: anyone can mark as anyone. The same door has
  a supported handle: calling `ledger.record_oracle` directly skips the verify hook's D3 checks (the
  handed-out code in the file, a default-strength run; crew review 6E A1). The writer itself does
  refuse a file whose bytes are not `content_sha256` and a file with more than one hard link (6E
  A3: a hard link into a surfaced folder has no surfaced ancestor to find), so a person whose real
  file is hard-linked must verify a copy of it.
  (b) **the event command is unrestricted** (G5, accepted by the maintainer 2026-11-15). `event` measures the
  exit code of the command the person CHOSE; `-- true` passes. The measurement proves "this command
  passed after the mark", not "the reused code was tested". Since F5 (decided 2026-11-16) the event
  row stores the command and the project as hashes only, taken BEFORE the run: `cmd_sha256` of the
  argv as canonical JSON, and `root_sha256` of the project's root (the innermost folder from the
  project upward holding `.git`, else the project folder). The command text is not stored. The
  hash confirms only the EXACT argv (same split, same Unicode form) and the typed `cmd[0]`, not
  the program it resolved to; a guessable argv is confirmable (see (h)). Scoring uses neither.
  `--kind` is the person's label, and scoring ignores it. A project inside a surfaced location (the
  ledger, the corpus export, the scout dir) is refused, so pool code is not run through `event`.
  (c) **selection skew (G6 / s2 review F3).** scout writes its exposure rows before the report is
  printed; a crash or a closed pipe between the two leaves exposures nobody saw. Exposures can only
  over-count, so 8b's Rule-2 (exposure without outcome) leans pessimistic, not optimistic.
  (d) **a torn last line stops every writer** (s1 review F7). Readers skip it; `_append` refuses
  ("does not end in a newline… Nothing was written") until it is repaired. There is no repair
  command. Procedure until there is one: take `ledger.lock` (`flock`), copy the file aside as an
  archive, cut the file back to just after its last newline in place (same inode, `truncate`, never
  a rewrite), release.
  (e) **the oracle writer will fire rarely** (DECISIONS-8a tick 1): verify needs a library pattern,
  and the reused code must be verified against one with the default lenses. Most outcomes will come
  from people, or not at all. And an oracle outcome proves the handed-out code was PRESENT in the
  file the oracle passed, not that it ran: D3's check is textual, so the code pasted into a
  docstring next to an implementation of one's own counts (6E A5).
  (f) **D1 as amended (accepted by the maintainer 2026-11-15) and its two boundaries.** D1, D2, D3 were
  accepted; the crew review then showed D1's keys failing D1's own purpose (6E A8, A9), and D1 was
  rebuilt stricter and accepted (one site per file, one person per item); then 6E's re-check N3
  (`cp a.py b.py` and one more verify counted 2) was decided by the maintainer 2026-11-16. As built now:
  per item, one oracle outcome per PROJECT ROOT (the nearest folder above the file holding a
  `.git` entry, else the file's own folder; the path as the file system spells it, so `Proj` and
  `PROJ` on a case-insensitive disk are one root, 8a s7 review F1), and one person outcome per
  PERSON, "with id" if any of their scored marks named a result. Undo lines in DECISIONS. Its
  boundaries: a new root costs one `touch x/.git` (ANY `.git` entry counts, an empty file
  included, and a git worktree is its own root); without `.git` every folder is its own root, so a
  copy into any other folder, a subfolder included, counts 2 (same account, as in (a)); two genuine
  reuses in one repository count 1 (an undercount, on the pessimistic side). N4: a person is the
  `--who` text, so `ann` and `Ann` are two persons (free text, as in (a)).
  (g) **the route scan does not see a writer named at run time** (`getattr(ledger, "record_" + x)`).
  The row check at read time is the second layer; it checks shape and writer/kind, not the caller.
  The hooks' callers are pinned (`cmd_verify`, `cmd_ledger`), but nothing stops code calling
  `cli.main(["ledger", ...])` from elsewhere (8a s4 review; same as `cmd_verify` since slice 3).
  (h) **path hashes are unsalted.** `reused_at.file_sha256` / `root_sha256` are sha256 of the path
  text, so anyone holding the ledger can confirm a guessed path (`/home/ann/payroll/model.py`). The
  ledger holds no query text and no code, but it is not anonymous. A moved or renamed file is a new
  reuse site only if it leaves its project root (D1 counts sites by `root_sha256` since N3). The
  event row's `cmd_sha256` is unsalted too: a guessable argv is confirmable from its hash, and that
  includes a short token inside a known command (`deploy --token abc123`), so a secret on the
  command line is only as safe as it is hard to guess.
  (i) **an event runs before the lock is taken** (a test run can be long). If another writer then
  holds the lock past 60 s, the run is dropped with "no event recorded" and must be run again. The
  same holds for a clock stepped back during the run: the after-run check refuses the event, and
  the command has run (a mark dated after now is refused BEFORE the run; crew review 5S C5).
  The command runs as the ledger's owner, so it can also rewrite or drop its own mark row while it
  runs: the after-run check then refuses and no event row is written (5S re-check, a NOTE; it
  costs the person their own claim). It grants nothing new: 5S showed in the same round that a
  hand-appended `person-event` row is read and counted, which is door (a).
  (j) **what 8b needs that 8a's reader does not name** (6E, G7): `counts()` and `exposures()` cannot
  say WHICH exposed result scored; that join is in `view()` (public, not named as 8b's API). 8b will
  also want per-lens attribution, a `slot` breakdown once "explore" exists, and a time window.
  (k) **two small mismatches with the decisions' wording.** `host` is the machine's hostname (the deploy
  host's is a stock laptop name), so rows from two machines become hard to tell apart if ledgers are ever merged
  (G2). G6 says one `write()` per call; the code writes one buffer in a retry loop (one syscall is
  not guaranteed; the torn-line rule makes that safe).
- **Done looks like:** (a) a writer that runs as another account (a setuid helper or a service) and
  files the person cannot write, or the maintainer's recorded acceptance of same-account trust; (b) a fixed
  set of commands, or the acceptance stays recorded; the maintainer decides whether event rows store
  sha256(cmd) and the project-root hash: DONE (F5, the maintainer 2026-11-16, the fields are audit-only); (c) exposures written after the print, or a
  "shown" flag set after it; (d) a `forge-gate ledger repair` that does the procedure under the lock,
  with a red arm; (e) a measured fire rate on real use; (f) DONE: a reuse site is a project root (N3, the maintainer 2026-11-16); (g) the
  route scan also flags any `getattr` on the ledger module; (h) a per-host salt kept outside the
  ledger; (i) a measured worst-case lock hold, or an event that keeps its result and retries;
  (j) DONE in 8b s5: experience joins through `view()` once per run, then `counts(item, v=)` and
  `exposures(item, v=)` (KD-15);
  (k) a host id chosen with the merge, and G6's sentence corrected to "one buffer".

## KD-15 · the 8b lenses (language meaning, intent, experience): what they cannot do yet

- **What:** step 8b built lens 4 (`scout/meaning_lens.py`), lens 5 (`scout/intent_lens.py`), the explore
  slot (`scout/slots.py`) and lens 6 (`scout/experience.py`), on a local embedder client
  (`scout/embed.py`, `FORGE_GATE_EMBED_URL`, `FORGE_GATE_EMBED_CACHE_DIR`). DECISIONS-8b H1-H13
  (the maintainer 2026-11-16). Open:
  (a) **meaning is OFF on the real model.** Its Rule-2 set (H3) runs on every invocation; on the deploy
  host's `nomic-embed-text` (digest 0a109f42) BOTH scorers fail the keyword-trap case (raw 0.692, words
  0.689, above the weakest same-job pair 0.62; DECISIONS-8b F12-F16), so the lens prints
  BLIND(rule2-failed) and `--embed-fill` refuses. Next: a code embedding model (a download: the maintainer's go),
  measured against the same set, the trap kept.
  (b) **intent has no live input.** No pool scout searches carries purpose text: the corpus export has no
  request (F8), the library and GitHub are not scout pools (F7). With `--intent` and a green Rule-2 set it
  prints BLIND(no-input) for the corpus. Next: the corpus project exports a scrubbed `request` field, or
  the library pool is wired into scout (H6 c).
  (c) **thresholds are calibrated ON their Rule-2 sets** (meaning tau 0.61 from the probe; intent's 0.75 is an
  uncalibrated prior, see m), so those sets are not held-out tests of the thresholds. The fixtures and requests
  were written by the builder: the crew review should attack them (my fixtures = my blind spots).
  (d) **experience reorders only exact (score, trust) ties** (H9: no band width is measured for any lens).
  In practice it is display-only until a band is measured per lens (design §4a.4 Rule 3 NEED). The
  ledger's refusal text still says "band must be null until 8b" (stale wording; 8b crew review, 6E H9).
  (e) **experience's Rule-2 set is synthetic** (an in-memory view built by the code it tests). It proves the
  ordering rule and that three named selection-rewarding variants are caught; it cannot prove that a
  variant nobody named is caught. Its red arm is exactly those three. E5 (an unreadable ledger) is not
  in the set, since an in-memory view cannot produce it: `_experience` and
  `test_an_unreadable_ledger_is_blind_never_zero` cover it. The runtime gate needs SOME case to fail
  each variant; only the suite pins which one (8b crew review, 6E H11).
  (f) **the explore slot is pseudo-random and reproducible** (seed = lens + query text): the same query
  always gets the same explore hit, so a repeated query explores nothing new. A per-run seed would need
  the query id before ranking (it is minted at exposure).
  (g) **the embedder trust boundary is the loopback port.** Anything that can listen on 127.0.0.1:11434 as
  another local user and answer with the pinned digest is trusted. Digest is re-checked per batch during
  a fill (s1 review F1), but a scout run checks it once per run.
  (h) `ledger._canon`'s undecodable-bytes `decode` (macOS only) has no test: APFS refuses such names
  (8b s0 review F1).
  (i) The embedder runs on the deploy host only; the dev machine prints BLIND(not-configured).
  (j) **"beats the keyword baseline" is implied, not separate evidence** (8b s2 review F1): in the meaning
  and intent sets, the tau cases already force the lens's separation above 0 and the baseline's is
  negative on these fixtures. Only the trap-lure half of that case can fail on its own.
  (k) **an explore row's rank is its shown position** (8b s4 review F2): 10 for shape and meaning when 9
  top hits exist; for behaviour, whose top holds only agreeing pairs, it is len(top)+1 (1 on a null
  query). The `slot` field, not the rank, says "explore".
  (l) **the one-off fill embeds every corpus language** (8b s3 review F6): about 1431 items, not the 594
  Python items H4 assumed; members under 3 words for the scorer are skipped and reported.
  (m) **both embedding lenses ship OFF on the pinned model.** Live on the deploy host (2026-11-16): meaning
  fails the keyword trap with both scorers (raw also mis-ranks the backup trio); intent ranks every
  partner first but no tau separates (min same-purpose 0.579 < max must-not 0.599, the keyword trap).
  Each prints BLIND(rule2-failed) on every run. Switching either on needs a model that passes, or a
  changed set with the maintainer's word; never a tau fitted to the set (that is the set testing itself).
  (n) **no switch-on may rest on builder-written fixtures** (8b crew review, 6E (i)): meaning's errcount
  pair passes largely on copied comment words; intent's positive floor (0.579) is the builder's most
  differently worded request, with the keyword trap (0.599) above it. Switching on needs held-out
  cases written by someone other than the builder.
  (o) **a cache row is checked for its key length (64 characters, not as hex) and its vector, not re-derived from its text** (8b crew
  review, 5S NOTE 1): a row holds no text, so a well-formed row planted by someone who can write the
  owner-only cache dir is trusted under its key.
- **Debt:** (a) a model that passes the meaning set, or the set changed with the maintainer's word; (b) a pool
  with purpose text; (c) held-out cases from the crew; (d) a measured band per lens; (e) crew-written
  variants; (f) a per-run seed; (g) a socket owned by the embedder's user, checked by uid.

## KD-16 · the shared tools (gate, mutant runner, path canonicaliser): what they do not do

- **What:** the shared-tools loop (2026-11-18/19) built `tools/fg_gate.py`, `tools/fg_mutrun.py`,
  their shared library `tools/fg_common.py`, and `src/forge_gate/paths.py` (`docs/shared-tools.md`).
  What they do not do:
  (a) **mutants are text-exact only.** A mutant replaces one exact occurrence of a string in one
  file. There is no AST mutation, no operator catalogue, and no mutant that spans two files or two
  places. The spec's author chooses every mutant, so a run measures the tests only against the
  mutants someone thought of.
  (b) **counts come from junit only.** Both tools read passed/failed/errors/skipped from the run's
  own fresh junit file, never from pytest's summary line. A case that pytest never writes to the
  junit is not counted; a run with no fresh junit is an ERROR, not a verdict.
  (c) **the load guard is the 1-minute load average.** It waits on `os.getloadavg()[0]` before
  pytest starts and does not watch the load during the run. Memory pressure, I/O, and a burst that
  starts after the check are not seen. The record holds the load before and after.
  (d) **the `F_GETPATH` decode branch of `paths.canonical` cannot be exercised on either host.**
  The macOS file system refuses non-UTF-8 names (EILSEQ), and Linux has no `F_GETPATH`. So
  `test_undecodable_name_round_trips` is a regression test of the Linux fallback only, and the
  branch that decodes an undecodable `F_GETPATH` result has no red arm (the same shape as KD-15 h).
  (e) no DEFER rows. The last review findings (a signal inside the gate's Popen could orphan
  its pytest group; an inherited SIG_IGN was overridden; a dead fallback in the interrupt
  message; an unbounded `tool_head` git call; the 120 s git timeout never measured; three
  untested paths) were left open by the convergence rule and then fixed in a last fix slice of
  the same loop: the four defects each with a test that fails without the fix, the three
  untested paths now tested (each test proven by a mutant it kills), and the 120 s bound by a
  measurement (`docs/shared-tools.md`, "git timeout").
- **Done looks like:** (a) a structural mutant kind, only if a measured miss needs it; (b) a
  cross-check of the junit counts against pytest's own summary, if a run is ever seen where they
  differ; (c) a load sample during the run, in the record; (d) a host whose file system accepts
  non-UTF-8 names and has `F_GETPATH`, or the branch removed.
- **Owner:** Claude. **Decide by:** 2026-12-15 (the next design doc that uses the tools).

## KD-17 · the GitHub fact tool (GitHub-lane loop): what it does not do

Opened 2026-11-22/23 (GitHub-lane loop, slice S5). Every number below was measured on 2026-11-22
in the loop's live run on the deploy host (more-itertools v10.8.0, run
`20261122T173940Z-1e154842`) and is traced to a row of the GitHub-lane loop ledger
(`loops/github-lane-2026-11/LEDGER.md`, in the private docs), named by its UTC stamp.
How the lane works: `docs/github-lane.md`.

- **What:** `forge-gate github-facts ORG/REPO@SHA` plants small bugs into one function at a time,
  runs the repo's own tests in the run-mode box, and appends one fact row per function to
  `$FORGE_GATE_SCOUT_DIR/github_facts.jsonl` (`scout/upstream_facts.py`, `scout/plant.py`,
  `tools/fg_unittest_json.py`). What it does not do, or does only partly:
  (a) **learn does not read github facts.** The tool writes `github_facts.jsonl` and nothing reads
  it; scout's learn and cards never see a fact. That is by design for this loop (DESIGN v3 §3
  "Out": no change to `learn.py` or `cards.py`). Measured 2026-11-22: no scheduled job or timer
  runs scout or learn on either host, and neither host's config sets `FORGE_GATE_SCOUT_DIR` or
  `AURA_SCOUT_REPORT_CARDS`; learning happens only when someone runs `forge-gate learn` by hand.
  Done looks like, in this order: (1) a design step "learn reads facts": scout uses a function's
  fact (strongly or weakly tested upstream) when it shows that function as related to agent code,
  never as a PASS, and the design says where the facts live (live scout dir → Brain); (2) one
  manual learn run, proven on a COPY of the Brain; (3) only then automation (a schedule, or a hook
  after each fact run), switched on by the maintainer explicitly, because that is the first time
  GitHub-derived facts reach the live system. Owner: the maintainer + Claude. Decide by: after the
  GitHub-lane loop closes. (Ledger row `2026-11-22 18:05Z`: the maintainer asked for this item in S5's known debt.)
  (b) **F-P4-1: one row says MEASURED without an import proof.** `distinct_permutations` is
  labelled MEASURED although one of its mutant runs timed out and so carries no import proof
  (R4: a timed-out run cannot print `module_file`). The loop's own recount (DONE 2: "every run's
  `module_file`") does not count it: the tool's 68 MEASURED became 67 in the recount. Where:
  `scout/upstream_facts.py`, the step that sets `outcome`. Done looks like: a row whose runs do not
  all carry a proof is not MEASURED (or the timed-out mutant is excluded from m, k, s and named),
  with a test that fails today. (Ledger row `2026-11-22T21:55Z`.)
  (c) **F-P4-2: a plain `pytest` can poison its own fixture.** DONE 2026-11-23 (close commits):
  the fixture is read through `_fixture_files()`, which skips `__pycache__/` and dot-files; the
  test `test_fixture_reader_skips_pycache` plants a `.pyc` and a `.DS_Store` and pins the 4 tracked
  names. Before the fix: `tests/test_scout_ghsearch.py`
  reads EVERY file under `tests/fixtures/ghsearch_fixture/` as text. On 2026-11-22 a plain `pytest`
  run (not the gate) on the deploy host wrote `__pycache__/*.pyc` under the fixture's `Lib/test/`,
  and that test then failed at collection. The pinned gate is immune (it sets its own
  `pycache_prefix`); a plain `pytest` on a clean clone is not. The stray files were archived, not
  deleted. Done looks like: the test reads only the fixture's tracked source files (or skips
  `__pycache__`), with a test that writes a `.pyc` there and still passes. This was fixed before
  the public PR. (Ledger row `2026-11-22T21:55Z`.)
  (d) **96 of 172 functions have no fact.** The 4 h budget (R6, 14 400 s) fitted the first 76
  functions in source order; the other 96 are `BLIND(budget-cut)`. Of the 172 rows: 68 MEASURED
  (67 after the recount, see b), 8 `BLIND(no-mutants)`, 96 `BLIND(budget-cut)`. Done looks like: a
  second run over the cut functions, or a bigger budget: that is the maintainer's call. (Ledger row
  `2026-11-22T21:55Z`.)
  (e) **the controls count 859 tests; the stdlib runner counts 669.** The tool's controls report
  859 tests run. The loop's own check ran stdlib `unittest tests.test_more` in the same box and got
  669 (OK). The difference is not explained (a guess, not measured: a different scope, for example
  the tool's discovery from `.` also loads `tests/test_recipes.py` and the doctests). Done looks
  like: the two counts reconciled by name, or the cause written here. (Ledger row
  `2026-11-22T21:55Z`.)
  (f) **R3 is not re-checkable from a row.** A control keeps only `{rc, tests_run,
  pass_set_sha256}`, so "the failing test passed in both controls" (R3) cannot be re-derived from
  the stored row; the loop's recount checked it on examples (one CAUGHT mutant of `chunked`,
  re-run by hand with the same failing tests), not in general. Done looks like: the controls store
  their passing test ids (or the row stores, per mutant, which failing ids were in the pass set).
  (Ledger row `2026-11-22T21:55Z`.)
  (g) **the operators are hand-picked.** Five classes only (comparison flip, `+`↔`-`, integer
  constant ±1, `return <expr>`→`return None`, `and`↔`or`), at most 12 per function, and only
  top-level functions (no methods, no async, no nested functions). A function's k/m is a fact
  about THESE mutants, not about every possible bug. (DESIGN v3 R5; ledger row
  `2026-11-22T14:34Z` [S3] READY, handoff open items.)
  (h) **equivalent mutants are not marked.** A mutant that cannot change behaviour counts as
  SURVIVED, the same as a real miss. The run had 16 SURVIVED; the one the loop re-ran by hand
  (`last:const:290:38`) is equivalent (rc 0, the tests are right not to notice). So s is an upper
  bound on real misses. (Ledger row `2026-11-22T21:55Z`.)
  (i) **unittest only.** The box has no pytest, so only a repo whose tests run under stdlib
  `unittest` discovery (start dir `.`, pattern `test*.py`, `load_tests`) can produce facts.
  (DESIGN v3 §6 A2, R2.)
  (j) **one repo, one pin older than the newest release.** Facts exist for more-itertools only, at
  v10.8.0 (8c1a6ef). The newest release line (11.x) needs Python ≥ 3.11 and the deploy host has
  3.10 only, so the pin is not the newest release. (DESIGN v3 §14 item 1; ledger row
  `2026-11-22 17:28Z`.)
  (k) **no PASS, by design.** A fact row says how strongly upstream's tests pin a function. It is
  never a PASS or FAIL card for anyone's code (the maintainer's decision of 2026-11-21: the output is a fact row, never a PASS).
  (l) **`ratify.py`'s docstring is out of date.** It says "Before any fetch, scout verifies every
  line". The real order, for both ratified repos, is fetch → line written from the fetched tree →
  signatures → cache: the fetch comes before the line exists (DESIGN v3 N10, §5 P4 step 3). The
  code is right; the docstring is not. Done looks like: the docstring corrected (a code-file edit,
  so not in this docs-only slice).
  (m) **version-gated code is skipped whole.** A candidate inside an `if` that reads
  `sys.version_info` (or `hexversion`) is not planted and is counted as `version_gated`. Every node
  under such an `if` is skipped, the `else` branch and the test itself too, so the branch that
  does run on the host's Python is never planted. The count is low, never too high. (DESIGN v3 R5,
  F18; ledger row `2026-11-22T14:34Z`, review F9.)
  (n) **smaller items queued by the slices** (each: ledger row, then the slice's handoff):
  - fetch mode without `record_dir` still keeps the record in memory only (the in-box tests and
    the reach check do this); a fix is `BLIND(fetch-needs-record-dir)` plus a test-only override
    (S1 F1, row `2026-11-22T09:24Z` DECISION item 2);
  - a failed error-path write of the fetch record leaves `state: started`, which looks the same as
    a kill -9 (S1 F2);
  - fetch records are created `0640` in the caller's folder; in a folder with the deploy host's
    default ACL (KD-13 h) the second local user can read them (S1 F5);
  - unparsable files (syntax newer than the host's Python) leave the GitHub pool, counted only,
    with no threshold that makes the pool BLIND (S2 F3, row `2026-11-22T13:23Z`);
  - the admission rules exist twice (`ghpool.admit` and `ghsearch._repo_members`) and can drift;
    tests pin today's behaviour (S2 review note);
  - every `forge-gate scout` run verifies the signatures and parses the whole GitHub pool, about
    7 s on the cpython cache, with no opt-out (S2 review note);
  - the fact tool has no DIRECT import of `learn` or `cards`, but `scout/__init__.py` imports
    `cards`, so cards is loaded in-process (S3 F5, row `2026-11-22T15:25Z`);
  - if removing the tool's staging copy under the box root fails, the error is ignored
    (`rmtree(ignore_errors=True)`) and a copy of the repo stays behind unreported (S3 F6);
  - a bad "after" control also blinds the next function of the module (conservative; S3 F8);
  - the test code runs in the same process as the runner that writes the report, so it could forge
    one; it is trusted because the repo is signed (S3 review question);
  - a function defined in a package's `__init__.py` gets the module name `pkg.__init__` (S3 parent
    mutant T9, real, low impact: more-itertools' `__init__.py` defines no functions);
  - the pinned gate builds its environment from nothing, so `-m box` tests cannot run through it;
    the box must-run was pytest run directly with the run settings (row `2026-11-22T15:25Z`
    DECISION). The gate needs an env pass-through allowlist (a tool change).
  - the publisher's quotes gate flags a line that names the maintainer AND holds a quotation or a
    speech verb; an allowed phrase is stripped first. A quotation whose only maintainer reference
    sits inside an allowed phrase would pass. This holds for all 13 entries in `QUOTE_ALLOW`
    (`tools/publish_public.py`), not only the three added when the GitHub lane closed. Each entry
    is an exact phrase in one file, so the risk is low. Fix to be designed: judging the rest for
    quotation marks alone would flag two of those three lines again (close review N1 and its
    follow-ups, in the private docs).
- **Done looks like:** each item's own line above; for (g), (i), (j), (k): stays as stated until a
  design step changes it; for (n): each fixed with a test that fails without the fix, or moved to
  its own KD item.
- **Owner:** (a) the maintainer + Claude; (d) the maintainer; the rest Claude. **Decide by:** (a) after the
  GitHub-lane loop closes; (b), (e), (f), (h),
  (l), (m), (n) by 2026-12-15 (the next GitHub-lane design doc); (d) at the post-11-28 crew review.
