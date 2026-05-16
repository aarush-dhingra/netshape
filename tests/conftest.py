"""Shared test fixtures."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest


@pytest.fixture()
def tmp_netshape_dir(tmp_path: Path) -> Path:
    """Create a temporary ~/.netshape equivalent for testing."""
    netshape_dir = tmp_path / ".netshape"
    netshape_dir.mkdir()
    (netshape_dir / "profiles").mkdir()
    (netshape_dir / "logs").mkdir()
    return netshape_dir
