"""The 2026-11-25 rename: the old names keep working for one release, and say so."""

import os
import subprocess
import sys
from pathlib import Path

import forgeproof

# The directory that holds the ``forgeproof`` package, source checkout or installed copy alike;
# the ``forge_gate`` alias package is shipped beside it.
PKG_ROOT = str(Path(forgeproof.__file__).resolve().parent.parent)


def _py(code: str, *flags: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONPATH"] = PKG_ROOT + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run([sys.executable, *flags, "-c", code], capture_output=True, text=True,
                          env=env, timeout=60)


def test_old_import_is_the_same_module_and_warns():
    r = _py("import forge_gate.lenses as old, forgeproof.lenses as new, forge_gate;"
            "assert old is new, (old, new);"
            "assert forge_gate.__version__ == __import__('forgeproof').__version__;"
            "print('SAME')", "-W", "always::DeprecationWarning")
    assert r.returncode == 0, r.stderr
    assert "SAME" in r.stdout
    assert "renamed to 'forgeproof'" in r.stderr
    assert "removed in 0.3.0" in r.stderr


def test_old_import_fails_when_warnings_are_errors():
    # The warning is real: a caller running with -W error sees the rename at import time.
    r = _py("import forge_gate", "-W", "error::DeprecationWarning")
    assert r.returncode != 0
    assert "renamed to 'forgeproof'" in r.stderr


def test_old_submodule_attribute_access_after_import():
    r = _py("import forge_gate.scout.ratify as old, forgeproof.scout.ratify as new;"
            "assert old is new; assert old.__name__ == 'forgeproof.scout.ratify'; print('OK')",
            "-W", "ignore::DeprecationWarning")
    assert r.returncode == 0, r.stderr
    assert "OK" in r.stdout


def test_old_cli_says_renamed_and_runs_forgeproof():
    r = _py("import sys\nfrom forge_gate._deprecated_cli import main\n"
            "try:\n    rc = main(['--help'])\nexcept SystemExit as e:\n    rc = e.code\n"
            "sys.exit(rc or 0)", "-W", "ignore::DeprecationWarning")
    assert r.returncode == 0, r.stderr
    assert "renamed to 'forgeproof'" in r.stderr and "removed in 0.3.0" in r.stderr
    assert "usage: forgeproof" in r.stdout


def _version(module: str) -> subprocess.CompletedProcess:
    return _py("import sys\nfrom %s import main\n"
               "try:\n    rc = main(['--version'])\nexcept SystemExit as e:\n    rc = e.code\n"
               "sys.exit(rc or 0)" % module, "-W", "ignore::DeprecationWarning")


def test_version_flag_prints_the_version_and_exits_0():
    r = _version("forgeproof.cli")
    assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
    assert r.stdout.strip() == f"forgeproof {forgeproof.__version__}", r.stdout


def test_old_command_version_flag_prints_the_same_plus_its_notice():
    r = _version("forge_gate._deprecated_cli")
    assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
    assert r.stdout.strip() == f"forgeproof {forgeproof.__version__}", r.stdout
    assert "renamed to 'forgeproof'" in r.stderr and "removed in 0.3.0" in r.stderr
