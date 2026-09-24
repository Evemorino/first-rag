"""Distillation orchestration: sanitize -> LLM -> validate -> cap (T016).

The module keeps policy decisions explicit and testable:

1. Secrets are removed before any LLM call or snapshot rewrite (FR-011).
2. Manual notes and trae records bypass the LLM (FR-013).
3. Unknown types get one corrective retry, then are dropped (FR-012).
4. The final entry list is capped by max_entries_per_day (FR-010).

拆出去的部分：
- src/sanitize.py            脱敏（宪法 V 的安全边界，独立职责）
- src/distill_candidates.py  候选条目校验与整理
- src/distill_messages.py    追问 LLM 的话术
"""

from __future__ import annotations

import hashlib
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date, datetime
from typing import Any

from src import config, distill_prompt, sanitize
from src.ark_client import chat
from src.collect import DayRaw, save_snapshot
from src.distill_batches import split_for_batches
from src.distill_candidates import project_of, split_candidates
from src.distill_messages import (
    invalid_json_messages,
    unknown_type_messages,
    unknown_type_names,
)
from src.ingest import Entry
from src.plugins import RawMaterial

logger = logging.getLogger(__name__)


class DistillError(RuntimeError):
    """Raised when LLM output cannot be recovered after one retry."""


def _rubric_hash(schema: dict) -> str:
    rubric = {"types": schema["types"], "distill": schema["distill"]}
    encoded = json.dumps(
        rubric,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _distill_version(model: str, rubric_hash: str) -> str:
    return f"{model}+rubric@{rubric_hash[:8]}"


def _allowed_types(schema: dict) -> list[str]:
    return [item["name"] for item in schema["types"]]


def _direct_type(material: RawMaterial, allowed_types: list[str]) -> str:
    requested = material.meta.get("note_type", "reflection")
    if requested in allowed_types:
        return requested
    fallback = "reflection" if "reflection" in allowed_types else allowed_types[0]
    logger.warning(
        "distill: unknown direct type '%s' on %s, using %s",
        requested,
        material.ref,
        fallback,
    )
    return fallback


def _direct_entries(
    day_raw: DayRaw,
    schema: dict,
    version: str,
    created_at: datetime,
) -> list[Entry]:
    """手记与 trae 记录不过 LLM（FR-013），直接成型。"""
    allowed = _allowed_types(schema)
    entries: list[Entry] = []
    for material in day_raw.materials:
        if material.kind not in ("note", "trae_record"):
            continue
        text = material.text.strip()
        if not text:
            continue
        type_name = _direct_type(material, allowed)
        tags = list(dict.fromkeys([material.source, type_name]))
        entries.append(
            Entry(
                text=text,
                date=day_raw.day.isoformat(),
                type=type_name,
                tags=tags,
                source=material.source,
                project=project_of(material),
                created_at=created_at,
                source_refs=[material.ref],
                distill_version=version,
                related=[],
            )
        )
    return entries


def _llm_materials(day_raw: DayRaw) -> list[RawMaterial]:
    return [
        material
        for material in day_raw.materials
        if material.kind not in ("note", "trae_record") and material.text.strip()
    ]


def _material_index(materials: list[RawMaterial]) -> dict[str, RawMaterial]:
    index: dict[str, RawMaterial] = {}
    for material in materials:
        index.setdefault(material.ref, material)
    return index


def _dedupe_entries(entries: list[Entry]) -> list[Entry]:
    seen: set[tuple[str, str, str]] = set()
    result: list[Entry] = []
    for entry in entries:
        key = (entry.source, entry.date, entry.text)
        if key in seen:
            continue
        seen.add(key)
        result.append(entry)
    return result


def _cap_entries(entries: list[Entry], limit: int) -> list[Entry]:
    if len(entries) <= limit:
        return entries
    logger.warning(
        "distill: circuit breaker capped entries from %d to %d",
        len(entries),
        limit,
    )
    return entries[:limit]


def _parse_json_object(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DistillError("LLM returned invalid JSON") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("entries"), list):
        raise DistillError("LLM JSON must be an object with an entries list")
    return payload


def _finish(day_raw: DayRaw, model: str, rubric_hash: str, status: str) -> None:
    """记录本次蒸馏结果并落盘快照 —— 三条出口路径都要做这一件事。"""
    day_raw.distill_run = {
        "model": model,
        "rubric_hash": rubric_hash,
        "status": status,
    }
    save_snapshot(day_raw)


def _parse_or_retry(messages: list[dict[str, str]], raw: str) -> dict:
    """解析 LLM 输出；不是合法 JSON 就带着提示再问一次，还不行才放弃。"""
    try:
        return _parse_json_object(raw)
    except DistillError:
        retry_raw = chat(invalid_json_messages(messages, raw), json_mode=True)
        return _parse_json_object(retry_raw)


def _correct_unknown_types(
    messages: list[dict[str, str]],
    raw: str,
    unknown: list[Any],
    allowed_types: list[str],
    material_index: dict[str, RawMaterial],
    day_raw: DayRaw,
    version: str,
    created_at: datetime,
) -> list[Entry]:
    """带着"你用了不存在的类型"再问一次；失败就把这些条目丢掉（FR-012）。"""
    try:
        retry_raw = chat(
            unknown_type_messages(messages, raw, unknown, allowed_types),
            json_mode=True,
        )
        retry_payload = _parse_json_object(retry_raw)
        retry_valid, retry_unknown = split_candidates(
            retry_payload,
            allowed_types=allowed_types,
            material_index=material_index,
            day=day_raw.day,
            version=version,
            created_at=created_at,
        )
    except DistillError:
        logger.warning(
            "distill: unknown type retry returned invalid JSON; "
            "dropping %d entries",
            len(unknown),
        )
        return []

    if retry_unknown:
        logger.warning(
            "distill: dropped %d entries with unknown type after retry: %s",
            len(retry_unknown),
            ", ".join(unknown_type_names(retry_unknown)),
        )
    return retry_valid


def _llm_entries(
    messages: list[dict[str, str]],
    allowed_types: list[str],
    day_raw: DayRaw,
    version: str,
    created_at: datetime,
) -> list[Entry]:
    """走一次 LLM 往返：调用 → 解析 → 校验 → 未知类型纠正一次。"""
    raw = chat(messages, json_mode=True)
    payload = _parse_or_retry(messages, raw)

    material_index = _material_index(day_raw.materials)
    valid, unknown = split_candidates(
        payload,
        allowed_types=allowed_types,
        material_index=material_index,
        day=day_raw.day,
        version=version,
        created_at=created_at,
    )
    if unknown:
        valid.extend(
            _correct_unknown_types(
                messages,
                raw,
                unknown,
                allowed_types,
                material_index,
                day_raw,
                version,
                created_at,
            )
        )
    return valid


def _batched_llm_entries(
    day_raw: DayRaw,
    schema: dict,
    allowed_types: list[str],
    version: str,
    created_at: datetime,
) -> list[Entry]:
    """按 batch_max_chars 分批蒸馏。

    一次往返要吐的 JSON 太长会被服务端的 completion 上限掐断（实测断在 ~6.9k
    字符，finish_reason='length'），而 _parse_or_retry 的重试是原样再发一遍，
    于是同一处再断一次，整天以 DistillError 收场。切小每批的输出就断不了。

    每批只看得见本批素材，source_refs 也因此只能引用本批；没有 LLM 素材的批次
    直接跳过 —— 那些内容已经走直并入入库了，再问一次只是白烧 token。

    并行化（NFR-006 sync ≤5min）：串行 11 批 × ~85s ≈ 983s 严重超预算，改成
    ThreadPoolExecutor 并发发各批请求。正确性关键在 pool.map —— 它按提交顺序
    收集结果，线程完成顺序虽不确定，返回的 entries 仍与串行逐批顺序一致；
    _cap_entries 取前 N 条，顺序乱了会裁错（宪法 IV 的确定性）。
    """
    budget = schema["distill"]["batch_max_chars"]
    workers = schema["distill"].get("parallel_workers", 4)  # 默认值仅供缺键时兜底

    jobs = []
    for group in split_for_batches(day_raw.materials, budget):
        batch = replace(day_raw, materials=group)
        if not _llm_materials(batch):
            continue
        jobs.append((distill_prompt.build_messages(batch, schema), batch))

    if not jobs:
        return []

    def _run(job):
        messages, batch = job
        return _llm_entries(messages, allowed_types, batch, version, created_at)

    entries: list[Entry] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for batch_entries in pool.map(_run, jobs):
            entries.extend(batch_entries)
    return entries


def distill(day_raw: DayRaw) -> list[Entry]:
    """Run the T016 pipeline and return validated entries for ingest."""
    sanitize.sanitize_materials(day_raw)
    save_snapshot(day_raw)

    model = "unknown"
    rubric_hash = ""
    try:
        schema = config.load_schema()
        allowed_types = _allowed_types(schema)
        rubric_hash = _rubric_hash(schema)
        max_entries = schema["distill"]["max_entries_per_day"]
        created_at = datetime.now(tz=config.TZ)

        if not day_raw.materials:
            _finish(day_raw, "none", rubric_hash, "noop")
            return []

        direct_entries = _direct_entries(
            day_raw,
            schema,
            _distill_version("direct", rubric_hash),
            created_at,
        )
        if not _llm_materials(day_raw):
            entries = _cap_entries(_dedupe_entries(direct_entries), max_entries)
            _finish(day_raw, "direct", rubric_hash, "ok")
            return entries

        model = config.env("CHAT_MODEL")
        version = _distill_version(model, rubric_hash)
        valid = _batched_llm_entries(
            day_raw, schema, allowed_types, version, created_at)

        entries = _cap_entries(
            _dedupe_entries([*direct_entries, *valid]),
            max_entries,
        )
        _finish(day_raw, model, rubric_hash, "ok")
        return entries
    except Exception:
        _finish(day_raw, model, rubric_hash, "failed")
        raise
