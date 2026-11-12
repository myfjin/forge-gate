"""Scout's report cards (design v1.4.5 §4.4.1 item 1, build step 3a).

A card is the validator's card, field for field, so the registry build can read scout's
report with the code that reads the validator's (D1b, D7-5). The validator's shape was
taken from the bytes of its reports on the deploy host on 2026-11-11 (chaos,
metamorphic, twin_divergence, invariant_mine; see tests/fixtures/scout/). Scout adds
two fields and never removes one:

- ``id``: the namespaced scout id (ids.py). The registry build matches it verbatim.
- ``source``: always ``"scout"``.

``pattern`` carries the same id, so a reader that still joins by ``stem(pattern)``
gets a key that keeps its ``scout:`` prefix and can match no library row.

A report is ``{"cards": [...]}``, the top-level shape every validator card report has.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .ids import BadScoutId, parse_id

# Present on every validator card read on 2026-11-11 (``why`` and ``ratified`` are optional).
VALIDATOR_FIELDS = (
    "pattern", "technique", "severity", "class", "fixture", "expected", "actual",
    "verdict", "verdict_target", "confidence", "repro_cmd", "oracle_coverage_kind",
)
OPTIONAL_FIELDS = ("why", "ratified")
SCOUT_FIELDS = ("id", "source")
SOURCE = "scout"

# Oracle kinds honest_pass knows (honest_pass.py: xlang, positive, chaos, metamorphic,
# invariant) plus the three D1 restores (roundtrip, reseed, reseed_ref; F42).
KINDS = frozenset({"xlang", "positive", "chaos", "metamorphic", "invariant",
                   "roundtrip", "reseed", "reseed_ref"})
VERDICTS = frozenset({"PASS", "FAIL", "UNSTABLE", "UNSUPPORTED", "PROPOSAL", "RATIFIED"})


class BadCard(ValueError):
    """A card scout must not write or accept."""


def check_validator_shape(card: dict) -> None:
    """The fields every validator card has, with string values. Raises BadCard."""
    if not isinstance(card, dict):
        raise BadCard(f"card is {type(card).__name__}, not dict")
    missing = [k for k in VALIDATOR_FIELDS if k not in card]
    if missing:
        raise BadCard(f"missing validator fields {missing}")
    wrong = [k for k in VALIDATOR_FIELDS if not isinstance(card[k], str)]
    if wrong:
        raise BadCard(f"non-string validator fields {wrong}")
    if "ratified" in card and not isinstance(card["ratified"], bool):
        raise BadCard("ratified must be a bool")


def check_scout_card(card: dict) -> None:
    """A scout card: validator shape + a canonical scout id in both id and pattern."""
    check_validator_shape(card)
    if card.get("source") != SOURCE:
        raise BadCard(f"source must be {SOURCE!r}, got {card.get('source')!r}")
    try:
        parse_id(card.get("id"))
    except BadScoutId as e:
        raise BadCard(str(e)) from None
    if card["pattern"] != card["id"]:
        raise BadCard("pattern must equal id (a stem() reader must never see a bare path)")
    if card["oracle_coverage_kind"] not in KINDS:
        raise BadCard(f"oracle_coverage_kind {card['oracle_coverage_kind']!r} not in {sorted(KINDS)}")
    if card["verdict"] not in VERDICTS:
        raise BadCard(f"verdict {card['verdict']!r} not in {sorted(VERDICTS)}")
    extra = set(card) - set(VALIDATOR_FIELDS) - set(OPTIONAL_FIELDS) - set(SCOUT_FIELDS)
    if extra:
        raise BadCard(f"fields the validator shape does not have: {sorted(extra)}")


def make_card(scout_id: str, *, kind: str, verdict: str, technique: str, fixture: str,
              expected: str, actual: str, repro_cmd: str, why: str | None = None,
              severity: str = "info", card_class: str | None = None,
              verdict_target: str = "PATTERN", confidence: str = "high") -> dict:
    card = {
        "id": scout_id,
        "source": SOURCE,
        "pattern": scout_id,
        "technique": technique,
        "severity": severity,
        "class": card_class or f"{kind}-coverage",
        "fixture": fixture,
        "expected": expected,
        "actual": actual,
        "verdict": verdict,
        "verdict_target": verdict_target,
        "confidence": confidence,
        "repro_cmd": repro_cmd,
        "oracle_coverage_kind": kind,
    }
    if why is not None:
        card["why"] = why
    check_scout_card(card)
    return card


def write_report(path: Path, cards: list[dict]) -> Path:
    """Write ``{"cards": [...]}`` atomically (temp file + rename, as honest_pass does)."""
    for c in cards:
        check_scout_card(c)
    kinds_by_id: dict = {}
    for c in cards:
        kinds_by_id.setdefault(c["id"], []).append(c["oracle_coverage_kind"])
    dup = sorted(i for i, ks in kinds_by_id.items() if len(ks) != len(set(ks)))
    if dup:
        raise BadCard(f"two cards for the same id and kind: {dup}")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"cards": cards}, fh, ensure_ascii=False, indent=1)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return path


def load_report(path: Path) -> list[dict]:
    """Read a scout report; every card is checked, the first bad one raises."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("cards"), list):
        raise BadCard(f"{path}: not a card report (needs a top-level 'cards' list)")
    seen: dict = {}
    for i, c in enumerate(data["cards"]):
        try:
            check_scout_card(c)
        except BadCard as e:
            raise BadCard(f"{path}: card {i}: {e}") from None
        key = (c["id"], c["oracle_coverage_kind"])
        if key in seen:  # the reader enforces what write_report does (6E review B3a-2)
            raise BadCard(f"{path}: card {i}: same id and kind as card {seen[key]}")
        seen[key] = i
    return data["cards"]
