"""Qdrant 直插路径：Entry → embedding → 确定性 Point ID → upsert。

T013 负责入库直插，T017 在 embedding 与 upsert 之间接入新颖度去重；
重复调用 upsert 时，相同 source/date/text 会命中同一个 Point ID，
由 Qdrant 的 upsert 语义覆盖旧点，保证不产生重复数据。
"""

from dataclasses import dataclass, field, replace
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


def _merge_related(existing: list[UUID], new: list[str]) -> list[UUID]:
    """合并条目既有的 related 与相似度新建的边（FR-017 上限 5）。

    条目从蒸馏阶段进来时 related 通常是空的，但重蒸馏或手工构造的条目可能
    已经带边；相似度搜索只是**补充**边，不能把既有边抹掉。去重按字符串 id
    比较（related 在 payload 里本就存成字符串），封顶 5 与 build_related_edges
    的 max_edges 保持一致。
    """
    merged: list[UUID] = list(existing)
    seen = {str(related) for related in merged}
    for related in new:
        if related not in seen and len(merged) < 5:
            merged.append(UUID(related))
            seen.add(related)
    return merged


# Ark embeddings 单次 input 上限 10 条，超了直接 400 InvalidParameter
# （第一次真跑 make sync 撞出来的：11 条条目 → "max 10, got 11"）。
# 这个限制本属于客户端契约，但 ark_client 是宪法 VII 的手写核心模块，
# 所以分批放在调用方；将来若把上限挪进 ark_client，记得同时删掉这里。
EMBED_BATCH = 10


def _embed_all(texts: list[str]) -> list[list[float]]:
    """按 provider 上限分批嵌入，返回值顺序与输入严格一致。"""
    vectors: list[list[float]] = []
    for start in range(0, len(texts), EMBED_BATCH):
        vectors.extend(embed(texts[start:start + EMBED_BATCH]))
    return vectors


def upsert(entries: list[Entry]) -> Report:
    """批量嵌入 Entry 并幂等 upsert 到 Qdrant。"""
    if not entries:
        return Report(upserted=0)

    for entry in entries:
        if not entry.text:
            raise ValueError("entry text must not be empty")

    # 先批量生成全部向量，避免逐条调用 Ark API。
    vectors = _embed_all([entry.text for entry in entries])

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
    # 只对会真正入库的条目建关联边（FR-017 / T027）：重复项已被 filter_novel
    # 跳过，不该再参与建边。复用同一个 client，不多建连接。
    kept = [ordered[index] for index in kept_indices]
    edges = similarity.build_related_edges(
        [point_id for point_id, _, _ in kept],
        [vector for _, vector, _ in kept],
        client=client,
        collection_name=config.COLLECTION,
    )
    points = [
        PointStruct(
            id=point_id,
            vector=vector,
            payload=_payload(
                replace(
                    entry,
                    related=_merge_related(
                        entry.related,
                        edges.new_related.get(str(point_id), []),
                    ),
                )
            ),
        )
        for point_id, vector, entry in kept
    ]
    if not points:
        return Report(upserted=0)

    client.upsert(
        collection_name=config.COLLECTION,
        points=points,
        wait=True,
    )
    # 关联边是双向的：新条目入库后，把新条目 id 回填到邻居已有的 related 里
    # （E -> N 方向）。build_related_edges 已算好回填后的完整列表，直接写回，
    # 避免在 upsert 之后逐条重读。
    for existing_id, related in edges.backfill.items():
        client.set_payload(
            collection_name=config.COLLECTION,
            payload={"related": related},
            points=[existing_id],
        )
    return Report(upserted=len(points))
