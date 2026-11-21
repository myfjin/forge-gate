# Shared tools: the gate, the mutant runner, the path canonicaliser

Three tools, each built once and tested once, so that later work calls them instead of
re-learning host quirks in a new script every step:

| tool | what it is | tests |
|---|---|---|
| `tools/fg_gate.py` | the gate: runs the suite on a worktree and prints one verdict | `tests/test_fg_gate.py` |
| `tools/fg_mutrun.py` | the mutant runner: text-exact mutants against named tests | `tests/test_fg_mutrun.py` |
| `src/forge_gate/paths.py` | one path canonicaliser (`real`, `canonical`, `is_under`) | `tests/test_paths.py` |

`tools/fg_common.py` is the library the two tools share (`tests/test_fg_common.py`). It is not
a CLI. Each tool loads it **from its own directory, by path**: never an installed copy, never
the tested tree's copy, never through `sys.path`. So both tools run from any working
directory. A missing or broken `fg_common.py` is an error (exit 2), never a verdict.

The tools are measuring instruments: **nothing the tested tree does may change the verdict.**
Stdlib only, Python 3.10+. Settings are environment variables of the tools, not config keys.

## The gate: `tools/fg_gate.py`

```
python tools/fg_gate.py --tree DIR --python PY --record FILE --tag T \
    [--must-run ENTRY ...] [--max-load F] [--wait S] [--timeout S] [-- extra pytest args]
```

- `--tree` is the top level of a git worktree. It must be clean, except for staged entries
  (`M`/`A`/`D`/`R` in the index column, nothing in the work-tree column), so a patch can be gated
  with `git apply --index` before it is committed.
- `--python` is the interpreter the suite runs under.
- `--record` is a JSONL file, appended to. It must be **outside** the tree. The run's junit file,
  log and pycache dir go in the record's directory.
- `--must-run` takes one flag and many values: test files or node ids that must have **passed**.
  Skipped, failed, errored or missing is not passed.
- `--max-load`, `--wait`: the load guard (below). `--wait` defaults to 900 s.
- `--timeout`: the suite's wall timeout, default 1800 s. On a timeout the whole pytest process
  group is killed.
- After `--`, extra pytest args pass an allow-list, else the run is refused before pytest starts:
  `-x`, `-v`, `-q`; `-k V`, `-m V`, `--deselect V`, `--maxfail N` (or `--long=V`); and paths or
  node ids that resolve inside the tree. Option spellings that could point pytest at another
  ini file or test path (`-o`, `--override-ini`, combined short flags, `@argfiles`) are refused.
  Only argv is checked: what the tree's own ini or conftest files do is not.
- `-h` is a usage error (exit 2), like any other bad argument.

**The last stdout line** is the verdict:

```
GATE <GREEN|RED|ERROR> rc=<rc|none> passed=.. failed=.. errors=.. skipped=.. tree=<hash> [reason]
```

**Exit codes:** 0 GREEN, 1 RED, 2 GATE ERROR.

- **GREEN**: pytest rc 0, at least one test, no failure or error, the tree unchanged, every
  must-run entry passed.
- **RED**: the tree unchanged, and either pytest rc 1 with at least one failure in this run's
  junit, or a must-run entry not passed while pytest ended normally (rc 0 or 1).
- **GATE ERROR** is everything else: the run says nothing about the code. Among others:
  - a missing `timeout` or `git` on the suite's PATH, a missing pytest, or `import forge_gate`
    resolving outside `DIR/src` (an editable install elsewhere);
  - the box stayed loaded past `--wait`;
  - pytest rc 1 **without** a failure in the junit (rc 1 also means "No module named pytest"),
    and rc 2, 3, 4 or 5;
  - a stale junit file, a timeout, a record that cannot be written;
  - `tree dirtied: <paths>`: the suite changed the tree. Nothing is restored; the tree stays
    as the suite left it, for reading;
  - an interrupt (below).

**What the record holds** (one JSON line, written **before** the verdict line is printed): the
tool's path and md5, `common_file`/`common_md5` (fg_common.py), `tool_head`, the tree hash before
and after, the porcelain before and after, the PATH the suite saw, which `timeout`/`git` it found,
where `forge_gate` was imported from, the pytest version and whether `pytest-timeout` loaded,
the umask, the load before/after and the seconds waited, the counts, the must-run outcomes, and
the verdict with its reason.

**Interrupts.** On SIGINT, SIGTERM or SIGHUP the gate ignores further signals, kills the whole
pytest process group, writes the record (`interrupted`, `group_killed`) and prints
`GATE ERROR rc=none … interrupted (signal N); pytest process group killed`, exit 2. A signal
that lands while pytest is being started is held until the gate has the process, then handled
the same way, so the group is still killed. A signal that was already ignored when the gate
started (`nohup` ignores SIGHUP; `cmd &` without job control ignores SIGINT) stays ignored: it
neither interrupts nor ends the run. An interrupt without a message is named by its type, as in
`interrupted (KeyboardInterrupt)`. The record and the verdict line are written with all three
signals ignored. An interrupt is never GREEN and never RED. Any crash of the gate itself is also a GATE ERROR, exit 2, never 1.

## The mutant runner: `tools/fg_mutrun.py`

```
python tools/fg_mutrun.py --tree DIR --python PY --spec FILE.json --record FILE --tag T \
    [--ids ID ...] [--timeout S] [--max-load F] [--wait S]
```

The spec is a JSON list. Each entry has exactly these keys:

```json
[{"id": "M1", "file": "src/forge_gate/x.py", "old": "a == b", "new": "a != b",
  "tests": ["tests/test_x.py::test_y"], "what": "the comparison is inverted"}]
```

- `old` must occur **exactly once** in `file` (relative to DIR, no `..`). The mutant replaces it
  with `new`. `tests` are paths or node ids inside DIR, never pytest options. `--ids` selects
  entries by id.
- The tree must be clean, with nothing staged. The runner never touches the index and never
  commits.

**Output, in order:**

```
CONTROL-BEFORE rc=0 tests=N
<id> <KILLED|SURVIVED|ERROR|NOT-APPLIED|VOID|ABORT> rc=<rc> | <what / reason>
...
CONTROL-AFTER rc=0 tests=N
SURVIVORS: <ids or none>
ERRORS: <ids or none>
KILLED: k/n
porcelain_after=<the tree's porcelain at the end; empty = clean>
```

**Exit codes:** 0 every mutant KILLED and both controls green; 1 a mutant SURVIVED and nothing
errored; 2 any ERROR, NOT-APPLIED, VOID, control failure, ABORT, interrupt or usage error.

**The controls before and after.** The union of all the spec's tests runs unmutated first
(CONTROL-BEFORE) and last (CONTROL-AFTER). No mutant runs on a red CONTROL-BEFORE: a test id
that does not exist makes pytest exit 4 and run nothing, which prints
`CONTROL-BEFORE RED rc=4 tests=0` and exits 2, so a typo can never become a kill. A red
CONTROL-AFTER turns every verdict into VOID. A green control needs at least one passed test.

**Verdicts:**

- **KILLED** only on pytest rc 1 with at least one failure in that run's own fresh junit file.
  rc 2 is an error even with a failure in the junit; rc 3, 4 and 5 are errors.
- **SURVIVED** only on rc 0 with at least one passed test and no failure or error. An
  all-skipped run proves nothing and is an ERROR.
- **NOT-APPLIED**: `old` not found exactly once, or after the write the file's md5 did not
  change, `new` is not in it, or `git diff --name-only` does not name exactly that file.
- **ABORT**: the restore could not be confirmed (below). The tree is left as it is, and
  CONTROL-AFTER prints `NOT-RUN (abort: the tree is left as it is)`.

**Restore.** Each mutated file is restored from its pristine bytes in a `finally`, then
checked: the md5 read back (`md5_restored`, against `md5_pristine`) and `git status`
(`porcelain_after_restore`). If either check itself fails, the record keeps `null` plus
`md5_restored_error` or `porcelain_error`, and the mutant is an ABORT with
`restore check failed: …`. A failed check is reported, never raised, so it cannot hide an
interrupt.

**Interrupts.** SIGTERM, SIGHUP and SIGINT are blocked from before a mutant is written until
its restore is done, except while that mutant's pytest runs. A signal therefore unwinds
through the restore. The mutant's record keeps `interrupted`; an ABORT reason becomes
`interrupted: signal N; <restore reason>`. The last line reports what the tree **is**, read
afterwards, never what the restore was meant to do. As in the gate, a signal that lands while
a mutant's pytest is being started is held until the runner has the process (so its group is
still killed), a signal ignored when the runner started stays ignored, and an interrupt without
a message is named by its type:

```
MUTRUN ERROR interrupted (signal N): <the tree is clean | the tree is LEFT AS IT IS: … |
    no mutant had been applied | the tree state is UNKNOWN: …>; see the record
```

After any interrupted or stopped run, read `git status` and `git diff` of the tree before
anything else.

**git timeout.** Every git call made through `fg_common` is bounded by 120 s
(`GIT_TIMEOUT_S`): git then runs with the signals blocked, so a hung git would otherwise
yield only to SIGKILL. A timed-out git is killed and reported (`… TIMED OUT after 120 s and
was killed`): the mutant becomes an ERROR, or an ABORT in the restore. The same bound covers
the gate's status and write-tree reads, and each tool's read of its own `git rev-parse HEAD`
(`tool_head`): a hung one is a GATE ERROR or MUTRUN ERROR, while a tool outside any git checkout
still records `tool_head` as null.
Measured on 2026-11-20 in a scratch worktree on the deploy host (Linux), while the full suite
ran beside it (1-minute load up to 5.6): 2,201 runs each of `git status --porcelain
--untracked-files=all`, `git write-tree` and `git diff --name-only` took at most 0.026 s,
0.017 s and 0.008 s. So 120 s is about 4,500 times the slowest call seen. It is a bound on a
hung git, not a tuning value, and it stays.

The record (M9) is written before the summary is printed.

## The path canonicaliser: `src/forge_gate/paths.py`

```python
from forge_gate import paths
paths.real(p)          # os.path.realpath, as a Path
paths.canonical(p)     # the spelling the file system stores (F_GETPATH where it exists)
paths.is_under(p, root)  # identity (device + inode) of p or a lexical parent equals root's
```

Callers pass **resolved** paths to `is_under`: it walks lexical parents. `ledger.py` keeps its
old private names (`_real`, `_canon`, `_under`) as aliases; no other module may use them
(`tests/test_ledger_routes.py`). The two tools import `paths` from their **own** checkout
(G0), never from the tested tree.

## Settings

| variable | what | default |
|---|---|---|
| `FORGE_GATE_GATE_PATH` | the PATH the suite runs under | `/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin`, plus `$HOME/homebrew/bin` and `/opt/homebrew/bin` when they exist |
| `FORGE_GATE_GATE_MAX_LOAD` | the 1-minute load average the run waits under | half the CPU count; `--max-load` wins over both |

The suite's environment is built from nothing (`env -i` semantics): `PATH` (above), `HOME`,
`PYTHONPATH=DIR/src`, a fresh `PYTHONPYCACHEPREFIX` outside the tree, `FORGE_GATE_HOME=DIR`,
and `LANG`/`LC_ALL` if set. Nothing else from the caller's environment reaches the suite.

**The load guard.** Before pytest starts, the gate polls the 1-minute load every 30 s until it
is at or below the threshold, for at most `--wait` seconds, then gives up with a GATE ERROR.
Run gates one at a time, with nothing else heavy on the box: a red gate under load is a
hypothesis about load first.

## Host notes

- **GNU `timeout` on macOS** may live only in a user's Homebrew prefix (`~/homebrew/bin`),
  which `env -i` hides. The default gate PATH adds that prefix when it exists; otherwise set
  `FORGE_GATE_GATE_PATH`. A `timeout` missing from the gate PATH is a GATE ERROR before pytest
  starts.
- **A user-site editable install** (an egg-link to another checkout) makes `import forge_gate`
  load that checkout's code from any worktree unless `PYTHONPATH=DIR/src` is set. The tools set
  it and then prove where `forge_gate` came from.
- **No `pytest-timeout` on a host:** pytest then warns about the unknown `timeout` option and the
  per-test marks do nothing. The gate's own wall timeout still bounds the run, and the record
  says whether the plugin loaded.
- **A umask-002 host:** a directory a test or a fixture makes there is group-writable
  (`0775`) unless it is created with an explicit mode, and guards that refuse group-writable
  directories then fail. Fixtures pass `mode=0o700`. The gate records the umask.
- **A loaded host:** set `--max-load` per host (an idle dev machine may already sit near 2).
- **A case-insensitive file system:** `paths.canonical` returns the stored spelling, so two
  spellings of one folder are one folder. On Linux it falls back to `realpath`.

## What the tools do not do

See `KNOWN-DEBT.md`, KD-16.
