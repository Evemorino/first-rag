"""好例子：把蒸馏编排拆成可验证的边界和明确的失败策略。

来源依据：
1. openai/openai-cookbook：
   https://github.com/openai/openai-cookbook
   需要程序解析模型输出时，应先定义结构，再把解析失败当成显式分支。
2. OWASP LLM Prompt Injection Prevention Cheat Sheet：
   https://github.com/OWASP/www-project-top-10-for-large-language-model-applications
   外部素材进入模型前必须被视为不可信数据，并且不能出现在日志中。

这个文件是学习对照，不是 T016 的正式实现。
"""

import json
import re


SECRET_RE = re.compile(r"\bsk-[A-Za-z0-9_-]{6,}\b")


def sanitize(text: str) -> str:
    """在素材进入模型或快照前先脱敏。"""
    # 好在哪里：脱敏发生在 LLM 调用之前，而不是拿到回答后再补救。
    return SECRET_RE.sub("[REDACTED]", text)


def parse_entries(raw: str) -> list[dict]:
    """只接受严格的 JSON 对象，并验证 entries 是列表。"""
    # 好在哪里：解析边界只做结构检查，类型校验留给下一层单独处理。
    payload = json.loads(raw)
    if not isinstance(payload, dict) or not isinstance(payload.get("entries"), list):
        raise ValueError("LLM output must be {'entries': [...]} ")
    return payload["entries"]


def distill(day_raw: dict, allowed_types: list[str], limit: int) -> list[dict]:
    """演示一个最小但完整的蒸馏流水线。"""
    # 好在哪里：先脱敏并写回素材，保证后续模型调用和快照都看不到密钥。
    materials = [
        {**material, "text": sanitize(material["text"])}
        for material in day_raw["materials"]
    ]

    direct = [
        {
            "text": material["text"],
            "type": material.get("note_type", "reflection"),
            "source_refs": [material["ref"]],
        }
        for material in materials
        if material["kind"] in {"note", "trae_record"}
    ]
    llm_materials = [
        material
        for material in materials
        if material["kind"] not in {"note", "trae_record"}
    ]

    # 好在哪里：没有需要 LLM 处理的素材时，完全不发起 API 调用。
    if not llm_materials:
        return direct[:limit]

    entries = parse_entries(call_llm(llm_materials))
    valid = []
    invalid = []
    for entry in entries:
        # 好在哪里：类型校验是代码边界，不依赖模型“应该会遵守”。
        if entry.get("type") in allowed_types:
            valid.append(entry)
        else:
            invalid.append(entry)

    # 好在哪里：只对未知类型重试一次，并在纠正 prompt 中给出允许枚举。
    if invalid:
        corrected = parse_entries(call_llm(llm_materials, invalid_types=invalid))
        valid.extend(
            entry for entry in corrected if entry.get("type") in allowed_types
        )

    # 好在哪里：熔断在返回前执行，保证调用方拿到的条目数不会失控。
    return (direct + valid)[:limit]


def call_llm(materials: list[dict], invalid_types: list[dict] | None = None) -> str:
    """占位函数：真实项目会调用 ark_client.chat(..., json_mode=True)。"""
    raise NotImplementedError
