"""Integration tests — require a real BERIL fork with projects/ populated.

Default-deselected via pytest marker. Run with:
    pytest tests/integration/ -m integration
"""

import os
import pytest


def pytest_collection_modifyitems(config, items):
    """Auto-apply the `integration` marker ONLY to tests in this directory.

    pytest invokes this hook with ALL collected items (not directory-scoped),
    so we filter by file path to avoid marking unit tests too.
    """
    this_dir = os.path.dirname(os.path.abspath(__file__))
    for item in items:
        if os.path.abspath(str(item.fspath)).startswith(this_dir):
            item.add_marker(pytest.mark.integration)
