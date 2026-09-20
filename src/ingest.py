"""Qdrant 直插路径：Entry → embedding → 确定性 Point ID → upsert。

T013 负责入库直插，T017 在 embedding 与 upsert 之间接入新颖度去重；
重复调用 upsert 时，相同 source/date/text 会命中同一个 Point ID，
由 Qdrant 的 upsert 语义覆盖旧点，保证不产生重复数据。
"""

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct

from src import config, ids, similarity
from src.ark_client import embed


@dataclass(frozen=True)
class Entry:
    """学习条目的完整 payload 模型（data-model.md / FR-015）。"""

    text: str
    date: str
    type: str
    tags: list[str]
    source: str
    project: str | None
    created_at: datetime
    source_refs: list[str]
    distill_version: str
    related: list[UUID] = field(default_factory=list)


@dataclass(frozen=True)
class Report:
    """一次入库的结果摘要。"""

    upserted: int


def _client() -> QdrantClient:
    """创建本地 Qdrant client；测试可用 fake client 替换。"""
    return QdrantClient(url=config.QDRANT_URL, trust_env=False)


def _payload(entry: Entry) -> dict[str, object]:
    """把 Entry 转成 Qdrant 可序列化的完整 payload。"""
    return {
        "text": entry.text,
        "date": entry.date,
        "type": entry.type,
        "tags": entry.tags,
        "source": entry.source,
        "project": entry.project,
        "created_at": entry.created_at.isoformat(),
        "source_refs": entry.source_refs,
        "distill_version": entry.distill_version,
        "related": [str(related) for related in entry.related],
    }


def upsert(entries: list[Entry]) -> Report:
    """批量嵌入 Entry 并幂等 upsert 到 Qdrant。"""
    if not entries:
        return Report(upserted=0)

    for entry in entries:
        if not entry.text:
            raise ValueError("entry text must not be empty")

    # 先批量生成全部向量，避免逐条调用 Ark API。
    vectors = embed([entry.text for entry in entries])

    # 同一批内如果出现相同 source/date/text，只保留第一个点。
    # 不同 metadata 不应导致同一身份被写成两个 point。
    ordered: list[tuple[UUID, list[float], Entry]] = []
    seen: set[UUID] = set()
    for entry, vector in zip(entries, vectors):
        point_uuid = ids.point_id(entry.source, entry.date, entry.text)
        if point_uuid in seen:
            continue
        seen.add(point_uuid)
        ordered.append((point_uuid, vector, entry))

    client = _client()
    kept_indices = similarity.filter_novel(
        [point_id for point_id, _, _ in ordered],
        [vector for _, vector, _ in ordered],
        client=client,
        collection_name=config.COLLECTION,
    )
    points = [
        PointStruct(
            id=ordered[index][0],
            vector=ordered[index][1],
            payload=_payload(ordered[index][2]),
        )
        for index in kept_indices
    ]
    if not points:
        return Report(upserted=0)

    client.upsert(
        collection_name=config.COLLECTION,
        points=points,
        wait=True,
    )
    return Report(upserted=len(points))
