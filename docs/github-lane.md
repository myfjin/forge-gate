# The GitHub lane

Forge Gate's GitHub pool admits files only from repos that are ratified: a signed line in
`scout/trusted_repos.jsonl`, checked against `scout/allowed_signers` and the trust pin in the
host's config (`FORGE_GATE_RATIFY_PIN`), at a pinned commit, under a licence on a closed list.
From GitHub we take **facts, not expression**: scout may show an admitted function as related to
an agent's code, and the fact tool measures whether the project's own tests would notice a bug in
that function. Nothing from GitHub ever produces a PASS. Known limits: `KNOWN-DEBT.md`, KD-13 (the
list, fetch and cache) and KD-17 (the fact tool).

Numbers on this page were measured on 2026-11-22 in the GitHub-lane loop's live run on the deploy
host, unless a line says otherwise. Each is traced to a row of that loop's ledger
(`loops/github-lane-2026-11/LEDGER.md`, in the private docs), named by its UTC stamp.

## The path, in order

1. **Pick a pin.** The newest tag whose `requires-python` admits the deploy host's Python (3.10).
   For more-itertools that is v10.8.0; the 11.x line needs ≥ 3.11 (KD-17 j).
2. **One reach check, one ls-remote, ONE depth-1 fetch**, all in the box's fetch mode with host set
   `github-git` (github.com:443 only), each with a `record_dir`, so its record is on disk before
   anything else happens (KD-13 a). The line's `commit` is the commit sha (ls-remote's peeled
   `^{}` line when the tag is annotated; a lightweight tag's ref sha is the commit).
3. **Write the line from the fetched tree**: `git fsck --full`, the LICENSE blob sha, the licence,
   `requires-python`, the test command, and that `tests/__init__.py` exists (without it, discovery
   from `.` misses the tests: stop before signing). The order is fetch → line → signatures →
   cache; `ratify.py`'s docstring still says "before any fetch" (KD-17 l).
4. **Two signatures on the line's canonical bytes**, namespace `forge-gate-ratify`: the maintainer's key
   (the trust pin requires it) and one crew seat. Check each: the real signature verifies (rc 0); one
   changed byte, a wrong namespace and a wrong principal each fail (rc 255).
5. **Commit the signed line**, then **build the cache** from the fetched store and run the pin check
   (`ghcache.build`, then `ghcache.check_cache(..., full=True)`). A copy with one byte changed must
   fail the pin check (`PinMismatch`).
6. **Admission** (`ghpool.admit`): signed line, pin, licence, inclusion rules; `tests/` and `docs/`
   files are refused.
7. **Scout** searches the GitHub pool on every run: `forge-gate scout FILE` shows hits as
   `github (raw)` with `pool=github trust=raw` and `scout:github:` ids. A planted control from the
   pool must find itself, else the pool is `BLIND(control)`; a pin failure makes it
   `BLIND(pin-mismatch)`.
8. **Facts, outside learn:** `forge-gate github-facts ORG/REPO@SHA [--functions NAME ...]`, with
   `FORGE_GATE_BOX_USER`, `FORGE_GATE_BOX_ROOT` and `FORGE_GATE_SCOUT_DIR` set in that command's own
   environment (no config edit). Without a box user it refuses and runs nothing
   (`BLIND(box-user-unset)`). It appends one row per function to
   `$FORGE_GATE_SCOUT_DIR/github_facts.jsonl`; its staging copy under the box root is removed
   after each module (a failed removal is not reported: KD-17 n). Learn does not
   read that file (KD-17 a).

## How a fact is measured (`scout/upstream_facts.py`)

For each admitted top-level function: up to 12 single-node mutants (KD-17 g), spliced into the
source bytes. The repo's own tests run in the run-mode box with stdlib `unittest` (the box has no
pytest), once unmutated before the mutants and once after: both must pass with ≥ 1 test and the
same passing set, else the function is BLIND. A mutant is CAUGHT only when the run finishes with
rc 1, a test that passed in both controls fails, and it fails again on one re-run. Timeouts,
signals, `MemoryError` and `RecursionError` are UNCLEAR, never caught. Every run that ends
with a report proves which file it imported; a run without one (a timeout, or no JSON report)
carries no proof (KD-17 b). A system copy of the package is visible inside the box, which is why
the proof matters. The whole run has a 4 h
budget; functions past it are `BLIND(budget-cut)`.

## The first live run: more-itertools v10.8.0 (2026-11-22)

| step | result | ledger row (2026-11-22) |
|---|---|---|
| reach check | the `-m network` red-arm test PASSED; the only reach check of the loop | 17:28Z |
| ls-remote | `refs/tags/v10.8.0` → `8c1a6ef`, a lightweight tag (no `^{}` line; the ref sha is the commit) | 17:28Z |
| fetch | ONE depth-1 fetch, 1.2 s, dials `[github.com:443]`, record on disk with `state: done` | 17:28Z |
| tree | fsck rc 0; 36 files; LICENSE MIT; `requires-python >=3.9`; `tests/__init__.py` present | 17:28Z |
| signatures | the maintainer and 5S; each real signature rc 0; changed byte, wrong namespace, wrong principal rc 255 | 17:35Z, 17:50Z |
| line | one line appended to `scout/trusted_repos.jsonl`; both lines of the list verify | 17:50Z |
| cache | full pin check ok, 36 files, on both hosts; one-byte tamper and a changed tree sha each `PinMismatch` | 17:50Z |
| admission | `more.py` and `recipes.py` admitted on both hosts; wrong pin, no pin, a pin naming the crew seat, a `tests/` file and another commit each refused | 17:50Z |
| facts | 172 rows: 68 MEASURED, 8 `BLIND(no-mutants)`, 96 `BLIND(budget-cut)` (the first 76 functions fit 14 400 s); median control 18.7 s; wall time 17:39:40Z → 21:34:56Z | 21:55Z |
| mutants | 323 CAUGHT, 16 SURVIVED, 2 UNCLEAR(timeout), 2 UNCLEAR(MemoryError) (the tool's own tally) | 21:55Z |
| recount | an independent recount from the raw fields: 67 functions count, all 67 with k ≥ 1; `distinct_permutations` is not counted (KD-17 b) | 21:55Z |
| scout | `chunked`, renamed, found as `github (raw)` at J = 0.96; the control found itself (J = 1.0); an unrelated matrix-algebra example: no github hit (best J = 0.0817) | 21:55Z |
| Brain registry | the `scout_registry*.jsonl` files of a Brain COPY byte-identical before and after | 21:55Z |
| suite | pinned gate on the line's commit: GREEN, 1148 passed, 6 skipped | 21:55Z |

## What the lane does NOT do

- It never gives a PASS or FAIL for anyone's code, and never runs GitHub code outside the
  run-mode box on the deploy host (only the repo's own tests and the planted mutants).
- It does not feed learn, cards or the Brain: the facts file is written and nothing reads it yet.
  Turning that on is a design step, then a run on a Brain copy, then the maintainer's switch (KD-17 a).
- It fetches once per ratified pin. A second fetch, another tag or another host is a new decision.
- It covers two ratified repos (cpython for scout's search, more-itertools for facts), one pin
  each; facts exist for more-itertools only (KD-17 j).
- It has no outside witness for the fetch box's dials (KD-13 b) and no source for most trust
  signals (KD-13 e).
- It runs stdlib `unittest` only, with five hand-picked operator classes, and does not mark
  equivalent mutants (KD-17 g, h, i).

# Operator notes

## Where the signed list is found (KD-13 f)

| key | default | unset |
|---|---|---|
| `FORGE_GATE_SCOUT_LIST_DIR` | `scout/` of the source checkout Forge Gate runs from | outside a checkout: `learn --github` fails `BLIND(unsigned: no signed list: FORGE_GATE_SCOUT_LIST_DIR is not set ...)`, nothing is read |

The folder must hold `trusted_repos.jsonl` and `allowed_signers`. A value that is not an existing
folder fails naming the key: a typo must not read as "no repo is ratified". Set it in the
environment or in `config.toml` `[paths]` (see `config.example.toml`).

The trust pin does not move with this key. Pointing the key at another folder cannot ratify a
repo, because the required signer's key id is pinned in the host's untracked config.

## The fetch record (KD-13 a)

A fetch runs in the box's fetch mode (`box.run(..., mode="fetch", host_set=..., record_dir=DIR)`).
With `record_dir`, `box.run` writes the record itself, to `DIR/fetch-<utc>-<pid>-<hex>.json`:

- `state: started`, with the host set and the command, before any socket opens. A kill -9 mid-run
  leaves this.
- `state: done` (with the outcome) before `run` returns, or `state: error` (with the exception)
  when it raises. Each write goes to a temp file, is fsynced, then renamed into place.

The record holds the set's members, the host-set file's sha256, every CONNECT the proxy logged
(red arm and run, allowed or denied), and box.py's own list of dials. `record_dir` must already
exist: a missing folder is `BLIND(fetch-record-dir-missing)` before anything runs. A record that
cannot be written makes the result `BLIND(fetch-record-unwritable)`. Without `record_dir` the
record is only returned in memory, as before; every real fetch passes one.

## Deploy host: default ACLs and new private keys (KD-13 h)

On the deploy host, the operator's home folder, its `.ssh` and `.config`, and most folders under
`.config` carry a **default ACL** that grants a second local user `rwx`, with mask `rwx`
(checked 2026-11-22 with `getfacl`). A file created inside such a folder inherits that entry. A new
private key made there comes out readable by that user, and `ssh-keygen` refuses to use it
(unprotected private key). That fails closed: nothing gets signed with an exposed key. But a crew
seat that makes its ratify key in such a folder cannot sign until the folder is fixed.

Before making a key on the deploy host:

```bash
getfacl -p "$DIR" | grep '^default:'
```

If a `default:user:<name>:` line grants anything, narrow it for that folder only, then make the key:

```bash
setfacl -m d:u:<name>:--- -m d:m::--- "$DIR"
```

Forge Gate's own config folder is already narrowed this way (default entry `---`, mask `---`).
Do not change the home folder's ACL itself: other services rely on it. After making a key, check
`getfacl -p <key>` shows no named user and `stat -c %a <key>` is `600`.
