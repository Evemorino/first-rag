"""Unit tests for config loading and schema validation (T002/T003).

Fixtures write to system tmp only (constitution V).
"""

import json

import pytest

from src import config


def _make_schema(tmp_path, mutate=None):
    """Write a valid schema to tmp, optionally mutated to be broken."""
    schema = json.loads(config.SCHEMA_PATH.read_text(encoding="utf-8"))
    if mutate:
        mutate(schema)
    path = tmp_path / "schema.json"
    path.write_text(json.dumps(schema), encoding="utf-8")
    return path


def test_load_schema_valid():
    schema = config.load_schema()
    assert config.type_names(schema) == [
        "progress", "error", "idea", "reflection",
    ]


def test_load_env_reads_fixture(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("ARK_API_KEY=test-key-123\n", encoding="utf-8")
    monkeypatch.setattr(config, "ROOT", tmp_path, raising=False)
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    config.load_env()
    assert config.env("ARK_API_KEY") == "test-key-123"


def test_env_missing_raises_actionable_error(monkeypatch):
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    with pytest.raises(config.ConfigError, match="ARK_API_KEY"):
        config.env("ARK_API_KEY")


def test_broken_schema_reports_exact_field(tmp_path):
    def drop_novelty(schema):
        del schema["distill"]["novelty_threshold"]

    path = _make_schema(tmp_path, drop_novelty)
    with pytest.raises(config.ConfigError, match="novelty_threshold"):
        config.load_schema(path)


def test_empty_types_rejected(tmp_path):
    def empty_types(schema):
        schema["types"] = []

    path = _make_schema(tmp_path, empty_types)
    with pytest.raises(config.ConfigError, match="types"):
        config.load_schema(path)


@pytest.mark.parametrize("mode", ["off", "all", 0.75])
def test_expand_mode_accepts_three_kinds(tmp_path, mode):
    schema = json.loads(config.SCHEMA_PATH.read_text(encoding="utf-8"))
    schema["retrieval"]["expand"]["mode"] = mode
    path = tmp_path / "schema.json"
    path.write_text(json.dumps(schema), encoding="utf-8")
    assert config.load_schema(path)["retrieval"]["expand"]["mode"] == mode


def test_expand_mode_invalid_rejected(tmp_path):
    def bad_mode(schema):
        schema["retrieval"]["expand"]["mode"] = "sometimes"

    path = _make_schema(tmp_path, bad_mode)
    with pytest.raises(config.ConfigError, match="mode"):
        config.load_schema(path)
