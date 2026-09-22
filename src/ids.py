"""为学习条目生成确定性、幂等的 Qdrant Point ID。

数据模型契约：
point_id = uuid5(NAMESPACE_URL, f"{source}|{date}|{content_hash}")
content_hash = sha256(text) 的前 16 个十六进制字符。

命名空间为什么是 NAMESPACE_URL：它跟 URL/DNS 的语义无关，这里只是拿 uuid5
把"来源+日期+正文"压成一个稳定 ID，选哪个命名空间都行。但**选定之后就不能
改** —— 换命名空间等于换掉整个 ID 空间，历史条目会全部对不上。
scripts/mutation_selfcheck.py 里有一个 canary 专门盯着这件事。
"""

import hashlib
import uuid


def content_hash(text: str) -> str:
    """返回正文的 sha256 前 16 位十六进制摘要。

    为什么只取 16 位：data-model.md 对 FR-014 的显式定义。
    这里的目标是生成稳定 ID，而不是做密码学校验。
    """
    if not text:
        raise ValueError("text must not be empty")

    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def point_id(source: str, date: str, text: str) -> uuid.UUID:
    """根据来源、日期和正文生成确定性 UUIDv5。

    同一 source、date、text 组合永远得到同一 ID；
    任一身份字段变化都会得到不同 ID。
    """
    if not source:
        raise ValueError("source must not be empty")
    if not date:
        raise ValueError("date must not be empty")

    identity = f"{source}|{date}|{content_hash(text)}"
    return uuid.uuid5(uuid.NAMESPACE_URL, identity)
