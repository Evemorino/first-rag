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
    with pytest.raises(config.ConfigError) as excinfo:
        config.env("ARK_API_KEY")

    assert str(excinfo.value) == (
        "ARK_API_KEY is not set. Copy .env.example to .env and fill it in."
    )


def test_broken_schema_reports_exact_field(tmp_path):
    def drop_novelty(schema):
        del schema["distill"]["novelty_threshold"]

    path = _make_schema(tmp_path, drop_novelty)
    with pytest.raises(config.ConfigError) as excinfo:
        config.load_schema(path)

    assert str(excinfo.value) == "schema.distill is missing 'novelty_threshold'"


def test_empty_types_rejected(tmp_path):
    def empty_types(schema):
        schema["types"] = []

    path = _make_schema(tmp_path, empty_types)
    with pytest.raises(config.ConfigError) as excinfo:
        config.load_schema(path)

    assert str(excinfo.value) == "schema.types must be a non-empty list"


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
    with pytest.raises(config.ConfigError) as excinfo:
        config.load_schema(path)

    assert str(excinfo.value) == (
        "schema.retrieval.expand.mode must be 'off', 'all', or a number"
    )


# --- 各 section 的分支：错误信息是用户改 schema.json 的唯一线索，必须精确 ---


@pytest.mark.parametrize("broken", [{"desc": "no name"}, {"name": "nameless"},
                                    "not-an-object"])
def test_type_entry_without_name_or_desc_rejected(tmp_path, broken):
    def break_type(schema):
        schema["types"] = [broken]

    path = _make_schema(tmp_path, break_type)
    with pytest.raises(config.ConfigError) as excinfo:
        config.load_schema(path)

    assert str(excinfo.value) == (
        "schema.types[0] must be an object with 'name' and 'desc'"
    )


@pytest.mark.parametrize("broken", ["not-an-object", ["list"], None])
def test_distill_must_be_an_object(tmp_path, broken):
    def break_distill(schema):
        schema["distill"] = broken

    path = _make_schema(tmp_path, break_distill)
    with pytest.raises(config.ConfigError) as excinfo:
        config.load_schema(path)

    assert str(excinfo.value) == "schema.distill must be an object"


@pytest.mark.parametrize("key", ["include_signals", "examples", "struggle_rounds",
                                 "max_raw_chars"])
def test_distill_missing_any_key_reports_which(tmp_path, key):
    def drop_key(schema):
        del schema["distill"][key]

    path = _make_schema(tmp_path, drop_key)
    with pytest.raises(config.ConfigError) as excinfo:
        config.load_schema(path)

    assert str(excinfo.value) == f"schema.distill is missing '{key}'"


@pytest.mark.parametrize("value", [0, -0.1, 1.5])
def test_novelty_threshold_out_of_range_rejected(tmp_path, value):
    def set_threshold(schema):
        schema["distill"]["novelty_threshold"] = value

    path = _make_schema(tmp_path, set_threshold)
    with pytest.raises(config.ConfigError) as excinfo:
        config.load_schema(path)

    assert str(excinfo.value) == (
        "schema.distill.novelty_threshold must be in (0, 1]"
    )


def test_max_entries_per_day_below_one_rejected(tmp_path):
    def set_max(schema):
        schema["distill"]["max_entries_per_day"] = 0

    path = _make_schema(tmp_path, set_max)
    with pytest.raises(config.ConfigError) as excinfo:
        config.load_schema(path)

    assert str(excinfo.value) == (
        "schema.distill.max_entries_per_day must be >= 1"
    )


@pytest.mark.parametrize("broken", ["not-an-object", {"expand": {}}])
def test_retrieval_without_top_k_rejected(tmp_path, broken):
    def break_retrieval(schema):
        schema["retrieval"] = broken

    path = _make_schema(tmp_path, break_retrieval)
    with pytest.raises(config.ConfigError) as excinfo:
        config.load_schema(path)

    assert str(excinfo.value) == "schema.retrieval must contain 'top_k'"


@pytest.mark.parametrize("key", ["mode", "neighbor_limit_per_hit", "context_cap"])
def test_expand_missing_any_key_reports_which(tmp_path, key):
    def drop_key(schema):
        del schema["retrieval"]["expand"][key]

    path = _make_schema(tmp_path, drop_key)
    with pytest.raises(config.ConfigError) as excinfo:
        config.load_schema(path)

    assert str(excinfo.value) == f"schema.retrieval.expand is missing '{key}'"


def test_trae_type_map_must_be_an_object(tmp_path):
    def break_map(schema):
        schema["trae_type_map"] = ["a", "b"]

    path = _make_schema(tmp_path, break_map)
    with pytest.raises(config.ConfigError) as excinfo:
        config.load_schema(path)

    assert str(excinfo.value) == "schema.trae_type_map must be an object"


@pytest.mark.parametrize("value", [-1, "7", 1.5])
def test_bad_retention_rejected(tmp_path, value):
    def set_retention(schema):
        schema["raw_retention_days"] = value

    path = _make_schema(tmp_path, set_retention)
    with pytest.raises(config.ConfigError) as excinfo:
        config.load_schema(path)

    assert str(excinfo.value) == (
        "schema.raw_retention_days must be a non-negative int or null"
    )


def test_null_retention_means_keep_forever(tmp_path):
    def set_retention(schema):
        schema["raw_retention_days"] = None

    path = _make_schema(tmp_path, set_retention)

    assert config.load_schema(path)["raw_retention_days"] is None
