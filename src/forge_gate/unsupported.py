"""Reason-coded UNSUPPORTED taxonomy for lenses (BRIEF §3.2).

A lens that cannot render a verdict must say WHY, in a way that names WHOSE
problem it might be. Free-string reasons hide the distinction that matters:
  "the file is malformed" (certain, the user must FIX)
vs.
  "our capture subprocess died" (undetermined, could be the lens).

Reporting "your code failed" when the honest answer is "our analyser could
not complete" is the single most damaging thing this tool can do.

Every reason code is declared here with (subclass, meaning, remedy,
dispositions). ``_u(code, detail)`` is the only permitted constructor;
an undeclared code raises ``KeyError``, by design — a code with no
declared remedy and no allowed dispositions must not reach a reader.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import FrozenSet


@dataclass(frozen=True)
class Unsupported:
    code: str
    subclass: str
    meaning: str
    remedy: str
    dispositions: FrozenSet[str]
    detail: str

    def summary(self) -> str:
        d = f": {self.detail}" if self.detail else ""
        return f"[{self.code}] {self.meaning}{d}"


_REGISTRY: dict[str, dict] = {
    "U1:no_coverage": {
        "subclass": "U1",
        "meaning": "no implementation for this lane",
        "remedy": "TOOL_WORK — extend the lens, or declare the lane out of scope",
        "dispositions": frozenset({"TOOL_WORK", "DEFER"}),
    },
    "U2:contract_unratified": {
        "subclass": "U2",
        "meaning": "contract exists but is not human-ratified",
        "remedy": "HUMAN_RATIFY — the ratification queue owns this",
        "dispositions": frozenset({"HUMAN_RATIFY", "DEFER"}),
    },
    "U3:input_malformed": {
        "subclass": "U3",
        "meaning": "required input is absent or malformed (cause CERTAIN)",
        "remedy": "FIX the input, or QUARANTINE the pattern",
        "dispositions": frozenset({"FIX", "QUARANTINE"}),
    },
    "U4:capture_failed": {
        "subclass": "U4",
        "meaning": "capture subprocess exited non-zero — cause UNDETERMINED",
        "remedy": "investigate stderr; the lens OR the input may be at fault",
        "dispositions": frozenset({"FIX", "TOOL_WORK", "QUARANTINE"}),
    },
    "U4:coverage_gap": {
        "subclass": "U4",
        "meaning": "lens ran but could not exercise the code enough to render a verdict — cause UNDETERMINED",
        "remedy": "widen lens coverage OR restructure the code so the lens can reach it; blame is not fixed",
        "dispositions": frozenset({"TOOL_WORK", "FIX", "DEFER"}),
    },
}


def _u(code: str, detail: str = "") -> Unsupported:
    entry = _REGISTRY[code]  # KeyError on undeclared, by design
    return Unsupported(
        code=code,
        subclass=entry["subclass"],
        meaning=entry["meaning"],
        remedy=entry["remedy"],
        dispositions=entry["dispositions"],
        detail=(detail or "")[:200],
    )


def declared_codes() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY.keys()))
