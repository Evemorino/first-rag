"""T030 integration: 过滤检索生效（AC-004）+ 扩展开关与关联补充标记（AC-005）。

内嵌 Qdrant（:memory:）承载真实 payload 过滤与邻居取数；embed/chat 用
fake（Ark 真调与 ≤10s 耗时实测见 tasks 待跑记录）。
"""

from datetime import datetime
from uuid import uuid5, NAMESPACE_URL

import pytest
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

from src import ask, config, similarity

TEST_COLLECTION = "learning_memory_test_t030"
DIM = 3


def point(key: str, vector, *, type_: str, date_: str, related=()):
    pid = str(uuid5(NAMESPACE_URL, f"test|{key}"))
    return PointStruct(
        id=pid,
        vector=vector,
        payload={
            "text": f"{key} entry text",
            "date": date_,
            "type": type_,
            "tags": ["t"],
            "source": "claude_code",
            "project": "first-rag",
            "created_at": f"{date_}T10:00:00+08:00",
            "source_refs": [key],
            "distill_version": "test+rubric@abcd1234",
            "related": list(related),
        },
    )


@pytest.fixture
def collection(monkeypatch):
    client = QdrantClient(":memory:")
    client.create_collection(
        collection_name=TEST_COLLECTION,
        vectors_config=VectorParams(size=DIM, distance=Distance.COSINE),
    )
    # e_error_recent: 与问题向量同向；关联边指向 e_neighbor（窗口外，
    # 主检索的 date 过滤打不到它，但扩展按 ID 取数不受过滤限制）
    neighbor_id = str(uuid5(NAMESPACE_URL, "test|e_neighbor"))
    client.upsert(collection_name=TEST_COLLECTION, wait=True, points=[
        point("e_error_recent", [1.0, 0.0, 0.0], type_="error",
              date_="2026-09-18", related=[neighbor_id]),
        point("e_progress", [0.9, 0.1, 0.0], type_="progress",
              date_="2026-09-19"),
        point("e_error_old", [0.9, 0.1, 0.0], type_="error",
              date_="2026-08-01"),
        point("e_neighbor", [0.5, 0.5, 0.0], type_="reflection",
              date_="2026-07-01"),
    ], )

    monkeypatch.setattr(config, "COLLECTION", TEST_COLLECTION)
    monkeypatch.setattr(similarity, "_client", lambda: client)
    monkeypatch.setattr(ask, "_client", lambda: client)
    monkeypatch.setattr(ask, "embed",
                        lambda texts: [[1.0, 0.0, 0.0]])
    captured = {}

    def fake_chat(messages, json_mode=False):
        captured["user"] = messages[-1]["content"]
        return "回答 [2026-09-18]"

    monkeypatch.setattr(ask, "chat", fake_chat)
    yield client, captured
    client.close()


def test_filters_apply_type_and_date(collection):
    client, _ = collection

    answer = ask.query("error question", type="error",
                       since="2026-09-13", until="2026-09-19")

    # 仅 2026-09-13~19 的 error 条目：e_error_recent；旧 error 与 progress 排除
    assert [c.id for c in answer.citations] == [
        str(uuid5(NAMESPACE_URL, "test|e_error_recent"))]
    assert answer.citations[0].type == "error"


def test_expansion_on_by_default_with_marker(collection):
    client, captured = collection

    answer = ask.query("error question", since="2026-09-13")

    # e_error_recent 的 related → 窗口外的 e_neighbor 作为关联补充进入
    assert [c.id for c in answer.expanded] == [
        str(uuid5(NAMESPACE_URL, "test|e_neighbor"))]
    assert "（关联补充）: e_neighbor entry text" in captured["user"]


def test_no_expand_removes_neighbors(collection):
    client, captured = collection

    answer = ask.query("error question", since="2026-09-13", expand=False)

    assert answer.expanded == []
    assert "关联补充" not in captured["user"]
