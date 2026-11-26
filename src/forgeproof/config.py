"""Every route out of ForgeProof, in one place (project standard v0.3, build step 1).

Each key resolves as: environment variable > ``config.toml`` ``[paths]`` > in-project
default. A route into ANOTHER project has no default: when it is unset, the caller
gets :class:`NotConfigured`, whose message names the key and where to set it.

Where ``config.toml`` is looked for, first hit wins:

1. ``$FORGEPROOF_CONFIG`` (a file path);
2. ``$FORGEPROOF_HOME/config.toml``;
3. ``<checkout>/config.toml`` when this file sits in a source checkout
   (``pyproject.toml`` and ``src/forgeproof`` next to each other).

``config.toml`` is per host and is not tracked; ``config.example.toml`` lists every key.
A relative value is taken from the directory that holds ``config.toml``.

Keys (classed as the standard's §2 asks):

=========================  ==============  =========================================
key                        class           default
=========================  ==============  =========================================
FORGEPROOF_HOME            in-project      the source checkout, else none
FORGEPROOF_LIBRARY_DIR     other project   NONE: unset fails, naming the key
FORGEPROOF_NODE/BUN/QJS    host binary     ``shutil.which`` (PATH), else BLIND
FORGEPROOF_SCRATCH         in-project      ``<home>/tmp`` (created on demand), else the system temp dir
FORGEPROOF_PYTHON          host binary     ``sys.executable`` (the interpreter running ForgeProof)
FORGEPROOF_CORPUS_EXPORT_DIR  other project  NONE: unset fails, naming the key
FORGEPROOF_BRAIN_DATA      other project   NONE: the Brain's data folder (``learn`` appends to scout_registry*.jsonl there)
FORGEPROOF_SCOUT_DIR       runtime state   NONE: scout's state folder (report cards, learn lock)
FORGEPROOF_REGISTRY_BUILD_LOCK other project NONE: the lock file the registry build holds
FORGEPROOF_LEDGER_DIR      runtime state   NONE: the exposure + outcome ledgers (step 8a); unset = ledgers OFF
FORGEPROOF_SCOUT_LIST_DIR  in-project      ``<checkout>/scout`` (signed list + allowed_signers), else unset fails, naming the key
=========================  ==============  =========================================

The old names (rename slice B, 0.2.1). Until 0.2.0 every key was ``FORGE_GATE_<X>``. Each key is
now read as ``FORGEPROOF_<X>`` first and the old ``FORGE_GATE_<X>`` second, in the environment
and in ``config.toml`` alike, through one helper (:func:`setting` for the environment,
:func:`configured` for both), so a host that still uses the old names keeps working unchanged.
When only the old name is set, it still works, and one line on stderr (once per process tree)
says it stops working in 0.3.0. When both names are set and differ, the new one wins and ONE notice goes to stderr, the first
time that key is read; the process environment is then made to agree (the old name takes the
new value), so a child process does not print the notice again.
Not settings and NOT renamed: the ``FORGE_GATE_RESULT`` fixture protocol line.
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

CODE: Path = Path(__file__).resolve().parents[2]  # src/forgeproof/config.py -> checkout root

LIBRARY_KEY = "FORGEPROOF_LIBRARY_DIR"
CORPUS_KEY = "FORGEPROOF_CORPUS_EXPORT_DIR"
# `forgeproof learn` (design §4.5, build step 6b-1). None of the three has a default: each is a
# route into another project's live state (or scout's own), and a guessed path there writes
# the wrong registry or locks a file nobody else locks.
BRAIN_DATA_KEY = "FORGEPROOF_BRAIN_DATA"
SCOUT_DIR_KEY = "FORGEPROOF_SCOUT_DIR"
BUILD_LOCK_KEY = "FORGEPROOF_REGISTRY_BUILD_LOCK"
LEARN_KEYS = (BRAIN_DATA_KEY, SCOUT_DIR_KEY, BUILD_LOCK_KEY)
# The ledgers (design §4a.5, build step 8a, DECISIONS-8a G1). No default: unset means the
# ledgers are switched OFF and nothing is recorded; setting it is the switch-on.
LEDGER_KEY = "FORGEPROOF_LEDGER_DIR"
# Build step 8b (DECISIONS-8b H1, H4): the local embedder for the meaning and intent lenses, and its
# vector cache. Both unset = those lenses are BLIND(not-configured).
EMBED_URL_KEY = "FORGEPROOF_EMBED_URL"
EMBED_CACHE_KEY = "FORGEPROOF_EMBED_CACHE_DIR"
# KD-13 (f), GitHub-lane loop S1: the folder holding the signed list ``trusted_repos.jsonl`` and
# ``allowed_signers``. Default: ``scout/`` of the source checkout this file sits in. An installed
# ForgeProof has no checkout, so it must be told; it never guesses (same shape as KD-2).
SCOUT_LIST_DIR_KEY = "FORGEPROOF_SCOUT_LIST_DIR"
PREFIX = "FORGEPROOF_"
OLD_PREFIX = "FORGE_GATE_"         # read when the new name is unset (rename slice B, 0.2.1)
_NOTICED: set = set()              # (source, key) pairs already announced on stderr
# The settings ForgeProof reads. Only these get the old-name notice: other programs use names that
# merely start with FORGE_GATE_ (the deploy host's measurement harness passes FORGE_GATE_PATH).
SETTINGS = frozenset({"HOME", "CONFIG", "LIBRARY_DIR", "CORPUS_EXPORT_DIR", "BRAIN_DATA", "SCOUT_DIR",
                      "REGISTRY_BUILD_LOCK", "LEDGER_DIR", "EMBED_URL", "EMBED_CACHE_DIR",
                      "SCOUT_LIST_DIR", "SCRATCH", "PYTHON", "NODE", "BUN", "QJS", "BOX_USER",
                      "BOX_ROOT", "RATIFY_PIN"})
# Set in os.environ after the old-name notice, so the notice is printed once per process tree
# (children inherit it). Not a setting: no FORGEPROOF_ prefix.
OLD_NAME_MARK = "_FORGEPROOF_OLD_NAME_NOTICE_SHOWN"
REMOVAL = "0.3.0"
LOOPBACK = ("127.0.0.1", "::1")          # literal addresses only: a name goes through /etc/hosts (s1 review F3)


class NotConfigured(RuntimeError):
    """A route out of the project is needed and nobody set it."""


def _is_checkout(p: Path) -> bool:
    return (p / "pyproject.toml").is_file() and (p / "src" / "forgeproof").is_dir()


def names(key: str) -> tuple:
    """``(new, old)`` names of one setting, given under either name."""
    for pre in (PREFIX, OLD_PREFIX):
        if key.startswith(pre) and len(key) > len(pre):
            rest = key[len(pre):]
            return PREFIX + rest, OLD_PREFIX + rest
    raise ValueError(f"not a ForgeProof setting: {key!r} (expected {PREFIX}<NAME>)")


def _pick(key: str, values, source: str) -> Optional[str]:
    """The value of ``key`` in ``values``: the new name, else the old one, else None."""
    new, old = names(key)
    a = str(values.get(new, "") or "")
    b = str(values.get(old, "") or "")
    if a and b and a != b:
        if (source, new) not in _NOTICED:
            _NOTICED.add((source, new))
            print(f"forgeproof: {new} and the old name {old} are both set {source} and differ; "
                  f"using {new}", file=sys.stderr)
        if values is os.environ:
            os.environ[old] = a     # the new name wins in child processes too: one notice, not one per child
    if b and not a and new[len(PREFIX):] in SETTINGS:
        _old_name_notice(old, new)
    return a or b or None


def _old_name_notice(old: str, new: str) -> None:
    """One stderr line per process tree when an old name is what a setting came from (0.2.1+)."""
    if os.environ.get(OLD_NAME_MARK):
        return
    os.environ[OLD_NAME_MARK] = "1"
    print(f"forgeproof: {old} is an old name; use {new}. The old FORGE_GATE_* names stop working "
          f"in {REMOVAL}.", file=sys.stderr)


def setting(key: str) -> Optional[str]:
    """A setting from the environment: ``FORGEPROOF_<X>``, else the old ``FORGE_GATE_<X>``, else None.

    ``key`` may be given under either name. Every environment read of a setting goes through here.
    """
    return _pick(key, os.environ, "in the environment")


def home() -> Optional[Path]:
    """The project folder: $FORGEPROOF_HOME, else the checkout this file sits in, else None.

    A non-editable install puts this file in site-packages, where parents[2] is
    lib/pythonX.Y; that is NOT a project home, so it is refused rather than guessed.
    """
    raw = setting("FORGEPROOF_HOME")
    if raw:
        return Path(raw).expanduser()
    return CODE if _is_checkout(CODE) else None


def config_file() -> Optional[Path]:
    raw = setting("FORGEPROOF_CONFIG")
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

    Order: environment (new name, then old), then config.toml (new name, then old). ``name`` may
    be given under either name. Read on every call (not cached at import), so a test or a caller
    that sets the environment after import is seen.
    """
    new, old = names(name)
    raw = setting(name)
    if raw:
        if not os.environ.get(new):                  # the value came from the OLD name
            filed = str(_load().get(new, "") or "")
            if filed and filed != raw and ("env-over-file", new) not in _NOTICED:
                _NOTICED.add(("env-over-file", new))
                print(f"forgeproof: {new} in {config_file()} is ignored: the old name {old} is set in "
                      f"the environment, and the environment comes first", file=sys.stderr)
        return raw
    return _pick(name, _load(), f"in {config_file()}")


def supplied_by(key: str) -> str:
    """The name that supplied ``key``'s value (environment first, then config.toml), for messages:
    an operator who set the old name must be able to find it. The new name when nothing is set."""
    new, old = names(key)
    if os.environ.get(new):
        return new
    if os.environ.get(old):
        return old
    try:
        filed = _load()
    except NotConfigured:
        return new
    return old if not filed.get(new) and filed.get(old) else new


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
            "Only the --live tier and the library tools need it; `forgeproof verify` does not."
        )
    p = _as_path(raw)
    if not p.is_dir():
        raise NotConfigured(f"{supplied_by(LIBRARY_KEY)}={p} is not a directory ({where()})")
    return p


def corpus_export_dir() -> Path:
    """The aura-code-corpus export root (holds ``<run_id>/`` folders, schema
    code-corpus-export/v1). No default: it is another project's output (design §12.4)."""
    raw = configured(CORPUS_KEY)
    if raw is None:
        raise NotConfigured(
            f"{CORPUS_KEY} is not set. Set it in the environment or in config.toml [paths] "
            f"to the corpus export root, the folder holding <run_id>/ exports ({where()}). "
            "Only `forgeproof scout` needs it."
        )
    p = _as_path(raw)
    if not p.is_dir():
        raise NotConfigured(f"{supplied_by(CORPUS_KEY)}={p} is not a directory ({where()})")
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
            f"to the Brain's data folder ({where()}). Only `forgeproof learn` needs it.")
    p = _as_path(raw)
    if not p.is_dir():
        raise NotConfigured(f"{supplied_by(BRAIN_DATA_KEY)}={p} is not an existing directory ({where()})")
    if not (p / "logic_registry.jsonl").is_file():
        raise NotConfigured(f"{supplied_by(BRAIN_DATA_KEY)}={p} holds no logic_registry.jsonl: not the Brain's "
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
            f"to scout's state folder ({where()}). Only `forgeproof learn` needs it.")
    p = _as_path(raw)
    if not p.is_dir():
        raise NotConfigured(f"{supplied_by(SCOUT_DIR_KEY)}={p} is not an existing directory ({where()})")
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
        raise NotConfigured(f"{supplied_by(LEDGER_KEY)}={p} is not an existing directory ({where()})")
    return p


def scout_list_dir() -> Path:
    """The folder holding ``trusted_repos.jsonl`` and ``allowed_signers`` (KD-13 f).

    ``$FORGEPROOF_SCOUT_LIST_DIR`` / config.toml first; else ``<checkout>/scout`` when this file
    sits in a source checkout; else NotConfigured naming the key. It must be an existing folder:
    a typo would otherwise read as "no repo is ratified", which looks like a quiet BLIND."""
    raw = configured(SCOUT_LIST_DIR_KEY)
    if raw is not None:
        p = _as_path(raw)
    elif _is_checkout(CODE):
        p = CODE / "scout"
    else:
        raise NotConfigured(
            f"{SCOUT_LIST_DIR_KEY} is not set and ForgeProof is not running from a source checkout. "
            f"Set it in the environment or in config.toml [paths] to the folder holding "
            f"trusted_repos.jsonl and allowed_signers ({where()}).")
    if not p.is_dir():
        raise NotConfigured(f"{supplied_by(SCOUT_LIST_DIR_KEY)}={p} is not an existing directory ({where()})")
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
        raise NotConfigured(f"{supplied_by(EMBED_URL_KEY)}={raw!r} is not a URL: {e}") from None
    if (u.scheme != "http" or u.hostname not in LOOPBACK or port is None or u.username or u.password
            or u.path not in ("", "/") or u.query or u.fragment):
        raise NotConfigured(f"{supplied_by(EMBED_URL_KEY)}={raw!r} must be http://<loopback>:<port> "
                            f"(one of {', '.join(LOOPBACK)}), nothing more")
    return raw.rstrip("/")


def embed_cache_dir() -> Path:
    """The embedder's vector cache folder (runtime state, not in git). No default; it must exist."""
    raw = configured(EMBED_CACHE_KEY)
    if raw is None:
        raise NotConfigured(f"{EMBED_CACHE_KEY} is not set: no vector cache ({where()})")
    p = _as_path(raw)
    if not p.is_dir():
        raise NotConfigured(f"{supplied_by(EMBED_CACHE_KEY)}={p} is not an existing directory ({where()})")
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
            f"to the lock file the registry build holds ({where()}). Only `forgeproof learn` needs it.")
    p = _as_path(raw)
    if not p.is_file():
        raise NotConfigured(
            f"{supplied_by(BUILD_LOCK_KEY)}={p} does not exist. learn never creates it: it must be the file "
            f"the registry build locks ({where()})")
    return p


def scratch_dir() -> Path:
    """Where lenses write short-lived child scripts. Created on demand.

    Before build step 1 the determinism lens wrote into ``<checkout>/tmp`` and relied on
    that folder already existing: true on the deploy host, false in a fresh clone or an
    install, where the lens went ERROR (measured 2026-11-11).
    """
    import tempfile

    raw = configured("FORGEPROOF_SCRATCH")
    if raw:
        d = _as_path(raw)
    else:
        h = home()
        d = h / "tmp" if h is not None else Path(tempfile.gettempdir()) / "forgeproof"
    d.mkdir(parents=True, exist_ok=True)
    return d


def python() -> str:
    """The interpreter for child runs (oracles, fixtures, mutants).

    Default: the interpreter running ForgeProof, which satisfies ``requires-python``.
    Not ``python3`` from PATH: on the dev machine that was Xcode's 3.9.6, which rejects
    the reference files' zero-width characters, and 7 of 10 patterns read as UNSTABLE
    (measured 2026-11-11, build step 1).
    """
    raw = configured("FORGEPROOF_PYTHON")
    return str(_as_path(raw)) if raw else sys.executable


def engine(name: str) -> Optional[str]:
    """A JavaScript engine binary: FORGEPROOF_<NAME> > PATH > None (the caller goes BLIND)."""
    raw = configured("FORGEPROOF_" + name.upper())
    if raw:
        return str(_as_path(raw))
    return shutil.which(name)


# F6 (review, slice B): make the two names agree in this process's environment at import, before a
# caller copies os.environ for a child; otherwise each child would print the notice again.
for _k in [k for k in list(os.environ) if k.startswith(OLD_PREFIX) and k[len(OLD_PREFIX):] in SETTINGS]:
    setting(_k)
