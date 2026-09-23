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


def _ok_result_event(ts, stdout):
    """工具跑通了：没有 is_error 的 tool_result —— 挣扎在这里结束。"""
    return _event(ts, "user", message={"content": [
        {"type": "tool_result", "content": stdout}]})


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
    """连续三次工具失败 = 3 轮挣扎 —— 哪怕中间夹着 assistant 叙述。

    真实的 Claude Code 会话里 user / assistant 是严格交替的：每次工具报错都以
    assistant 的一条消息回应，再来下一次 tool_result。所以"报错→助手→报错"就是
    三次连续失败；只有工具**成功**才算挣扎结束（与 codex 插件同一套语义，那边
    在模块 docstring 里明写了「assistant 叙述不打断失败连击」）。

    之前这条用的是三条报错紧挨着的夹具 —— 那种形状真实日志里不会出现，所以它
    一直是绿的，而实现里「见到 assistant 就归零」的 bug 让真数据永远只算出 1，
    config 里的 struggle_rounds=3 阈值等于没有（FR-008 失效）。
    """
    path = write_session(sessions_dir, "proj-a", "s1.jsonl", [
        _user_event(TS, "为什么 upsert 报 409"),
        _error_result_event(TS, "fail 1"),
        _assistant_event(TS, "维度可能不一致，我换个写法"),
        _error_result_event(TS, "fail 2"),
        _assistant_event(TS, "再看看 collection 配置"),
        _error_result_event(TS, "fail 3"),
    ])
    from src.plugins import SourceRef
    mat = claude_code.parse(SourceRef(source="claude_code", ref=str(path), day=DAY))
    assert mat.meta["struggle_rounds"] == 3


def test_a_successful_tool_result_ends_the_struggle(sessions_dir):
    """工具成功一次 = 连击归零，之后的失败从 1 重新数。

    不这么做的话，一个「先失败、修好了、后面又失败一次」的长会话会被算成连续
    挣扎，把已经解决的问题记成没解决 —— 而挣扎轮次是要喂蒸馏去判"重复踩坑"的。
    """
    path = write_session(sessions_dir, "proj-a", "s1.jsonl", [
        _error_result_event(TS, "fail 1"),
        _ok_result_event(TS, "all good"),
        _error_result_event(TS, "fail 2"),
    ])
    from src.plugins import SourceRef
    mat = claude_code.parse(SourceRef(source="claude_code", ref=str(path), day=DAY))
    assert mat.meta["struggle_rounds"] == 1
    assert mat.meta["error_count"] == 2


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
