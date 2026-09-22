"""构造"让 LLM 改"的追问消息。

蒸馏允许最多两次纠正：一次是输出不是合法 JSON，一次是用了 schema 里没有的
类型。追问的话术和策略都集中在这里 —— 改提示词的时候不用去翻编排代码。

这些消息只追加到原会话末尾，不改写历史，这样 LLM 能看到自己上一轮说了什么。
"""

from __future__ import annotations

from typing import Any


def unknown_type_names(unknown: list[Any]) -> list[str]:
    """把不合法的候选类型去重排序，用来拼进提示里。"""
    return sorted(
        {
            str(candidate.get("type"))
            if isinstance(candidate, dict)
            else "missing"
            for candidate in unknown
        }
    )


def invalid_json_messages(
    messages: list[dict[str, str]],
    previous: str,
) -> list[dict[str, str]]:
    return [
        *messages,
        {"role": "assistant", "content": previous},
        {
            "role": "user",
            "content": (
                "Your previous response was not valid JSON. "
                "Return exactly one JSON object with an entries list. "
                "Do not add Markdown fences."
            ),
        },
    ]


def unknown_type_messages(
    messages: list[dict[str, str]],
    previous: str,
    unknown: list[Any],
    allowed_types: list[str],
) -> list[dict[str, str]]:
    invalid_types = unknown_type_names(unknown)
    return [
        *messages,
        {"role": "assistant", "content": previous},
        {
            "role": "user",
            "content": (
                f"Your previous response used invalid type(s): "
                f"{', '.join(invalid_types)}. "
                f"Allowed types: {', '.join(allowed_types)}. "
                "Return corrected replacements for the invalid entries only. "
                "Use the same JSON contract and preserve source_refs from the "
                "supplied material."
            ),
        },
    ]
