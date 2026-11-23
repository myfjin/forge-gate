import importlib.util
import sys
import unittest


def _copy_outside_work():
    # Stands in for the system copy the box can see under /usr: this loads fgfix.other from
    # /src (the box's read-only source mount), not from /work, and leaves it in sys.modules.
    spec = importlib.util.spec_from_file_location("fgfix.other", "/src/fgfix/other.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    sys.modules["fgfix.other"] = mod
    return mod


class Elsewhere(unittest.TestCase):
    def test_elsewhere(self):
        self.assertEqual(_copy_outside_work().elsewhere(2), 3)
