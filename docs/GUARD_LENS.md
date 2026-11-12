# Guard Lens

The Guard Lens is a **gating** NaN/Inf finiteness check for scalar parameters
in Python functions submitted to Forge Gate. It needs no oracle: it poisons
scalar arguments with `NaN` or `±Inf`, runs the function, and decides whether
the code silently turns poison into a plausible finite result.

## Gating status

The Guard Lens is **enforced**: a `FAIL` verdict sets `objected=True` and can
turn an otherwise passing build into a `FAIL`. Only a genuine NaN-defect may
gate; `INCONCLUSIVE`, `ERROR`, `UNSUPPORTED`, and `PASS` never do.

## Why this lens matters

Every other Forge Gate lens needs an **oracle** — a known-correct
implementation of a known algorithm. When a submitted file does not match a
pattern in the catalog, those lenses return `UNSUPPORTED`. They cannot judge
code they have never seen.

The Guard Lens does not compare against a reference implementation. It checks a
universal structural property: does the function contain a finiteness guard on
its scalar inputs? Because it needs no oracle, it can judge **arbitrary code**.
That is the only reason it can return a verdict on submitted code that is not
already in the pattern library.

It is now a **gating** lens: a genuine NaN-defect `FAIL` sets `objected=True`
and can fail a build. `INCONCLUSIVE`, `ERROR`, and `UNSUPPORTED` remain
non-objecting by design.

## What the lens does

For each top-level public function in a fixture:

1. Identify scalar parameters (`float`/`int` annotations, numeric defaults, or
   parameter-name heuristics for unannotated arguments).
2. Build a clean baseline argument set from type hints and defaults.
3. Replace one scalar at a time with `NaN` or `±Inf`.
4. Run the function and classify the returned float elements.
5. Compare returned collections componentwise against the clean baseline when
   needed.

Container parameters (lists, dicts, matrices, label vectors) are filled with
small synthetic fixtures so the function can be exercised.

## The criterion

The lens classifies the **float-typed elements** of the return value. Ints and
bools are treated as counters, status flags, or lengths — not numeric results.
So `(10000, nan)` is handled (the counter is an int; the float is poison), but
`(10000, 3.5)` from a `NaN` input is a defect.

### NaN is conclusive

| NaN input produces | Verdict |
|---|---|
| Raises an exception | HANDLED → PASS |
| All returned float elements are `NaN` or `±Inf` | HANDLED → PASS |
| A mixture of finite and `NaN`/`±Inf` floats | DEFECT → FAIL |
| All returned float elements are finite | DEFECT → FAIL |

A scalar `NaN` must never silently become a finite float, a non-NaN
collection, or a mixture of finite and NaN float values.

### Inf is **not** conclusive

| `±Inf` input produces | Verdict |
|---|---|
| Raises an exception | HANDLED → PASS |
| All returned float elements are `NaN` or `±Inf` | HANDLED → PASS |
| A mixture of finite and `NaN`/`±Inf` floats | ALLOWED → PASS |
| All returned float elements are finite | ALLOWED → PASS |

Many functions have correct finite limits at `±Inf`: `sigmoid(+Inf)=1.0`,
`sigmoid(-Inf)=0.0`, `tanh(±Inf)=±1.0`. The Guard Lens has no oracle, so it
must not object to finite output from `±Inf` input.

### Componentwise reachability rule

In a returned collection, a finite float component is a defect **only if it
changed** relative to the clean baseline. If it is unchanged, the component is
independent of the poisoned input and the result is correct.

```text
clean  (2000.0, 20.0)    poisoned (NaN, 20.0)   → y unchanged → CORRECT
clean  (0.4,   0.6)      poisoned (NaN, 1.0)    → y changed   → DEFECT
```

The first case is a separable gradient: the second component does not depend on
the poisoned parameter. The second case is a coupled computation where NaN
comparison falls through and silently replaces a finite value.

### Multi-shape guard

"Unchanged" can be a coincidence: both values might be `0.0`, or two unrelated
computations might collide on the same numeric value. The lens must verify
independence across **several genuinely different argument shapes** — varying
row count, feature count, magnitude, and label balance — before treating a
finite component as correct. Independence that survives many shapes is real;
once may be luck.

### Tolerance = NaN rule (honest heuristic)

A `NaN` tolerance/epsilon that disables an early-exit test (e.g. `fx <
tolerance`) is **weak**, not fabrication. The numeric value is still sound; only
the iteration-count or status meaning is corrupted. The lens must not object
when the float result itself is correct and only an int counter or status is
affected. If a `NaN` tolerance causes a finite float result to be wrong, that is
a defect. This is implemented as a parameter-name heuristic
(`tolerance`/`tol`/`epsilon`/`eps`/`atol`/`rtol`), labelled honestly, because
the lens has no oracle and cannot judge the correctness of a finite float
result.

### Summary

```text
NaN → finite float                    = DEFECT
Inf → finite float                    = ALLOWED
NaN → raise / all-floats-poison       = HANDLED
Inf → raise / all-floats-poison       = HANDLED
Component finite AND unchanged vs clean across multiple shapes = HANDLED
Component finite AND changed vs clean = DEFECT
```

## INCONCLUSIVE

`INCONCLUSIVE` means the lens could not exercise the code well enough to return
a meaningful verdict. Common causes:

* The clean baseline call raised an exception.
* The clean baseline produced degenerate output (all zeros, empty collections,
  `None`), so the fixture probably did not exercise the algorithm.
* The poisoned parameter produced output identical to the clean baseline,
  proving the parameter was unreachable.
* The fixture has no scalar parameters to test.
* The fixture exceeded the wall-clock timeout, CPU limit, or memory limit
  (submitted code is run in a sandboxed subprocess with hard limits).

`INCONCLUSIVE` is **never collapsed into PASS** and **never gates**. "Could not
measure" and "measured and clean" are different facts. A harness that cannot
exercise the code must not accuse the code.

## Known limits (documented, not bugs)

1. **Int status flags and loop counters are ignored by design.** The lens only
   judges float-typed return elements. A NaN comparison that silently corrupts
   an `int` status flag or loop counter is reported as `PASS` if the float
   payload is unchanged, because an oracle-free lens cannot know whether an
   `int` is meaningful metadata or a true numeric result.

2. **The multi-shape probe set is finite.** The lens tests
   `MULTI_SHAPE_COUNT` genuinely different argument shapes and `8` scalar
   multipliers spanning sign and a 1000× magnitude range. A fixture could in
   principle be tuned to make the clean and poisoned finite components collide
   across every shape in that finite set and evade detection. That is an
   accepted boundary: this lens targets ordinary sloppiness in AI-generated
   code, not adversarially crafted submissions. The limit is stated plainly so
   users do not mistake "no defect found" for "formally verified".

## Promotion checklist

The Guard Lens was promoted to gating on 2026-08-03 after the canonical
oracles survived the criterion:

* `tests/verify_guard_lens.sh`: 15/15 gates PASS, exit 0.
* `tests/verify_guard_lens_isolation.sh`: 4/4 adversarial probes PASS, exit 0.
* Canonical oracles: 0 of 10 objecting.
* Corpus sweep: 0 objections / 91 pass / 10 INCONCLUSIVE.

## Source files

* `tools/guard_lens.py` — implementation
* `tests/verify_guard_lens.sh` — 15-gate acceptance criterion
* `tests/verify_guard_lens_isolation.sh` — adversarial isolation criterion
* `src/forge_gate/lenses.py` — gating integration
