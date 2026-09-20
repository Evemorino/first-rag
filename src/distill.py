"""Distillation orchestration: sanitize -> LLM -> validate -> cap (T016).

The module keeps policy decisions explicit and testable:

1. Secrets are removed before any LLM call or snapshot rewrite (FR-011).
2. Manual notes and trae records bypass the LLM (FR-013).
3. Unknown types get one corrective retry, then are dropped (FR-012).
4. The final entry list is capped by max_entries_per_day (FR-010).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

from src import config, distill_prompt
from src.ark_client import chat
from src.collect import DayRaw, save_snapshot
from src.ingest import Entry
from src.plugins import RawMaterial

logger = logging.getLogger(__name__)


class DistillError(RuntimeError):
    """Raised when LLM output cannot be recovered after one retry."""


_ASSIGNMENT_SECRET_RE = re.compile(
    r"""(?ix)
    (\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|token|secret|password|passwd|pwd|密码)\b\s*[:=]\s*)
    (["']?)
    ([^\s"'<>]{6,})
    \2
    """
)
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
    """Redact common secret/token/password patterns from one string."""
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


def _sanitize_materials(day_raw: DayRaw) -> None:
    for material in day_raw.materials:
        material.text = sanitize_text(material.text)
        material.meta = _sanitize_value(material.meta)


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


def _project(material: RawMaterial) -> str | None:
    project = material.meta.get("project")
    if isinstance(project, str) and project.strip():
        return project.strip()
    cwd = material.meta.get("cwd")
    if isinstance(cwd, str) and cwd.strip():
        return Path(cwd).name
    return None


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
                project=_project(material),
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
            project=_project(material),
            created_at=created_at,
            source_refs=refs,
            distill_version=version,
            related=[],
        ),
        None,
    )


def _split_candidates(
    payload: dict,
    *,
    allowed_types: list[str],
    material_index: dict[str, RawMaterial],
    day: date,
    version: str,
    created_at: datetime,
) -> tuple[list[Entry], list[Any]]:
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


def _invalid_json_messages(
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


def _unknown_type_names(unknown: list[Any]) -> list[str]:
    return sorted(
        {
            str(candidate.get("type"))
            if isinstance(candidate, dict)
            else "missing"
            for candidate in unknown
        }
    )


def _unknown_type_messages(
    messages: list[dict[str, str]],
    previous: str,
    unknown: list[Any],
    allowed_types: list[str],
) -> list[dict[str, str]]:
    invalid_types = _unknown_type_names(unknown)
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


def distill(day_raw: DayRaw) -> list[Entry]:
    """Run the T016 pipeline and return validated entries for ingest."""
    _sanitize_materials(day_raw)
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
            day_raw.distill_run = {
                "model": "none",
                "rubric_hash": rubric_hash,
                "status": "noop",
            }
            save_snapshot(day_raw)
            return []

        direct_version = _distill_version("direct", rubric_hash)
        direct_entries = _direct_entries(
            day_raw,
            schema,
            direct_version,
            created_at,
        )
        llm_materials = _llm_materials(day_raw)

        if not llm_materials:
            entries = _cap_entries(_dedupe_entries(direct_entries), max_entries)
            day_raw.distill_run = {
                "model": "direct",
                "rubric_hash": rubric_hash,
                "status": "ok",
            }
            save_snapshot(day_raw)
            return entries

        model = config.env("CHAT_MODEL")
        version = _distill_version(model, rubric_hash)
        messages = distill_prompt.build_messages(day_raw, schema)
        raw = chat(messages, json_mode=True)
        try:
            payload = _parse_json_object(raw)
        except DistillError:
            raw = chat(_invalid_json_messages(messages, raw), json_mode=True)
            payload = _parse_json_object(raw)

        material_index = _material_index(day_raw.materials)
        valid, unknown = _split_candidates(
            payload,
            allowed_types=allowed_types,
            material_index=material_index,
            day=day_raw.day,
            version=version,
            created_at=created_at,
        )

        if unknown:
            try:
                retry_raw = chat(
                    _unknown_type_messages(
                        messages,
                        raw,
                        unknown,
                        allowed_types,
                    ),
                    json_mode=True,
                )
                retry_payload = _parse_json_object(retry_raw)
                retry_valid, retry_unknown = _split_candidates(
                    retry_payload,
                    allowed_types=allowed_types,
                    material_index=material_index,
                    day=day_raw.day,
                    version=version,
                    created_at=created_at,
                )
                valid.extend(retry_valid)
                if retry_unknown:
                    logger.warning(
                        "distill: dropped %d entries with unknown type after "
                        "retry: %s",
                        len(retry_unknown),
                        ", ".join(_unknown_type_names(retry_unknown)),
                    )
            except DistillError:
                logger.warning(
                    "distill: unknown type retry returned invalid JSON; "
                    "dropping %d entries",
                    len(unknown),
                )

        entries = _cap_entries(
            _dedupe_entries([*direct_entries, *valid]),
            max_entries,
        )
        day_raw.distill_run = {
            "model": model,
            "rubric_hash": rubric_hash,
            "status": "ok",
        }
        save_snapshot(day_raw)
        return entries
    except Exception:
        day_raw.distill_run = {
            "model": model,
            "rubric_hash": rubric_hash,
            "status": "failed",
        }
        save_snapshot(day_raw)
        raise
