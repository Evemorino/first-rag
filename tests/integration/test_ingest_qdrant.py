"""T014：真实 Qdrant 上的同日重跑幂等集成测试。

向量使用 fake embedding，避免测试依赖 Ark 密钥；
Qdrant 使用本地 Docker 服务，验证 FR-014 / AC-003。
"""

from datetime import datetime

import pytest
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams

from src import config, ids, ingest

TEST_COLLECTION = "learning_memory_test_t014"


def make_entry(text: str) -> ingest.Entry:
    return ingest.Entry(
        text=text,
        date="2026-09-20",
        type="progress",
        tags=["qdrant", "idempotency"],
        source="claude_code",
        project="first-rag",
        created_at=datetime(2026, 9, 20, 10, 0, 0),
        source_refs=["integration-test"],
        distill_version="test-model+rubric@abcd1234",
        related=[],
    )


@pytest.fixture
def qdrant_collection(monkeypatch):
    # Local Qdrant must bypass host proxy settings; ALL_PROXY may be SOCKS5.
    client = QdrantClient(url=config.QDRANT_URL, trust_env=False)
    if client.collection_exists(TEST_COLLECTION):
        client.delete_collection(TEST_COLLECTION)
    client.create_collection(
        collection_name=TEST_COLLECTION,
        vectors_config=VectorParams(size=2, distance=Distance.COSINE),
    )

    monkeypatch.setattr(config, "COLLECTION", TEST_COLLECTION)
    # Reuse the proxy-isolated real client inside ingest.upsert().
    monkeypatch.setattr(ingest, "_client", lambda: client)
    monkeypatch.setattr(
        ingest,
        "embed",
        lambda texts: [[float(i), float(i) + 0.5] for i, _ in enumerate(texts)],
    )

    yield client
    client.delete_collection(TEST_COLLECTION)
    client.close()


def test_same_day_rerun_keeps_point_count_unchanged(qdrant_collection):
    entries = [
        make_entry("UUIDv5 gives stable Qdrant point IDs."),
        make_entry("Qdrant upsert overwrites points with the same ID."),
    ]

    first_report = ingest.upsert(entries)
    first_count = qdrant_collection.count(
        collection_name=TEST_COLLECTION,
        exact=True,
    ).count

    second_report = ingest.upsert(entries)
    second_count = qdrant_collection.count(
        collection_name=TEST_COLLECTION,
        exact=True,
    ).count

    assert first_report.upserted == 2
    assert second_report.upserted == 2
    assert first_count == 2
    assert second_count == 2

    expected_ids = [
        ids.point_id(entry.source, entry.date, entry.text) for entry in entries
    ]
    retrieved = qdrant_collection.retrieve(
        collection_name=TEST_COLLECTION,
        ids=expected_ids,
        with_payload=True,
    )

    assert {str(point.id) for point in retrieved} == {
        str(point_id) for point_id in expected_ids
    }
    assert {point.payload["text"] for point in retrieved} == {
        entry.text for entry in entries
    }
