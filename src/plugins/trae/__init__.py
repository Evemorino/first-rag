"""trae plugin: pre-summarized 轻转换路径（FR-002 / FR-013），读 `~/.trae`。

`~/.trae/memory/projects/<编码路径>/<YYYYMMDD>/session_memory_*.jsonl`，每行是
一条已蒸馏记录 `{intent, actions[], outcome, learned[], message_summary_time,
message_id, compact_summary_meta{…}}`。

与 `trae_work_cn`（`~/.trae-cn/memory`）**同格式、同映射规则，仅 MEMORY_DIR
不同**——PRD FR-002a 的这条断言已在本机真实数据上核对（2026-09-25），两个根的
记录键集合、字段类型、时间戳可解比例、同级兄弟文件都一致；差异只在数据量
（`~/.trae-cn` 55 文件 / 706 条，`~/.trae` 3 文件 / 5 条）与日期集合。但仍是
**各自实现**（契约规则 6：同源插件对也不例外，不得互相 import），代价由
T053a 的同 fixture 等价性测试补偿。

`~/.trae` 实测：5 条记录的 `learned`/`actions` 恒为 list、`outcome`/`intent`
恒为 str、`message_summary_time` 5/5 可按 `%Y-%m-%d %H:%M:%S` 解出，没有一条
`learned`/`outcome` 同时为空。`compact_summary_meta`（`trigger`/`mode`/
`server_history_id`/`summary_digest`/`created_at_ms`）是纯机器字段——它**不**进
正文，正文只由 `learned`/`outcome` 拼装（FR-002），`intent`/`actions` 也不进。

映射规则（PRD FR-002）：每条记录生成一条条目 → discover 按「文件#行号」返回
SourceRef，parse 只物化那一行（一个 SourceRef 一条素材，符合插件契约）；
type 按配置 `trae_type_map` 的键序取第一个有内容的字段映射，未匹配落默认
reflection（不硬编码阈值/映射，宪法 III）。

`project` 一律留空：项目目录名把路径分隔符编码成 '-'，项目名本身含连字符时无法
无歧义还原。实测真实目录名如
`-Users-nava-Code-llm-rag-first-rag--p2-a36559cd5ee996ba2f18`（`--p2-` 后还挂哈希），
甚至 `brary-Application-Support-TRAE-SOLO-…-vkzdoq--p2-e22ad8c9`（**没有盘符
前缀**，开头就是残缺路径）——与其给出误导性的过滤值，不如留空（payload 允许
project 为 null）。

NG-011：只使用这份明文 `session_memory.jsonl`，不碰 trae 系产品的加密数据库。
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

# 路径允许列表：只读这一层 glob。把日期目录旁的 topics.md、项目目录级的
# project_memory.md、memory/user_profile.md 以及 ~/.trae 下其余全部内容
# （mcps/ permission/ plugins/ installed-plugins.json 等）都挡在外面（NFR-001）。
MEMORY_DIR = Path.home() / ".trae" / "memory"
SOURCE = "trae"
TZ = timezone(timedelta(hours=8))  # Asia/Shanghai

DEFAULT_TYPE = "reflection"

# 实测两个根都是 `%Y-%m-%d %H:%M:%S`；ISO 变体一并留着，因为 trae_work_cn 认它
# 两种，映射规则要求保持一致（等价性测试会比对时间戳）。
_TS_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S")


def discover(day: date) -> list[SourceRef]:
    """One SourceRef per record line in the day's session_memory files."""
    if not MEMORY_DIR.is_dir():
        logger.info("trae: %s missing, no-op", MEMORY_DIR)
        return []
    refs: list[SourceRef] = []
    pattern = day.strftime("%Y%m%d")
    for path in sorted(MEMORY_DIR.glob(f"projects/*/{pattern}/session_memory_*.jsonl")):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as e:
            logger.warning("trae: cannot read %s: %s", path, e)
            continue
        for index, line in enumerate(lines):
            if not line.strip():
                continue
            try:
                json.loads(line)
            except json.JSONDecodeError:
                continue
            refs.append(SourceRef(source=SOURCE, ref=f"{path}#L{index}", day=day))
    return refs


def _line(ref: str) -> dict:
    """Resolve a `path#L<n>` pointer to its parsed record.

    文件在 discover 与 parse 之间消失（或那一行变成坏 JSON）时返回 {}：parse 仍
    产出一条空素材，不把异常抛进 sync（NFR-004）。
    """
    path, _, marker = ref.partition("#L")
    index = int(marker) if marker.isdigit() else 0
    try:
        with open(path, encoding="utf-8") as f:
            for i, text in enumerate(f):
                if i == index:
                    return json.loads(text)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("trae: cannot parse %s: %s", ref, e)
    return {}


def _parse_ts(value, day: date) -> datetime:
    """时间戳归一化为上海时间；缺失或读不出来就落当天零点。"""
    if isinstance(value, str):
        for fmt in _TS_FORMATS:
            try:
                return datetime.strptime(value, fmt).replace(tzinfo=TZ)
            except ValueError:
                continue
    return datetime.combine(day, datetime.min.time(), tzinfo=TZ)


def _field_text(record: dict, field: str) -> str:
    """把一个字段拼成正文：字符串原样，列表每项成一条 bullet。"""
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


def parse(ref: SourceRef) -> RawMaterial:
    record = _line(ref.ref)

    type_map: dict = {}
    try:
        type_map = config.load_schema().get("trae_type_map", {})
    except Exception as e:  # noqa: BLE001 — 配置坏了也要能并入（落默认类型）
        logger.warning("trae: cannot load schema, using default type: %s", e)

    parts = [
        text
        for field in ("learned", "outcome")
        if (text := _field_text(record, field))
    ]
    return RawMaterial(
        source=SOURCE,
        ref=ref.ref,
        ts=_parse_ts(record.get("message_summary_time"), ref.day),
        kind="trae_record",
        text="\n".join(parts),
        meta={
            "note_type": _mapped_type(record, type_map),
            "project": None,
        },
    )


PLUGIN = Plugin(name=SOURCE, discover=discover, parse=parse)
