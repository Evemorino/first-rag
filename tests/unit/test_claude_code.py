"""Unit tests for the claude_code plugin against fixture JSONL (T010).

The fixture mirrors the real event shape: timestamp (UTC 'Z'),
type user/assistant, message.content (str | blocks with tool_result).
Fixtures live in system tmp (constitution V).
"""

import json
from datetime import date, datetime, timedelta, timezone

import pytest

from src.plugins import claude_code

DAY = date(2026, 9, 18)
TZ = timezone(timedelta(hours=8))


def _event(ts, type_, **kw):
    return json.dumps({"type": type_, "timestamp": ts, "cwd": "/tmp/proj", **kw})


def _user_event(ts, text):
    return _event(ts, "user", message={"content": text})


def _assistant_event(ts, text):
    return _event(ts, "assistant", message={"content": [{"type": "text", "text": text}]})


def _error_result_event(ts, stderr):
    return _event(ts, "user", message={"content": [
        {"type": "tool_result", "is_error": True, "content": stderr}]})


@pytest.fixture
def sessions_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(claude_code, "SESSIONS_DIR", tmp_path)
    return tmp_path


def write_session(sessions_dir, project, name, lines):
    d = sessions_dir / project
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return d / name


# 2026-09-18 00:30 UTC = 08:30 in Shanghai — same local day
TS = "2026-09-18T00:30:00.000Z"
OLD_TS = "2026-09-10T00:30:00.000Z"


def test_discover_finds_day_sessions(sessions_dir):
    write_session(sessions_dir, "proj-a", "s1.jsonl", [_user_event(TS, "hello")])
    write_session(sessions_dir, "proj-a", "s2.jsonl", [_user_event(OLD_TS, "old")])
    refs = claude_code.discover(DAY)
    assert len(refs) == 1
    assert refs[0].source == "claude_code"
    assert refs[0].day == DAY
    assert refs[0].ref.endswith("s1.jsonl")


def test_discover_missing_dir_is_noop(tmp_path, monkeypatch):
    monkeypatch.setattr(claude_code, "SESSIONS_DIR", tmp_path / "nope")
    assert claude_code.discover(DAY) == []


def test_discover_warns_on_layout_drift(tmp_path, monkeypatch, caplog):
    """装了 claude（~/.claude 存在）但 projects/ 缺失 → 版本漂移警告（可观测性）。"""
    monkeypatch.setattr(claude_code, "SESSIONS_DIR", tmp_path / "projects")
    monkeypatch.setattr(claude_code, "CLAUDE_HOME", tmp_path / "home")
    (tmp_path / "home").mkdir()

    with caplog.at_level("WARNING"):
        assert claude_code.discover(DAY) == []

    assert any("drift" in r.message.lower() or "layout" in r.message.lower()
               for r in caplog.records
               if r.name.endswith("claude_code"))


def test_parse_ignores_summary_lines(sessions_dir):
    """文件头部的 summary 行不是学习素材，锁定忽略现状。"""
    path = write_session(sessions_dir, "proj-a", "s1.jsonl", [
        _event(TS, "summary", summary="Earlier session about other work"),
        _user_event(TS, "actual question"),
    ])
    from src.plugins import SourceRef
    mat = claude_code.parse(SourceRef(source="claude_code", ref=str(path), day=DAY))
    assert "Earlier session" not in mat.text
    assert "actual question" in mat.text


def test_parse_extracts_messages_and_errors(sessions_dir):
    path = write_session(sessions_dir, "proj-a", "s1.jsonl", [
        _user_event(TS, "why did upsert 409"),
        _assistant_event(TS, "dimension mismatch"),
        _error_result_event(TS, "Traceback: dimension is not equal"),
    ])
    from src.plugins import SourceRef
    mat = claude_code.parse(SourceRef(source="claude_code", ref=str(path), day=DAY))
    assert mat.kind == "message"
    assert "why did upsert 409" in mat.text
    assert "dimension mismatch" in mat.text
    assert "Traceback" in mat.text  # error text folded into the transcript
    assert mat.meta["cwd"] == "/tmp/proj"
    assert mat.meta["error_count"] == 1
    assert mat.ts.date() == DAY
    assert mat.ts.tzinfo is not None


def test_parse_counts_struggle_rounds(sessions_dir):
    path = write_session(sessions_dir, "proj-a", "s1.jsonl", [
        _error_result_event(TS, "fail 1"),
        _error_result_event(TS, "fail 2"),
        _error_result_event(TS, "fail 3"),
    ])
    from src.plugins import SourceRef
    mat = claude_code.parse(SourceRef(source="claude_code", ref=str(path), day=DAY))
    assert mat.meta["struggle_rounds"] == 3


def test_parse_skips_other_days(sessions_dir):
    path = write_session(sessions_dir, "proj-a", "s1.jsonl", [
        _user_event(OLD_TS, "old day"),
        _user_event(TS, "today"),
    ])
    from src.plugins import SourceRef
    mat = claude_code.parse(SourceRef(source="claude_code", ref=str(path), day=DAY))
    assert "old day" not in mat.text
    assert "today" in mat.text


# --- 错误信号提取：蒸馏靠它判断"这一天踩了什么坑"（FR-002）---


@pytest.mark.parametrize("event, expected", [
    ({"is_error": True}, True),
    ({"toolUseResult": {"is_error": True}}, True),
    ({"toolUseResult": {"stderr": "Traceback: boom"}}, True),
    ({"toolUseResult": {"stdout": "all good"}}, False),
    ({"toolUseResult": "not-a-dict"}, False),
    ({}, False),
], ids=["flagged", "result-flagged", "stderr", "stdout-only",
        "non-dict-result", "empty"])
def test_is_error_detects_failure_signals(event, expected):
    assert claude_code._is_error(event) is expected


def test_error_text_prefers_content_over_tool_result():
    event = {"content": "permission denied",
             "toolUseResult": {"stderr": "secondary"}}

    assert claude_code._error_text(event) == "permission denied"


@pytest.mark.parametrize("result, expected", [
    ({"stderr": "from stderr"}, "from stderr"),
    ({"stdout": "from stdout"}, "from stdout"),
])
def test_error_text_falls_back_to_result_streams(result, expected):
    assert claude_code._error_text({"toolUseResult": result}) == expected


def test_error_text_stringifies_when_no_stream_available():
    """既没 stderr 也没 stdout 时，退到 str(result) —— 总比丢掉这次失败强。"""
    result = {"exit_code": 1}

    assert claude_code._error_text({"toolUseResult": result}) == str(result)


def test_error_text_serializes_non_string_content():
    event = {"content": [{"type": "text", "text": "boom"}]}

    assert claude_code._error_text(event) == json.dumps(
        [{"type": "text", "text": "boom"}], ensure_ascii=False)


def test_error_text_truncates_very_long_output():
    long_text = "x" * 5000

    result = claude_code._error_text({"content": long_text})

    assert len(result) == 2001
    assert result.endswith("…")


def test_registered_in_registry():
    from src.plugins import iter_plugins
    names = [p.name for p in iter_plugins()]
    assert "claude_code" in names
