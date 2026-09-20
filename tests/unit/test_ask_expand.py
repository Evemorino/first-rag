"""T029 unit tests: 关联扩展三模式（off/all/阈值）+ 关联补充标记。

similarity.py 是 ★ 手写模块，扩展取数在 ask.py 内实现（qdrant
retrieve by id + 阈值模式用与问题的 cosine），不触碰 T027 的边构建。
"""

from types import SimpleNamespace

import pytest

from src import ask
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


def hit(id: str, related: list[str], text: str = "entry") -> Hit:
    return Hit(id=id, score=0.9,
               payload={"text": text, "date": "2026-09-18", "type": "progress",
                        "source": "claude_code", "source_refs": [], "related": related})


def point(id: str, text: str, vector=None):
    return SimpleNamespace(id=id,
                           payload={"text": text, "date": "2026-09-18",
                                    "type": "reflection", "source": "trae",
                                    "source_refs": [], "related": []},
                           vector=vector)


class FakeClient:
    def __init__(self, points):
        self.points = {str(p.id): p for p in points}
        self.requested = []

    def retrieve(self, *, collection_name, ids, with_payload, with_vector=False):
        self.requested.append(list(ids))
        return [self.points[str(i)] for i in ids if str(i) in self.points]


@pytest.fixture
def schema(monkeypatch):
    monkeypatch.setattr(ask.config, "load_schema", lambda: SCHEMA)


def test_expand_all_adds_neighbors_as_related_supplement(schema):
    client = FakeClient([
        point("n1", "neighbor one"), point("n2", "neighbor two")])
    hits = [hit("h1", ["n1", "n2"]), hit("h2", ["n1"])]

    expanded = ask._expand_neighbors(hits, [0.1, 0.2], client=client,
                                     collection_name="c")

    assert [c.id for c in expanded] == ["n1", "n2"]  # 去重
    assert client.requested == [["n1", "n2"]]
    # 关联补充标记（AC-005）
    lines = ask._context_lines(expanded, marker="关联补充")
    assert lines[0] == "[2026-09-18] reflection（关联补充）: neighbor one"


def test_expand_off_returns_empty(schema):
    client = FakeClient([point("n1", "x")])
    hits = [hit("h1", ["n1"])]

    assert ask._expand_neighbors(
        hits, [0.1], mode="off", client=client, collection_name="c") == []
    assert client.requested == []


def test_expand_threshold_filters_by_cosine_to_question(
        schema, monkeypatch):
    # 问题向量 [1,0]；n1 与之间 cosine=1.0，n2 cosine≈0.0
    client = FakeClient([
        point("n1", "aligned", vector=[1.0, 0.0]),
        point("n2", "orthogonal", vector=[0.0, 1.0]),
    ])
    hits = [hit("h1", ["n1", "n2"])]

    expanded = ask._expand_neighbors(
        hits, [1.0, 0.0], mode=0.5, client=client, collection_name="c")

    assert [c.id for c in expanded] == ["n1"]


def test_expand_respects_per_hit_limit_and_cap(schema):
    client = FakeClient([
        point("n1", "a"), point("n2", "b"), point("n3", "c")])
    hits = [hit("h1", ["n1", "n2", "n3"])]  # limit_per_hit=2 → n1,n2

    expanded = ask._expand_neighbors(
        hits, [0.1], client=client, collection_name="c")

    assert [c.id for c in expanded] == ["n1", "n2"]


def test_query_no_expand_flag_skips_expansion(schema, monkeypatch):
    monkeypatch.setattr(ask, "embed", lambda texts: [[0.1, 0.2]])
    monkeypatch.setattr(ask.similarity, "search", lambda *a, **k: [
        hit("h1", ["n1"])])
    monkeypatch.setattr(ask, "chat", lambda messages, json_mode=False: "ans")
    boom = FakeClient([])
    monkeypatch.setattr(ask, "_client", lambda: boom)

    answer = ask.query("q", expand=False)

    assert answer.expanded == []
    assert boom.requested == []


def test_query_context_includes_related_marker(schema, monkeypatch):
    captured = {}

    def fake_chat(messages, json_mode=False):
        captured["user"] = messages[-1]["content"]
        return "ans"

    monkeypatch.setattr(ask, "embed", lambda texts: [[1.0, 0.0]])
    monkeypatch.setattr(ask.similarity, "search", lambda *a, **k: [
        hit("h1", ["n1"], text="primary entry")])
    monkeypatch.setattr(ask, "chat", fake_chat)
    monkeypatch.setattr(ask, "_client", lambda: FakeClient(
        [point("n1", "neighbor entry")]))

    answer = ask.query("q")

    assert "（关联补充）: neighbor entry" in captured["user"]
    assert "primary entry" in captured["user"]
    assert [c.id for c in answer.expanded] == ["n1"]
