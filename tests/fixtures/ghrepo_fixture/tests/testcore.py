import doctest
import os
import unittest

from fgfix import core


def load_tests(loader, tests, ignore):
    tests.addTests(doctest.DocTestSuite(core))
    return tests


class Clamp(unittest.TestCase):
    def test_below(self):
        self.assertEqual(core.clamp(-1, 0, 3), 0)

    def test_above(self):
        self.assertEqual(core.clamp(5, 0, 3), 3)

    def test_inside(self):
        self.assertEqual(core.clamp(2, 0, 3), 2)


class Flaky(unittest.TestCase):
    def test_flaky(self):
        # Stands in for a test whose outcome changes between runs: it fails while a marker file
        # sits in the work folder (the tests plant it for chosen runs).
        self.assertFalse(os.path.exists("FG_FLAKY_FAIL"))


class Label(unittest.TestCase):
    def test_label(self):
        self.assertEqual(core.label(1), "größer")


class Gated(unittest.TestCase):
    def test_gated(self):
        self.assertEqual(core.gated(1), 2)


class Guarded(unittest.TestCase):
    def test_guarded(self):
        self.assertEqual(core.guarded(3), 6)
