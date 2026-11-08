"""threedice.batch is an optional extra: without NumPy it must fail with an actionable message, and the base package must not care."""
import importlib
import sys

import pytest


def _purge():
    for name in [m for m in sys.modules if m == "threedice.batch" or m.startswith("threedice.batch.")]:
        del sys.modules[name]


def test_batch_without_numpy_names_the_extra(monkeypatch):
    _purge()
    monkeypatch.setitem(sys.modules, "numpy", None)  # makes `import numpy` raise ImportError
    try:
        with pytest.raises(ImportError, match=r"threedice\[batch\]"):
            importlib.import_module("threedice.batch")
    finally:
        _purge()


def test_base_package_imports_without_numpy(monkeypatch):
    monkeypatch.setitem(sys.modules, "numpy", None)
    monkeypatch.delitem(sys.modules, "threedice", raising=False)
    importlib.import_module("threedice")
