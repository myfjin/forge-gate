"""P1 outcomes (design v1.4.5 §4a.2): every check ends FOUND, LOOKED-CLEAN or BLIND(reason).

Empty is never a verdict. BLIND reasons are scout's own vocabulary, separate from the
U-codes in ``forge_gate.unsupported``.
"""
from __future__ import annotations

from dataclasses import dataclass, field

FOUND = "FOUND"
LOOKED_CLEAN = "LOOKED-CLEAN"
BLIND = "BLIND"
KINDS = (FOUND, LOOKED_CLEAN, BLIND)
BLIND_FLAG_SHARE = 0.10  # more than 10% of a run's items BLIND -> the run is flagged


@dataclass(frozen=True)
class Outcome:
    kind: str
    reason: str = ""                       # required for BLIND, empty otherwise
    evidence: tuple = field(default=())    # what was found / what was looked at

    def __post_init__(self):
        if self.kind not in KINDS:
            raise ValueError(f"outcome {self.kind!r} not in {KINDS}")
        if (self.kind == BLIND) != bool(self.reason):
            raise ValueError("BLIND needs a reason; FOUND and LOOKED-CLEAN take none")

    def label(self) -> str:
        return f"BLIND({self.reason})" if self.kind == BLIND else self.kind


def found(*evidence) -> Outcome:
    return Outcome(FOUND, "", tuple(evidence))


def clean(*evidence) -> Outcome:
    return Outcome(LOOKED_CLEAN, "", tuple(evidence))


def blind(reason: str, *evidence) -> Outcome:
    return Outcome(BLIND, reason, tuple(evidence))
