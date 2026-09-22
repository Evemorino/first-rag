"""T012 的幂等 Point ID 契约测试。

数据模型要求：
point_id = uuid5(NAMESPACE_URL, f"{source}|{date}|{content_hash}")
content_hash = sha256(text) 的前 16 个十六进制字符。
"""

import hashlib
import uuid

import pytest

from src import ids


def test_content_hash_uses_first_16_sha256_hex_characters():
    text = "first-rag id probe"
    expected = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]

    assert ids.content_hash(text) == expected


def test_point_id_uses_documented_uuid5_formula():
    source = "claude_code"
    day = "2026-09-20"
    text = "first-rag id probe"
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    expected = uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"{source}|{day}|{digest}",
    )

    assert ids.point_id(source, day, text) == expected


def test_point_id_is_deterministic_for_same_input():
    first = ids.point_id("claude_code", "2026-09-20", "same text")
    second = ids.point_id("claude_code", "2026-09-20", "same text")

    assert first == second


def test_point_id_changes_when_identity_input_changes():
    base = ids.point_id("claude_code", "2026-09-20", "same text")

    changed_source = ids.point_id("codex", "2026-09-20", "same text")
    changed_day = ids.point_id("claude_code", "2026-09-21", "same text")
    changed_text = ids.point_id("claude_code", "2026-09-20", "different text")

    assert changed_source != base
    assert changed_day != base
    assert changed_text != base


@pytest.mark.parametrize("source, day, text, message", [
    ("", "2026-09-20", "text", "source must not be empty"),
    ("claude_code", "", "text", "date must not be empty"),
    ("claude_code", "2026-09-20", "", "text must not be empty"),
])
def test_point_id_rejects_empty_identity_components(source, day, text, message):
    """报错要指名道姓说清是哪个字段空了，否则用户只能猜。"""
    with pytest.raises(ValueError) as excinfo:
        ids.point_id(source, day, text)

    assert str(excinfo.value) == message
