"""T013 的 Qdrant 直插路径契约测试。

测试使用 fake Qdrant client 和 fake embedding 函数，
不访问真实 Ark API，也不访问本地 Qdrant。
"""

from dataclasses import fields
from datetime import datetime
from types import SimpleNamespace

import pytest

from src import ids, ingest


def make_entry(**overrides):
    values = {
        "text": "Learned that UUIDv5 gives stable point IDs.",
        "date": "2026-09-20",
        "type": "progress",
        "tags": ["qdrant", "idempotency"],
        "source": "claude_code",
        "project": "first-rag",
        "created_at": datetime(2026, 9, 20, 10, 0, 0),
        "source_refs": ["session-a", "commit-a"],
        "distill_version": "test-model+rubric@abcd1234",
        "related": [],
    }
    values.update(overrides)
    return ingest.Entry(**values)


class FakeQdrantClient:
    def __init__(self, *, exists: bool = False, points: list | None = None):
        self.exists = exists
        self.points = points or []
        self.calls = []
        self.queries = []

    def collection_exists(self, collection_name: str) -> bool:
        return self.exists

    def query_points(self, **kwargs):
        self.queries.append(kwargs)
        return SimpleNamespace(points=self.points)

    def upsert(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(operation_id=1)


@pytest.fixture
def fake_qdrant(monkeypatch):
    client = FakeQdrantClient()
    monkeypatch.setattr(ingest, "_client", lambda: client)
    return client


@pytest.fixture
def fake_embed(monkeypatch):
    calls = []

    def embed(texts):
        calls.append(list(texts))
        return [[float(i), float(i) + 0.5] for i, _ in enumerate(texts)]

    monkeypatch.setattr(ingest, "embed", embed)
    return calls


def test_entry_has_all_payload_fields_defined_by_data_model():
    expected = {
        "text",
        "date",
        "type",
        "tags",
        "source",
        "project",
        "created_at",
        "source_refs",
        "distill_version",
        "related",
    }
    actual = {f.name for f in fields(ingest.Entry)}

    assert actual == expected


def test_upsert_empty_entries_returns_zero_without_network(fake_qdrant, fake_embed):
    report = ingest.upsert([])

    assert report.upserted == 0
    assert fake_qdrant.calls == []
    assert fake_embed == []


def test_upsert_batches_embeds_and_writes_all_payload_fields(fake_qdrant, fake_embed):
    first = make_entry()
    second = make_entry(
        text="Learned that Qdrant upsert overwrites the same point ID.",
        source="codex",
        related=[ids.point_id("claude_code", "2026-09-20", "neighbor")],
    )

    report = ingest.upsert([first, second])

    assert report.upserted == 2
    assert fake_embed == [[first.text, second.text]]

    assert len(fake_qdrant.calls) == 1
    call = fake_qdrant.calls[0]
    assert call["collection_name"] == "learning_memory"
    assert call["wait"] is True
    assert len(call["points"]) == 2

    point_one, point_two = call["points"]
    assert point_one.id == ids.point_id(first.source, first.date, first.text)
    assert point_one.vector == [0.0, 0.5]
    assert point_two.id == ids.point_id(second.source, second.date, second.text)
    assert point_two.vector == [1.0, 1.5]

    assert point_one.payload == {
        "text": first.text,
        "date": "2026-09-20",
        "type": "progress",
        "tags": ["qdrant", "idempotency"],
        "source": "claude_code",
        "project": "first-rag",
        "created_at": "2026-09-20T10:00:00",
        "source_refs": ["session-a", "commit-a"],
        "distill_version": "test-model+rubric@abcd1234",
        "related": [],
    }
    assert point_two.payload["related"] == [
        str(ids.point_id("claude_code", "2026-09-20", "neighbor"))
    ]


def test_upsert_deduplicates_same_identity_within_one_batch(fake_qdrant, fake_embed):
    first = make_entry()
    duplicate = make_entry(project="changed-project")

    report = ingest.upsert([first, duplicate])

    assert report.upserted == 1
    assert fake_embed == [[first.text, duplicate.text]]
    assert len(fake_qdrant.calls[0]["points"]) == 1


def test_upsert_empty_text_error_message_is_exact():
    """错误文案是给人看的，写错半个字都不能算通过。

    与空列表 no-op 的区别：含空 text 的 Entry 既产不出合法 point ID，
    也产不出合法 payload，所以必须是硬错误而不是静默跳过。
    """
    with pytest.raises(ValueError) as excinfo:
        ingest.upsert([make_entry(text="")])

    assert str(excinfo.value) == "entry text must not be empty"


def test_upsert_continues_past_a_duplicate_inside_the_batch(fake_qdrant, fake_embed):
    """批内重复只应跳过它自己（continue），不能把后面的条目一起吞掉（break）。"""
    first = make_entry()
    duplicate = make_entry(project="changed-project")
    third = make_entry(text="Learned that dedupe must not stop the batch.",
                       source="codex")

    report = ingest.upsert([first, duplicate, third])

    assert report.upserted == 2
    written = [point.id for point in fake_qdrant.calls[0]["points"]]
    assert ids.point_id(third.source, third.date, third.text) in written


def test_upsert_filters_novelty_against_configured_collection(monkeypatch,
                                                             fake_embed):
    """查重与写入都用 config.COLLECTION，不能悄悄落到 None 上。"""
    monkeypatch.setattr(ingest.config, "COLLECTION", "configured-collection")
    client = FakeQdrantClient(exists=True, points=[])
    monkeypatch.setattr(ingest, "_client", lambda: client)

    ingest.upsert([make_entry()])

    assert client.queries[0]["collection_name"] == "configured-collection"
    assert client.calls[0]["collection_name"] == "configured-collection"


def test_upsert_skips_novelty_duplicate(fake_embed, monkeypatch):
    entry = make_entry()
    client = FakeQdrantClient(
        exists=True,
        points=[SimpleNamespace(id="existing-point", score=0.95, payload={})],
    )
    monkeypatch.setattr(ingest, "_client", lambda: client)

    report = ingest.upsert([entry])

    assert report.upserted == 0
    assert client.calls == []


def test_upsert_allows_exact_same_point_id_on_rerun(fake_embed, monkeypatch):
    entry = make_entry()
    point_id = ids.point_id(entry.source, entry.date, entry.text)
    client = FakeQdrantClient(
        exists=True,
        points=[SimpleNamespace(id=str(point_id), score=1.0, payload={})],
    )
    monkeypatch.setattr(ingest, "_client", lambda: client)

    report = ingest.upsert([entry])

    assert report.upserted == 1
    assert len(client.calls) == 1
