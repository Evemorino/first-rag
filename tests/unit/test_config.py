"""Unit tests for config loading and schema validation (T002/T003).

Fixtures write to system tmp only (constitution V).
"""

import ast
import json
from pathlib import Path

import pytest

from src import config

SRC_DIR = Path(__file__).resolve().parents[2] / "src"


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


def test_every_cli_entry_bootstraps_env():
    """src/ 里每个 `main()` 都必须先 `config.load_env()`。

    真实事故：`config.env()` 只读 os.environ，而 load_env() 只有 ark_client /
    api / embed_test 调过 —— sync、ask、redistill 三个入口都没调，distill 又在
    构造任何 client 之前就读 CHAT_MODEL，于是 `make sync` 在有 LLM 素材的日子
    必然 ConfigError。全套测试当时是绿的，因为 test_distill 直接 monkeypatch
    掉了 config.env 本身，等于把这条路径整段绕开。

    规则做成"所有 main 一律 bootstrap"而不是"需要 env 的那些才要"：后者要靠
    人判断谁需要，而这个判断会随重构失效 —— 又因为 load_env() 对缺失的 .env
    无害幂等，一律要求不比挑着要求贵。
    """
    missing = []
    for path in sorted(SRC_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef) or node.name != "main":
                continue
            bootstrapped = any(
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == "load_env"
                for call in ast.walk(node))
            if not bootstrapped:
                missing.append(path.as_posix())
    assert missing == [], f"这些 CLI 入口没 bootstrap 环境: {missing}"


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
                                 "max_raw_chars", "batch_max_chars"])
def test_distill_missing_any_key_reports_which(tmp_path, key):
    def drop_key(schema):
        del schema["distill"][key]

    path = _make_schema(tmp_path, drop_key)
    with pytest.raises(config.ConfigError) as excinfo:
        config.load_schema(path)

    assert str(excinfo.value) == f"schema.distill is missing '{key}'"


@pytest.mark.parametrize("value", [0, -1])
def test_batch_max_chars_below_one_rejected(tmp_path, value):
    """分批预算必须为正：0 或负数会让每一天切成 0 批，等于静默不蒸馏。"""
    def set_budget(schema):
        schema["distill"]["batch_max_chars"] = value

    path = _make_schema(tmp_path, set_budget)
    with pytest.raises(config.ConfigError) as excinfo:
        config.load_schema(path)

    assert str(excinfo.value) == (
        "schema.distill.batch_max_chars must be >= 1"
    )


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


def test_parallel_workers_missing_rejected(tmp_path):
    def drop(schema):
        del schema["distill"]["parallel_workers"]

    path = _make_schema(tmp_path, drop)
    with pytest.raises(config.ConfigError) as excinfo:
        config.load_schema(path)

    assert str(excinfo.value) == "schema.distill is missing 'parallel_workers'"


@pytest.mark.parametrize("value", [0, -1])
def test_parallel_workers_below_one_rejected(tmp_path, value):
    """并发度必须为正：0 或负数会让蒸馏退化成串行甚至什么都不跑。"""
    def set_workers(schema):
        schema["distill"]["parallel_workers"] = value

    path = _make_schema(tmp_path, set_workers)
    with pytest.raises(config.ConfigError) as excinfo:
        config.load_schema(path)

    assert str(excinfo.value) == "schema.distill.parallel_workers must be >= 1"


def test_answer_max_tokens_missing_rejected(tmp_path):
    def drop(schema):
        del schema["retrieval"]["answer_max_tokens"]

    path = _make_schema(tmp_path, drop)
    with pytest.raises(config.ConfigError) as excinfo:
        config.load_schema(path)

    assert str(excinfo.value) == "schema.retrieval is missing 'answer_max_tokens'"


@pytest.mark.parametrize("value", [0, -1])
def test_answer_max_tokens_below_one_rejected(tmp_path, value):
    """答案长度上限必须为正：0 会让 ask 的答案被截成空。"""
    def set_tokens(schema):
        schema["retrieval"]["answer_max_tokens"] = value

    path = _make_schema(tmp_path, set_tokens)
    with pytest.raises(config.ConfigError) as excinfo:
        config.load_schema(path)

    assert str(excinfo.value) == "schema.retrieval.answer_max_tokens must be >= 1"


def test_disable_thinking_missing_rejected(tmp_path):
    def drop(schema):
        del schema["retrieval"]["disable_thinking"]

    path = _make_schema(tmp_path, drop)
    with pytest.raises(config.ConfigError) as excinfo:
        config.load_schema(path)

    assert str(excinfo.value) == "schema.retrieval is missing 'disable_thinking'"


@pytest.mark.parametrize("value", ["yes", 1, None])
def test_disable_thinking_must_be_bool(tmp_path, value):
    """必须是真布尔：字符串 "false" 是真值，会让开关静默失效（最糟的一种）。"""
    def set_flag(schema):
        schema["retrieval"]["disable_thinking"] = value

    path = _make_schema(tmp_path, set_flag)
    with pytest.raises(config.ConfigError) as excinfo:
        config.load_schema(path)

    assert str(excinfo.value) == (
        "schema.retrieval.disable_thinking must be a boolean"
    )
