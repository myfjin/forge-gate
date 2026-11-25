"""Crew-corpus intake (design v1.4.5 §12.4; build step 5).

Reads the newest ``<run_id>/`` under the export root whose manifest says
``schema = code-corpus-export/v1`` and ``run_status = OK``, re-checks every file's sha256,
byte count and row count against the manifest, and returns the items. Any failure makes
the whole source BLIND(reason) for the run: never clean, never partly read (P1).

Reads only. Scout never writes into the corpus. Item bodies are text: nothing here
compiles, imports or runs them.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .. import config
from .outcomes import BLIND_FLAG_SHARE, Outcome, blind, clean, found

SCHEMA = "code-corpus-export/v1"
_RUN_ID = re.compile(r"^[0-9]{8}T[0-9]{6}Z$")
POOL = "corpus"
TRUST = "raw"


@dataclass
class Intake:
    outcome: Outcome                 # FOUND = items read; BLIND(reason) otherwise
    run_id: str = ""
    items: list = field(default_factory=list)
    skipped: list = field(default_factory=list)   # (run_id, why) for newer runs not used
    unreadable: int = 0             # items without a usable body
    flagged: bool = False           # unreadable share > 10%


def _verify(run: Path, manifest: dict):
    """Return None when every file matches the manifest, else the first mismatch."""
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        return "manifest has no files table"
    for name, meta in sorted(files.items()):
        p = run / name
        if not p.is_file():
            return f"missing {name}"
        b = p.read_bytes()
        got = hashlib.sha256(b).hexdigest()
        if got != meta.get("sha256"):
            return f"sha256 mismatch {name}"
        if len(b) != meta.get("bytes"):
            return f"bytes mismatch {name}"
        if name.endswith(".jsonl") and "rows" not in meta:  # 6E review S1-1: never a weaker check
            return f"schema: no rows for {name}"
        if "rows" in meta and b.count(b"\n") != meta["rows"]:
            return f"rows mismatch {name}"
    return None


def read_export(root: Path | None = None) -> Intake:
    if root is None:
        try:
            root = config.corpus_export_dir()
        except config.NotConfigured as e:
            return Intake(blind(f"not-configured: {config.CORPUS_KEY}", str(e)))
    root = Path(root)
    runs = sorted((p for p in root.iterdir() if p.is_dir() and _RUN_ID.match(p.name)),
                  key=lambda p: p.name, reverse=True)
    if not runs:
        return Intake(blind("no-export", str(root)))
    skipped = []
    for run in runs:
        try:
            manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            skipped.append((run.name, f"manifest unreadable: {type(e).__name__}"))
            continue
        if manifest.get("schema") != SCHEMA:
            skipped.append((run.name, f"schema {manifest.get('schema')!r}"))
            continue
        if manifest.get("run_status") != "OK" or manifest.get("run_id") != run.name:
            skipped.append((run.name, f"run_status {manifest.get('run_status')!r}"))
            continue
        # The newest OK run is THE source. A bad sha here is BLIND, never a fallback to an
        # older run: falling back would hide the damage behind a clean-looking read.
        bad = _verify(run, manifest)
        if bad:
            return Intake(blind(f"sha-recheck: {bad}", run.name), run.name, skipped=skipped)
        items, unreadable = [], 0
        for line in (run / "items.jsonl").read_text(encoding="utf-8").splitlines():
            try:
                d = json.loads(line)
            except ValueError:
                unreadable += 1
                continue
            if not isinstance(d, dict) or not isinstance(d.get("body"), str) or not d.get("item_id"):
                unreadable += 1
                continue
            items.append(d)
        total = len(items) + unreadable
        flagged = total == 0 or unreadable / total > BLIND_FLAG_SHARE
        return Intake(found(f"{len(items)} items", run.name), run.name, items, skipped,
                      unreadable, flagged)
    return Intake(blind("no-OK-export", *(f"{r}: {w}" for r, w in skipped)), skipped=skipped)
