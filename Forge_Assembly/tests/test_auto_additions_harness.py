"""Run the pytest-style integration cases under the built-in unittest runner."""
import inspect
import tempfile
import unittest
from pathlib import Path

import test_auto_additions as cases


class _MonkeyPatch:
    def __init__(self):
        self._saved = []

    def setattr(self, obj, name, value):
        self._saved.append((obj, name, getattr(obj, name)))
        setattr(obj, name, value)

    def undo(self):
        for obj, name, value in reversed(self._saved):
            setattr(obj, name, value)


def _test_case(function):
    def run(self):
        with tempfile.TemporaryDirectory() as folder:
            patch = _MonkeyPatch()
            try:
                params = inspect.signature(function).parameters
                supplied = {'tmp_path': Path(folder), 'monkeypatch': patch}
                function(*(supplied[name] for name in params))
            finally:
                patch.undo()
    run.__name__ = function.__name__
    run.__doc__ = function.__doc__
    return run


class AutoAdditionIntegrationTests(unittest.TestCase):
    pass


for _name, _function in inspect.getmembers(cases, inspect.isfunction):
    if _name.startswith('test_'):
        setattr(AutoAdditionIntegrationTests, _name, _test_case(_function))
