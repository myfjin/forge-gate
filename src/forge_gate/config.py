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
FORGE_GATE_CORPUS_EXPORT_DIR  other project  NONE: unset fails, naming the key
FORGE_GATE_BRAIN_DATA      other project   NONE: the Brain's data folder (``learn`` appends to scout_registry*.jsonl there)
FORGE_GATE_SCOUT_DIR       runtime state   NONE: scout's state folder (report cards, learn lock)
FORGE_GATE_REGISTRY_BUILD_LOCK other project NONE: the lock file the registry build holds
FORGE_GATE_LEDGER_DIR      runtime state   NONE: the exposure + outcome ledgers (step 8a); unset = ledgers OFF
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
# `forge-gate learn` (design §4.5, build step 6b-1). None of the three has a default: each is a
# route into another project's live state (or scout's own), and a guessed path there writes
# the wrong registry or locks a file nobody else locks.
BRAIN_DATA_KEY = "FORGE_GATE_BRAIN_DATA"
SCOUT_DIR_KEY = "FORGE_GATE_SCOUT_DIR"
BUILD_LOCK_KEY = "FORGE_GATE_REGISTRY_BUILD_LOCK"
LEARN_KEYS = (BRAIN_DATA_KEY, SCOUT_DIR_KEY, BUILD_LOCK_KEY)
# The ledgers (design §4a.5, build step 8a, DECISIONS-8a G1). No default: unset means the
# ledgers are switched OFF and nothing is recorded; setting it is the switch-on.
LEDGER_KEY = "FORGE_GATE_LEDGER_DIR"
# Build step 8b (DECISIONS-8b H1, H4): the local embedder for the meaning and intent lenses, and its
# vector cache. Both unset = those lenses are BLIND(not-configured).
EMBED_URL_KEY = "FORGE_GATE_EMBED_URL"
EMBED_CACHE_KEY = "FORGE_GATE_EMBED_CACHE_DIR"
LOOPBACK = ("127.0.0.1", "::1")          # literal addresses only: a name goes through /etc/hosts (s1 review F3)


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


def brain_data() -> Path:
    """The Brain's data folder: its ``paths.DATA`` (deploy host:
    ``/home/me/Projects/aura-brain/data``). ``learn`` appends to scout's own ladder files there
    (``scout_registry*.jsonl``, 6E's decision A, 2026-11-13) and reads the real ladders for its
    duplicate check. No default. It must be an existing folder holding ``logic_registry.jsonl``:
    a typo, or the Brain's checkout root instead of its data folder, would otherwise pass."""
    raw = configured(BRAIN_DATA_KEY)
    if raw is None:
        raise NotConfigured(
            f"{BRAIN_DATA_KEY} is not set. Set it in the environment or in config.toml [paths] "
            f"to the Brain's data folder ({where()}). Only `forge-gate learn` needs it.")
    p = _as_path(raw)
    if not p.is_dir():
        raise NotConfigured(f"{BRAIN_DATA_KEY}={p} is not an existing directory ({where()})")
    if not (p / "logic_registry.jsonl").is_file():
        raise NotConfigured(f"{BRAIN_DATA_KEY}={p} holds no logic_registry.jsonl: not the Brain's "
                            f"data folder ({where()})")
    return p


def scout_dir() -> Path:
    """Scout's runtime state folder (design §4.5): report cards, ``scout_learn.lock``. Never in
    git. No default, and it must already exist (a typo would otherwise create a folder the
    registry build never reads)."""
    raw = configured(SCOUT_DIR_KEY)
    if raw is None:
        raise NotConfigured(
            f"{SCOUT_DIR_KEY} is not set. Set it in the environment or in config.toml [paths] "
            f"to scout's state folder ({where()}). Only `forge-gate learn` needs it.")
    p = _as_path(raw)
    if not p.is_dir():
        raise NotConfigured(f"{SCOUT_DIR_KEY}={p} is not an existing directory ({where()})")
    return p


def ledger_dir() -> Path:
    """The ledgers' folder (``exposure.jsonl``, ``outcome.jsonl``, ``ledger.lock``; standard §4
    ``data/ledgers/``, not in git). No default, and it must already exist: the switch-on creates
    it and its files with their owner and ACL, never a writer."""
    raw = configured(LEDGER_KEY)
    if raw is None:
        raise NotConfigured(
            f"{LEDGER_KEY} is not set: the ledgers are off. Set it in the environment or in "
            f"config.toml [paths] to the ledger folder ({where()}).")
    p = _as_path(raw)
    if not p.is_dir():
        raise NotConfigured(f"{LEDGER_KEY}={p} is not an existing directory ({where()})")
    return p


def embed_url() -> str:
    """The local embedder's base URL: plain http on a loopback address with a port, nothing else
    (DECISIONS-8b H1: private corpus text never leaves the host). No default."""
    raw = configured(EMBED_URL_KEY)
    if raw is None:
        raise NotConfigured(f"{EMBED_URL_KEY} is not set: the meaning and intent lenses are off "
                            f"({where()})")
    return loopback_url(raw)


def loopback_url(raw: str) -> str:
    """``raw`` if it is http://<literal loopback address>:<port> and nothing more, else NotConfigured."""
    from urllib.parse import urlsplit
    try:
        u = urlsplit(raw)
        port = u.port
    except ValueError as e:
        raise NotConfigured(f"{EMBED_URL_KEY}={raw!r} is not a URL: {e}") from None
    if (u.scheme != "http" or u.hostname not in LOOPBACK or port is None or u.username or u.password
            or u.path not in ("", "/") or u.query or u.fragment):
        raise NotConfigured(f"{EMBED_URL_KEY}={raw!r} must be http://<loopback>:<port> "
                            f"(one of {', '.join(LOOPBACK)}), nothing more")
    return raw.rstrip("/")


def embed_cache_dir() -> Path:
    """The embedder's vector cache folder (runtime state, not in git). No default; it must exist."""
    raw = configured(EMBED_CACHE_KEY)
    if raw is None:
        raise NotConfigured(f"{EMBED_CACHE_KEY} is not set: no vector cache ({where()})")
    p = _as_path(raw)
    if not p.is_dir():
        raise NotConfigured(f"{EMBED_CACHE_KEY}={p} is not an existing directory ({where()})")
    return p


def registry_build_lock() -> Path:
    """The lock file the registry build holds while it rewrites the registry (the maintainer L1,
    2026-11-13). On the deploy host: the file bu's hourly ``flock -n`` names. No default, and it
    must already exist: a lock file learn created itself would be one the build never takes,
    so holding it would protect nothing."""
    raw = configured(BUILD_LOCK_KEY)
    if raw is None:
        raise NotConfigured(
            f"{BUILD_LOCK_KEY} is not set. Set it in the environment or in config.toml [paths] "
            f"to the lock file the registry build holds ({where()}). Only `forge-gate learn` needs it.")
    p = _as_path(raw)
    if not p.is_file():
        raise NotConfigured(
            f"{BUILD_LOCK_KEY}={p} does not exist. learn never creates it: it must be the file "
            f"the registry build locks ({where()})")
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
