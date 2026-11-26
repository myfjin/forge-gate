"""Test-session routes (project standard §6: the default tier runs on fixtures only).

1. The suite runs from a source checkout. The lens tools ship in the package (KD-2, done
   2026-11-25) and never read FORGEPROOF_HOME; it is still set to this checkout, unless the
   caller chose one, for config.toml and the checkout tools in tools/ (github-facts' runner).
   The KD-2 route tests in test_routes.py unset it themselves.
2. Outside `pytest -m live`, the library route is switched off: an exported
   FORGEPROOF_LIBRARY_DIR is removed, and the host's config.toml is replaced by a copy
   without that key. `learn`'s three routes (step 6b-1) are removed the same way in EVERY
   tier, live included: on the deploy host they name the Brain's live registry. On the deploy host the key switches the twin lens onto the live
   library and changes verdicts (seen 2026-11-11, KNOWN-DEBT KD-1). Every other key
   (JS engines, interpreter, scratch) still applies: the JS tier needs them.
"""
import os
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
# Rename slice B: an exported old name (FORGE_GATE_<X>) is moved to its new name (FORGEPROOF_<X>)
# unless the new one is set, so each test sets and unsets ONE name. The fallback itself is tested
# in test_env_names.py. FORGE_GATE_RESULT is a protocol line, not a setting: left alone.
for _k in [k for k in os.environ if k.startswith("FORGE_GATE_") and k != "FORGE_GATE_RESULT"]:
    _v = os.environ.pop(_k)
    os.environ.setdefault("FORGEPROOF_" + _k[len("FORGE_GATE_"):], _v)
os.environ.setdefault("FORGEPROOF_HOME", str(REPO))

LIBRARY_KEY = "FORGEPROOF_LIBRARY_DIR"
# 8b s2: the local embedder and its cache are live-tier only (a real Ollama answers on the deploy host).
EMBED_URL_KEY = "FORGEPROOF_EMBED_URL"
EMBED_CACHE_KEY = "FORGEPROOF_EMBED_CACHE_DIR"
# `learn`'s routes (step 6b-1) point at the Brain's live registry and the build's lock on the
# deploy host. The suite never inherits them: each learn test sets its own, inside tmp_path.
LEARN_KEYS = ("FORGEPROOF_BRAIN_DATA", "FORGEPROOF_SCOUT_DIR", "FORGEPROOF_REGISTRY_BUILD_LOCK",
              # step 8a: the ledgers. A test never writes a host's real ledger; each sets its own.
              "FORGEPROOF_LEDGER_DIR")


def _live_selected(markexpr: str) -> bool:
    m = " ".join(markexpr.split())
    return "live" in m and "not live" not in m


def _config_without(drop) -> None:
    from forgeproof import config

    cf = config.config_file()
    raw = config._load() if cf is not None and cf.is_file() else {}
    keep = {}
    for k, v in raw.items():          # old and new names alike; the copy carries the new name only
        try:
            new = config.names(k)[0]
        except ValueError:
            continue
        if new in drop or not isinstance(v, str) or (new in keep and k != new):
            continue                  # the new name wins over the old one, as in config.configured
        keep[new] = str(config._as_path(v)) if not str(v).startswith("/") else str(v)
    tmpdir = REPO / "tmp"   # gitignored; never the system temp dir (/tmp on the deploy host)
    tmpdir.mkdir(exist_ok=True)
    fd, path = tempfile.mkstemp(prefix="forgeproof-test-config-", suffix=".toml", dir=tmpdir)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("[paths]\n")
        for k, v in sorted(keep.items()):
            fh.write(f'{k} = "{v}"\n')
    os.environ["FORGEPROOF_CONFIG"] = path
    _TEMP.append(path)


_TEMP: list = []


def pytest_unconfigure(config):
    for p in _TEMP:
        try:
            os.unlink(p)
        except OSError:
            pass


def pytest_configure(config):
    live = _live_selected(config.option.markexpr or "")
    for k in LEARN_KEYS:  # always, live tier included
        os.environ.pop(k, None)
    off = (LIBRARY_KEY, EMBED_URL_KEY, EMBED_CACHE_KEY)   # 8b s2: the real embedder only in -m live
    if not live:
        for k in off:
            os.environ.pop(k, None)
    _config_without(LEARN_KEYS + (() if live else off))


def pytest_report_header(config):
    """Prove which code this run measures (standard §5: print __file__ in every arm)."""
    import forgeproof

    return [f"forgeproof imported from: {forgeproof.__file__}",
            f"config: {os.environ.get('FORGEPROOF_CONFIG')}"]
