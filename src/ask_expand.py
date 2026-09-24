"""引用构建与一跳关联扩展（T029 / FR-019 / FR-020 / FR-021，AC-005）。

两件事放在一起，因为它们共用 `Citation` 这一个产物：

- `citations_from_hits` —— 检索命中 → 可追溯引用（FR-019：日期/类型/摘要/来源）
- `expand_neighbors`    —— 主命中的 related 邻居作为「关联补充」进上下文

`Citation` 定义在这里而不是 `ask.py`，是为了让依赖保持单向：ask.py 从这里
import，本模块不 import ask.py。两边都要用这个类型，放在上层就成了环。
ask.py 仍然把它导出，`from src.ask import Citation` 照旧可用。

扩展取数用 qdrant retrieve by id，阈值模式用与问题的 cosine 打分；similarity.py
是 ★ 手写模块，这里不碰 T027 的边构建。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Sequence

from qdrant_client import QdrantClient

from src import config, similarity


@dataclass(frozen=True)
class Citation:
    """一条可追溯引用（FR-019：日期、类型、摘要、来源）。"""

    id: str
    date: str
    type: str
    text: str
    source: str
    source_refs: list[str]
    score: float


def _client() -> QdrantClient:
    """Local Qdrant client for expansion fetches (similarity.py 保持 ★ 不动)."""
    return QdrantClient(url=config.QDRANT_URL, trust_env=False)


def citations_from_hits(hits: list[similarity.Hit]) -> list[Citation]:
    return [
        Citation(
            id=h.id,
            date=h.payload.get("date", ""),
            type=h.payload.get("type", ""),
            text=h.payload.get("text", ""),
            source=h.payload.get("source", ""),
            source_refs=h.payload.get("source_refs", []),
            score=h.score,
        )
        for h in hits
    ]


# --- 关联扩展（T029 / FR-020 / FR-021，AC-005）---


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def expand_neighbors(
    hits: list[similarity.Hit],
    question_vector: Sequence[float],
    *,
    mode: str | float | None = None,
    client: QdrantClient | None = None,
    collection_name: str | None = None,
) -> list[Citation]:
    """一跳关联扩展：主命中的 related 邻居作为「关联补充」进入上下文。

    三模式（FR-020）：off=不扩展；all=全量（受 neighbor_limit_per_hit /
    context_cap 约束）；数值=阈值，邻居与问题的 cosine ≥ 阈值才纳入。
    """
    expand = config.load_schema()["retrieval"]["expand"]
    effective = expand["mode"] if mode is None else mode
    if effective == "off" or not hits:
        return []

    threshold = None
    if isinstance(effective, (int, float)):
        threshold = float(effective)
    elif expand.get("neighbor_min_score") is not None:
        threshold = float(expand["neighbor_min_score"])

    seen = {h.id for h in hits}
    queue: list[str] = []
    for h in hits:
        for neighbor_id in h.payload.get("related", [])[: expand["neighbor_limit_per_hit"]]:
            neighbor_id = str(neighbor_id)
            if neighbor_id not in seen:
                seen.add(neighbor_id)
                queue.append(neighbor_id)
    queue = queue[: expand["context_cap"]]
    if not queue:
        return []

    qdrant = client or _client()
    # with_vector 仅阈值模式需要；本地（:memory:）模式不支持该参数
    # （即便传 False 也会报 Unknown arguments），所以按需注入。
    retrieve_kwargs = {"with_vector": True} if threshold is not None else {}
    records = qdrant.retrieve(
        collection_name=collection_name or config.COLLECTION,
        ids=queue,
        with_payload=True,
        **retrieve_kwargs,
    )
    expanded = []
    for record in records:
        score = 1.0
        if threshold is not None:
            score = _cosine(question_vector, record.vector or [])
            if score < threshold:
                continue
        expanded.append(_citation_from_record(record, score))
    return expanded


def _citation_from_record(record: Any, score: float) -> Citation:
    payload = record.payload or {}
    return Citation(
        id=str(record.id),
        date=payload.get("date", ""),
        type=payload.get("type", ""),
        text=payload.get("text", ""),
        source=payload.get("source", ""),
        source_refs=payload.get("source_refs", []),
        score=score,
    )
