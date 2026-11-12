# Forge Gate — Client Verdict Report

## Specification v1 — 2026-08-03

This document specifies the format and language of the report Forge Gate
emits after checking a submitted file. It is a template for code generation,
not the code itself.

---

## Structure

Every report has six sections, in this order:

1. **Verdict** — one line, unambiguous
2. **Coverage** — what was NOT checked and why (as prominent as findings)
3. **Mode** — MATCHED or UNMATCHED
4. **What Was Checked** — plain language
5. **Findings** — evidence for each defect
6. **How to Reproduce** — the literal command

---

## 1. Verdict

One of three, at the very top, in a box or bold:

> **NO DEFECT FOUND** — we checked [N] parameters and found no evidence of
> the defects we test for. See "What Was Not Checked" below for limits.

> **DEFECT FOUND** — [N] parameter(s) produced a wrong result. See
> "Findings" below.

> **COULD NOT CHECK** — the file could not be loaded or executed. Reason:
> [syntax error / missing import / no functions found / etc.]

No "verified correct." No "fully tested." No "guaranteed." The verdict is
a factual statement about what the tool observed, not a warranty.

---

## 2. Coverage — What Was NOT Checked

This section is as prominent as the findings. Never a footnote. Every report
must state its blind spots in plain language.

### Always present, verbatim or close:

> **Correctness of the algorithm itself was not checked.** Forge Gate tests
> whether the code handles invalid inputs and edge cases correctly. It does
> not verify that the algorithm implements the intended mathematics. A file
> that passes all our checks may still compute the wrong thing for valid
> inputs.

### Mode-specific additions:

**If UNMATCHED (no reference algorithm available):**

> **This file does not match any algorithm we hold a reference for.** We
> could only check general robustness — whether the code detects invalid
> inputs like NaN, infinity, or negative counts. We could not check whether
> the algorithm is correct. "No defect found" under this mode means we saw
> no robustness failures, not that the code is correct.

**If MATCHED (reference algorithm available):**

> **This file matches [algorithm name].** We checked both correctness
> (output matches the reference implementation within tolerance) and
> robustness (handling of invalid inputs). See "What Was Checked" for
> details.

### Per-file limits, generated dynamically:

> **Functions with no numeric parameters:** [N] function(s) take no numeric
> parameters. These received minimal coverage — we can only check that they
> run without crashing, not that their logic is sound.

> **Could not execute:** [N] function(s) could not be executed because
> [reason: missing dependency / type mismatch / etc.]. These were not
> judged at all.

> **Parameter unreachable:** [N] parameter(s) were not reached by the test
> data — the function returned the same result whether we supplied a valid
> or invalid value. This usually means the parameter is not used in the
> code path that was exercised. We report this honestly rather than
> claiming the parameter was tested.

---

## 3. Mode

One line at the top of the report, before the findings:

> **Mode: MATCHED** — this file was identified as implementing
> [algorithm name]. We hold a reference implementation and can check
> correctness.

> **Mode: UNMATCHED** — this file does not match any algorithm we hold a
> reference for. We checked robustness only.

---

## 4. What Was Checked

Written in plain language, never lens names. Examples:

> **Invalid numeric inputs (NaN, infinity):** For every numeric parameter,
> we supplied the special value "not a number" (NaN) and observed whether
> the code detected it (raised an error, returned NaN) or silently produced
> a confident-looking finite number. A finite number from NaN input is a
> defect — it means the code fabricated a plausible result from poison.
>
> Infinity is treated differently: some functions correctly saturate at
> infinity (e.g., a sigmoid function returns exactly 1.0 for +infinity).
> We report infinity observations but do not flag them as defects, because
> we cannot tell correct saturation from a bug without knowing the
> intended mathematics.

> **Negative or zero values for parameters that must be positive:** For
> parameters whose name or type annotation suggests they should be positive
> (counts, learning rates, tolerances, probabilities), we supplied negative
> values and zero and checked whether the code rejected them or produced a
> wrong result.

> **Non-deterministic output:** We ran the code three times with the same
> inputs and compared the outputs. If they differed, the code is
> non-deterministic — a defect for any algorithm that should be
> deterministic.

> **Cross-engine agreement (JavaScript only):** We ran the code in three
> JavaScript engines (Node.js, Bun, QuickJS) and compared the outputs. If
> they differed beyond floating-point precision, the code depends on
> engine-specific behaviour — a defect.

> **Correctness (MATCHED mode only):** We compared the output of this
> implementation against a known-correct reference implementation of the
> same algorithm, using the same inputs. We checked that the outputs match
> within a tolerance of [value]. We also checked that mathematical
> relationships hold (e.g., scaling the input scales the output
> proportionally, shifting the input shifts the output by the same amount).

---

## 5. Findings

Each finding has four parts:

### 5a. What was wrong

One sentence in plain language.

> **Parameter `learning_rate` accepted NaN and produced a finite result.**
> The function returned `weights=[1.84, 2.87], bias=0.52` when
> `learning_rate=NaN` — the same as if a valid learning rate had been
> supplied. A NaN learning rate should either raise an error or cause the
> output to contain NaN. A finite result from NaN input means the NaN was
> silently ignored, and the code would behave unpredictably if a real
> computation ever produced one.

### 5b. The exact input

> ```
> linear_regression_gd(
>     X = [[1, 2], [2, 3], [3, 5], [4, 7], [5, 8]],
>     y = [1, 2, 3, 4, 5],
>     learning_rate = NaN,
>     iterations = 1000
> )
> ```

### 5c. The exact output

> ```
> {
>   "weights": [1.842168533693048, 2.8712117069626166],
>   "bias": 0.5186281751329075
> }
> ```

### 5d. Why it is wrong

> A NaN learning rate should make gradient descent impossible — the update
> step `w -= lr * dw` becomes `w -= NaN * dw = NaN`, and every subsequent
> value should be NaN. Returning finite weights and bias means the NaN was
> never used. The code has no guard against NaN input.

---

## 6. How to Reproduce

The literal command, so the client can run it themselves:

```
python3 tools/guard_lens.py --fixture /path/to/submitted/file.py
```

Or for a full report:

```
python3 -m forge_gate.evaluate --file /path/to/submitted/file.py
```

---

## Worked Example

### MATCHED mode — defect found

```
╔══════════════════════════════════════════════════════════════╗
║  FORGE GATE — Verdict Report                               ║
║  File: gradient_descent_momentum.py                         ║
║  Date: 2026-08-03                                          ║
╚══════════════════════════════════════════════════════════════╝

═══ VERDICT ═══

  DEFECT FOUND — 2 parameters produced a wrong result.

═══ MODE ═══

  Mode: MATCHED — this file was identified as implementing
  gradient descent with momentum. We hold a reference
  implementation and can check correctness.

═══ WHAT WAS NOT CHECKED ═══

  Correctness of the algorithm itself was not checked in full.
  Forge Gate tests whether the code handles invalid inputs and
  edge cases correctly. It does not verify that the algorithm
  implements the intended mathematics. A file that passes all our
  checks may still compute the wrong thing for valid inputs.

  This file matched an algorithm we hold a reference for, so we
  checked both correctness (output matches reference) and
  robustness (handling of invalid inputs). However, correctness
  was checked only for the specific test inputs we chose — not
  for all possible inputs.

  One parameter (tolerance) was not reached by the test data:
  the function returned the same result whether we supplied a
  valid or invalid tolerance value. This usually means the
  parameter is not used in the code path that was exercised. We
  report this honestly rather than claiming the parameter was
  tested.

═══ WHAT WAS CHECKED ═══

  Invalid numeric inputs (NaN, infinity): For every numeric
  parameter, we supplied the special value "not a number" (NaN)
  and observed whether the code detected it (raised an error,
  returned NaN) or silently produced a confident-looking finite
  number. A finite number from NaN input is a defect — it means
  the code fabricated a plausible result from poison.

  Infinity is treated differently: some functions correctly
  saturate at infinity (e.g., a sigmoid function returns exactly
  1.0 for +infinity). We report infinity observations but do not
  flag them as defects, because we cannot tell correct
  saturation from a bug without knowing the intended mathematics.

  Correctness: We compared the output of this implementation
  against a known-correct reference implementation of gradient
  descent with momentum, using the same inputs. We checked that
  the outputs match within a tolerance of 1e-6.

═══ FINDINGS ═══

  ── Finding 1: Parameter `x` accepted NaN and produced a
     mixture of valid and invalid output.

     Input:
       gradient_descent_momentum(x=NaN, y=2.0, learning_rate=0.01,
                                momentum=0.9, iterations=100)

     Output:
       (NaN, 4.0)

     Why it is wrong:
       The gradient of f(x,y) = 100x² + y² is (200x, 2y). When x
       is NaN, the x-component of the gradient is NaN, which is
       correct propagation. But the y-component (4.0) is finite
       and differs from the clean baseline (2.0) — it changed
       because the NaN in x affected the y computation through
       the momentum term. A NaN in one parameter should not
       corrupt an independent component of the result.

  ── Finding 2: Parameter `y` accepted NaN and produced a
     mixture of valid and invalid output.

     Input:
       gradient_descent_momentum(x=2.0, y=NaN, learning_rate=0.01,
                                momentum=0.9, iterations=100)

     Output:
       (400.0, NaN)

     Why it is wrong:
       Same pattern as Finding 1, reversed. The NaN in y
       corrupts the x-component through the momentum term.

═══ HOW TO REPRODUCE ═══

  python3 tools/guard_lens.py \
    --fixture /path/to/gradient_descent_momentum.py

═══ WHY THIS REPORT IS HONEST ═══

  Every verdict in this report was produced by running the code,
  never by a language model's opinion. The same input gives the
  same verdict every time, and anyone with access to the tool
  can re-run it. That is the reason to pay for this rather than
  ask a chatbot: you get a reproducible measurement, not a
  plausible-sounding guess.
```

### UNMATCHED mode — no defect found

```
╔══════════════════════════════════════════════════════════════╗
║  FORGE GATE — Verdict Report                               ║
║  File: custom_processor.py                                 ║
║  Date: 2026-08-03                                          ║
╚══════════════════════════════════════════════════════════════╝

═══ VERDICT ═══

  NO DEFECT FOUND — we checked 5 parameters and found no
  evidence of the defects we test for. See "What Was Not Checked"
  below for limits.

═══ MODE ═══

  Mode: UNMATCHED — this file does not match any algorithm we
  hold a reference for. We checked robustness only.

═══ WHAT WAS NOT CHECKED ═══

  ⚠️  THIS IS THE MOST IMPORTANT SECTION OF THIS REPORT.

  Correctness of the algorithm itself was not checked. Forge Gate
  tests whether the code handles invalid inputs and edge cases
  correctly. It does not verify that the algorithm implements the
  intended mathematics. A file that passes all our checks may
  still compute the wrong thing for valid inputs.

  This file does not match any algorithm we hold a reference for.
  We could only check general robustness — whether the code
  detects invalid inputs like NaN, infinity, or negative counts.
  We could not check whether the algorithm is correct.

  "No defect found" under this mode means we saw no robustness
  failures. It does NOT mean the code is correct. A separate
  review of the algorithm's logic is needed.

  One function (validate_input) takes no numeric parameters and
  received minimal coverage — we could only check that it runs
  without crashing.

═══ WHAT WAS CHECKED ═══

  Invalid numeric inputs (NaN, infinity): For every numeric
  parameter, we supplied the special value "not a number" (NaN)
  and observed whether the code detected it (raised an error,
  returned NaN) or silently produced a confident-looking finite
  number.

  Infinity is treated differently: some functions correctly
  saturate at infinity. We report infinity observations but do
  not flag them as defects.

═══ FINDINGS ═══

  No defects found.

═══ HOW TO REPRODUCE ═══

  python3 tools/guard_lens.py \
    --fixture /path/to/custom_processor.py

═══ WHY THIS REPORT IS HONEST ═══

  Every verdict in this report was produced by running the code,
  never by a language model's opinion. The same input gives the
  same verdict every time, and anyone with access to the tool
  can re-run it. That is the reason to pay for this rather than
  ask a chatbot: you get a reproducible measurement, not a
  plausible-sounding guess.
```

---

## Design Notes

### Why "What Was Not Checked" comes before findings

A client reads top-to-bottom. If the first thing they see after the
verdict is the list of blind spots, they understand the limits before
they see the results. If the blind spots come after the findings, they
may have already formed a conclusion that the limits would have
qualified.

### Why "Mode" is its own section

MATCHED and UNMATCHED have fundamentally different meanings for "no
defect found." Putting the mode in its own section, before findings,
forces the reader to see it. A MATCHED "no defect" means the code
produces the right answer for the tested inputs. An UNMATCHED "no
defect" means only that the code does not crash on NaN. These are
different facts and the report must not let them be confused.

### Why the honest selling point is at the bottom

The client reads the verdict first, then the limits, then the
findings. By the time they reach the bottom, they have seen what we
actually did. The selling point is not a headline — it is the answer to
the question "why should I trust this over a chatbot?" that the reader
will ask after seeing the limits. Putting it at the bottom means they
read the limits first and can evaluate the claim honestly.

### What I wanted to claim but could not honestly support

I wanted to say "every defect is reproducible" — but that is only true
for deterministic code. Non-deterministic code (using unseeded random,
current time, or network state) will produce different results on every
run. We detect non-determinism and flag it as a defect, but we cannot
guarantee reproducibility for code that is already broken in that way.
The honest statement is "same input gives the same verdict for
deterministic code, and we flag non-determinism when we find it."
