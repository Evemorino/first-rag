"""坏例子：破坏幂等和批量入库的常见错误。

来源依据：
qdrant-client 开源仓库：
https://github.com/qdrant/qdrant-client/blob/master/src/qdrant_client/qdrant_client.py

其 upsert() 文档说明：如果相同 ID 的 point 已存在，会被覆盖。
因此，用随机 ID 会让同一素材反复写入多个 point，破坏本项目 FR-014。
"""

# ===== 坏例子：常见错误 =====
#
# 这个例子故意展示下面这些问题：
# 1. 使用 uuid4() 随机 ID，同一素材重跑会生成新 point；
# 2. 每条素材单独调用一次 upsert，制造 N 次网络请求；
# 3. payload 缺少 date/type/source/source_refs 等必要字段；
# 4. wait=False 后立即返回，测试无法确认写入已生效；
# 5. 模块顶层创建 client 并执行写入。

BAD_EXAMPLE = r'''
import uuid
from qdrant_client import QdrantClient

client = QdrantClient(url="http://localhost:6333")

for text in ["entry one", "entry two"]:
    client.upsert(
        collection_name="learning_memory",
        points=[{
            "id": str(uuid.uuid4()),  # 坏：随机 ID，无法覆盖旧 point。
            "vector": [0.1, 0.2],
            "payload": {"text": text},  # 坏：payload 字段不完整。
        }],
        wait=False,  # 坏：无法在调用后可靠验证写入结果。
    )
'''
