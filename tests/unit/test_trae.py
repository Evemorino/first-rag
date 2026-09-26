"""Unit tests for the trae plugin (T053) — `~/.trae/memory`。

与 `trae_work_cn`（`~/.trae-cn/memory`）**同格式、同映射规则，仅 MEMORY_DIR
不同**——PRD FR-002a 的这条断言已在本机真实数据上核对过（2026-09-25）：
两个根的记录键集合、字段类型、时间戳可解比例、同级兄弟文件都一致，
差异只在数据量（706 条 vs 5 条）与日期集合。但**仍是各自实现**（契约规则 6，
同源插件对也不例外），代价由 `test_same_source_plugin_pairs.py` 的同 fixture
等价性测试补偿。

`~/.trae` 实测（2026-09-25）：3 个会话文件 / 5 条记录，日期 2026-09-20（3 条）与
2026-09-21（2 条）。字段：`intent` str、`actions` list、`outcome` str、
`learned` list、`message_summary_time` `%Y-%m-%d %H:%M:%S`、`message_id`，
外加 `compact_summary_meta`（`trigger`/`mode`/`server_history_id`/
`summary_digest`/`created_at_ms`）——后者是纯机器字段，不得进正文。

进正文的只有 `learned` 与 `outcome`（FR-002），`intent`/`actions` 不进；
type 由 `config/schema.json` 的 `trae_type_map` 按键序取第一个有内容的字段映射，
未匹配落 `reflection`。源目录只读（宪法 V）。
"""

import json
from datetime import date, datetime, timedelta, timezone

import pytest

from src import config
from src.plugins import SourceRef, trae

DAY = date(2026, 9, 20)
OTHER = date(2026, 9, 21)
TZ = timezone(timedelta(hours=8))

PROJECT = "-Users-nava-Code-llm-rag-first-rag--p2-a36559cd5ee996ba2f18"


def record(learned=None, outcome=None, ts="2026-09-20 09:17:31",
           with_meta=True):
    """照实测形状造一条记录（含真实的 `compact_summary_meta`）。"""
    r = {
        "intent": "了解项目现状并确定下一步开发方向",
        "actions": ["分析 tasks.md 梳理开发进度", "检查 src 目录下核心模块"],
        "outcome": outcome if outcome is not None else "清晰呈现了已完成和待办项清单",
        "learned": learned if learned is not None else [
            "项目是一个基于 RAG 的个人学习记忆系统",
            "插件系统采用 registry 模式支持多源采集",
        ],
        "message_summary_time": ts,
        "message_id": "6aaf33f0542a496b86a19d81",
    }
    if with_meta:
        r["compact_summary_meta"] = {
            "trigger": "auto", "mode": "async",
            "server_history_id": "6aaf33f0542a496b86a19d81",
            "summary_digest": "940b8822deea819bcb08ff6cc784d404",
            "created_at_ms": 1789867051573,
        }
    return r


@pytest.fixture
def memory_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(trae, "MEMORY_DIR", tmp_path)
    return tmp_path


@pytest.fixture
def type_map(monkeypatch):
    monkeypatch.setattr(
        config, "load_schema",
        lambda: {"trae_type_map": {"learned": "reflection", "outcome": "progress"}},
    )


def write_memory(memory_dir, ymd: str, records, project: str = PROJECT):
    d = memory_dir / "projects" / project / ymd
    d.mkdir(parents=True, exist_ok=True)
    path = d / "session_memory_6aaf33f0542a496b86a19d80.jsonl"
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
        encoding="utf-8")
    return path


def ref(path, day=DAY) -> SourceRef:
    return SourceRef(source="trae", ref=f"{path}#L0", day=day)


# --- discover ---


def test_discover_returns_one_ref_per_record(memory_dir, type_map):
    path = write_memory(memory_dir, "20260920", [record(), record()])

    refs = trae.discover(DAY)

    assert len(refs) == 2  # 每条记录一个引用（每条记录一条条目）
    assert refs[0].source == "trae"
    assert refs[0].ref == f"{path}#L0"
    assert refs[1].ref == f"{path}#L1"
    assert refs[0].day == DAY


def test_discover_filters_by_day_dir_and_missing_noop(
        memory_dir, type_map, tmp_path, monkeypatch):
    write_memory(memory_dir, "20260920", [record()])
    write_memory(memory_dir, "20260921", [record(ts="2026-09-21 13:00:06")])

    assert len(trae.discover(DAY)) == 1

    monkeypatch.setattr(trae, "MEMORY_DIR", tmp_path / "nope")
    assert trae.discover(DAY) == []


def test_discover_ignores_the_sibling_markdown_files(memory_dir, type_map):
    """日期目录旁的 `topics.md` 与项目目录级的 `project_memory.md` 都不是记录。

    实测两个根的日期目录里都有 `topics.md`，`~/.trae` 的项目目录级还有
    `project_memory.md`——路径允许列表（`projects/*/<YYYYMMDD>/session_memory_*.jsonl`）
    把两者都挡在外面。
    """
    path = write_memory(memory_dir, "20260920", [record()])
    (path.parent / "topics.md").write_text("## 话题\n- 项目现状\n", encoding="utf-8")
    (memory_dir / "projects" / PROJECT / "project_memory.md").write_text(
        "## 项目记忆\n", encoding="utf-8")

    refs = trae.discover(DAY)

    assert [r.ref for r in refs] == [f"{path}#L0"]


def test_discover_skips_unparsable_lines(memory_dir, type_map):
    d = memory_dir / "projects" / PROJECT / "20260920"
    d.mkdir(parents=True)
    path = d / "session_memory_x.jsonl"
    path.write_text("{不是 json}\n" + json.dumps(record(), ensure_ascii=False) + "\n",
                    encoding="utf-8")

    refs = trae.discover(DAY)

    assert [r.ref for r in refs] == [f"{path}#L1"]


# --- parse ---


def test_parse_assembles_text_from_learned_and_outcome(memory_dir, type_map):
    path = write_memory(memory_dir, "20260920", [record()])

    material = trae.parse(ref(path))

    assert material.source == "trae"
    assert material.kind == "trae_record"
    assert "项目是一个基于 RAG 的个人学习记忆系统" in material.text
    assert "清晰呈现了已完成和待办项清单" in material.text
    # 只有 learned / outcome 进正文
    assert "了解项目现状并确定下一步开发方向" not in material.text
    assert "分析 tasks.md 梳理开发进度" not in material.text


def test_parse_drops_the_machine_fields(memory_dir, type_map):
    """`compact_summary_meta`（实测 5/5 条都有）是纯机器字段，不得进正文。"""
    path = write_memory(memory_dir, "20260920", [record()])

    material = trae.parse(ref(path))

    assert "940b8822deea819bcb08ff6cc784d404" not in material.text
    assert "1789867051573" not in material.text
    assert "server_history_id" not in material.text
    assert "6aaf33f0542a496b86a19d81" not in material.text


def test_parse_anchors_ts_to_shanghai(memory_dir, type_map):
    path = write_memory(memory_dir, "20260920",
                        [record(ts="2026-09-20 09:17:31")])

    ts = trae.parse(ref(path)).ts

    assert ts.tzinfo is not None
    assert ts.astimezone(TZ) == datetime(2026, 9, 20, 9, 17, 31, tzinfo=TZ)


def test_parse_falls_back_to_day_midnight_without_a_timestamp(
        memory_dir, type_map):
    """时间戳缺失/读不出时落当天零点（该记录仍要并入，不能整条丢）。"""
    path = write_memory(memory_dir, "20260920",
                        [record(ts="not-a-timestamp")])

    ts = trae.parse(ref(path)).ts

    assert ts == datetime.combine(DAY, datetime.min.time(), tzinfo=TZ)


def test_parse_maps_type_from_first_matched_field(memory_dir, type_map):
    path = write_memory(memory_dir, "20260920", [record()])  # learned 先匹配
    outcome_only = write_memory(
        memory_dir, "20260921",
        [record(learned=[], outcome="Shipped the fix.", ts="2026-09-21 10:00:00")])

    assert trae.parse(ref(path)).meta["note_type"] == "reflection"

    material = trae.parse(SourceRef(
        source="trae", ref=f"{outcome_only}#L0", day=OTHER))
    assert material.meta["note_type"] == "progress"


def test_parse_unmatched_field_falls_back_to_reflection(memory_dir, monkeypatch):
    monkeypatch.setattr(
        config, "load_schema",
        lambda: {"trae_type_map": {"nonexistent_field": "progress"}},
    )
    path = write_memory(memory_dir, "20260920", [record()])

    assert trae.parse(ref(path)).meta["note_type"] == "reflection"


def test_parse_project_is_none_not_misdecoded(memory_dir, type_map):
    """项目目录名把路径分隔符编码成 '-'，无歧义还原不可能 → 宁缺毋错，留空。

    实测真实目录名如 `-Users-nava-Code-llm-rag-first-rag--p2-a36559cd5ee996ba2f18`
    （`--p2-` 后面还挂着哈希），以及更离谱的
    `brary-Application-Support-TRAE-SOLO-...-vkzdoq--p2-e22ad8c9`（**没有盘符
    前缀**，开头就是残缺路径）。
    """
    path = write_memory(memory_dir, "20260920", [record()])

    assert trae.parse(ref(path)).meta["project"] is None


def test_parse_survives_a_file_that_vanished_after_discover(
        memory_dir, type_map, tmp_path):
    """discover 到 parse 之间文件被删（NFR-004）：不崩，产一条空素材。"""
    path = write_memory(memory_dir, "20260920", [record()])
    r = ref(path)
    path.unlink()

    material = trae.parse(r)

    assert material.text == ""
    assert material.ts == datetime.combine(DAY, datetime.min.time(), tzinfo=TZ)


def test_parse_with_a_broken_schema_still_yields_material(memory_dir, monkeypatch):
    """配置坏了也要能并入（落默认类型），不能因为读 schema 失败整条丢。"""
    def boom():
        raise RuntimeError("schema.json is corrupt")

    monkeypatch.setattr(config, "load_schema", boom)
    path = write_memory(memory_dir, "20260920", [record()])

    material = trae.parse(ref(path))

    assert material.meta["note_type"] == "reflection"
    assert "项目是一个基于 RAG 的个人学习记忆系统" in material.text


# --- registry ---


def test_registered_in_registry():
    from src.plugins import iter_plugins

    assert "trae" in {p.name for p in iter_plugins()}
