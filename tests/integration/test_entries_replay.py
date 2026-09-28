"""T104 integration：人工修正遇上"同日重放"（AC-019 / AC-020 / AC-021）。

为什么必须是集成而不是单测：**覆盖恰好发生在真实 upsert 的合并语义里** ——
单测里的假客户端不会替我们合并 payload，也就证明不了"重放不覆盖人工值"。
这里用内嵌 Qdrant（`:memory:`）跑真实的 `ingest.upsert` 路径，另加真实检索
以证明软删条目确实从检索面消失（FR-029 / NFR-011 / ADR-18、19）。
"""

from datetime import datetime

import pytest
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams

from src import ask, ask_expand, config, entries, ids, ingest, similarity, similarity as sim

TEST_COLLECTION = "learning_memory_test_v08_replay"
DAY = "2026-09-20"
EDITED_TEXT = "人工改过的正文"


def fake_embed(texts):
    """确定性向量：同一段文字永远同一向量；改过的文字是另一个向量。"""
    return [
        [float((sum(map(ord, text)) + i) % 13) / 13 for i in range(8)]
        for text in texts
    ]


def make_entry(text="原始蒸馏正文", created_at=datetime(2026, 9, 20, 10, 0, 0)):
    return ingest.Entry(
        text=text,
        date=DAY,
        type="error",
        tags=["a"],
        source="claude_code",
        project="first-rag",
        created_at=created_at,
        source_refs=["session-a"],
        distill_version="mock+rubric@abcd1234",
        related=[],
    )


@pytest.fixture
def store(monkeypatch):
    client = QdrantClient(":memory:")
    client.create_collection(
        collection_name=TEST_COLLECTION,
        vectors_config=VectorParams(size=8, distance=Distance.COSINE),
    )
    monkeypatch.setattr(config, "COLLECTION", TEST_COLLECTION)
    for module in (ingest, similarity, entries):
        monkeypatch.setattr(module, "_client", lambda: client)
    monkeypatch.setattr(ingest, "embed", fake_embed)
    monkeypatch.setattr(entries, "embed", fake_embed)
    monkeypatch.setattr(entries, "_now", lambda: "2026-09-28T12:00:00+00:00")
    return client


def record_of(store, point_id):
    records = store.retrieve(
        collection_name=TEST_COLLECTION,
        ids=[point_id],
        with_payload=True,
        with_vectors=True,
    )
    return records[0] if records else None


def test_edit_changes_text_and_vector_but_not_identity(store):
    """AC-019 前半：编辑生效、向量重算、**ID 不变**。"""
    entry = make_entry()
    ingest.upsert([entry])
    point_id = str(ids.point_id(entry.source, entry.date, entry.text))
    before = record_of(store, point_id)

    view = entries.edit_entry(point_id, rev=0, text=EDITED_TEXT)

    after = record_of(store, point_id)
    assert after.id == before.id                      # 身份没变
    assert after.vector != before.vector              # 向量按新正文重算
    # Qdrant 存 cosine 向量时会归一化，所以比"方向"而不是比原始数组。
    assert ask_expand._cosine(after.vector, fake_embed([EDITED_TEXT])[0]) == pytest.approx(1.0)
    assert after.payload["override"]["text"] == EDITED_TEXT
    assert after.payload["original_text"] == "原始蒸馏正文"
    assert after.payload["edited_prev_text"] == "原始蒸馏正文"
    assert after.payload["rev"] == 1
    assert view["text"] == EDITED_TEXT


def test_replay_keeps_human_values_and_the_edited_vector(store):
    """AC-019 后半 + AC-021：同日重放既不改人工值，也不把向量改回旧正文。"""
    entry = make_entry()
    ingest.upsert([entry])
    point_id = str(ids.point_id(entry.source, entry.date, entry.text))
    entries.edit_entry(point_id, rev=0, text=EDITED_TEXT, type="idea")
    edited_vector = record_of(store, point_id).vector

    ingest.upsert([make_entry()])                     # 重放同一条素材

    replayed = record_of(store, point_id)
    assert replayed.payload["override"] == {"text": EDITED_TEXT, "type": "idea"}
    assert replayed.payload["original_text"] == "原始蒸馏正文"
    assert replayed.payload["edited_at"] == "2026-09-28T12:00:00+00:00"
    assert replayed.payload["edited_prev_text"] == "原始蒸馏正文"
    assert replayed.payload["rev"] == 1               # 重放不动版本号
    assert replayed.vector == edited_vector           # 向量仍是人工版的
    assert entries.get_entry(point_id)["text"] == EDITED_TEXT


def test_soft_delete_hides_from_list_and_search(store):
    """AC-020 前半：软删后从列表与**检索**同时消失。"""
    entry = make_entry()
    ingest.upsert([entry])
    point_id = str(ids.point_id(entry.source, entry.date, entry.text))

    entries.delete_entry(point_id, rev=0, reason="蒸馏错了")

    assert entries.list_entries()["total"] == 0
    assert entries.list_entries(include_deleted=True)["total"] == 1
    assert record_of(store, point_id).payload["deleted_reason"] == "蒸馏错了"

    # 检索面走 ask 的过滤器（同一份可见性判据）
    hits = sim.search(
        fake_embed([EDITED_TEXT])[0],
        k=5,
        filters=ask.build_filters(),
        client=store,
        collection_name=TEST_COLLECTION,
    )
    assert hits == []


def test_replay_does_not_revive_a_deleted_entry(store):
    """AC-020 后半：重放不复活；恢复后重新可见。"""
    entry = make_entry()
    ingest.upsert([entry])
    point_id = str(ids.point_id(entry.source, entry.date, entry.text))
    entries.delete_entry(point_id, rev=0)

    ingest.upsert([make_entry()])
    assert entries.list_entries()["total"] == 0                 # 没复活
    assert record_of(store, point_id).payload["deleted_at"]     # 标记还在

    entries.restore_entry(point_id, rev=1)
    assert entries.list_entries()["total"] == 1
    assert "deleted_at" not in record_of(store, point_id).payload


def test_related_expansion_ignores_deleted_neighbours(store):
    """指向已软删条目的边不产生死链（FR-029）。"""
    keeper, doomed = make_entry(text="保留的条目"), make_entry(text="要删掉的条目")
    ingest.upsert([keeper, doomed])
    keeper_id = str(ids.point_id(keeper.source, keeper.date, keeper.text))
    doomed_id = str(ids.point_id(doomed.source, doomed.date, doomed.text))
    store.set_payload(
        collection_name=TEST_COLLECTION,
        payload={"related": [doomed_id]},
        points=[keeper_id],
    )
    entries.delete_entry(doomed_id, rev=0)

    hits = [
        similarity.Hit(id=keeper_id, score=0.9, payload=record_of(store, keeper_id).payload)
    ]
    expanded = ask_expand.expand_neighbors(
        hits, fake_embed(["问题"])[0], mode="all", client=store,
        collection_name=TEST_COLLECTION,
    )
    assert expanded == []


def test_neighbour_navigation_skips_soft_deleted(store):
    """"当日前后翻"要跳过已软删的条目 —— 否则点进去就是一个"不存在"的详情页。"""
    early = make_entry(text="早的", created_at=datetime(2026, 9, 20, 9, 0, 0))
    middle = make_entry(text="中间的", created_at=datetime(2026, 9, 20, 10, 0, 0))
    late = make_entry(text="晚的", created_at=datetime(2026, 9, 20, 11, 0, 0))
    ingest.upsert([early, middle, late])
    ids_by_text = {
        entry.text: str(ids.point_id(entry.source, entry.date, entry.text))
        for entry in (early, middle, late)
    }

    entries.delete_entry(ids_by_text["中间的"], rev=0)

    late_view = entries.get_entry(ids_by_text["晚的"])
    early_view = entries.get_entry(ids_by_text["早的"])
    assert late_view["next_id"] == ids_by_text["早的"]   # 跳过了中间那条
    assert early_view["prev_id"] == ids_by_text["晚的"]
