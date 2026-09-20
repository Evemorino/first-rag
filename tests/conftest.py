"""Shared pytest fixtures.

All fixtures write to system tmp; never touch the project's real data/
or notes/ (constitution V, NON-NEGOTIABLE).
"""

import pytest


@pytest.fixture
def tmp_data_dir(tmp_path, monkeypatch):
    """Point config paths at a tmp dir so tests can write freely."""
    from src import config

    for name in ("DATA_DIR", "RAW_DIR", "QDRANT_DIR", "NOTES_DIR",
                 "SYNC_LOCK_PATH"):
        monkeypatch.setattr(config, name, tmp_path / name.lower())
    return tmp_path
