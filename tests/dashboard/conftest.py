"""Dashboard-specific conftest.

delta.py is now shared code in modules/aws/shared/ (on pytest pythonpath).
"""

import delta as _delta_module
import pytest


@pytest.fixture()
def dashboard_delta():
    """Return the shared delta module."""
    return _delta_module
