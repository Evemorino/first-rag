"""T017 contract tests for novelty search and deduplication."""

from types import SimpleNamespace

import pytest

from src import similarity


def scored_point(point_id: str, score: float, payload: dict | None = None):
    return SimpleNamespace(id=point_id, score=score, payload=payload or {})


class FakeQdrantClient:
    def __init__(self, *, exists: bool = True, responses: dict | None = None):
        self.exists = exists
        self.responses = responses or {}
        self.queries = []

    def collection_exists(self, collection_name: str) -> bool:
        return self.exists

    def query_points(self, **kwargs):
        self.queries.append(kwargs)
        key = tuple(kwargs["query"])
        points = self.responses.get(key, [])
        return SimpleNamespace(points=points[: kwargs["limit"]])


def test_search_returns_hits_with_payload():
    client = FakeQdrantClient(
        responses={(0.1, 0.2): [scored_point("existing", 0.91, {"text": "old"})]}
    )

    hits = similarity.search(
        [0.1, 0.2],
        k=1,
        client=client,
        collection_name="learning_memory",
    )

    assert hits == [
        similarity.Hit(id="existing", score=0.91, payload={"text": "old"})
    ]
    assert client.queries[0]["limit"] == 1
    assert client.queries[0]["with_payload"] is True


def test_search_passes_filter_to_qdrant():
    marker = object()
    client = FakeQdrantClient()

    similarity.search(
        [0.1, 0.2],
        k=3,
        filters=marker,
        client=client,
        collection_name="learning_memory",
    )

    assert client.queries[0]["query_filter"] is marker


def test_search_returns_empty_when_collection_is_missing():
    client = FakeQdrantClient(exists=False)

    assert similarity.search(
        [0.1, 0.2],
        client=client,
        collection_name="learning_memory",
    ) == []
    assert client.queries == []


def test_filter_novel_keeps_all_when_collection_is_missing():
    client = FakeQdrantClient(exists=False)

    kept = similarity.filter_novel(
        ["candidate-a", "candidate-b"],
        [[1.0, 0.0], [0.0, 1.0]],
        client=client,
        collection_name="learning_memory",
        threshold=0.82,
    )

    assert kept == [0, 1]
    assert client.queries == []


def test_filter_novel_skips_similar_existing_point():
    client = FakeQdrantClient(
        responses={(1.0, 0.0): [scored_point("existing", 0.91)]}
    )

    kept = similarity.filter_novel(
        ["candidate"],
        [[1.0, 0.0]],
        client=client,
        collection_name="learning_memory",
        threshold=0.82,
    )

    assert kept == []


def test_filter_novel_keeps_candidate_at_threshold():
    client = FakeQdrantClient(
        responses={(1.0, 0.0): [scored_point("existing", 0.82)]}
    )

    kept = similarity.filter_novel(
        ["candidate"],
        [[1.0, 0.0]],
        client=client,
        collection_name="learning_memory",
        threshold=0.82,
    )

    assert kept == [0]


def test_filter_novel_ignores_exact_same_point_id():
    client = FakeQdrantClient(
        responses={(1.0, 0.0): [scored_point("candidate", 1.0)]}
    )

    kept = similarity.filter_novel(
        ["candidate"],
        [[1.0, 0.0]],
        client=client,
        collection_name="learning_memory",
        threshold=0.82,
    )

    assert kept == [0]


def test_filter_novel_uses_config_threshold_when_not_supplied(
    monkeypatch,
):
    monkeypatch.setattr(
        similarity.config,
        "load_schema",
        lambda: {"distill": {"novelty_threshold": 0.5}},
    )
    client = FakeQdrantClient(
        responses={(1.0, 0.0): [scored_point("existing", 0.6)]}
    )

    kept = similarity.filter_novel(
        ["candidate"],
        [[1.0, 0.0]],
        client=client,
        collection_name="learning_memory",
    )

    assert kept == []


def test_filter_novel_returns_indices_for_mixed_candidates():
    client = FakeQdrantClient(
        responses={
            (1.0, 0.0): [scored_point("existing", 0.9)],
            (0.0, 1.0): [scored_point("unrelated", 0.1)],
        }
    )

    kept = similarity.filter_novel(
        ["duplicate", "novel"],
        [[1.0, 0.0], [0.0, 1.0]],
        client=client,
        collection_name="learning_memory",
        threshold=0.82,
    )

    assert kept == [1]


def test_search_defaults_to_five_hits():
    """默认 k=5 是对外契约，调用方不传时也必须成立。"""
    client = FakeQdrantClient()

    similarity.search([0.1, 0.2], client=client,
                      collection_name="learning_memory")

    assert client.queries[0]["limit"] == 5


def test_client_factory_points_at_local_qdrant(monkeypatch):
    """_client 必须连本地 Qdrant，且不继承环境代理（FR-011 / 宪法 VI）。"""
    captured = {}

    class Recorder:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(similarity, "QdrantClient", Recorder)
    monkeypatch.setattr(similarity.config, "QDRANT_URL",
                        "http://localhost:6333")

    similarity._client()

    assert captured["url"] == "http://localhost:6333"
    assert captured["trust_env"] is False


def test_filter_novel_prefers_explicit_collection_over_config(monkeypatch):
    """显式传入的 collection_name 必须盖过 config 默认值（`or` 而非 `and`）。"""
    monkeypatch.setattr(similarity.config, "COLLECTION", "default-collection")
    client = FakeQdrantClient(
        responses={(1.0, 0.0): [scored_point("existing", 0.91)]}
    )

    similarity.filter_novel(
        ["candidate"],
        [[1.0, 0.0]],
        client=client,
        collection_name="explicit-collection",
        threshold=0.82,
    )

    assert client.queries[0]["collection_name"] == "explicit-collection"


def test_filter_novel_logs_why_a_candidate_was_skipped(caplog):
    """跳过候选时要留下可排障的记录：候选是谁、撞上谁、分数多少。"""
    client = FakeQdrantClient(
        responses={(1.0, 0.0): [scored_point("existing", 0.91)]}
    )

    with caplog.at_level("INFO", logger="src.similarity"):
        similarity.filter_novel(
            ["candidate"],
            [[1.0, 0.0]],
            client=client,
            collection_name="learning_memory",
            threshold=0.82,
        )

    messages = [record.getMessage() for record in caplog.records]
    assert any(
        message.startswith("similarity: skipped candidate as duplicate of existing")
        for message in messages
    )
    assert any("0.9100" in message for message in messages)


def test_filter_novel_rejects_mismatched_lengths():
    with pytest.raises(ValueError) as excinfo:
        similarity.filter_novel(
            ["candidate"],
            [],
            client=FakeQdrantClient(),
            collection_name="learning_memory",
            threshold=0.82,
        )

    # 文案是给调用方看的，写错半个字也不能算通过
    assert str(excinfo.value) == "point_ids and vectors must have the same length"


def test_filter_novel_ignores_all_current_batch_ids_on_rerun():
    client = FakeQdrantClient(
        responses={
            (1.0, 0.0): [
                scored_point("candidate-a", 1.0),
                scored_point("candidate-b", 0.95),
            ],
            (0.0, 1.0): [
                scored_point("candidate-b", 1.0),
                scored_point("candidate-a", 0.95),
            ],
        }
    )

    kept = similarity.filter_novel(
        ["candidate-a", "candidate-b"],
        [[1.0, 0.0], [0.0, 1.0]],
        client=client,
        collection_name="learning_memory",
        threshold=0.82,
    )

    assert kept == [0, 1]


def test_filter_novel_sees_external_duplicate_after_batch_hits():
    client = FakeQdrantClient(
        responses={
            (1.0, 0.0): [
                scored_point("candidate-a", 1.0),
                scored_point("candidate-b", 0.95),
                scored_point("external-duplicate", 0.9),
            ],
            (0.0, 1.0): [
                scored_point("candidate-b", 1.0),
                scored_point("candidate-a", 0.95),
            ],
        }
    )

    kept = similarity.filter_novel(
        ["candidate-a", "candidate-b"],
        [[1.0, 0.0], [0.0, 1.0]],
        client=client,
        collection_name="learning_memory",
        threshold=0.82,
    )

    assert kept == [1]
