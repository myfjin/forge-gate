"""Every route out of Forge Gate, in one place (project standard v0.3, build step 1).

Each key resolves as: environment variable > ``config.toml`` ``[paths]`` > in-project
default. A route into ANOTHER project has no default: when it is unset, the caller
gets :class:`NotConfigured`, whose message names the key and where to set it.

Where ``config.toml`` is looked for, first hit wins:

1. ``$FORGE_GATE_CONFIG`` (a file path);
2. ``$FORGE_GATE_HOME/config.toml``;
3. ``<checkout>/config.toml`` when this file sits in a source checkout
   (``pyproject.toml`` and ``src/forge_gate`` next to each other).

``config.toml`` is per host and is not tracked; ``config.example.toml`` lists every key.
A relative value is taken from the directory that holds ``config.toml``.

Keys (classed as the standard's §2 asks):

=========================  ==============  =========================================
key                        class           default
=========================  ==============  =========================================
FORGE_GATE_HOME            in-project      the source checkout, else none
FORGE_GATE_LIBRARY_DIR     other project   NONE: unset fails, naming the key
FORGE_GATE_NODE/BUN/QJS    host binary     ``shutil.which`` (PATH), else BLIND
FORGE_GATE_SCRATCH         in-project      ``<home>/tmp`` (created on demand), else the system temp dir
FORGE_GATE_PYTHON          host binary     ``sys.executable`` (the interpreter running Forge Gate)
=========================  ==============  =========================================
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Optional

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11: tomli is the same parser
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ModuleNotFoundError:
        tomllib = None  # type: ignore[assignment]

CODE: Path = Path(__file__).resolve().parents[2]  # src/forge_gate/config.py -> checkout root

LIBRARY_KEY = "FORGE_GATE_LIBRARY_DIR"
CORPUS_KEY = "FORGE_GATE_CORPUS_EXPORT_DIR"


class NotConfigured(RuntimeError):
    """A route out of the project is needed and nobody set it."""


def _is_checkout(p: Path) -> bool:
    return (p / "pyproject.toml").is_file() and (p / "src" / "forge_gate").is_dir()


def home() -> Optional[Path]:
    """The project folder: $FORGE_GATE_HOME, else the checkout this file sits in, else None.

    A non-editable install puts this file in site-packages, where parents[2] is
    lib/pythonX.Y; that is NOT a project home, so it is refused rather than guessed.
    """
    raw = os.environ.get("FORGE_GATE_HOME", "")
    if raw:
        return Path(raw).expanduser()
    return CODE if _is_checkout(CODE) else None


def config_file() -> Optional[Path]:
    raw = os.environ.get("FORGE_GATE_CONFIG", "")
    if raw:
        return Path(raw).expanduser()
    h = home()
    return h / "config.toml" if h is not None else None


def _load() -> dict:
    cf = config_file()
    if cf is None or not cf.is_file():
        return {}
    if tomllib is None:
        raise NotConfigured(
            f"config: {cf} exists but this Python has no TOML parser "
            "(needs 3.11+, or: pip install 'tomli>=2')"
        )
    with cf.open("rb") as fh:
        return tomllib.load(fh).get("paths", {})


def configured(name: str) -> Optional[str]:
    """The raw value of ``name`` from the environment or config.toml, else None.

    Read on every call (not cached at import), so a test or a caller that sets the
    environment after import is seen.
    """
    raw = os.environ.get(name, "")
    if raw:
        return raw
    raw = str(_load().get(name, "") or "")
    return raw or None


def _as_path(raw: str) -> Path:
    p = Path(raw).expanduser()
    if p.is_absolute():
        return p
    cf = config_file()
    base = cf.parent if cf is not None else Path.cwd()
    return base / p


def where() -> str:
    """One line for error messages and the route check: host, config file, CODE."""
    import socket

    return f"host={socket.gethostname()} config={config_file()} CODE={CODE}"


def library_dir() -> Path:
    """The AURA pattern library checkout. No default: it is another project."""
    raw = configured(LIBRARY_KEY)
    if raw is None:
        raise NotConfigured(
            f"{LIBRARY_KEY} is not set. Set it in the environment or in config.toml "
            f"[paths] to a checkout of aura-pattern-library ({where()}). "
            "Only the --live tier and the library tools need it; `forge-gate verify` does not."
        )
    p = _as_path(raw)
    if not p.is_dir():
        raise NotConfigured(f"{LIBRARY_KEY}={p} is not a directory ({where()})")
    return p


def corpus_export_dir() -> Path:
    """The aura-code-corpus export root (holds ``<run_id>/`` folders, schema
    code-corpus-export/v1). No default: it is another project's output (design §12.4)."""
    raw = configured(CORPUS_KEY)
    if raw is None:
        raise NotConfigured(
            f"{CORPUS_KEY} is not set. Set it in the environment or in config.toml [paths] "
            f"to the corpus export root, the folder holding <run_id>/ exports ({where()}). "
            "Only `forge-gate scout` needs it."
        )
    p = _as_path(raw)
    if not p.is_dir():
        raise NotConfigured(f"{CORPUS_KEY}={p} is not a directory ({where()})")
    return p


def scratch_dir() -> Path:
    """Where lenses write short-lived child scripts. Created on demand.

    Before build step 1 the determinism lens wrote into ``<checkout>/tmp`` and relied on
    that folder already existing: true on the deploy host, false in a fresh clone or an
    install, where the lens went ERROR (measured 2026-11-11).
    """
    import tempfile

    raw = configured("FORGE_GATE_SCRATCH")
    if raw:
        d = _as_path(raw)
    else:
        h = home()
        d = h / "tmp" if h is not None else Path(tempfile.gettempdir()) / "forge-gate"
    d.mkdir(parents=True, exist_ok=True)
    return d


def python() -> str:
    """The interpreter for child runs (oracles, fixtures, mutants).

    Default: the interpreter running Forge Gate, which satisfies ``requires-python``.
    Not ``python3`` from PATH: on the dev machine that was Xcode's 3.9.6, which rejects
    the reference files' zero-width characters, and 7 of 10 patterns read as UNSTABLE
    (measured 2026-11-11, build step 1).
    """
    raw = configured("FORGE_GATE_PYTHON")
    return str(_as_path(raw)) if raw else sys.executable


def engine(name: str) -> Optional[str]:
    """A JavaScript engine binary: FORGE_GATE_<NAME> > PATH > None (the caller goes BLIND)."""
    raw = configured("FORGE_GATE_" + name.upper())
    if raw:
        return str(_as_path(raw))
    return shutil.which(name)
