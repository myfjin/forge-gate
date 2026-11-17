"""Test-session routes (project standard §6: the default tier runs on fixtures only).

1. The suite runs from a source checkout, so the lens tools (tools/) are right here.
   An installed (non-editable) Forge Gate cannot find tools/ by itself (KNOWN-DEBT
   KD-2), so point it at this checkout unless the caller already chose one.
2. Outside `pytest -m live`, the library route is switched off: an exported
   FORGE_GATE_LIBRARY_DIR is removed, and the host's config.toml is replaced by a copy
   without that key. `learn`'s three routes (step 6b-1) are removed the same way in EVERY
   tier, live included: on the deploy host they name the Brain's live registry. On the deploy host the key switches the twin lens onto the live
   library and changes verdicts (seen 2026-11-11, KNOWN-DEBT KD-1). Every other key
   (JS engines, interpreter, scratch) still applies: the JS tier needs them.
"""
import os
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
os.environ.setdefault("FORGE_GATE_HOME", str(REPO))

LIBRARY_KEY = "FORGE_GATE_LIBRARY_DIR"
# 8b s2: the local embedder and its cache are live-tier only (a real Ollama answers on the deploy host).
EMBED_URL_KEY = "FORGE_GATE_EMBED_URL"
EMBED_CACHE_KEY = "FORGE_GATE_EMBED_CACHE_DIR"
# `learn`'s routes (step 6b-1) point at the Brain's live registry and the build's lock on the
# deploy host. The suite never inherits them: each learn test sets its own, inside tmp_path.
LEARN_KEYS = ("FORGE_GATE_BRAIN_DATA", "FORGE_GATE_SCOUT_DIR", "FORGE_GATE_REGISTRY_BUILD_LOCK",
              # step 8a: the ledgers. A test never writes a host's real ledger; each sets its own.
              "FORGE_GATE_LEDGER_DIR")


def _live_selected(markexpr: str) -> bool:
    m = " ".join(markexpr.split())
    return "live" in m and "not live" not in m


def _config_without(drop) -> None:
    from forge_gate import config

    cf = config.config_file()
    raw = config._load() if cf is not None and cf.is_file() else {}
    keep = {k: str(config._as_path(v)) if not str(v).startswith("/") else str(v)
            for k, v in raw.items() if k not in drop and isinstance(v, str)}
    tmpdir = REPO / "tmp"   # gitignored; never the system temp dir (/tmp on the deploy host)
    tmpdir.mkdir(exist_ok=True)
    fd, path = tempfile.mkstemp(prefix="forge-gate-test-config-", suffix=".toml", dir=tmpdir)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("[paths]\n")
        for k, v in sorted(keep.items()):
            fh.write(f'{k} = "{v}"\n')
    os.environ["FORGE_GATE_CONFIG"] = path
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
    import forge_gate

    return [f"forge_gate imported from: {forge_gate.__file__}",
            f"config: {os.environ.get('FORGE_GATE_CONFIG')}"]
