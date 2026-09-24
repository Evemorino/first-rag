"""T018: AC 专项验收测试（AC-012 / AC-013 / AC-014）。

AC-012: 植入伪造密钥的素材经蒸馏后，条目、原始快照与日志均不含密钥串。
AC-013: 配置外类型的条目重试一次后丢弃并留日志，其余条目正常产出。
AC-014: 条数熔断（临时上限 3）恰好放行上限数量、留日志、正常结束。
"""

import json
import re
from datetime import date, datetime

import pytest

from src import collect, config, distill
from src.collect import DayRaw
from src.plugins import RawMaterial

DAY = date(2026, 9, 20)

# 临时上限 3（AC-014 的验收措辞）
SCHEMA = {
    "types": [
        {"name": "progress", "desc": "Concrete work."},
        {"name": "error", "desc": "A failure worth remembering."},
        {"name": "reflection", "desc": "Meta-learning."},
    ],
    "distill": {
        "include_signals": ["decisions made and why"],
        "exclude_signals": ["routine edits"],
        "examples": {"keep": ["Chose uuid5"], "drop": ["Ran pytest"]},
        "novelty_threshold": 0.82,
        "max_entries_per_day": 3,
        "struggle_rounds": 3,
        "max_raw_chars": 200000,
        # 分批预算给到很大：这些 AC 用例要的是"一次往返"的语义，分批另有专测。
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
    "trae_type_map": {},
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


# --- AC-012: 伪造密钥 fixture ---

FAKE_SECRETS = {
    # openai 风格 sk- key（注意 sk- 后首字符需为字母数字）
    "sk_key": "sk-proj" + "a" * 30,
    # github classic / fine-grained token
    "ghp_key": "ghp_" + "b" * 36,
    "gh_pat": "github_pat_" + "c" * 30,
    # AWS access key id
    "aws_key": "AKIA" + "ABCDEFGH12345678",
    # JWT
    "jwt": "eyJ" + "d" * 16 + "." + "e" * 16 + "." + "f" * 16,
    # 赋值形式（明文与带引号）
    "assigned": "api_key = \"super-secret-value-42\"",
    "assigned_quoted": 'password: "hunter2hunter2"',
    "plain_pwd": "passwd=hunter2hunter2",
    # 方舟 Agent/Coding Plan 的密钥形态。AC-012 原来只用 sk- 家族的夹具，
    # 而本项目真实持有的那一种恰好不在任何一条正则里 —— 换模型/换厂商时
    # "脱敏过"这个结论会静默失效，所以把真实形态也钉进端到端用例。
    "ark_key": "ar" + "k-" + "9" * 42,
    "ark_assigned": "ARK_API_KEY=" + "ar" + "k-" + "8" * 42,
}


def note_text() -> str:
    parts = [
        f"分享一个踩坑：当时配置写成了 {FAKE_SECRETS['sk_key']} 导致泄漏。",
        f"还有 {FAKE_SECRETS['ghp_key']} 和 {FAKE_SECRETS['gh_pat']}。",
        f"AWS 那个是 {FAKE_SECRETS['aws_key']}，JWT 是 {FAKE_SECRETS['jwt']}。",
        f"环境变量 {FAKE_SECRETS['assigned']}，{FAKE_SECRETS['assigned_quoted']}，"
        f"{FAKE_SECRETS['plain_pwd']}。",
        f"方舟那把是 {FAKE_SECRETS['ark_key']}，写成 {FAKE_SECRETS['ark_assigned']} 也算。",
        "教训：密钥必须走 .env。",
    ]
    return "\n".join(parts)


def make_material(text: str, *, kind: str = "note", source: str = "manual",
                  ref: str = "inbox.md") -> RawMaterial:
    return RawMaterial(
        source=source,
        ref=ref,
        ts=datetime(2026, 9, 20, 10, 0, 0),
        kind=kind,
        text=text,
        meta={"note_type": "idea"} if kind == "note" else {},
    )


def snapshot_json(day: date) -> dict:
    return json.loads(
        collect.snapshot_path(day).read_text(encoding="utf-8"))


def all_secret_strings() -> list[str]:
    """展开成待检测的密钥子串（赋值形式只查值部分）。"""
    values = [
        FAKE_SECRETS["sk_key"],
        FAKE_SECRETS["ghp_key"],
        FAKE_SECRETS["gh_pat"],
        FAKE_SECRETS["aws_key"],
        FAKE_SECRETS["jwt"],
        FAKE_SECRETS["ark_key"],
        "ar" + "k-" + "8" * 42,  # ark_assigned 的值部分
        "super-secret-value-42",
        "hunter2hunter2",
    ]
    return values


def test_ac012_secrets_never_reach_entries_snapshot_or_log(
    isolated_config, monkeypatch, caplog
):
    """伪造密钥经同步后不出现在条目、快照、日志中（AC-012）。

    素材含一个快记（直并入路径，不进 LLM）与一条会话消息
    （LLM 路径），两条路都要过脱敏。
    """
    captured = []

    def fake_chat(messages, json_mode=False):
        captured.append(messages)
        return json.dumps({"entries": [{
            "text": "Keys belong in .env, never in transcripts.",
            "type": "progress",
            "tags": ["security"],
            "source_refs": ["session-a"],
        }]})

    monkeypatch.setattr(distill, "chat", fake_chat)
    day_raw = DayRaw(
        day=DAY,
        collected_at=datetime(2026, 9, 20, 12, 0, 0),
        materials=[
            make_material(note_text(), kind="note", source="manual"),
            make_material(
                # 每一种密钥的**完整形态**都塞进这条素材（赋值式要连键名一起给：
                # 光给值那一段，任何正则都认不出来，断言就成了空转）。
                "assistant transcript containing "
                + " ".join(FAKE_SECRETS.values()),
                kind="message",
                source="claude_code",
                ref="session-a",
            ),
        ],
    )

    with caplog.at_level("WARNING"):
        entries = distill.distill(day_raw)

    # 条目文本：直并入的快记与 LLM 产物都不含任何密钥子串
    joined_entries = "\n".join(entry.text for entry in entries)
    # 快照与 LLM 请求报文
    snapshot_text = json.dumps(snapshot_json(DAY), ensure_ascii=False)
    chat_text = json.dumps(captured, ensure_ascii=False)
    for secret in all_secret_strings():
        assert secret not in joined_entries, f"secret leaked into entries: {secret}"
        assert secret not in snapshot_text, f"secret leaked into snapshot: {secret}"
        assert secret not in chat_text, f"secret leaked into LLM messages: {secret}"
        assert secret not in caplog.text, f"secret leaked into logs: {secret}"
    assert "[REDACTED]" in joined_entries


def test_ac012_direct_note_path_is_sanitized_without_llm(
    isolated_config, monkeypatch
):
    """AC-012 的验收素材就是"快记"：直并入路径同样必须脱敏。"""
    calls = []
    monkeypatch.setattr(
        distill, "chat",
        lambda *a, **k: calls.append(1) or "{}",
    )
    day_raw = DayRaw(
        day=DAY,
        collected_at=datetime(2026, 9, 20, 12, 0, 0),
        materials=[make_material(note_text(), kind="note")],
    )

    entries = distill.distill(day_raw)

    assert calls == []  # 快记不进 LLM（FR-013）
    assert len(entries) == 1
    for secret in all_secret_strings():
        assert secret not in entries[0].text
        assert secret not in json.dumps(snapshot_json(DAY), ensure_ascii=False)
    assert "[REDACTED]" in entries[0].text


# --- AC-013: 未知类型 ---


def candidate(text: str, type_name: str = "progress",
              ref: str = "session-a") -> dict:
    return {
        "text": text,
        "type": type_name,
        "tags": ["t"],
        "source_refs": [ref],
    }


def test_ac013_unknown_type_dropped_after_one_retry_others_kept(
    isolated_config, monkeypatch, caplog
):
    """配置外类型：重试一次仍失败 → 丢弃并留日志；其余条目正常产出。"""
    bad_payload = json.dumps({"entries": [
        candidate("Valid progress entry"),
        candidate("Invalid type entry", type_name="banana"),
        candidate("Valid error entry", type_name="error"),
    ]})
    # 重试仍返回未知类型 → 该条被丢弃
    responses = iter([bad_payload, bad_payload])
    calls = []

    def fake_chat(messages, json_mode=False):
        calls.append(messages)
        return next(responses)

    monkeypatch.setattr(distill, "chat", fake_chat)
    day_raw = DayRaw(
        day=DAY,
        collected_at=datetime(2026, 9, 20, 12, 0, 0),
        materials=[make_material("m", kind="message", source="claude_code",
                                 ref="session-a")],
    )

    with caplog.at_level("WARNING"):
        entries = distill.distill(day_raw)

    assert len(calls) == 2  # 初次 + 重试一次，不再更多
    assert [entry.text for entry in entries] == [
        "Valid progress entry",
        "Valid error entry",
    ]
    assert "unknown type" in caplog.text.lower()
    assert snapshot_json(DAY)["distill_run"]["status"] == "ok"


def test_ac013_retry_can_recover_into_allowed_type(isolated_config, monkeypatch):
    """重试一次后改回合法类型 → 条目保留（不丢弃）。"""
    responses = iter([
        json.dumps({"entries": [
            candidate("Wrong then fixed", type_name="banana")]}),
        json.dumps({"entries": [
            candidate("Wrong then fixed", type_name="reflection")]}),
    ])
    monkeypatch.setattr(
        distill, "chat",
        lambda messages, json_mode=False: next(responses),
    )
    day_raw = DayRaw(
        day=DAY,
        collected_at=datetime(2026, 9, 20, 12, 0, 0),
        materials=[make_material("m", kind="message", source="claude_code",
                                 ref="session-a")],
    )

    entries = distill.distill(day_raw)

    assert [entry.text for entry in entries] == ["Wrong then fixed"]
    assert entries[0].type == "reflection"


# --- AC-014: 条数熔断（临时上限 3）---


def llm_day_raw(n_materials: int) -> DayRaw:
    return DayRaw(
        day=DAY,
        collected_at=datetime(2026, 9, 20, 12, 0, 0),
        materials=[
            make_material(f"material {i}", kind="message",
                          source="claude_code", ref=f"session-{i}")
            for i in range(n_materials)
        ],
    )


def test_ac014_breaker_caps_at_limit_with_log_and_clean_exit(
    isolated_config, monkeypatch, caplog
):
    """超出上限：恰好入库上限数量、日志含熔断记录、正常结束。"""
    monkeypatch.setattr(
        distill, "chat",
        lambda messages, json_mode=False: json.dumps({"entries": [
            candidate(f"Entry {i}", ref=f"session-{i}") for i in range(5)
        ]}),
    )
    day_raw = llm_day_raw(5)

    with caplog.at_level("WARNING"):
        entries = distill.distill(day_raw)  # 不应抛异常：同步正常结束

    assert len(entries) == 3  # 恰好上限数量
    assert "circuit breaker" in caplog.text.lower()
    assert snapshot_json(DAY)["distill_run"]["status"] == "ok"


def test_ac014_at_exact_limit_no_breaker_log(isolated_config, monkeypatch, caplog):
    """恰好等于上限：全部保留，不触发熔断日志。"""
    monkeypatch.setattr(
        distill, "chat",
        lambda messages, json_mode=False: json.dumps({"entries": [
            candidate(f"Entry {i}", ref=f"session-{i}") for i in range(3)
        ]}),
    )
    day_raw = llm_day_raw(3)

    with caplog.at_level("WARNING"):
        entries = distill.distill(day_raw)

    assert len(entries) == 3
    assert "circuit breaker" not in caplog.text.lower()
