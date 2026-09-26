"""Unit tests for the trae_work_cn plugin against fixture records (T023).

trae_work_cn is the pre-summarized path (FR-002/FR-013): each session_memory_*.jsonl
line is one already-distilled record; one record -> one entry, type mapped
via config trae_type_map, unmatched falls back to reflection.
"""

import json
from datetime import date

import pytest

from src.plugins import SourceRef, trae_work_cn
from src import config

DAY = date(2026, 9, 12)
OTHER = date(2026, 9, 15)


def record(learned=None, outcome=None, ts="2026-09-12 10:27:48"):
    return {
        "intent": "teach DICOM import",
        "actions": ["write tutorial"],
        "outcome": outcome if outcome is not None else "Finished the tutorial.",
        "learned": learned if learned is not None else [
            "DICOM import uses the DICOM Browser window",
            "Import by selecting the whole folder",
        ],
        "message_summary_time": ts,
        "message_id": "m-1",
    }


@pytest.fixture
def memory_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(trae_work_cn, "MEMORY_DIR", tmp_path)
    return tmp_path


@pytest.fixture
def type_map(monkeypatch):
    monkeypatch.setattr(
        config, "load_schema",
        lambda: {"trae_type_map": {"learned": "reflection", "outcome": "progress"}},
    )


def write_memory(memory_dir, project: str, ymd: str, records):
    d = memory_dir / "projects" / project / ymd
    d.mkdir(parents=True, exist_ok=True)
    path = d / "session_memory_abc123.jsonl"
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
        encoding="utf-8")
    return path


def test_discover_returns_one_ref_per_record(memory_dir, type_map):
    path = write_memory(memory_dir, "-c-Code-proj--p2-abc", "20260912",
                        [record(), record()])
    refs = trae_work_cn.discover(DAY)

    assert len(refs) == 2  # 每条记录一个引用（每条记录一条条目）
    assert refs[0].source == "trae_work_cn"
    assert refs[0].ref == f"{path}#L0"
    assert refs[0].day == DAY


def test_discover_filters_by_day_dir_and_missing_noop(memory_dir, type_map,
                                                      tmp_path, monkeypatch):
    write_memory(memory_dir, "-c-Code-proj--p2-abc", "20260912", [record()])
    write_memory(memory_dir, "-c-Code-proj--p2-abc", "20260915", [record()])
    assert len(trae_work_cn.discover(DAY)) == 1

    monkeypatch.setattr(trae_work_cn, "MEMORY_DIR", tmp_path / "nope")
    assert trae_work_cn.discover(DAY) == []


def test_parse_assembles_text_from_learned_and_outcome(memory_dir, type_map):
    path = write_memory(memory_dir, "-c-Code-proj--p2-abc", "20260912",
                        [record()])
    mat = trae_work_cn.parse(SourceRef(source="trae_work_cn", ref=f"{path}#L0", day=DAY))

    assert mat.kind == "trae_record"
    assert "DICOM import uses the DICOM Browser window" in mat.text
    assert "Finished the tutorial." in mat.text
    assert "teach DICOM import" not in mat.text  # text 只由 learned/outcome 拼装
    assert mat.ts.tzinfo is not None
    assert mat.ts.date() == DAY


def test_parse_maps_type_from_first_matched_field(memory_dir, type_map):
    path = write_memory(memory_dir, "-c-Code-proj--p2-abc", "20260912",
                        [record()])  # learned + outcome 都有：learned 先匹配
    outcome_only = write_memory(memory_dir, "-c-Code-proj--p2-abc", "20260913",
                                [record(learned=[], outcome="Shipped the fix.")])

    mat = trae_work_cn.parse(SourceRef(source="trae_work_cn", ref=f"{path}#L0", day=DAY))
    assert mat.meta["note_type"] == "reflection"

    mat2 = trae_work_cn.parse(SourceRef(
        source="trae_work_cn", ref=f"{outcome_only}#L0", day=date(2026, 9, 13)))
    assert mat2.meta["note_type"] == "progress"


def test_parse_unmatched_field_falls_back_to_reflection(
        memory_dir, monkeypatch):
    monkeypatch.setattr(
        config, "load_schema",
        lambda: {"trae_type_map": {"nonexistent_field": "progress"}},
    )
    path = write_memory(memory_dir, "-c-Code-proj--p2-abc", "20260912",
                        [record()])

    mat = trae_work_cn.parse(SourceRef(source="trae_work_cn", ref=f"{path}#L0", day=DAY))

    assert mat.meta["note_type"] == "reflection"  # 未匹配键落默认值


def test_parse_project_is_none_not_misdecoded(memory_dir, type_map):
    """项目目录名编码不可靠还原（'-' 歧义）→ 宁缺毋错，project 留空。"""
    path = write_memory(memory_dir, "-c-Code-first-rag--p2-abc", "20260912",
                        [record()])
    mat = trae_work_cn.parse(SourceRef(source="trae_work_cn", ref=f"{path}#L0", day=DAY))
    assert mat.meta["project"] is None


def test_registered_in_registry():
    from src.plugins import iter_plugins
    names = [p.name for p in iter_plugins()]
    assert "trae_work_cn" in names
