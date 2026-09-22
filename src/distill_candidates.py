"""把 LLM 吐出的候选条目整理成合法 Entry。

从 distill.py 拆出来是因为它是一段完整独立的职责：拿一份 candidates 列表，
按 schema 校验类型、规整标签、核对来源引用，分成"能用"和"待重试"两堆。
它不关心 LLM 怎么调、快照怎么写。

分层上属于编排层，只依赖数据模型（Entry / RawMaterial）。
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from src.ingest import Entry
from src.plugins import RawMaterial

logger = logging.getLogger(__name__)


def project_of(material: RawMaterial) -> str | None:
    """条目属于哪个项目：优先 meta.project，退回 cwd 的目录名。"""
    project = material.meta.get("project")
    if isinstance(project, str) and project.strip():
        return project.strip()
    cwd = material.meta.get("cwd")
    if isinstance(cwd, str) and cwd.strip():
        from pathlib import Path

        return Path(cwd).name
    return None


def _normalize_tags(raw_tags: Any, type_name: str) -> list[str] | None:
    if not isinstance(raw_tags, list):
        return None
    tags: list[str] = []
    for raw_tag in raw_tags:
        if not isinstance(raw_tag, str):
            continue
        tag = raw_tag.strip()
        if tag and tag not in tags:
            tags.append(tag)
    if not tags:
        return None
    if len(tags) == 1 and type_name not in tags:
        tags.append(type_name)
    return tags[:5]


def _normalize_refs(
    raw_refs: Any,
    material_index: dict[str, RawMaterial],
) -> list[str] | None:
    """引用必须真的指向当天采集到的素材 —— 编造的 ref 会让条目无法溯源。"""
    if not isinstance(raw_refs, list):
        return None
    refs: list[str] = []
    for raw_ref in raw_refs:
        if (
            isinstance(raw_ref, str)
            and raw_ref in material_index
            and raw_ref not in refs
        ):
            refs.append(raw_ref)
    return refs or None


def _candidate_entry(
    candidate: Any,
    *,
    allowed_types: list[str],
    material_index: dict[str, RawMaterial],
    day: date,
    version: str,
    created_at: datetime,
) -> tuple[Entry | None, str | None]:
    """返回 (条目, 失败原因)。原因区分 unknown_type（可重试）与 malformed。"""
    if not isinstance(candidate, dict):
        return None, "malformed"

    type_name = candidate.get("type")
    if type_name not in allowed_types:
        return None, "unknown_type"

    text = candidate.get("text")
    if not isinstance(text, str) or not text.strip():
        return None, "malformed"
    text = text.strip()
    if len(text) > 1000:
        logger.warning("distill: candidate text exceeds 1000 chars, truncating")
        text = text[:1000]

    tags = _normalize_tags(candidate.get("tags"), type_name)
    refs = _normalize_refs(candidate.get("source_refs"), material_index)
    if tags is None or refs is None:
        return None, "malformed"

    material = material_index[refs[0]]
    return (
        Entry(
            text=text,
            date=day.isoformat(),
            type=type_name,
            tags=tags,
            source=material.source,
            project=project_of(material),
            created_at=created_at,
            source_refs=refs,
            distill_version=version,
            related=[],
        ),
        None,
    )


def split_candidates(
    payload: dict,
    *,
    allowed_types: list[str],
    material_index: dict[str, RawMaterial],
    day: date,
    version: str,
    created_at: datetime,
) -> tuple[list[Entry], list[Any]]:
    """分成 (可用条目, 类型不合法的候选)。后者可以带着提示再问一次 LLM。"""
    valid: list[Entry] = []
    unknown: list[Any] = []
    malformed = 0
    for candidate in payload["entries"]:
        entry, reason = _candidate_entry(
            candidate,
            allowed_types=allowed_types,
            material_index=material_index,
            day=day,
            version=version,
            created_at=created_at,
        )
        if entry is not None:
            valid.append(entry)
        elif reason == "unknown_type":
            unknown.append(candidate)
        else:
            malformed += 1
    if malformed:
        logger.warning("distill: dropped %d malformed entries", malformed)
    return valid, unknown
