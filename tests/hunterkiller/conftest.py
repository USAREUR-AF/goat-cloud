"""Hunterkiller-specific conftest — handles index.py import collision.

Both scanner and hunterkiller have `index.py`. Since pytest.ini lists
scanner first in pythonpath, `from index import ...` gets the scanner version.
We use importlib to explicitly load the hunterkiller version.
"""

import importlib.util
import os

import pytest

_HK_LAMBDA = os.path.normpath(
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "modules",
        "aws",
        "hunterkiller",
        "lambda",
    )
)


@pytest.fixture()
def hk_index():
    """Import and return the hunterkiller-specific index module."""
    spec = importlib.util.spec_from_file_location(
        "hk_index",
        os.path.join(_HK_LAMBDA, "index.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
