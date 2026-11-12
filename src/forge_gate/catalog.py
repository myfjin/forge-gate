"""Pattern catalog resolver.

Maps a pattern name to its reference implementation and the matching gate fixture.
`gate_catalog.json` is the curated, gate-ready subset that ships with Forge Gate. The
reference implementations ship too, in `forge_gate/reference/` (byte copies of library
patterns; see `reference/ORIGIN.md`), so `forge-gate verify` reads no other project.
The live library is read only when a caller passes `library_root` or uses
`load_aura_catalog`, and then through `config.library_dir()` (FORGE_GATE_LIBRARY_DIR).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, Optional, Tuple


class CatalogError(Exception):
    """Raised when a pattern cannot be resolved or is not gate-ready."""


def _data_dir() -> Path:
    return Path(__file__).resolve().parent / "data"


def reference_dir() -> Path:
    """The shipped reference implementations (see reference/ORIGIN.md)."""
    return Path(__file__).resolve().parent / "reference"


def reference_path(name: str) -> Path:
    """The shipped reference implementation for a gate-ready pattern."""
    return reference_dir() / f"{name}.py"


def load_gate_catalog() -> Dict:
    """Load the curated gate catalog shipped with this package."""
    catalog_path = _data_dir() / "gate_catalog.json"
    with open(catalog_path, encoding="utf-8") as f:
        return json.load(f)


def load_aura_catalog(library_root: Optional[Path] = None) -> Dict[str, Tuple[str, str]]:
    """Parse CATALOG.md and return {pattern_id: (languages, category)}.

    This is used for validation and to discover the canonical path of a pattern
    when it is not in the curated gate catalog.
    """
    if library_root is None:
        from forge_gate.config import library_dir

        library_root = library_dir()
    catalog_md = library_root / "CATALOG.md"
    if not catalog_md.exists():
        raise CatalogError(f"AURA CATALOG.md not found at {catalog_md}")

    text = catalog_md.read_text(encoding="utf-8")
    mapping: Dict[str, Tuple[str, str]] = {}
    # Table rows look like: | `id` | python·cpp | Category |
    for line in text.splitlines():
        m = re.match(r"^\|\s*`([^`]+)`\s*\|\s*([^|]+)\|\s*([^|]+)\|\s*$", line)
        if m:
            pid = m.group(1).strip()
            langs = m.group(2).strip()
            category = m.group(3).strip()
            mapping[pid] = (langs, category)
    return mapping


def resolve_pattern(name: str, library_root: Optional[Path] = None) -> Dict:
    """Resolve a pattern name to its gate metadata.

    Returns a dict with keys:
      - name
      - aura_path (absolute Path): the shipped reference copy by default; the file in
        the live library when ``library_root`` is given
      - fixture
      - category
      - description
    """
    catalog = load_gate_catalog()

    entry = catalog["patterns"].get(name)
    if not entry:
        raise CatalogError(
            f"Pattern '{name}' is not in the Forge Gate v{catalog['version']} catalog. "
            f"Run `forge-gate list` to see gate-ready patterns."
        )

    if library_root is None:
        aura_path = reference_path(name).resolve()
        where = "the shipped reference copy (forge_gate/reference/)"
    else:
        aura_path = (Path(library_root) / entry["aura_path"]).resolve()
        where = f"the library at {library_root}"
    if not aura_path.exists():
        raise CatalogError(f"Reference implementation missing for '{name}': {aura_path} ({where}).")

    return {
        "name": name,
        "aura_path": aura_path,
        "fixture": entry["fixture"],
        "category": entry["category"],
        "description": entry.get("description", ""),
    }


def list_patterns() -> Dict[str, Dict]:
    """Return all gate-ready patterns."""
    return load_gate_catalog()["patterns"]
