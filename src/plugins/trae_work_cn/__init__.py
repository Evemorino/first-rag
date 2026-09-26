"""trae_work_cn plugin: pre-summarized 轻转换路径（FR-002 / FR-013）。

真实格式（research.md + 本机实测）：~/.trae-cn/memory/projects/<编码路径>/
YYYYMMDD/session_memory_*.jsonl，每行是一条已蒸馏记录
{intent, actions, outcome, learned[], message_summary_time}。

PRD FR-002 的映射规则在这里实现：
- 每条记录生成一条条目 → discover 按「文件#行号」返回 SourceRef，
  parse 只物化那一行（一个 SourceRef 一条素材，符合插件契约）；
- text 由 learned/outcome 字段拼装（intent/actions 不进 text）；
- type 按配置 trae_type_map 的键序取第一个有内容的字段映射，
  未匹配键落默认 reflection（不硬编码阈值/映射，宪法 III）。
源目录只读（宪法 V）。
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from src import config
from src.plugins import Plugin, RawMaterial, SourceRef

logger = logging.getLogger(__name__)

MEMORY_DIR = Path.home() / ".trae-cn" / "memory"
TZ = timezone(timedelta(hours=8))  # Asia/Shanghai

DEFAULT_TYPE = "reflection"


def discover(day: date) -> list[SourceRef]:
    """One SourceRef per record line in the day's session_memory files."""
    if not MEMORY_DIR.is_dir():
        logger.info("trae_work_cn: %s missing, no-op", MEMORY_DIR)
        return []
    refs: list[SourceRef] = []
    pattern = f"{day.strftime('%Y%m%d')}"
    for path in sorted(MEMORY_DIR.glob(f"projects/*/{pattern}/session_memory_*.jsonl")):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as e:
            logger.warning("trae_work_cn: cannot read %s: %s", path, e)
            continue
        for index, line in enumerate(lines):
            if not line.strip():
                continue
            try:
                json.loads(line)
            except json.JSONDecodeError:
                continue
            refs.append(SourceRef(source="trae_work_cn", ref=f"{path}#L{index}", day=day))
    return refs


def _line(ref: str) -> dict:
    """Resolve a `path#L<n>` pointer to its parsed record."""
    path, _, marker = ref.partition("#L")
    index = int(marker) if marker.isdigit() else 0
    try:
        with open(path, encoding="utf-8") as f:
            for i, text in enumerate(f):
                if i == index:
                    return json.loads(text)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("trae_work_cn: cannot parse %s: %s", ref, e)
    return {}


def _parse_ts(value: str | None, day: date) -> datetime:
    if isinstance(value, str):
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(value, fmt).replace(tzinfo=TZ)
            except ValueError:
                continue
    return datetime.combine(day, datetime.min.time(), tzinfo=TZ)


def _field_text(record: dict, field: str) -> str:
    """Assemble one record field into text bullets."""
    value = record.get(field)
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, list):
        return "\n".join(
            f"- {str(item).strip()}" for item in value
            if str(item).strip())
    return ""


def _mapped_type(record: dict, type_map: dict) -> str:
    """trae_type_map 键序优先：第一个有内容的字段决定类型，未匹配落默认。"""
    for field, type_name in type_map.items():
        if _field_text(record, field):
            return type_name
    return DEFAULT_TYPE


# trae 的项目目录名把路径分隔符编码成 '-'（如 -c-Code-first-rag--p2-abc），
# 项目名本身含连字符时无法无歧义还原（真实样本甚至无盘符前缀）。
# 与其给出误导性的 project 过滤值，不如统一留空——payload 允许 project 为 null。


def parse(ref: SourceRef) -> RawMaterial:
    record = _line(ref.ref)

    type_map = {}
    try:
        type_map = config.load_schema().get("trae_type_map", {})
    except Exception as e:  # noqa: BLE001 — 配置坏了也要能并入（落默认类型）
        logger.warning("trae_work_cn: cannot load schema, using default type: %s", e)

    parts = [
        text
        for field in ("learned", "outcome")
        if (text := _field_text(record, field))
    ]
    return RawMaterial(
        source="trae_work_cn",
        ref=ref.ref,
        ts=_parse_ts(record.get("message_summary_time"), ref.day),
        kind="trae_record",
        text="\n".join(parts),
        meta={
            "note_type": _mapped_type(record, type_map),
            "project": None,
        },
    )


PLUGIN = Plugin(name="trae_work_cn", discover=discover, parse=parse)
