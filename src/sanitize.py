"""脱敏：把密钥/令牌从正文里抹掉（FR-011、宪法 V）。

为什么单独成模块：脱敏是"进 LLM 之前必须发生"的安全边界，和蒸馏编排
没什么关系。放在一起只会让 distill.py 变长，而长度本身会掩盖真正重要的
东西 —— 这条边界是不是真的守住了。

调用点：distill.distill() 在调 LLM 和改写快照之前各走一次。
"""

from __future__ import annotations

import re
from typing import Any

from src.collect import DayRaw

# 形如 `api_key: xxx` / `密码 = xxx` 的赋值式泄漏
_ASSIGNMENT_SECRET_RE = re.compile(
    r"""(?ix)
    (\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|token|secret|password|passwd|pwd|密码)\b\s*[:=]\s*)
    (["']?)
    ([^\s"'<>]{6,})
    \2
    """
)
# 长得像令牌的字符串，不需要上下文就能认出来
_TOKEN_SECRET_RES = (
    re.compile(r"\bsk-[A-Za-z0-9][A-Za-z0-9_-]{5,}\b"),
    re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(
        r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"
    ),
)


def sanitize_text(text: str) -> str:
    """抹掉一段文本里的密钥与令牌。"""
    sanitized = _ASSIGNMENT_SECRET_RE.sub(_redact_assignment, text)
    for pattern in _TOKEN_SECRET_RES:
        sanitized = pattern.sub("[REDACTED]", sanitized)
    return sanitized


def _redact_assignment(match: re.Match[str]) -> str:
    prefix, quote = match.group(1), match.group(2)
    return f"{prefix}{quote}[REDACTED]{quote}"


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, str):
        return sanitize_text(value)
    if isinstance(value, list):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _sanitize_value(item) for key, item in value.items()}
    return value


def sanitize_materials(day_raw: DayRaw) -> None:
    """就地脱敏一天的素材 —— 正文与 meta 都要，meta 里也会塞命令行。"""
    for material in day_raw.materials:
        material.text = sanitize_text(material.text)
        material.meta = _sanitize_value(material.meta)
