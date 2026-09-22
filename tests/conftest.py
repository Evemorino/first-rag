"""Shared pytest fixtures.

All fixtures write to system tmp; never touch the project's real data/
or notes/ (constitution V, NON-NEGOTIABLE).
"""

import pytest

from tests.mutmut_compat import apply_if_mutating

# 必须在导入任何 src 模块之前打补丁，否则 mutmut 的 trampoline 会因为
# 本项目的 `src.` 包名直接断言失败（详见 tests/mutmut_compat.py）。
apply_if_mutating()


@pytest.fixture
def tmp_data_dir(tmp_path, monkeypatch):
    """Point config paths at a tmp dir so tests can write freely."""
    from src import config

    for name in ("DATA_DIR", "RAW_DIR", "QDRANT_DIR", "NOTES_DIR",
                 "SYNC_LOCK_PATH"):
        monkeypatch.setattr(config, name, tmp_path / name.lower())
    return tmp_path
