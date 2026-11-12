"""FAIL-evidence payload for forge-gate lenses. Taxonomy-agnostic.

A lens that FAILs must be able to say WHY in a form a human can act on: the
source under test, the failing input, a traceback if there was one, the
intermediate values that led to the wrong output, and — for the twin lens —
the compiler stderr when the C++ half didn't build. This module owns the
payload shape and the source-capture helper. Nothing more.

Every field the caller cannot recover post-mortem is a REQUIRED constructor
argument. The one that matters most is ``source_text``: forge-gate's five lens
adapters run patterns in ``NamedTemporaryFile(delete=False)`` subprocesses and
unlink the temp file in a ``finally:`` block after the subprocess dies
(``src/forge_gate/lenses.py:357/378``, ``tools/guard_lens.py:679/817``). By
the time debug wants to introspect the FAIL, the source file is already gone
and ``inspect.getsource`` / ``traceback.extract_tb`` cannot recover it. The
``finally: unlink()`` is a safety boundary, not accidental — so the fix
belongs HERE, not in the isolation code. Callers pre-capture source with
``capture_source`` BEFORE they enter the ``finally:`` block, and hand the
text in as a required field.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
EXTRACT-READY DISCIPLINE — three rules the module enforces on itself
(BRIEF-DEBUG-FEATURE-2026-09-05.md §3a). A future patterns-tool debug
caller must be able to import this module without picking up forge-gate
concepts. If any of these rules break, ``tests/verify_debug_core_purity.sh``
goes RED.

1. This module never imports from ``forge_gate.lenses`` or
   ``forge_gate.unsupported``. Dependency graph is one-way:
   ``lenses -> debug_core``, never back. If the core needs a type from a
   lens, take a dataclass in the signature — do not reach across.

2. No U-code awareness inside the core. ``DebugEvidence.caller_label``
   accepts an opaque ``str | None``; the core never inspects it, never
   pattern-matches on ``U1``/``U2``/``U3``/``U4``, never imports the
   registry. Routing on U-codes lives in the forge-gate caller layer.

3. Dataclasses in signatures, never ``LensResult`` or forge-gate-native
   types. When a second caller shows up (patterns-tool or otherwise) it
   must be able to construct the debug call from its own types without
   depending on forge-gate. If a helper here would take a lens verdict,
   it takes a plain dict or a locally-defined dataclass instead.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
from __future__ import annotations

import inspect
import traceback
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from types import TracebackType
from typing import Any, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class DebugEvidence:
    """One FAIL's evidence payload. Immutable; the caller composes it before
    the isolation sandbox tears down, then hands it to a formatter.

    Required fields carry what cannot be recovered post-mortem:

    - ``source_text``    -- the code being tested, captured BEFORE unlink
    - ``source_origin``  -- provenance of ``source_text`` (a resolved path,
                            or ``"<inline>"`` when the caller passed source
                            text directly rather than a file). A formatter
                            uses this to print a header the reader can find.
    - ``failing_input``  -- the actual input value that produced the FAIL,
                            in a form the caller can serialise. The core
                            does not interpret it.

    Optional fields are populated only by the components that own them
    (traceback capture, intermediate-value dump, delta-debugger, compiler-
    output parser, formatter). ``None`` means "not captured for this FAIL"
    — distinct from an empty string / empty dict, which mean "captured and
    was empty". A formatter must respect that distinction.

    ``caller_label`` is an opaque string the caller attaches for its own
    routing (forge-gate puts a U-code here, patterns-tool would put a
    check-name). The core never reads it. See rule 2 in the module
    docstring.
    """

    source_text: str
    source_origin: str
    failing_input: Any

    traceback_text: str | None = None
    intermediate_values: dict[str, Any] | None = None
    compiler_output: str | None = None
    shrunk_input: Any = None

    caller_label: str | None = None
    caller_metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def capture_source(target: str | Path | Any) -> tuple[str, str]:
    """Pre-capture source text so a later ``finally: unlink()`` in the caller
    cannot destroy it. Returns ``(source_text, source_origin)``.

    Accepts:
      - a filesystem path (``str`` or ``Path``): read the file NOW
      - a callable / module / class: fall back to ``inspect.getsource``
      - an inline source string: pass through with origin ``"<inline>"``

    Callers that write a ``NamedTemporaryFile(delete=False)`` and subprocess-
    execute it MUST call this BEFORE the ``finally:`` block that unlinks
    the temp file. That is the one line that would have burned the first
    debug session on a determinism/guard FAIL — see module docstring.
    """
    if isinstance(target, (str, Path)):
        p = Path(target)
        if p.exists() and p.is_file():
            return p.read_text(), str(p.resolve())
        # Not a live path. Accept as inline source only when the string
        # actually looks like source; otherwise refuse rather than guess.
        if isinstance(target, str) and (
            "\n" in target
            or target.lstrip().startswith(("def ", "class ", "import ", "from "))
        ):
            return target, "<inline>"
        raise FileNotFoundError(
            f"capture_source: not a live file and not inline source: {target!r}. "
            "If this is a temp file that has been unlinked, capture BEFORE the "
            "finally: block that unlinks it."
        )
    try:
        origin = f"<inspect:{getattr(target, '__name__', type(target).__name__)}>"
        return inspect.getsource(target), origin
    except (OSError, TypeError) as e:
        raise ValueError(
            f"capture_source: could not obtain source for {target!r}: {e}"
        ) from e


# ─────────────────────────────────────────────────────────────────────────────
# Traceback + intermediate-value capture (step 3 of the brief)
#
# Both helpers stay taxonomy-agnostic. They know about Python exceptions and
# frames; they do not know about lenses, U-codes, or LensResult. This is
# rule 1 of the extract-ready discipline stated at the top of the file.
# ─────────────────────────────────────────────────────────────────────────────

_TB_CAP_BYTES = 8_000  # generous; a formatted traceback is bounded by depth
_REPR_CAP = 200        # per-value repr cap — locals dumps stay bounded


def capture_traceback(exc: BaseException, tb: TracebackType | None = None) -> str:
    """Format an exception + traceback into a bounded string.

    Tail-truncates when it exceeds ``_TB_CAP_BYTES`` so a runaway recursion
    trace cannot dominate the evidence payload. A trailing marker names the
    truncation so a reader does not mistake a cut trace for the whole story.
    """
    lines = traceback.format_exception(type(exc), exc, tb if tb is not None else exc.__traceback__)
    text = "".join(lines)
    if len(text) <= _TB_CAP_BYTES:
        return text
    keep = _TB_CAP_BYTES - 80
    return text[:keep] + f"\n... [traceback truncated: {len(text) - keep} bytes elided]\n"


def _safe_repr(v: Any, cap: int = _REPR_CAP) -> str:
    """repr(v) that never itself raises and never returns more than ``cap``
    characters. A custom ``__repr__`` that raises is a common defect in the
    code we are trying to debug — we must not add a second failure on top.

    NAMED LIMIT (6E review 2026-09-05): a ``__repr__`` that *hangs* (infinite
    loop or blocking I/O) is NOT protected — Python has no interruptible-repr
    primitive. If a hanging-repr defect is suspected, run the target under a
    timeout at the caller layer, not here."""
    try:
        r = repr(v)
    except Exception as e:  # noqa: BLE001 — repr is the failure we defend against
        return f"<unrepr-able: {type(v).__name__}: {type(e).__name__}>"
    if len(r) > cap:
        suffix = f"... [{len(r)} chars total]"
        keep = max(0, cap - len(suffix))
        return r[:keep] + suffix
    return r


def dump_leaf_locals(tb: TracebackType | None, *, source_only: str | None = None) -> dict[str, str] | None:
    """Return ``{name: safe_repr(value)}`` for the LEAF frame of a traceback.

    "Leaf frame" is the innermost frame where the exception actually surfaced
    -- typically the user's own code. Traversal skips no frames; the leaf is
    just the last one in the ``tb_next`` chain.

    ``source_only``: when set, and when the leaf frame's ``co_filename`` does
    NOT match, returns ``None`` -- protection against dumping stdlib locals
    when the crash surfaced deep inside a library the user did not write.
    A caller that wants everything passes ``source_only=None``.

    NAMED LIMIT (6E review 2026-09-05): ``source_only=""`` filters EVERY
    frame out (empty string never equals a real ``co_filename``). This is a
    footgun for callers that build the filter from a possibly-empty variable
    -- use ``None`` for "no filter", not ``""``. Callers must not pass ``""``
    unless they specifically want to reject every frame.

    Returns ``None`` (not an empty dict) when there is no frame to inspect,
    or when ``source_only`` filtered the leaf out. Distinct from ``{}``
    which would mean "leaf frame had no locals".
    """
    if tb is None:
        return None
    # Walk to the leaf
    while tb.tb_next is not None:
        tb = tb.tb_next
    frame = tb.tb_frame
    if source_only is not None and frame.f_code.co_filename != source_only:
        return None
    return {name: _safe_repr(value) for name, value in frame.f_locals.items()}


# ─────────────────────────────────────────────────────────────────────────────
# Delta debugging (step 4 of the brief §3.3, §8.2)
#
# Given a failing input and a predicate that says whether a subset still fails,
# shrink the input to a minimal subset that still fails. Zeller & Hildkamp
# 2002 ddmin, list-based form: the input is a sequence of elements; the
# minimal output is a subsequence.
#
# TAXONOMY-AGNOSTIC. The predicate is opaque — the core does not know or care
# whether "still fails" means "metamorphic relation still breaks" or "invariant
# still out of band" or "twin still disagrees". Rule 1 of the extract-ready
# discipline stated at the top of the file: no forge-gate imports.
#
# DETERMINISTIC. Partitions are index-based, chunk selection is left-to-right.
# Two runs on the same (elements, predicate) return the same ShrinkResult.
# Acceptance criterion 4 of the brief.
#
# BOUNDED. §8.2 of the brief: shrinking can be expensive; cap the search and
# report the cap. `max_steps` counts predicate evaluations; on hit, the loop
# returns the best-so-far and sets `hit_step_cap=True` so a formatter can say
# "shrunk to N elements after M steps (cap reached)".
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ShrinkResult:
    """Outcome of a ddmin run.

    ``minimal`` is the smallest subset of the original that still made the
    predicate return True. If the predicate never returned True (input did not
    reproduce the failure at all), ``minimal`` equals the original input and
    ``predicate_ever_failed`` is False — callers must respect that distinction
    rather than treating an unshrunk result as a shrunk one.

    ``steps`` counts predicate evaluations, not iterations of the outer loop.
    ``hit_step_cap`` is True iff the search exited because it reached
    ``max_steps`` rather than because the input was already minimal.
    """

    minimal: list[Any]
    steps: int
    hit_step_cap: bool
    predicate_ever_failed: bool


def ddmin(
    elements: Sequence[T],
    still_fails: Callable[[list[T]], bool],
    *,
    max_steps: int = 200,
) -> ShrinkResult:
    """Return a minimal subset of ``elements`` for which ``still_fails`` is True.

    The Zeller & Hildkamp 2002 ddmin, list-based. Partition the current
    candidate into ``n`` chunks; if removing chunk i still fails, drop it and
    reduce granularity; otherwise if removing every chunk passes, double n
    and try again; stop when n exceeds the candidate size.

    The predicate is called only with subsequences of the original
    ``elements`` (in original order) — never with reorderings, never with
    duplicates. A predicate that RAISES is treated as PASS (do not shrink to
    an input that crashed the predicate itself); the core cannot distinguish
    "the predicate broke" from "the target broke differently" without more
    signal, and the safe default is not to remove elements based on a
    predicate error.

    ``max_steps`` caps the number of predicate evaluations. If hit, the
    best-so-far is returned and ``hit_step_cap`` is True — the caller should
    surface both to the human reading the debug artifact.

    NAMED LIMIT: this shrinker operates on element SEQUENCES. A caller that
    wants to shrink a source string first splits it into lines/tokens; a
    caller that wants to shrink a dict of kwargs first flattens it to a list
    of (key, value) pairs. Reconstructing the original type after shrinking
    is the caller's job — the core stays type-agnostic on purpose.
    """
    elements = list(elements)

    def _run(subset: list[T]) -> bool:
        try:
            return bool(still_fails(subset))
        except Exception:  # noqa: BLE001 — predicate errors are PASS by policy
            return False

    steps = 0
    ever_failed = False

    # Verify the input reproduces the failure at all. Zeller ddmin assumes it
    # does; if not we return the original unshrunk with the flag off so a
    # caller can distinguish "already minimal" from "did not reproduce".
    if not elements:
        return ShrinkResult(minimal=[], steps=0, hit_step_cap=False, predicate_ever_failed=False)
    steps += 1
    if not _run(elements):
        return ShrinkResult(
            minimal=elements, steps=steps, hit_step_cap=False, predicate_ever_failed=False,
        )
    ever_failed = True

    current = elements
    n = 2

    while len(current) >= 2:
        if steps >= max_steps:
            return ShrinkResult(
                minimal=current, steps=steps, hit_step_cap=True, predicate_ever_failed=ever_failed,
            )

        # Partition current into n roughly-equal chunks by index.
        size = len(current)
        chunk_size = max(size // n, 1)
        chunks: list[list[T]] = []
        i = 0
        while i < size and len(chunks) < n:
            end = size if len(chunks) == n - 1 else min(i + chunk_size, size)
            chunks.append(current[i:end])
            i = end

        # Try removing each chunk. First shrink that still fails wins; reduce
        # granularity and restart the outer loop with the smaller candidate.
        reduced = False
        for idx, chunk in enumerate(chunks):
            if steps >= max_steps:
                return ShrinkResult(
                    minimal=current, steps=steps, hit_step_cap=True, predicate_ever_failed=ever_failed,
                )
            candidate = [x for j, c in enumerate(chunks) if j != idx for x in c]
            if not candidate:
                continue
            steps += 1
            if _run(candidate):
                current = candidate
                n = max(n - 1, 2)
                reduced = True
                break

        if reduced:
            continue

        # No chunk-removal helped. Increase granularity; stop when n exceeds
        # the candidate size (nothing left to partition).
        if n >= len(current):
            break
        n = min(n * 2, len(current))

    return ShrinkResult(
        minimal=current, steps=steps, hit_step_cap=False, predicate_ever_failed=ever_failed,
    )
