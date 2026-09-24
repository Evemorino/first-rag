"""T016 core tests for distillation orchestration."""

import hashlib
import json
import re
from datetime import date, datetime, timedelta

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
        # 默认给到很大，好让既有测试仍然是"一次往返"；专测分批的用例自己改小。
        "batch_max_chars": 100000,
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


def test_direct_type_defaults_to_reflection_without_note_type(caplog):
    """meta 里没写 note_type 时默认 reflection —— 且**不该**走未知类型分支。

    只断言返回值不够：把默认值写成 `"REFLECTION"`，它会因为不在 allowed 里而
    落进未知类型兜底，最终返回值同样是 `"reflection"`，于是变异体存活
    （实测 `distill.x__direct_type__mutmut_9`）。差别只在多打一条 WARNING ——
    而那条告警是会说谎的：用户根本没写错类型，日志却告诉他写错了。
    """
    material = make_material("A note", source="manual", ref="inbox.md",
                             kind="note")

    with caplog.at_level("WARNING"):
        assert distill._direct_type(material, ["progress", "reflection"]) == "reflection"

    assert caplog.text == ""


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


# --- 变异测试分诊后补的：distill 的时区与两条跳过式循环 ---


def test_direct_entries_keep_going_after_unusable_materials():
    """不可用的素材排在前面时，后面的好素材仍要成型。

    对应存活的 `continue` → `break`（_direct_entries 里有两个 continue：kind 不
    对、文本为空）。原夹具只放一条 note 素材，两个 continue 一个都触发不了。
    """
    day_raw = make_day_raw([
        make_material("会话消息，不该直接成型", kind="message"),
        make_material("   ", kind="note", source="manual", ref="inbox.md"),
        make_material("真正的一条快记", kind="note", source="manual",
                      ref="inbox.md", meta={"note_type": "idea"}),
    ])

    entries = distill._direct_entries(
        day_raw, SCHEMA, "direct+rubric@x",
        datetime(2026, 9, 20, 12, 0, tzinfo=config.TZ))

    assert [e.text for e in entries] == ["真正的一条快记"]
    assert entries[0].type == "idea"


def test_dedupe_entries_keeps_entries_after_a_duplicate():
    """重复项夹在中间：`continue` 改成 `break` 会把后面的 B 一起丢掉。

    条目由 _direct_entries 真造出来（而不是手搓 Entry），这样 (source, date,
    text) 这个去重键是真实形状，测试也不会跟着数据结构漂移。
    """
    day_raw = make_day_raw([
        make_material("A", kind="note", source="manual", ref="inbox.md"),
        make_material("A", kind="note", source="manual", ref="inbox.md"),
        make_material("B", kind="note", source="manual", ref="inbox.md"),
    ])
    entries = distill._direct_entries(
        day_raw, SCHEMA, "direct+rubric@x",
        datetime(2026, 9, 20, 12, 0, tzinfo=config.TZ))

    kept = distill._dedupe_entries(entries)

    assert [e.text for e in kept] == ["A", "B"]


def test_direct_path_created_at_is_shanghai_aware(isolated_config):
    """条目的 created_at 必须带 Asia/Shanghai，不能退化成进程本地时间。

    对应存活的 `datetime.now(tz=config.TZ)` → `tz=None`。本机时区恰好也是 +08，
    所以不写这条断言的话，"created_at 是 aware 且偏移 +8" 这个 payload 契约
    （FR-015）从来没被测过。
    """
    entries = distill.distill(make_day_raw([
        make_material("一条快记", kind="note", source="manual", ref="inbox.md"),
    ]))

    assert entries[0].created_at.utcoffset() == timedelta(hours=8)


def test_llm_runs_once_per_batch_and_merges_entries(isolated_config, monkeypatch):
    """一批一次往返，条目全部合并 —— 修的是"一天一次请求被输出上限掐断"。

    实测天花板：单次 chat 在 ~6.9k 字符处 finish_reason='length'，JSON 断在
    半截字符串里，distill 报 "LLM returned invalid JSON" 且那次重试也断在
    8370/8651 字符（sync D=2026-09-18）。所以把预算调小、素材分三批跑。
    """
    monkeypatch.setitem(SCHEMA["distill"], "batch_max_chars", 60)
    prompts = []

    def fake_chat(messages, json_mode=False):
        body = messages[1]["content"]
        prompts.append(body)
        refs = [r for r in ("session-a", "session-b", "session-c")
                if f'"{r}"' in body]
        return json.dumps({"entries": [candidate(f"条目 {r}", ref=r) for r in refs]})

    monkeypatch.setattr(distill, "chat", fake_chat)
    day_raw = make_day_raw([
        make_material("x" * 50, ref="session-a"),
        make_material("y" * 50, ref="session-b"),
        make_material("z" * 50, ref="session-c"),
    ])

    entries = distill.distill(day_raw)

    assert len(prompts) == 3, "三批素材应该发三次请求"
    assert sorted(e.text for e in entries) == ["条目 session-a", "条目 session-b",
                                               "条目 session-c"]


def test_batch_without_llm_material_is_not_sent(isolated_config, monkeypatch):
    """只含直并入素材（快记/trae）的那一批不必浪费一次 LLM 往返。

    它们已经由 _direct_entries 原样入库；再送去蒸馏既费 token，又可能把同一段
    话蒸成第二条重复条目。
    """
    monkeypatch.setitem(SCHEMA["distill"], "batch_max_chars", 60)
    prompts = []

    def fake_chat(messages, json_mode=False):
        prompts.append(messages[1]["content"])
        return json.dumps({"entries": [candidate("来自 claude 的条目", ref="session-a")]})

    monkeypatch.setattr(distill, "chat", fake_chat)
    day_raw = make_day_raw([
        make_material("x" * 50, ref="session-a"),
        make_material("q" * 50, ref="inbox.md", source="manual",
                      kind="note", meta={"note_type": "idea"}),
    ])

    entries = distill.distill(day_raw)

    assert len(prompts) == 1
    assert "session-a" in prompts[0] and "inbox.md" not in prompts[0]
    assert len(entries) == 2  # claude 蒸出来的一条 + 快记直并入的一条


# --- distill_run 与 rubric_hash 的字面值契约 ---
#
# 2026-09-24 重判里 `distill` 是键名族最大的一簇（16 条）：model 的
# "none"/"direct"/"unknown"、status、以及 rubric_hash 的输入键。这些值决定
# 重蒸馏对照怎么判定条目是哪一版蒸出来的（FR-023），改一个字母不会让任何
# 现有测试响。


def test_empty_day_records_the_noop_run(isolated_config):
    distill.distill(make_day_raw([]))

    run = load_snapshot(date(2026, 9, 20))["distill_run"]
    assert set(run) == {"model", "rubric_hash", "status"}
    assert run["model"] == "none"
    assert run["status"] == "noop"
    assert re.fullmatch(r"[0-9a-f]{64}", run["rubric_hash"])


def test_direct_only_day_records_the_direct_model(isolated_config):
    """只有直并入素材时记 `direct`，不是 `none` 也不是聊天模型名。"""
    distill.distill(make_day_raw([
        make_material("一条快记", kind="note", source="manual",
                      ref="inbox.md", meta={"note_type": "idea"}),
    ]))

    run = load_snapshot(date(2026, 9, 20))["distill_run"]
    assert run["model"] == "direct"
    assert run["status"] == "ok"


def test_rubric_hash_covers_exactly_types_and_distill(isolated_config):
    """rubric_hash 只覆盖 types + distill 两节，且可独立重算。

    它是条目 distill_version 的一部分：换成别的键名（"TYPES"/"DISTILL"）或
    把 retrieval 也卷进来，都会悄悄改掉所有条目的版本号，重蒸馏对照就废了。
    """
    unrelated = json.loads(json.dumps(SCHEMA))
    unrelated["retrieval"]["top_k"] = 99

    assert distill._rubric_hash(unrelated) == distill._rubric_hash(SCHEMA)
    expected = hashlib.sha256(json.dumps(
        {"types": SCHEMA["types"], "distill": SCHEMA["distill"]},
        ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert distill._rubric_hash(SCHEMA) == expected


def test_failed_run_still_records_unknown_model_and_failed_status(isolated_config, monkeypatch):
    """异常路径也要留下可审计的运行记录：model=unknown、status=failed。

    `model = "unknown"` 是"还没读到 CHAT_MODEL 就炸了"的哨兵值；它被改成大写
    或 XX 包装时没有任何测试会响（对应 distill.x_distill__mutmut_3/4/5），
    而下一个人查"那天为什么没入库"全靠这行记录。
    """
    def boom():
        raise RuntimeError("schema unreadable")

    monkeypatch.setattr(distill.config, "load_schema", boom)

    with pytest.raises(RuntimeError):
        distill.distill(make_day_raw([make_material("随便一条")]))

    run = load_snapshot(date(2026, 9, 20))["distill_run"]
    assert run["model"] == "unknown"
    assert run["status"] == "failed"
    assert run["rubric_hash"] == ""
