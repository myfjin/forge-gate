"""Deprecated alias: Forge Gate was renamed ForgeProof on 2026-11-25.

``import forge_gate`` and ``import forge_gate.<module>`` keep working for one release: each
``forge_gate.<module>`` IS ``forgeproof.<module>`` (the same module object). Import ``forgeproof``
instead; this alias will be removed in a later release.
"""

import importlib
import importlib.abc
import importlib.util
import sys
import warnings

_OLD, _NEW = "forge_gate", "forgeproof"
_OWN = (_OLD + "._deprecated_cli",)  # real modules of this shim package, never aliased

warnings.warn(
    "the 'forge_gate' package was renamed to 'forgeproof' (2026-11-25); import 'forgeproof' instead. "
    "This alias will be removed in a later release.",
    DeprecationWarning,
    stacklevel=2,
)


class _Alias(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Serve ``forge_gate.x`` as the already-imported module ``forgeproof.x``."""

    def find_spec(self, name, path=None, target=None):
        if name.startswith(_OLD + ".") and name not in _OWN:
            return importlib.util.spec_from_loader(name, self)
        return None

    def create_module(self, spec):
        return importlib.import_module(_NEW + spec.name[len(_OLD):])

    def exec_module(self, module):
        return None


if not any(isinstance(f, _Alias) for f in sys.meta_path):
    sys.meta_path.insert(0, _Alias())

from forgeproof import *  # noqa: E402,F401,F403
from forgeproof import __version__  # noqa: E402,F401
