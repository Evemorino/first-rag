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


def test_assistant_narration_does_not_break_a_struggle_run(sessions_dir):
    """助手自己的叙述不算"挣扎结束"——它是每次报错之后的必然产物。

    真实会话里 error 与 assistant 发言交替出现，所以把 assistant 也当结束信号，
    struggle_rounds 永远只数到 1，config 里的 struggle_rounds=3 等于没有
    （FR-008 失效）。与 claude_code / codex 同一条语义：助手叙述不打断失败连击。

    kimi 的 wire 词表里没有 tool 结果事件（`_classify` 只认得出 message / error），
    所以这里能用的结束信号只有「人重新发了一句话」——上面那条测试守的就是它。
    """
    d = write_session(sessions_dir, "wd_a", "session_1", [
        error_event(TS, "upsert 409"),
        msg(TS, "assistant", "维度可能不一致，我换个写法"),
        error_event(TS, "upsert 409 again"),
        msg(TS, "assistant", "再看看 collection 配置"),
        error_event(TS, "upsert 409 第三次"),
    ])

    mat = kimi_code.parse(SourceRef(source="kimi_code", ref=str(d), day=DAY))

    assert mat.meta["struggle_rounds"] == 3
    assert mat.meta["error_count"] == 3


def test_typed_assistant_message_does_not_break_a_struggle_run(sessions_dir):
    """Shape C（{type: assistant_message}）也得同样处理：它走 _classify 的另一条分支。

    只修 role 那条分支的话，用 {type: *_message} 记录会话的产品照样塌成 1，
    而这两种形状在同一个文件里混着出现（见 test_parse_extracts_all_message_shapes）。
    """
    d = write_session(sessions_dir, "wd_a", "session_1", [
        error_event(TS, "tool crashed"),
        typed_msg(TS, "assistant_message", "让我换个思路"),
        error_event(TS, "tool crashed again"),
    ])

    mat = kimi_code.parse(SourceRef(source="kimi_code", ref=str(d), day=DAY))

    assert mat.meta["struggle_rounds"] == 2


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
