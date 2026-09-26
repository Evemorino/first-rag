"""qoder plugin: parse ~/.qoder/projects/<slug>/<uuid>.jsonl (raw path).

schema 是 Claude 系 JSONL 的变体（2026-09-25 本机实测 3 会话 / 38629 行），
每行一条记录、`type` 决定它是什么。只有三件事进素材：

- `type=user` 且 `origin.kind == "human"`：真实用户输入（实测 146 条）
- `type=assistant` 里 content[].type == "text" 的块（实测 971 块）
- 失败的工具调用：`toolUseResult.isError` 为真或 `exitCode` 非零（实测 138 条）

**注入物不是用户输入**。user 角色下 7397 条记录里只有 146 条是人说的：
7051 条工具结果回填、106 条压缩摘要（`isCompactSummary`，正文里引着用户原话）、
5 条 `origin.kind == "task-notification"`、88 条 `isMeta` 元记录。user 记录总共
258 个 text 块，其中只有 146 个是人的 —— 按「user 角色就取 text」实现会多收
112 条机器文本，素材量虚增 77%。这里用**正标记**（origin.kind == "human"）
而不是黑名单：将来新出现的注入类型不会被悄悄收进来。
助手侧同理：13524 条记录的 content 块里 tool_use 7051、thinking 5071、
redacted_thinking 431，text 只有 971 —— 只取 text 块。

**时间戳双编码**（PRD FR-002a）：消息类记录是 ISO 8601 UTC 字符串，bookkeeping
（active-leaf 14412 条 / runtime-config 240 条）是 epoch 毫秒整数，同一文件内
混排 —— 解析 MUST 先判类型，假定单一编码会在真实文件上直接崩。另有 1016 条
bookkeeping 记录根本没有时间戳，它们本就不产素材，跳过即可。

struggle 语义（FR-008）：连续失败轮次只在「工具成功输出」时归零；
助手插话不打断连击计数（与 codex / kimi_code 同一条语义）。
时间戳统一折算 Asia/Shanghai 后判定归属日。源目录只读（宪法 V）。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from src.plugins import Plugin, RawMaterial, SourceRef

logger = logging.getLogger(__name__)

# 路径允许列表：只读这一个 glob，且只挑 .jsonl（NFR-001）。
# 同目录树下的其它文件（如 ~/.qoder-cn/ 的 state.json）不碰。
PROJECTS_DIR = Path.home() / ".qoder" / "projects"
SOURCE = "qoder"
TZ = timezone(timedelta(hours=8))  # Asia/Shanghai

# 单个会话合并后的转写上限，与 codex / claude_code 插件保持一致
MAX_SESSION_CHARS = 200000

# 工具报错正文的截断长度（与 codex 一致）
MAX_ERROR_CHARS = 2000


def _iter_records(path: str | Path) -> Iterator[dict]:
    """逐行读 JSONL；半截行跳过（NFR-004 的本地版）。"""
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                yield record


def _as_shanghai(ts) -> datetime | None:
    """时间戳归一化为上海时间；两种编码都吃，读不出来返回 None。

    ISO 8601 字符串（消息类）与 epoch 毫秒整数（bookkeeping 类）在同一个文件
    里混排，所以先判类型。没有时间戳的 1016 条 bookkeeping 记录走 None。
    """
    if isinstance(ts, bool):  # bool 是 int 的子类，但不是时间戳
        return None
    if isinstance(ts, str):
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(TZ)
        except ValueError:
            return None
    if isinstance(ts, (int, float)):
        try:
            return datetime.fromtimestamp(ts / 1000, tz=timezone.utc).astimezone(TZ)
        except (OverflowError, OSError, ValueError):
            return None
    return None


def _content_blocks(record: dict) -> list[dict]:
    content = (record.get("message") or {}).get("content")
    if not isinstance(content, list):
        return []
    return [block for block in content if isinstance(block, dict)]


def _text_blocks(record: dict) -> str:
    """只取 type=text 的块：thinking / redacted_thinking / tool_use 都不是正文。"""
    return "\n".join(
        block["text"] for block in _content_blocks(record)
        if block.get("type") == "text" and block.get("text")
    )


def _is_human(record: dict) -> bool:
    """真实用户输入的正标记（允许列表，不是黑名单）。"""
    origin = record.get("origin")
    return isinstance(origin, dict) and origin.get("kind") == "human"


def _tool_result_text(record: dict) -> str:
    """tool_result 块的正文：content 可能是字符串，也可能是块列表。"""
    parts: list[str] = []
    for block in _content_blocks(record):
        if block.get("type") != "tool_result":
            continue
        content = block.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            parts.extend(sub["text"] for sub in content
                         if isinstance(sub, dict) and sub.get("text"))
    return "\n".join(part for part in parts if part).strip()


def _has_tool_result(record: dict) -> bool:
    return any(block.get("type") == "tool_result" for block in _content_blocks(record))


def _tool_failure_text(record: dict) -> str:
    """失败的工具调用 → "[error] …"；不是失败返回 ''。

    失败判据（实测 138 条）：`toolUseResult.isError` 为真，或 `exitCode` 是
    非零整数 —— 与 codex 不同，这里不需要从输出正文里**找**失败信号，标志位
    是显式给出的，所以正文为空也照样记账（kimi_code 同此：失败已知，就不该因为
    没抓到输出而把信号丢掉）。
    """
    result = record.get("toolUseResult")
    if not isinstance(result, dict):
        return ""
    code = result.get("exitCode")
    failed = result.get("isError") is True or (
        isinstance(code, int) and not isinstance(code, bool) and code != 0)
    if not failed:
        return ""
    return f"[error] {_tool_result_text(record)[:MAX_ERROR_CHARS]}"


def _material_text(record: dict) -> str:
    """该记录贡献的素材文本；不产素材返回 ''。

    这是「注入物不得产生素材」的唯一出口：user 角色必须带 human 正标记才算，
    助手角色只认 text 块。
    """
    failure = _tool_failure_text(record)
    if failure:
        return failure
    record_type = record.get("type")
    if record_type == "assistant" or (record_type == "user" and _is_human(record)):
        return _text_blocks(record)
    return ""


def discover(day: date) -> list[SourceRef]:
    """One SourceRef per session file with material on `day`. [] if nothing.

    只认「当天真能产出素材」的会话：一天里每个跨夜会话都会写 active-leaf，
    照单全收等于往快照里塞一堆空素材。
    """
    if not PROJECTS_DIR.is_dir():
        logger.info("qoder: %s missing, no-op", PROJECTS_DIR)
        return []
    refs = []
    for path in sorted(PROJECTS_DIR.glob("*/*.jsonl")):
        try:
            if _file_hits_day(path, day):
                refs.append(SourceRef(source=SOURCE, ref=str(path), day=day))
        except OSError as e:
            logger.warning("qoder: cannot read %s: %s", path, e)
    return refs


def _file_hits_day(path: Path, day: date) -> bool:
    """Cheap mtime prefilter, then per-record timestamp check."""
    day_start = datetime.combine(day, datetime.min.time(), tzinfo=TZ)
    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    if mtime < day_start:
        return False  # 最后写入都在当天开始前：不可能含当天记录
    for record in _iter_records(path):
        ts = _as_shanghai(record.get("timestamp"))
        if ts is None or ts.date() != day:
            continue
        if _material_text(record):
            return True
    return False


def parse(ref: SourceRef) -> RawMaterial:
    """Extract user text, assistant text and tool failures for ref.day, merged."""
    meta: dict = {"cwd": None, "session_id": Path(ref.ref).stem, "git_branch": None}
    parts: list[str] = []
    error_count = 0
    error_run = 0
    struggle = 0
    first_ts: datetime | None = None

    for record in _iter_records(ref.ref):
        ts = _as_shanghai(record.get("timestamp"))
        if ts is None or ts.date() != ref.day:
            continue  # 没有时间戳的是 bookkeeping（1016 条），本就不产素材
        if meta["cwd"] is None and record.get("cwd"):
            meta["cwd"] = record["cwd"]
        if meta["git_branch"] is None and record.get("gitBranch"):
            meta["git_branch"] = record["gitBranch"]

        failure = _tool_failure_text(record)
        if failure:
            error_count += 1
            error_run += 1
            struggle = max(struggle, error_run)
            contribution = failure
        elif _has_tool_result(record):
            error_run = 0  # 工具成功：挣扎结束
            contribution = ""
        else:
            contribution = _material_text(record)

        if contribution:
            parts.append(contribution)
            if first_ts is None:
                first_ts = ts

    if first_ts is None:
        first_ts = datetime.combine(ref.day, datetime.min.time(), tzinfo=TZ)
    return RawMaterial(
        source=SOURCE,
        ref=ref.ref,
        ts=first_ts,
        kind="message",
        text="\n---\n".join(parts)[:MAX_SESSION_CHARS],
        meta={**meta, "struggle_rounds": struggle, "error_count": error_count},
    )


PLUGIN = Plugin(name=SOURCE, discover=discover, parse=parse)
