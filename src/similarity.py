"""Similarity search, novelty dedup, related edges, and redistill alignment.

The module owns Qdrant search mechanics: T017 novelty filtering (FR-009),
T027 related-edge construction (FR-017), and T037 redistill alignment
(FR-023). It stays free of any entry protocol / embedding dependency
(constitution II / lint_layers: 核心层只能依赖 config 与 collect).
"""

from __future__ import annotations

import difflib
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


@dataclass(frozen=True)
class RelatedEdges:
    """一次关联边构建的结果（T027 / FR-017）。

    new_related: 新条目 id -> 邻居 id 列表（写入新条目 payload 的 related）
    backfill: 已有条目 id -> 回填后的完整 related 列表（写回已有条目）
    """

    new_related: dict[str, list[str]]
    backfill: dict[str, list[str]]


def build_related_edges(
    point_ids: Sequence[object],
    vectors: Sequence[Sequence[float]],
    *,
    threshold: float = 0.75,
    max_edges: int = 5,
    top_k: int = 5,
    client: QdrantClient | None = None,
    collection_name: str | None = None,
) -> RelatedEdges:
    """为一整批新条目构建双向关联边（FR-017）。

    对每条新条目做相似度搜索，取 top-5 中 score > threshold 的已有条目建
    双向边，单条 related 上限 max_edges；对方已达上限时跳过该方向回填。

    为什么排除本批 ID：幂等重跑时本批条目已经在库里，会占掉最前面的相似度
    槽位（和自己最像的正是自己），不排除的话首跑与重跑看到的外部邻居不同，
    建出的边就不同 —— 违反 NFR-003 幂等。这与 filter_novel 的
    current_batch_ids 是同一套理由，只是这里要多留 top_k 个外部槽位。
    """
    if len(point_ids) != len(vectors):
        raise ValueError("point_ids and vectors must have the same length")
    if not point_ids:
        return RelatedEdges({}, {})

    qdrant = client or _client()
    collection = collection_name or config.COLLECTION
    if not qdrant.collection_exists(collection):
        return RelatedEdges({str(pid): [] for pid in point_ids}, {})

    batch_ids = {str(pid) for pid in point_ids}
    search_limit = len(point_ids) + top_k

    new_related: dict[str, list[str]] = {str(pid): [] for pid in point_ids}
    backfill: dict[str, list[str]] = {}

    for pid, vector in zip(point_ids, vectors):
        pid_str = str(pid)
        hits = _search_existing(
            vector,
            k=search_limit,
            filters=None,
            client=qdrant,
            collection_name=collection,
        )
        outside = [h for h in hits if h.id not in batch_ids][:top_k]
        for hit in outside:
            if hit.score <= threshold:
                continue
            # N -> E：新条目关联到已有条目（未满才加）。
            if hit.id not in new_related[pid_str] and \
                    len(new_related[pid_str]) < max_edges:
                new_related[pid_str].append(hit.id)
            # E -> N：回填已有条目。以 backfill 里已累积的为基准，而不是
            # 每次从 hit.payload 重读 —— 否则同批多个新条目回填同一个已有
            # 条目时会互相覆盖（每次都以"库里原始 related"起算）。
            e_related = backfill.get(hit.id)
            if e_related is None:
                e_related = [str(r) for r in hit.payload.get("related", [])]
            if pid_str not in e_related and len(e_related) < max_edges:
                e_related.append(pid_str)
                backfill[hit.id] = e_related

    return RelatedEdges(new_related=new_related, backfill=backfill)


def align_redistill(
    old_entries: Sequence[Any],
    new_entries: Sequence[Any],
    *,
    threshold: float = 0.85,
) -> dict:
    """按「共享溯源引用 且 文本相似度 ≥0.85」对齐新旧条目（FR-023 / T037）。

    返回 {rewritten, unchanged, added, removed}：
    - rewritten / unchanged 是 (old, new) 对；文本相同记 unchanged，否则改写
    - added / removed 是未配对的单边条目

    为什么用 difflib 而不是向量余弦：本模块是核心层（宪法 II / lint_layers），
    只能 import config 与 collect，碰不到 ark_client.embed；而文本相似度本身
    确定、可复现（宪法 IV），SequenceMatcher.ratio() 正好满足，也无需网络。

    配对是贪心的：按相似度从高到低扫候选对，先到先得，tie-break 用原始下标
    保证确定性。共享溯源引用是「交集非空」而不是子集 —— 旧 {a,b} 与新 {b,c}
    也算共享（都有 b）。
    """
    candidates: list[tuple[float, int, int]] = []
    for oi, old in enumerate(old_entries):
        old_refs = set(old.source_refs)
        for ni, new in enumerate(new_entries):
            if not (old_refs & set(new.source_refs)):
                continue
            ratio = difflib.SequenceMatcher(None, old.text, new.text).ratio()
            if ratio >= threshold:
                candidates.append((ratio, oi, ni))

    candidates.sort(key=lambda c: (-c[0], c[1], c[2]))
    paired_old: set[int] = set()
    paired_new: set[int] = set()
    rewritten: list[tuple[Any, Any]] = []
    unchanged: list[tuple[Any, Any]] = []
    for _, oi, ni in candidates:
        if oi in paired_old or ni in paired_new:
            continue
        paired_old.add(oi)
        paired_new.add(ni)
        old, new = old_entries[oi], new_entries[ni]
        if old.text == new.text:
            unchanged.append((old, new))
        else:
            rewritten.append((old, new))

    return {
        "rewritten": rewritten,
        "unchanged": unchanged,
        "added": [new for ni, new in enumerate(new_entries)
                  if ni not in paired_new],
        "removed": [old for oi, old in enumerate(old_entries)
                    if oi not in paired_old],
    }
