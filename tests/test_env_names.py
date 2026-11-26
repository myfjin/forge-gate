"""Rename slice B (0.2.1): every setting is read as FORGEPROOF_<X> first, then the old FORGE_GATE_<X>.

Both set and different: the new name wins and exactly one notice goes to stderr; a child process
then sees the two names agree and stays quiet. config.toml keys follow the same order, after the
environment. The two gate-tool keys (tools/fg_common.py, stdlib-only) have their own copy of it.
"""
from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from forgeproof import config

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _fresh(monkeypatch, tmp_path):
    config._NOTICED.clear()
    monkeypatch.delenv(config.OLD_NAME_MARK, raising=False)
    for k in ("SCRATCH", "PYTHON", "LIBRARY_DIR", "NODE"):
        monkeypatch.delenv("FORGEPROOF_" + k, raising=False)
        monkeypatch.delenv("FORGE_GATE_" + k, raising=False)
    monkeypatch.setenv("FORGEPROOF_CONFIG", str(tmp_path / "absent-config.toml"))
    yield
    config._NOTICED.clear()


def test_names_maps_either_spelling_and_refuses_others():
    assert config.names("FORGEPROOF_LIBRARY_DIR") == ("FORGEPROOF_LIBRARY_DIR", "FORGE_GATE_LIBRARY_DIR")
    assert config.names("FORGE_GATE_LIBRARY_DIR") == ("FORGEPROOF_LIBRARY_DIR", "FORGE_GATE_LIBRARY_DIR")
    for bad in ("LIBRARY_DIR", "FORGEPROOF_", "PATH"):
        with pytest.raises(ValueError):
            config.names(bad)


def test_old_name_alone_still_works(monkeypatch, tmp_path, capsys):
    lib = tmp_path / "lib"; lib.mkdir()
    monkeypatch.setenv("FORGE_GATE_LIBRARY_DIR", str(lib))
    monkeypatch.setenv("FORGE_GATE_PYTHON", "/opt/old/python3")
    assert config.library_dir() == lib
    assert config.python() == "/opt/old/python3"
    assert config.configured("FORGEPROOF_LIBRARY_DIR") == str(lib)
    out, err = capsys.readouterr()
    assert out == ""                               # never on stdout
    lines = [l for l in err.splitlines() if "is an old name" in l]
    assert len(lines) == 1, err                    # once per process, however many old names are read
    assert "FORGE_GATE_" in lines[0] and "FORGEPROOF_" in lines[0] and "0.3.0" in lines[0], lines


def test_old_name_notice_is_not_repeated_by_a_child(monkeypatch, tmp_path):
    monkeypatch.setenv("FORGE_GATE_PYTHON", "/opt/old/python3")
    assert config.python() == "/opt/old/python3"
    code = "from forgeproof import config; print(config.python())"
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60,
                       env=dict(os.environ, PYTHONPATH=str(REPO / "src")))
    assert r.returncode == 0 and r.stdout.strip() == "/opt/old/python3", (r.stdout, r.stderr)
    assert "is an old name" not in r.stderr, r.stderr


def test_a_foreign_FORGE_GATE_variable_gets_no_notice(monkeypatch, capsys):
    monkeypatch.setenv("FORGE_GATE_PATH", "/home/me/.local/bin/forge-gate")
    assert config.setting("FORGE_GATE_PATH") == "/home/me/.local/bin/forge-gate"   # read, but not ours
    assert "is an old name" not in capsys.readouterr().err
    # bu's measurement harness on the deploy host passes FORGE_GATE_PATH (its own variable)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("FORGEPROOF_", "FORGE_GATE_", "_FORGEPROOF"))}
    env.update(PYTHONPATH=str(REPO / "src"), FORGE_GATE_PATH="/home/me/.local/bin/forge-gate",
               FORGEPROOF_CONFIG="/nonexistent/config.toml")
    code = "from forgeproof import config; config.configured('FORGEPROOF_SCRATCH'); print('ok')"
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60, env=env)
    assert r.returncode == 0 and r.stdout.strip() == "ok", (r.stdout, r.stderr)
    assert r.stderr == "", r.stderr


def test_old_home_and_config_names_still_work(monkeypatch, tmp_path):
    cfg = tmp_path / "old.toml"; cfg.write_text('[paths]\nFORGEPROOF_SCRATCH = "s"\n')
    monkeypatch.delenv("FORGEPROOF_CONFIG")
    monkeypatch.setenv("FORGE_GATE_CONFIG", str(cfg))
    assert config.config_file() == cfg
    monkeypatch.delenv("FORGEPROOF_HOME", raising=False)
    monkeypatch.setenv("FORGE_GATE_HOME", str(tmp_path / "h"))
    assert config.home() == tmp_path / "h"


def test_new_name_wins_with_exactly_one_notice(monkeypatch, tmp_path, capsys):
    new, old = tmp_path / "new", tmp_path / "old"
    new.mkdir(); old.mkdir()
    monkeypatch.setenv("FORGEPROOF_LIBRARY_DIR", str(new))
    monkeypatch.setenv("FORGE_GATE_LIBRARY_DIR", str(old))
    assert config.library_dir() == new
    assert config.library_dir() == new
    assert config.configured("FORGE_GATE_LIBRARY_DIR") == str(new)   # asked by the old name: still the new value
    err = capsys.readouterr().err
    assert err.count("FORGEPROOF_LIBRARY_DIR and the old name FORGE_GATE_LIBRARY_DIR") == 1, err
    assert os.environ["FORGE_GATE_LIBRARY_DIR"] == str(new)            # children inherit one value


def test_both_set_and_equal_is_quiet(monkeypatch, capsys):
    monkeypatch.setenv("FORGEPROOF_PYTHON", "/x/py")
    monkeypatch.setenv("FORGE_GATE_PYTHON", "/x/py")
    assert config.python() == "/x/py"
    assert capsys.readouterr().err == ""


def test_a_child_process_does_not_repeat_the_notice(monkeypatch):
    monkeypatch.setenv("FORGEPROOF_NODE", "/new/node")
    monkeypatch.setenv("FORGE_GATE_NODE", "/old/node")
    assert config.engine("node") == "/new/node"
    code = "from forgeproof import config; print(config.engine('node'))"
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60,
                       env=dict(os.environ, PYTHONPATH=str(REPO / "src")))
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "/new/node" and "old name" not in r.stderr, (r.stdout, r.stderr)


def test_config_toml_new_name_wins_old_name_still_read_env_beats_both(monkeypatch, tmp_path, capsys):
    cfg = tmp_path / "config.toml"
    monkeypatch.setenv("FORGEPROOF_CONFIG", str(cfg))
    cfg.write_text('[paths]\nFORGE_GATE_SCRATCH = "/cfg/old"\n')
    assert config.configured("FORGEPROOF_SCRATCH") == "/cfg/old"
    cfg.write_text('[paths]\nFORGE_GATE_SCRATCH = "/cfg/old"\nFORGEPROOF_SCRATCH = "/cfg/new"\n')
    assert config.configured("FORGEPROOF_SCRATCH") == "/cfg/new"
    assert config.configured("FORGEPROOF_SCRATCH") == "/cfg/new"   # a file is not rewritten: the second read must stay quiet
    assert capsys.readouterr().err.count("FORGEPROOF_SCRATCH and the old name") == 1
    monkeypatch.setenv("FORGE_GATE_SCRATCH", "/env/old")
    assert config.configured("FORGEPROOF_SCRATCH") == "/env/old"   # environment (either name) beats the file


def test_old_env_name_over_new_file_name_says_so(monkeypatch, tmp_path, capsys):
    cfg = tmp_path / "config.toml"; cfg.write_text('[paths]\nFORGEPROOF_SCRATCH = "/cfg/new"\n')
    monkeypatch.setenv("FORGEPROOF_CONFIG", str(cfg))
    monkeypatch.setenv("FORGE_GATE_SCRATCH", "/env/old")
    assert config.configured("FORGEPROOF_SCRATCH") == "/env/old"
    assert config.configured("FORGEPROOF_SCRATCH") == "/env/old"
    assert capsys.readouterr().err.count("FORGEPROOF_SCRATCH in ") == 1


def test_a_message_names_the_key_the_operator_set(monkeypatch, tmp_path):
    monkeypatch.setenv("FORGE_GATE_LIBRARY_DIR", str(tmp_path / "nope"))
    with pytest.raises(config.NotConfigured, match=r"^FORGE_GATE_LIBRARY_DIR="):
        config.library_dir()
    monkeypatch.setenv("FORGEPROOF_LIBRARY_DIR", str(tmp_path / "nope2"))
    with pytest.raises(config.NotConfigured, match=r"^FORGEPROOF_LIBRARY_DIR="):
        config.library_dir()


def test_import_makes_the_two_names_agree_before_any_read():
    env = {k: v for k, v in os.environ.items() if not k.startswith(("FORGEPROOF_", "FORGE_GATE_"))}
    env.update(PYTHONPATH=str(REPO / "src"), FORGEPROOF_QJS="/new/qjs", FORGE_GATE_QJS="/old/qjs")
    code = "import os; from forgeproof import config; print(os.environ['FORGE_GATE_QJS'])"
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60, env=env)
    assert r.returncode == 0 and r.stdout.strip() == "/new/qjs", (r.stdout, r.stderr)
    assert r.stderr.count("FORGEPROOF_QJS and the old name FORGE_GATE_QJS") == 1, r.stderr


# Every read of a setting goes through config.setting / config.configured: no module reads a
# FORGEPROOF_/FORGE_GATE_ key from os.environ itself (tools/fg_common.py has its own two-name read).
DIRECT = re.compile(r"""(?:environ(?:\.get)?\s*[\[(]|getenv\s*\()\s*(?:f?["'](?:FORGEPROOF_|FORGE_GATE_)|[A-Z_]*_KEY\b)""")


def test_no_module_reads_a_setting_around_the_helper():
    hits = []
    for p in sorted((REPO / "src").rglob("*.py")) + sorted((REPO / "tools").rglob("*.py")):
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if DIRECT.search(line):
                hits.append(f"{p.relative_to(REPO)}:{n}: {line.strip()}")
    assert not hits, "direct environment reads of a setting:\n" + "\n".join(hits)


def _fg_common():
    spec = importlib.util.spec_from_file_location("fg_common_under_test", REPO / "tools" / "fg_common.py")
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


def test_gate_tool_reads_new_then_old_and_sets_both_homes(tmp_path):
    C = _fg_common()
    assert C.gate_path({"FORGE_GATE_GATE_PATH": "/old/bin"}) == "/old/bin"
    assert C.gate_path({"FORGE_GATE_GATE_PATH": "/old/bin", "FORGEPROOF_GATE_PATH": "/new/bin"}) == "/new/bin"
    assert C.threshold(None, {"FORGE_GATE_GATE_MAX_LOAD": "7.5"}) == 7.5
    assert C.threshold(None, {"FORGE_GATE_GATE_MAX_LOAD": "7.5", "FORGEPROOF_GATE_MAX_LOAD": "2.5"}) == 2.5
    env = C.build_env({"HOME": "/h"}, tmp_path, tmp_path / "pyc")
    assert env["FORGEPROOF_HOME"] == env["FORGE_GATE_HOME"] == str(tmp_path)
