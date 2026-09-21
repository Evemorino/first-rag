"""好例子：批量、幂等、完整 payload 的 Qdrant upsert。

来源依据：
qdrant-client 开源仓库：
https://github.com/qdrant/qdrant-client/blob/master/src/qdrant_client/qdrant_client.py

其 upsert() 文档明确说明：
1. 一次可以传入 points 列表；
2. 如果相同 ID 的 point 已存在，会被覆盖。

这正好支持本项目的 FR-014：重复 sync 同一天不产生重复数据。
"""

import uuid
from dataclasses import dataclass

from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct


@dataclass(frozen=True)
class ExampleEntry:
    """演示用的最小完整条目。"""

    source: str
    date: str
    text: str
    payload: dict


def stable_point_id(entry: ExampleEntry) -> uuid.UUID:
    """生成确定性 ID：同一身份永远得到同一 UUID。"""
    digest = str(abs(hash(entry.text)))  # 教学演示；项目正式实现用 sha256。
    return uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"{entry.source}|{entry.date}|{digest}",
    )


def upsert_entries(client: QdrantClient, entries: list[ExampleEntry]) -> int:
    """一次批量 upsert 全部条目，并返回写入的 point 数。"""
    # 好在哪里：一次构造完整 points 列表，而不是循环发起 N 次网络请求。
    points = [
        PointStruct(
            id=stable_point_id(entry),
            vector=[0.1, 0.2],  # 教学演示；正式实现来自 ark_client.embed()。
            payload=entry.payload,
        )
        for entry in entries
    ]

    # 好在哪里：upsert 语义保证相同 ID 覆盖，不产生重复 point。
    client.upsert(
        collection_name="learning_memory",
        points=points,
        wait=True,
    )

    return len(points)
