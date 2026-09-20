"""T026 unit tests for src/ask.py (filtered search + cited answers)."""

import json
from datetime import date, timedelta

import pytest

from src import ask, config, similarity
from src.similarity import Hit

SCHEMA = {
    "types": [{"name": "progress", "desc": "d"}, {"name": "error", "desc": "d"}],
    "distill": {},
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
def schema(monkeypatch):
    monkeypatch.setattr(ask.config, "load_schema", lambda: SCHEMA)


# --- date/filter parsing ---


def test_parse_relative_and_absolute_dates():
    today = date(2026, 9, 20)
    assert ask._parse_date("7d", today=today) == date(2026, 9, 13)
    assert ask._parse_date("2026-09-01", today=today) == date(2026, 9, 1)
    assert ask._parse_date(None, today=today) is None


def test_build_filters_type_project_and_range():
    filters = ask.build_filters(
        type="error", project="first-rag",
        since="2026-09-01", until="7d", today=date(2026, 9, 20))
    conditions = {c.key: c for c in filters.must}
    assert conditions["type"].match.value == "error"
    assert conditions["project"].match.value == "first-rag"
    assert conditions["date"].range.gte.date() == date(2026, 9, 1)
    assert conditions["date"].range.lte.date() == date(2026, 9, 13)


def test_build_filters_none_when_no_filters():
    assert ask.build_filters() is None


# --- query() ---


def hit(payload: dict, score: float = 0.9, id: str = "id-1") -> Hit:
    return Hit(id=id, score=score, payload=payload)


@pytest.fixture
def pipeline_fakes(monkeypatch):
    captured = {}

    def fake_embed(texts):
        captured["question_embedded"] = texts[0]
        return [[0.1, 0.2, 0.3]]

    def fake_search(vector, k=5, filters=None, **kwargs):
        captured["k"] = k
        captured["filters"] = filters
        return [
            hit({"text": "Qdrant upsert 409 root cause", "date": "2026-09-18",
                 "type": "error", "source": "claude_code",
                 "source_refs": ["s1"], "related": []}),
            hit({"text": "Chose uuid5 for idempotency", "date": "2026-09-19",
                 "type": "progress", "source": "codex",
                 "source_refs": ["r1"], "related": []}, id="id-2"),
        ]

    def fake_chat(messages, json_mode=False):
        captured["messages"] = messages
        return "409 的根因是维度不匹配 [2026-09-18]。"

    monkeypatch.setattr(ask, "embed", fake_embed)
    monkeypatch.setattr(ask.similarity, "search", fake_search)
    monkeypatch.setattr(ask, "chat", fake_chat)
    return captured


def test_query_answers_with_citations(pipeline_fakes, schema):
    answer = ask.query("我在 qdrant 上踩过什么坑？", type="error")

    assert pipeline_fakes["question_embedded"] == "我在 qdrant 上踩过什么坑？"
    assert pipeline_fakes["k"] == 8  # schema retrieval.top_k
    assert pipeline_fakes["filters"] is not None
    user_content = pipeline_fakes["messages"][-1]["content"]
    assert "[2026-09-18] error:" in user_content  # 引用式上下文（FR-019）
    assert "我在 qdrant 上踩过什么坑？" in user_content
    assert answer.text.startswith("409 的根因")
    assert [c.type for c in answer.citations] == ["error", "progress"]
    assert answer.citations[0].date == "2026-09-18"
    assert answer.citations[0].source == "claude_code"


def test_query_no_hits_returns_guidance_without_llm(
        pipeline_fakes, schema, monkeypatch):
    monkeypatch.setattr(
        ask.similarity, "search", lambda *a, **k: [])
    calls = []
    monkeypatch.setattr(ask, "chat",
                        lambda *a, **k: calls.append(1) or "x")

    answer = ask.query("什么都没有")

    assert calls == []
    assert answer.citations == []
    assert "make sync" in answer.text  # 明确引导提示（spec Edge Cases）


def test_query_no_expand_flag(pipeline_fakes, schema):
    captured_filters = pipeline_fakes
    answer = ask.query("问题", expand=False)
    assert answer.expanded == []


# --- CLI (FR-025 surface) ---


def test_main_parses_args_and_prints_answer(
        pipeline_fakes, schema, monkeypatch, capsys):
    code = ask.main(["ask", "Q=我学了啥", "--type", "error",
                     "--since", "7d", "--no-expand"])

    out = capsys.readouterr().out
    assert code == 0
    assert "409 的根因" in out
    assert "[2026-09-18] error" in out  # 引用清单输出
    assert pipeline_fakes["filters"] is not None


def test_main_requires_question(schema):
    assert ask.main(["ask"]) != 0


def test_main_failure_exits_nonzero(pipeline_fakes, schema, monkeypatch):
    def boom(texts):
        raise RuntimeError("ark down")
    monkeypatch.setattr(ask, "embed", boom)
    assert ask.main(["ask", "Q=x"]) != 0
