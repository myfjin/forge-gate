"""Build step 3a (design v1.4.5 §4.4.1 items 1 and 3; §8 exit gate):
cards parse in the validator's card shape; ids never reduce to a bare stem.

Fixtures only. ``tests/fixtures/scout/validator_cards.json`` holds card 0 of four
validator reports on the deploy host, copied 2026-11-11 (sha256 of each source report in
its ``_origin``).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from forge_gate.scout import (BadCard, BadScoutId, check_scout_card, check_validator_shape,
                              is_scout_id, load_report, make_card, make_id, parse_id,
                              write_report)
from forge_gate.scout.cards import OPTIONAL_FIELDS, SCOUT_FIELDS, VALIDATOR_FIELDS

FIX = Path(__file__).parent / "fixtures" / "scout" / "validator_cards.json"
COMMIT = "d7462d320c263f9870e80321e1edbb0d112fe6e6"


def honest_pass_stem(p):
    """Verbatim copy of stem() from the Brain's src/aura_brain/conscience/honest_pass.py:21-23
    (file md5 afe7c53d123f5f7a32d58bd9188442ab, read 2026-11-11). If the Brain changes it,
    re-copy; this test exists to prove scout ids survive it."""
    b = os.path.basename(p)
    return b[:-3] if b.endswith('.py') else (b.rsplit('.', 1)[0] if '.' in b else b)


def _validator_cards():
    return json.loads(FIX.read_text(encoding="utf-8"))["cards"]


def _card(sid, kind="chaos", verdict="PASS"):
    return make_card(sid, kind=kind, verdict=verdict, technique="chaos-fault-injection",
                     fixture="crash before commit", expected="no partial write",
                     actual="survival predicate held", repro_cmd="forge-gate scout --replay x")


# Ids that a raw "pool:source@commit:path:function" form would reduce to a bare, colliding stem.
NASTY = [
    ("github", "python/cpython", COMMIT, "Lib/urllib/request.py", "urlopen"),
    ("github", "python/cpython", COMMIT, "Lib/json/__init__.py", "dumps"),
    ("corpus", "corpus-20261111", "20261111T130229Z", "a/b.c/d.py", "Klass.method"),
    ("corpus", "c:1@x", "20261111T130229Z", "weird dir/x@y:z.py", "f"),
    ("github", "o/r", COMMIT, "pkg/ab_interleaving.py", "ab_interleaving"),
    ("github", "o/r", COMMIT, "π/ünï.py", "λ"),
    ("corpus", "c", "20261111T130229Z", "noext", "f.g.h"),
]


def test_real_validator_cards_parse_in_the_validator_shape():
    cards = _validator_cards()
    assert [c["oracle_coverage_kind"] for c in cards] == ["chaos", "metamorphic", "xlang", "invariant"]
    for c in cards:
        check_validator_shape(c)


def test_scout_card_is_the_validator_card_plus_id_and_source():
    real_keys = set().union(*(set(c) for c in _validator_cards()))
    card = _card(make_id(*NASTY[0]))
    check_validator_shape(card)
    check_scout_card(card)
    assert set(VALIDATOR_FIELDS) <= set(card)
    assert set(card) - real_keys == set(SCOUT_FIELDS)        # only id + source are new
    assert real_keys - set(card) <= set(OPTIONAL_FIELDS)    # nothing required is dropped


@pytest.mark.parametrize("parts", NASTY, ids=[p[3] for p in NASTY])
def test_id_round_trips_and_never_reduces_to_a_bare_stem(parts):
    sid = make_id(*parts)
    assert "/" not in sid
    assert tuple(vars(parse_id(sid)).values()) == parts
    s = honest_pass_stem(sid)
    assert s.startswith("scout:"), s
    assert s != honest_pass_stem(parts[3]) and s != parts[4]


def test_a_stem_reader_on_a_scout_report_keys_nothing_a_library_row_can_have(tmp_path):
    """honest_pass's own join loop, run on a scout report written to disk."""
    rep = write_report(tmp_path / "scout_report.json", [_card(make_id(*p)) for p in NASTY])
    keys = set()
    for c in json.loads(rep.read_text())["cards"]:
        if c.get("verdict") == "PASS":
            keys.add(honest_pass_stem(c["pattern"]))
    assert len(keys) == len(NASTY)
    assert all(k.startswith("scout:") for k in keys), keys
    assert not keys & {"request", "__init__", "ab_interleaving", "b", "ünï", "noext"}


def test_ids_match_verbatim_only():
    a = make_id(*NASTY[0])
    b = make_id(NASTY[0][0], NASTY[0][1], NASTY[0][2], "Lib/urllib/requesT.py", "urlopen")
    assert a != b and len(a) == len(b)
    assert sum(x != y for x, y in zip(a, b)) == 1
    with pytest.raises(BadScoutId):
        parse_id(a.replace("%2F", "%2f"))           # same decoded value, not canonical
    for bare in ["request", "ab_interleaving", "Lib/urllib/request.py",
                 f"github:python/cpython@{COMMIT}:Lib/urllib/request.py:urlopen", a + " ", ""]:
        assert not is_scout_id(bare), bare


@pytest.mark.parametrize("bad", [
    ("corpus", "s", COMMIT[:7], "a.py", "f"),          # abbreviated hash (B3a-1)
    ("corpus", "s", COMMIT[:39], "a.py", "f"),
    ("corpus", "s", COMMIT.upper(), "a.py", "f"),
    ("pool", "s", COMMIT, "a.py", "f"),
    ("corpus", "s", "not-a-commit", "a.py", "f"),
    ("corpus", "s", COMMIT, "/abs/a.py", "f"),
    ("corpus", "s", COMMIT, "a/../b.py", "f"),
    ("corpus", "s", COMMIT, "a//b.py", "f"),
    ("corpus", "", COMMIT, "a.py", "f"),
    ("corpus", "s", COMMIT, "a.py", " f"),
])
def test_make_id_refuses_what_it_could_not_parse_back(bad):
    with pytest.raises(BadScoutId):
        make_id(*bad)


def test_report_round_trip_and_rejections(tmp_path):
    sid = make_id(*NASTY[0])
    cards = [_card(sid, "chaos"), _card(sid, "metamorphic"), _card(make_id(*NASTY[1]), "xlang", "FAIL")]
    p = write_report(tmp_path / "r" / "scout_report.json", cards)
    assert load_report(p) == cards
    assert sorted(x.name for x in p.parent.iterdir()) == ["scout_report.json"]  # no temp litter

    with pytest.raises(BadCard, match="same id and kind"):
        write_report(tmp_path / "dup.json", [cards[0], cards[0]])

    planted = dict(cards[0], id="request", pattern="request")  # a bare stem slipped in
    (tmp_path / "planted.json").write_text(json.dumps({"cards": [cards[1], planted]}))
    with pytest.raises(BadCard, match="card 1: not a scout id"):
        load_report(tmp_path / "planted.json")

    (tmp_path / "dup-read.json").write_text(json.dumps({"cards": [cards[0], cards[2], cards[0]]}))
    with pytest.raises(BadCard, match="card 2: same id and kind as card 0"):   # B3a-2
        load_report(tmp_path / "dup-read.json")


@pytest.mark.parametrize("mutate,msg", [
    (lambda c: c.update(pattern="Lib/urllib/request.py"), "pattern must equal id"),
    (lambda c: c.update(source="validator"), "source must be"),
    (lambda c: c.pop("repro_cmd"), "missing validator fields"),
    (lambda c: c.update(oracle_coverage_kind="vibes"), "oracle_coverage_kind"),
    (lambda c: c.update(verdict="pass"), "verdict"),
    (lambda c: c.update(tier="hard"), "does not have"),   # a card cannot carry a tier
    (lambda c: c.update(ratified="yes"), "ratified must be a bool"),
])
def test_scout_card_rejections(mutate, msg):
    c = _card(make_id(*NASTY[0]))
    mutate(c)
    with pytest.raises(BadCard, match=msg):
        check_scout_card(c)
