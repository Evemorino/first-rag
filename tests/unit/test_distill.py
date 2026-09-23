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


def test_sanitizes_manual_note_in_entry_snapshot_and_logs(
    isolated_config, monkeypatch, caplog
):
    secret = "sk-test-" + ("y" * 24)
    calls = []
    monkeypatch.setattr(
        distill,
        "chat",
        lambda *args, **kwargs: calls.append((args, kwargs)) or "{}",
    )
    day_raw = make_day_raw([
        make_material(
            f"Remember api_key={secret}",
            source="manual",
            ref="inbox.md",
            kind="note",
            meta={"note_type": "reflection"},
        )
    ])

    entries = distill.distill(day_raw)

    assert calls == []
    assert len(entries) == 1
    assert secret not in entries[0].text
    assert "[REDACTED]" in entries[0].text
    assert secret not in json.dumps(load_snapshot(day_raw.day), ensure_ascii=False)
    assert secret not in caplog.text


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
    first_response = json.dumps({"entries": [
        candidate("Valid entry"),
        candidate("Bad type entry", type_name="banana"),
    ]})
    responses = iter([
        first_response,
        json.dumps({"entries": [
            candidate("Corrected entry", type_name="error"),
        ]}),
    ])
    calls = []
    json_modes = []

    def fake_chat(messages, json_mode=False):
        calls.append(messages)
        json_modes.append(json_mode)
        return next(responses)

    monkeypatch.setattr(distill, "chat", fake_chat)
    day_raw = make_day_raw([make_material()])

    entries = distill.distill(day_raw)

    assert [entry.text for entry in entries] == [
        "Valid entry",
        "Corrected entry",
    ]
    assert len(calls) == 2
    # 追问也必须要求 JSON：拿回自由文本会被当成 invalid JSON，条目静默丢掉。
    assert json_modes == [True, True]
    # distill_version 是溯源字段（`模型+rubric@哈希`），追问出来的条目不能变成 None
    assert all(e.distill_version.startswith("test-model+rubric@")
               for e in entries)
    correction = calls[1][-1]["content"]
    assert "banana" in correction
    assert "progress" in correction
    assert "error" in correction
    # 追问必须把模型上一次的原始输出原样当 assistant 轮带回去。丢了它，
    # 模型看不到自己写了哪几条、错在哪，只能凭 allowed_types 瞎猜，改不对
    # 的条目会被静默丢掉（FR-012）—— 而"追问确实发了"这个断言还是绿的。
    assert calls[1][-2] == {"role": "assistant", "content": first_response}


def test_unknown_type_is_dropped_after_retry(
    isolated_config, monkeypatch, caplog
):
    responses = iter([
        json.dumps({"entries": [
            candidate("Valid entry"),
            candidate("Bad type entry", type_name="banana"),
        ]}),
        json.dumps({"entries": [
            candidate("Still bad", type_name="banana"),
        ]}),
    ])
    calls = []
    monkeypatch.setattr(
        distill,
        "chat",
        lambda messages, json_mode=False: calls.append(messages) or next(responses),
    )
    day_raw = make_day_raw([make_material()])

    entries = distill.distill(day_raw)

    assert [entry.text for entry in entries] == ["Valid entry"]
    assert len(calls) == 2
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

    with pytest.raises(distill.DistillError) as excinfo:
        distill.distill(day_raw)

    assert str(excinfo.value) == "LLM returned invalid JSON"
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
    assert "capped entries from 4 to 3" in caplog.text
    assert load_snapshot(day_raw.day)["distill_run"]["status"] == "ok"


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


def test_direct_type_defaults_to_reflection_without_note_type():
    """meta 里没写 note_type 时默认 reflection —— 默认值是契约的一部分。"""
    material = make_material("A note", source="manual", ref="inbox.md",
                             kind="note")

    assert distill._direct_type(material, ["progress", "reflection"]) == "reflection"


def test_direct_type_falls_back_to_reflection_when_requested_type_is_unknown():
    """手记写了 schema 里没有的 note_type → 兜底成 reflection，不是报错。

    这条兜底分支此前一行断言都没有：三个变异体（`"REFLECTION" in`、
    `not in`、`allowed_types[1]`）全部存活，也就是说"未知类型会变成什么"
    完全没被定义过 —— 而它直接决定这条记忆以后能被哪种过滤检索到。
    """
    material = make_material(
        "A note", source="manual", ref="inbox.md", kind="note",
        meta={"note_type": "nonsense"})

    assert distill._direct_type(material, ["progress", "reflection"]) == "reflection"


def test_direct_type_falls_back_to_first_allowed_type_without_reflection():
    """schema 里连 reflection 都没有 → 用第一个允许的类型，不能凭空造一个。

    兜底成硬编码的 "reflection" 会让 payload 里出现一个 schema 不允许的
    type：以后按类型过滤永远检索不到它，而且不报错。
    """
    material = make_material(
        "A note", source="manual", ref="inbox.md", kind="note",
        meta={"note_type": "nonsense"})

    assert distill._direct_type(material, ["progress", "error"]) == "progress"


def test_unknown_note_type_still_produces_a_retrievable_entry(
        isolated_config, monkeypatch):
    monkeypatch.setattr(
        distill, "chat", lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("手记不该走 LLM")))
    day_raw = make_day_raw([
        make_material("A note", source="manual", ref="inbox.md",
                      kind="note", meta={"note_type": "nonsense"})])

    entries = distill.distill(day_raw)

    assert [e.type for e in entries] == ["reflection"]
