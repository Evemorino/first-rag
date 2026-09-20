"""好例子：用 Qdrant 分数做新颖度判断，并保护幂等重跑。

来源依据：
1. qdrant-client 开源仓库：
   https://github.com/qdrant/qdrant-client
   其中 query_points() 支持按向量查询并按 score 返回最相近的 point。
2. Qdrant 官方文档中的 Cosine 相似度说明：
   https://qdrant.tech/documentation/concepts/collections/
   Cosine 距离用于语义相近度判断，分数越高表示越相似。

这个文件是学习对照，不是 T017 的正式实现。
"""


def is_novel(
    *,
    client,
    collection_name: str,
    candidate_id: str,
    candidate_vector: list[float],
    threshold: float,
) -> bool:
    """返回候选条目是否足够新颖。"""
    # 好在哪里：collection 不存在时视为首次入库，不制造伪重复。
    if not client.collection_exists(collection_name):
        return True

    # 好在哪里：多取 1 条，是为了排除“同 ID 的幂等重跑”命中。
    response = client.query_points(
        collection_name=collection_name,
        query=candidate_vector,
        limit=2,
        with_payload=False,
    )

    for point in response.points:
        # 好在哪里：相同 Point ID 代表同一条目重跑，应继续走 upsert 而不是跳过。
        if str(point.id) == str(candidate_id):
            continue
        # 好在哪里：阈值来自配置，不在代码里写死。
        if float(point.score) > threshold:
            return False
    return True


def filter_novel_entries(
    entries: list[dict],
    vectors: list[list[float]],
    *,
    client,
    collection_name: str,
    threshold: float,
) -> list[int]:
    """返回应保留的候选下标，避免同时过滤 entries 和 vectors 时错位。"""
    # 好在哪里：函数只返回下标，不在这里重新 embedding，复用上游批量向量。
    return [
        index
        for index, (entry, vector) in enumerate(zip(entries, vectors))
        if is_novel(
            client=client,
            collection_name=collection_name,
            candidate_id=entry["point_id"],
            candidate_vector=vector,
            threshold=threshold,
        )
    ]
