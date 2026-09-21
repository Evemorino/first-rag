"""Unit tests for the kimi_code plugin against fixture sessions (T022).

Fixture mirrors research.md: sessions/wd_<ws>/session_<id>/ holds
state.json (cwd/title/createdAt) + agents/main/wire.jsonl event stream.
The wire format tolerates the common event shapes documented in the plugin.
"""

import json
from datetime import date

import pytest

from src.plugins import SourceRef, kimi_code

DAY = date(2026, 9, 18)
TS = "2026-09-17T16:30:00.000Z"        # Shanghai 2026-09-18 00:30
OLD_TS = "2026-09-10T02:00:00.000Z"    # Shanghai 2026-09-10


@pytest.fixture
def sessions_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(kimi_code, "SESSIONS_DIR", tmp_path)
    return tmp_path


def write_session(sessions_dir, ws: str, sid: str, events, state=None):
    d = sessions_dir / ws / sid
    (d / "agents" / "main").mkdir(parents=True, exist_ok=True)
    state = {"cwd": "C:\\work\\proj", "title": "fix bug",
             "createdAt": TS} | (state or {})
    (d / "state.json").write_text(
        json.dumps(state), encoding="utf-8")
    (d / "agents" / "main" / "wire.jsonl").write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n",
        encoding="utf-8")
    return d


def msg(ts, role, text):
    """Shape A: flat {timestamp, role, content}."""
    return {"timestamp": ts, "role": role, "content": text}


def block_msg(ts, role, text):
    """Shape B: claude-style content blocks."""
    return {"timestamp": ts, "role": role,
            "content": [{"type": "text", "text": text}]}


def typed_msg(ts, type_, text):
    """Shape C: {type: user_message/assistant_message, text}."""
    return {"timestamp": ts, "type": type_, "text": text}


def error_event(ts, message):
    return {"timestamp": ts, "type": "error", "message": message}


def test_discover_finds_local_day_sessions(sessions_dir):
    write_session(sessions_dir, "wd_a", "session_1", [msg(TS, "user", "hi")])
    write_session(sessions_dir, "wd_a", "session_2", [msg(OLD_TS, "user", "old")])

    refs = kimi_code.discover(DAY)

    assert len(refs) == 1
    assert refs[0].source == "kimi_code"
    assert refs[0].ref.endswith("session_1")


def test_discover_missing_dir_is_noop(tmp_path, monkeypatch):
    monkeypatch.setattr(kimi_code, "SESSIONS_DIR", tmp_path / "nope")
    assert kimi_code.discover(DAY) == []


def test_parse_extracts_all_message_shapes(sessions_dir):
    d = write_session(sessions_dir, "wd_a", "session_1", [
        msg(TS, "user", "flat user text"),
        block_msg(TS, "assistant", "block assistant text"),
        typed_msg(TS, "user_message", "typed user text"),
        typed_msg(TS, "assistant_message", "typed assistant text"),
    ])

    mat = kimi_code.parse(SourceRef(source="kimi_code", ref=str(d), day=DAY))

    assert mat.kind == "message"
    for piece in ("flat user text", "block assistant text",
                  "typed user text", "typed assistant text"):
        assert piece in mat.text
    assert mat.meta["cwd"] == "C:\\work\\proj"
    assert mat.meta["title"] == "fix bug"
    assert mat.meta["session_id"] == "session_1"
    assert mat.ts.tzinfo is not None
    assert mat.ts.date() == DAY


def test_parse_collects_errors_and_struggle(sessions_dir):
    d = write_session(sessions_dir, "wd_a", "session_1", [
        error_event(TS, "tool crashed: exit 1"),
        error_event(TS, "tool crashed again"),
        msg(TS, "user", "retry"),
        error_event(TS, "isolated later crash"),  # 被消息隔开：不累计连续轮次
    ])

    mat = kimi_code.parse(SourceRef(source="kimi_code", ref=str(d), day=DAY))

    assert mat.meta["error_count"] == 3
    assert mat.meta["struggle_rounds"] == 2  # 最大连续报错轮次
    assert "tool crashed" in mat.text


def test_parse_skips_other_days(sessions_dir):
    d = write_session(sessions_dir, "wd_a", "session_1", [
        msg(OLD_TS, "user", "old day"),
        msg(TS, "user", "today"),
    ])

    mat = kimi_code.parse(SourceRef(source="kimi_code", ref=str(d), day=DAY))

    assert "old day" not in mat.text
    assert "today" in mat.text


def test_parse_tolerates_missing_state_json(sessions_dir):
    d = write_session(sessions_dir, "wd_a", "session_1", [msg(TS, "user", "hi")])
    (d / "state.json").unlink()

    mat = kimi_code.parse(SourceRef(source="kimi_code", ref=str(d), day=DAY))

    assert "hi" in mat.text
    assert mat.meta["cwd"] is None


def test_registered_in_registry():
    from src.plugins import iter_plugins
    names = [p.name for p in iter_plugins()]
    assert "kimi_code" in names
