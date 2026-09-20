"""T016 core tests for distillation orchestration."""

import json
from datetime import date, datetime

import pytest

from src import collect, config, distill
from src.collect import DayRaw
from src.plugins import RawMaterial


SCHEMA = {
    "types": [
        {"name": "progress", "desc": "Concrete work with an observable outcome."},
        {"name": "error", "desc": "A failure worth remembering."},
        {"name": "idea", "desc": "A thought not yet acted on."},
        {"name": "reflection", "desc": "Meta-learning from the day."},
    ],
    "distill": {
        "include_signals": ["decisions made and why"],
        "exclude_signals": ["routine file edits"],
        "examples": {
            "keep": ["Chose uuid5 to make re-sync idempotent"],
            "drop": ["Ran pytest, 42 passed"],
        },
        "novelty_threshold": 0.82,
        "max_entries_per_day": 3,
        "struggle_rounds": 3,
        "max_raw_chars": 200000,
    },
    "retrieval": {
        "top_k": 8,
        "expand": {
            "mode": "all",
            "neighbor_limit_per_hit": 2,
            "context_cap": 12,
            "neighbor_min_score": None,
        },
    },
    "trae_type_map": {"learned": "reflection", "outcome": "progress"},
    "raw_retention_days": 90,
}


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RAW_DIR", tmp_path / "raw")
    monkeypatch.setattr(distill.config, "load_schema", lambda: SCHEMA)
    monkeypatch.setattr(
        distill.config,
        "env",
        lambda name: {"CHAT_MODEL": "test-model"}[name],
    )


def make_day_raw(materials: list[RawMaterial]) -> DayRaw:
    return DayRaw(
        day=date(2026, 9, 20),
        collected_at=datetime(2026, 9, 20, 12, 0, 0),
        materials=materials,
    )


def make_material(
    text: str = "Learned something useful.",
    *,
    source: str = "claude_code",
    ref: str = "session-a",
    kind: str = "message",
    meta: dict | None = None,
) -> RawMaterial:
    return RawMaterial(
        source=source,
        ref=ref,
        ts=datetime(2026, 9, 20, 10, 0, 0),
        kind=kind,
        text=text,
        meta=meta or {},
    )


def candidate(
    text: str,
    *,
    type_name: str = "progress",
    ref: str = "session-a",
) -> dict:
    return {
        "text": text,
        "type": type_name,
        "tags": ["learning", "rag"],
        "source_refs": [ref],
    }


def load_snapshot(day: date) -> dict:
    return json.loads(collect.snapshot_path(day).read_text(encoding="utf-8"))


def test_manual_note_bypasses_llm_and_preserves_type(
    isolated_config, monkeypatch
):
    calls = []
    monkeypatch.setattr(
        distill,
        "chat",
        lambda *args, **kwargs: calls.append((args, kwargs)) or "{}",
    )
    day_raw = make_day_raw([
        make_material(
            "A quick idea",
            source="manual",
            ref="inbox.md",
            kind="note",
            meta={"note_type": "idea"},
        )
    ])

    entries = distill.distill(day_raw)

    assert calls == []
    assert len(entries) == 1
    assert entries[0].type == "idea"
    assert entries[0].source == "manual"
    assert entries[0].source_refs == ["inbox.md"]
    assert entries[0].tags == ["manual", "idea"]
    assert entries[0].distill_version.startswith("direct+rubric@")
    assert load_snapshot(day_raw.day)["distill_run"]["status"] == "ok"


def test_trae_record_bypasses_llm(isolated_config, monkeypatch):
    calls = []
    monkeypatch.setattr(
        distill,
        "chat",
        lambda *args, **kwargs: calls.append((args, kwargs)) or "{}",
    )
    day_raw = make_day_raw([
        make_material(
            "Trae already summarized this",
            source="trae",
            ref="session-memory.jsonl",
            kind="trae_record",
            meta={"note_type": "progress"},
        )
    ])

    entries = distill.distill(day_raw)

    assert calls == []
    assert len(entries) == 1
    assert entries[0].type == "progress"
    assert entries[0].source == "trae"


def test_sanitizes_materials_before_llm_and_snapshot(
    isolated_config, monkeypatch, caplog
):
    secret = "sk-test-" + ("x" * 24)
    captured_messages = []

    def fake_chat(messages, json_mode=False):
        captured_messages.append((messages, json_mode))
        return json.dumps({"entries": [candidate("Sanitized learning")]})

    monkeypatch.setattr(distill, "chat", fake_chat)
    day_raw = make_day_raw([
        make_material(f"api_key={secret}\nKeep the lesson.")
    ])

    entries = distill.distill(day_raw)

    assert len(entries) == 1
    assert secret not in json.dumps(captured_messages, ensure_ascii=False)
    assert secret not in json.dumps(load_snapshot(day_raw.day), ensure_ascii=False)
    assert secret not in caplog.text
    assert "[REDACTED]" in day_raw.materials[0].text


def test_valid_llm_entry_uses_traceable_material_metadata(
    isolated_config, monkeypatch
):
    monkeypatch.setattr(
        distill,
        "chat",
        lambda messages, json_mode=False: json.dumps({
            "entries": [candidate("Learned the root cause.")]
        }),
    )
    day_raw = make_day_raw([
        make_material(meta={"project": "first-rag", "struggle_rounds": 3})
    ])

    entries = distill.distill(day_raw)

    assert len(entries) == 1
    assert entries[0].text == "Learned the root cause."
    assert entries[0].type == "progress"
    assert entries[0].date == "2026-09-20"
    assert entries[0].source == "claude_code"
    assert entries[0].project == "first-rag"
    assert entries[0].source_refs == ["session-a"]
    assert entries[0].distill_version.startswith("test-model+rubric@")
    snapshot = load_snapshot(day_raw.day)
    assert snapshot["distill_run"]["model"] == "test-model"
    assert snapshot["distill_run"]["status"] == "ok"


def test_unknown_type_retries_once_with_correction(
    isolated_config, monkeypatch
):
    responses = iter([
        json.dumps({"entries": [
            candidate("Valid entry"),
            candidate("Bad type entry", type_name="banana"),
        ]}),
        json.dumps({"entries": [
            candidate("Corrected entry", type_name="error"),
        ]}),
    ])
    calls = []

    def fake_chat(messages, json_mode=False):
        calls.append(messages)
        return next(responses)

    monkeypatch.setattr(distill, "chat", fake_chat)
    day_raw = make_day_raw([make_material()])

    entries = distill.distill(day_raw)

    assert [entry.text for entry in entries] == [
        "Valid entry",
        "Corrected entry",
    ]
    assert len(calls) == 2
    correction = calls[1][-1]["content"]
    assert "banana" in correction
    assert "progress" in correction
    assert "error" in correction


def test_unknown_type_is_dropped_after_retry(
    isolated_config, monkeypatch, caplog
):
    bad = json.dumps({"entries": [candidate("Bad", type_name="banana")]})
    monkeypatch.setattr(
        distill,
        "chat",
        lambda messages, json_mode=False: bad,
    )
    day_raw = make_day_raw([make_material()])

    entries = distill.distill(day_raw)

    assert entries == []
    assert "unknown type" in caplog.text.lower()
    assert load_snapshot(day_raw.day)["distill_run"]["status"] == "ok"


def test_invalid_json_retries_once(isolated_config, monkeypatch):
    responses = iter([
        "not json",
        json.dumps({"entries": [candidate("Recovered entry")]}),
    ])
    calls = []
    monkeypatch.setattr(
        distill,
        "chat",
        lambda messages, json_mode=False: calls.append(messages) or next(responses),
    )
    day_raw = make_day_raw([make_material()])

    entries = distill.distill(day_raw)

    assert [entry.text for entry in entries] == ["Recovered entry"]
    assert len(calls) == 2


def test_invalid_json_marks_snapshot_failed_after_retry(
    isolated_config, monkeypatch
):
    monkeypatch.setattr(
        distill,
        "chat",
        lambda messages, json_mode=False: "still not json",
    )
    day_raw = make_day_raw([make_material()])

    with pytest.raises(distill.DistillError):
        distill.distill(day_raw)

    assert load_snapshot(day_raw.day)["distill_run"]["status"] == "failed"


def test_circuit_breaker_caps_entries_and_logs(
    isolated_config, monkeypatch, caplog
):
    entries = [
        candidate(f"Entry {index}", ref=f"session-{index}")
        for index in range(4)
    ]
    day_raw = make_day_raw([
        make_material(ref=f"session-{index}")
        for index in range(4)
    ])
    monkeypatch.setattr(
        distill,
        "chat",
        lambda messages, json_mode=False: json.dumps({"entries": entries}),
    )

    result = distill.distill(day_raw)

    assert len(result) == 3
    assert "circuit breaker" in caplog.text.lower()


def test_no_materials_is_noop_without_llm_call(
    isolated_config, monkeypatch
):
    calls = []
    monkeypatch.setattr(
        distill,
        "chat",
        lambda *args, **kwargs: calls.append((args, kwargs)) or "{}",
    )
    day_raw = make_day_raw([])

    entries = distill.distill(day_raw)

    assert entries == []
    assert calls == []
    assert load_snapshot(day_raw.day)["distill_run"]["status"] == "noop"
