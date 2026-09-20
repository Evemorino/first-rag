"""Unit tests for the codex plugin against fixture JSONL (T021).

Fixture mirrors the real rollout-*.jsonl shape (research.md):
top-level {timestamp, ordinal, type, payload}; session_meta carries cwd;
response_item carries role messages; event_msg task_complete carries errors.
"""

import json
from datetime import date

import pytest

from src.plugins import codex
from src.plugins import SourceRef

DAY = date(2026, 9, 18)


def line(type_: str, payload: dict, ts: str, ordinal: int = 0) -> str:
    return json.dumps(
        {"timestamp": ts, "ordinal": ordinal, "type": type_, "payload": payload})


def session_meta(cwd="C:\\work\\proj"):
    return ("session_meta", {
        "session_id": "sid-123", "cwd": cwd, "cli_version": "0.147.0",
    })


def user_msg(text):
    return ("response_item", {
        "type": "message", "role": "user",
        "content": [{"type": "input_text", "text": text}],
    })


def assistant_msg(text):
    return ("response_item", {
        "type": "message", "role": "assistant",
        "content": [{"type": "output_text", "text": text}],
    })


def developer_msg():
    return ("response_item", {
        "type": "message", "role": "developer",
        "content": [{"type": "input_text", "text": "You are Codex …"}],
    })


def task_error(message):
    return ("event_msg", {
        "type": "task_complete", "last_agent_message": None,
        "error": {"message": message},
    })


# 2026-09-17T16:30Z = 2026-09-18 00:30 Shanghai — belongs to DAY
# 2026-09-10T02:00Z = 2026-09-10 10:00 Shanghai — other day
TS = "2026-09-17T16:30:00.000Z"
OLD_TS = "2026-09-10T02:00:00.000Z"


@pytest.fixture
def sessions_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(codex, "SESSIONS_DIR", tmp_path)
    return tmp_path


def write_rollout(sessions_dir, ymd: str, name: str, events):
    d = sessions_dir / ymd.replace("-", "/")
    d.mkdir(parents=True, exist_ok=True)
    lines = []
    for i, event in enumerate(events):
        if len(event) == 2:  # ((type, payload), ts) convenience form
            (type_, payload), ts = event
        else:
            type_, payload, ts = event
        lines.append(line(type_, payload, ts, ordinal=i))
    path = d / name
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_discover_finds_local_day_sessions(sessions_dir):
    # UTC date dir is 09/17 but the event lands on Shanghai 09/18
    write_rollout(sessions_dir, "2026-09-17", "rollout-a.jsonl", [
        (session_meta(), TS),
        (user_msg("hello"), TS),
    ])
    write_rollout(sessions_dir, "2026-09-10", "rollout-old.jsonl", [
        (user_msg("old"), OLD_TS),
    ])

    refs = codex.discover(DAY)

    assert len(refs) == 1
    assert refs[0].source == "codex"
    assert refs[0].ref.endswith("rollout-a.jsonl")


def test_discover_missing_dir_is_noop(tmp_path, monkeypatch):
    monkeypatch.setattr(codex, "SESSIONS_DIR", tmp_path / "nope")
    assert codex.discover(DAY) == []


def test_parse_extracts_user_assistant_skips_developer(sessions_dir):
    path = write_rollout(sessions_dir, "2026-09-17", "rollout-a.jsonl", [
        (session_meta(), TS),
        (developer_msg(), TS),
        (user_msg("why did the api return 403"), TS),
        (assistant_msg("the quota was exhausted"), TS),
    ])

    mat = codex.parse(SourceRef(source="codex", ref=str(path), day=DAY))

    assert mat.kind == "message"
    assert "why did the api return 403" in mat.text
    assert "the quota was exhausted" in mat.text
    assert "You are Codex" not in mat.text  # developer instructions dropped
    assert mat.meta["cwd"] == "C:\\work\\proj"
    assert mat.meta["session_id"] == "sid-123"
    assert mat.ts.tzinfo is not None
    assert mat.ts.date() == DAY


def test_parse_collects_errors_and_struggle(sessions_dir):
    path = write_rollout(sessions_dir, "2026-09-17", "rollout-a.jsonl", [
        (session_meta(), TS),
        (task_error("403 quota exhausted"), TS),
        (user_msg("retry"), TS),
        (task_error("403 again"), TS),
    ])

    mat = codex.parse(SourceRef(source="codex", ref=str(path), day=DAY))

    assert mat.meta["error_count"] == 2
    assert mat.meta["struggle_rounds"] == 2
    assert "403 quota exhausted" in mat.text


def test_parse_skips_other_days(sessions_dir):
    path = write_rollout(sessions_dir, "2026-09-10", "rollout-old.jsonl", [
        (session_meta(), OLD_TS),
        (user_msg("old day"), OLD_TS),
        (user_msg("today"), TS),
    ])

    mat = codex.parse(SourceRef(source="codex", ref=str(path), day=DAY))

    assert "old day" not in mat.text
    assert "today" in mat.text


def test_parse_drops_injected_user_boilerplate(sessions_dir):
    path = write_rollout(sessions_dir, "2026-09-17", "rollout-a.jsonl", [
        (session_meta(), TS),
        (user_msg("# AGENTS.md instructions for C:\\proj\n…"), TS),
        (user_msg("<skill>\n<name>speckit-implement</name>…"), TS),
        (user_msg("<environment_context>\ncwd…"), TS),
        (user_msg("$speckit-implement 继续执行"), TS),  # 命令+真实意图：保留
    ])

    mat = codex.parse(SourceRef(source="codex", ref=str(path), day=DAY))

    assert "AGENTS.md instructions" not in mat.text
    assert "<skill>" not in mat.text
    assert "<environment_context>" not in mat.text
    assert "$speckit-implement 继续执行" in mat.text


def test_registered_in_registry():
    from src.plugins import iter_plugins
    names = [p.name for p in iter_plugins()]
    assert "codex" in names
