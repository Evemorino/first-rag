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
from src.secret_patterns import ASSIGNMENT_SECRET_RE, TOKEN_SECRET_RES

# 规则本体在 src/secret_patterns.py —— 运行时脱敏与提交门禁共用同一份清单，
# 抄两份就会漂（2026-09-24 就是漂在两边都不认 ark- 形态）。这两个私有别名只是
# 保留本模块原有的调用点名字。
_ASSIGNMENT_SECRET_RE = ASSIGNMENT_SECRET_RE
_TOKEN_SECRET_RES = TOKEN_SECRET_RES


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
