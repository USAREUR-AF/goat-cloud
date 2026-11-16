"""Enforce that shared/delta.py is the single source of truth.

Both scanner and dashboard lambda dirs get delta.py copied from
modules/aws/shared/ during terraform apply (null_resource local-exec).
This test ensures the shared copy exists and imports cleanly.
"""

import importlib
import os

_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
_SHARED_DELTA = os.path.join(_ROOT, "modules", "aws", "shared", "delta.py")


def test_shared_delta_exists():
    """The canonical delta.py must exist in modules/aws/shared/."""
    assert os.path.isfile(_SHARED_DELTA), f"Missing shared delta.py at {_SHARED_DELTA}"


def test_shared_delta_imports():
    """The shared delta.py must import cleanly and expose compute_delta."""
    spec = importlib.util.spec_from_file_location("delta_shared", _SHARED_DELTA)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert hasattr(mod, "compute_delta"), "delta.py missing compute_delta function"
