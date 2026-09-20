"""T032/T033 unit tests: 手动快记（log → collect → 轻路径直并入）。"""

from datetime import date, datetime

import pytest

from src import collect, config, distill, log


@pytest.fixture
def notes_dir(tmp_data_dir):
    config.NOTES_DIR.mkdir(parents=True, exist_ok=True)
    return config.NOTES_DIR


def test_log_appends_timestamped_line(notes_dir):
    log.log("配置漂移的根因是两处默认值", type_="error")

    content = (notes_dir / "inbox.md").read_text(encoding="utf-8")
    assert content.startswith("- [")
    assert content.rstrip().endswith("配置漂移的根因是两处默认值")
    assert "#error" in content  # 类型标注写入行内
    ts = content.split("[")[1].split(" ")[0]
    assert datetime.fromisoformat(ts).tzinfo is not None  # 上海时区感知


def test_log_without_type_writes_no_marker(notes_dir):
    log.log("一个还没行动的想法")
    content = (notes_dir / "inbox.md").read_text(encoding="utf-8")
    assert "#" not in content.split("]")[0]


def test_log_line_is_collectible(notes_dir):
    """快记行能被 collect 解析回素材，未标注默认归 reflection。"""
    log.log("学习：快记走轻路径", type_="idea")
    log.log("没有标注类型的想法")

    materials = collect._note_materials(
        datetime.now(tz=config.TZ).date())

    by_text = {m.text: m for m in materials}
    assert by_text["学习：快记走轻路径"].meta["note_type"] == "idea"
    assert by_text["没有标注类型的想法"].meta["note_type"] == "reflection"


def test_note_flows_to_direct_path_without_llm(notes_dir, monkeypatch, tmp_path):
    """T033：快记入库走轻路径、不进 LLM（FR-013）。"""
    calls = []
    monkeypatch.setattr(distill, "chat",
                        lambda *a, **k: calls.append(1) or "{}")
    monkeypatch.setattr(distill.config, "load_schema", lambda: {
        "types": [{"name": "reflection", "desc": "d"},
                  {"name": "idea", "desc": "d"}],
        "distill": {"include_signals": [], "exclude_signals": [],
                    "examples": {"keep": [], "drop": []},
                    "novelty_threshold": 0.82, "max_entries_per_day": 30,
                    "struggle_rounds": 3, "max_raw_chars": 100000},
        "retrieval": {"top_k": 8, "expand": {
            "mode": "all", "neighbor_limit_per_hit": 2,
            "context_cap": 12, "neighbor_min_score": None}},
        "trae_type_map": {}, "raw_retention_days": 90})
    monkeypatch.setattr(config, "RAW_DIR", tmp_path / "raw")

    log.log("轻路径验证条目")
    day_raw = collect.DayRaw(
        day=datetime.now(tz=config.TZ).date(),
        collected_at=datetime.now(tz=config.TZ))
    day_raw.materials = collect._note_materials(
        datetime.now(tz=config.TZ).date())

    entries = distill.distill(day_raw)

    assert calls == []  # 不进 LLM
    assert [e.text for e in entries] == ["轻路径验证条目"]
    assert entries[0].type == "reflection"
    assert entries[0].source == "manual"


def test_main_parses_m_and_t(notes_dir, capsys):
    assert log.main(["log", "m=来自 CLI 的快记", "t=idea"]) == 0
    content = (notes_dir / "inbox.md").read_text(encoding="utf-8")
    assert "来自 CLI 的快记" in content
    assert "#idea" in content


def test_main_requires_message(notes_dir):
    assert log.main(["log"]) != 0
