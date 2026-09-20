"""好例子：用确定性哈希和 UUIDv5 生成幂等 ID。

来源依据：
1. CPython 开源标准库：
   https://github.com/python/cpython/blob/main/Lib/uuid.py
   uuid5() 的文档说明：Generate a UUID from the SHA-1 hash of a namespace UUID and a name。
2. 同一文件中的 uuid4() 文档说明：Generate a random UUID。
   这说明 UUIDv4 是随机 ID，不适合“同样输入必须得到同样 ID”的幂等场景。

本项目数据模型的额外规定：
content_hash = sha256(text) 前 16 位十六进制；
point_id = uuid5(NAMESPACE_URL, f"{source}|{date}|{content_hash}")。
"""

import hashlib
import uuid


# ===== 好例子：确定性身份 =====


def content_hash(text: str) -> str:
    """返回正文的 sha256 前 16 位十六进制摘要。

    好在哪里：
    1. 同一段文本永远得到同一个摘要；
    2. 不依赖时间、随机数或进程状态；
    3. 使用 UTF-8 编码，行为跨平台稳定；
    4. 只取 16 位是为了遵循项目 data-model 的显式契约。
    """
    if not text:
        raise ValueError("text must not be empty")

    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def point_id(source: str, date: str, text: str) -> uuid.UUID:
    """生成稳定的 Qdrant Point ID。

    好在哪里：
    1. source、date、text 完全相同则 UUID 完全相同；
    2. 任一身份字段变化都会改变 UUID；
    3. 使用 UUIDv5，而不是随机 UUIDv4；
    4. 重复 upsert 会命中同一条 Qdrant point，天然支持幂等。
    """
    if not source:
        raise ValueError("source must not be empty")
    if not date:
        raise ValueError("date must not be empty")

    identity = f"{source}|{date}|{content_hash(text)}"
    return uuid.uuid5(uuid.NAMESPACE_URL, identity)
