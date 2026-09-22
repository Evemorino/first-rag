"""Similarity search and novelty deduplication (T017 / FR-009).

The module owns Qdrant search mechanics. T017 implements novelty filtering;
related-edge construction and re-distillation alignment belong to T027/T037.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Sequence

from qdrant_client import QdrantClient

from src import config

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Hit:
    """One scored Qdrant search result."""

    id: str
    score: float
    payload: dict


def _client() -> QdrantClient:
    """Create a local Qdrant client; tests may replace this factory."""
    return QdrantClient(url=config.QDRANT_URL, trust_env=False)


def _search_existing(
    vector: Sequence[float],
    *,
    k: int,
    filters: Any,
    client: QdrantClient,
    collection_name: str,
) -> list[Hit]:
    response = client.query_points(
        collection_name=collection_name,
        query=list(vector),
        query_filter=filters,
        limit=k,
        with_payload=True,
    )
    return [
        Hit(
            id=str(point.id),
            score=float(point.score),
            payload=point.payload or {},
        )
        for point in response.points
    ]


def search(
    vector: Sequence[float],
    k: int = 5,
    filters: Any = None,
    *,
    client: QdrantClient | None = None,
    collection_name: str | None = None,
) -> list[Hit]:
    """Return the top-k Qdrant hits for one vector."""
    qdrant = client or _client()
    collection = collection_name or config.COLLECTION
    if not qdrant.collection_exists(collection):
        return []
    return _search_existing(
        vector,
        k=k,
        filters=filters,
        client=qdrant,
        collection_name=collection,
    )


def filter_novel(
    point_ids: Sequence[object],
    vectors: Sequence[Sequence[float]],
    *,
    threshold: float | None = None,
    client: QdrantClient | None = None,
    collection_name: str | None = None,
) -> list[int]:
    """Return indices whose candidates are novel enough to upsert.

    Exact same point IDs are ignored so idempotent same-day reruns still reach
    Qdrant's upsert path. A different point above the configured threshold is
    treated as a duplicate and skipped.
    """
    if len(point_ids) != len(vectors):
        raise ValueError("point_ids and vectors must have the same length")
    if not point_ids:
        return []

    qdrant = client or _client()
    collection = collection_name or config.COLLECTION
    if not qdrant.collection_exists(collection):
        return list(range(len(point_ids)))

    if threshold is None:
        schema = config.load_schema()
        threshold = float(schema["distill"]["novelty_threshold"])

    # Ignore every point from the current batch, not just the candidate's own
    # ID. A same-day rerun contains the same IDs and must reach upsert again;
    # only points outside this batch count as genuine novelty duplicates.
    current_batch_ids = {str(point_id) for point_id in point_ids}
    # The +1 is exactly enough, not a safety margin: at most len(batch) of the
    # returned points can belong to this batch (Qdrant returns distinct points,
    # score-descending), so slot N+1 is always an outside point. Asking for more
    # slots only appends worse-scoring hits behind it and `next()` still picks
    # the same one -- that is why `+ 2` is an equivalent mutation no test can
    # kill (recorded in pyproject's do_not_mutate_patterns).
    # Dropping the +1 is the dangerous direction: the batch can then occupy all
    # returned slots and push a real duplicate out of the result set, silently
    # disabling dedup. That direction is covered by a test.
    search_limit = len(current_batch_ids) + 1

    kept: list[int] = []
    for index, (point_id, vector) in enumerate(zip(point_ids, vectors)):
        hits = _search_existing(
            vector,
            k=search_limit,
            filters=None,
            client=qdrant,
            collection_name=collection,
        )
        duplicate = next(
            (
                hit
                for hit in hits
                if hit.id not in current_batch_ids and hit.score > threshold
            ),
            None,
        )
        if duplicate is not None:
            logger.info(
                "similarity: skipped %s as duplicate of %s (score %.4f)",
                point_id,
                duplicate.id,
                duplicate.score,
            )
            continue
        kept.append(index)
    return kept
