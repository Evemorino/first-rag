"""qoder_cn plugin: parse ~/.qoder-cn/projects/<slug>/<uuid>.jsonl (raw path).

与 `qoder` 同一套 schema（字段名一致），但**是各自独立的实现**：两者不得互相
import（契约规则 6）。代价由 T053a 的同源等价性测试补偿——同一份 fixture 喂给
两个解析器，除 `source` 外逐字段断言相同。

2026-09-25 本机实测 12 会话 / 34513 行。进素材的仍是三件事：

- `type=user` 且 `origin.kind == "human"`：真实用户输入（实测 284 条）
- `type=assistant` 里 content[].type == "text" 的块（实测 1804 块）
- 失败的工具调用：`toolUseResult.isError` 为真或 `exitCode` 非零（实测 77 条）

**同一套 schema 不等于同样的分布**，而这里的分布差异会直接改变过滤的分量：
user 角色下 5968 条记录里只有 284 条是人说的，其余是 5584 条工具结果回填、
65 条 `origin.kind == "task-notification"`（qoder 那边只有 5 条，**13 倍**）、
25 条 `isMeta` 元记录、10 条压缩摘要。照抄「只丢 isCompactSummary」的实现，
在 qoder 上看着没事，在这里会多收 65 条机器文本。所以用**正标记**
（origin.kind == "human"）而不是黑名单：将来新出现的注入类型不会被悄悄收进来。
助手侧同理：12670 条记录的 content 块里 tool_use 5583、thinking 5283、text 1804
（另有 redacted_thinking 431 条在 qoder 有、这里一块没有——同一套 schema，
不同版本写出来的差别），只取 text 块。human 记录里还有 5 条挂着 image 块
（base64 图片），同样被 text 过滤挡在外面。

**时间戳双编码**（PRD FR-002a）：消息类是 ISO 8601 UTC 字符串
（user 5968 / assistant 12670），bookkeeping 是 epoch 毫秒整数
（active-leaf 12505 / runtime-config 450），同一文件内混排 —— 解析 MUST 先判
类型，假定单一编码会在真实文件上 AttributeError 崩掉。另有 1466 条 bookkeeping
根本没有时间戳，它们本就不产素材，跳过即可。

`toolUseResult` 实测 5577 条是 dict、2 条是字符串：字符串里没有 exitCode /
isError，即没有失败证据，既不该崩也不该平白记一次失败。

struggle 语义（FR-008）：连续失败轮次只在「工具成功输出」时归零；助手插话不
打断连击计数（与 codex / kimi_code / qoder 同一条语义）。时间戳统一折算
Asia/Shanghai 后判定归属日。源目录只读（宪法 V）。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from src.plugins import Plugin, RawMaterial, SourceRef

logger = logging.getLogger(__name__)

# 路径允许列表：只读这一个 glob，且只挑 .jsonl（NFR-001）。两层深度把
# `subagents/*.jsonl`（实测 113 个子代理转写）和每个会话旁边的 state.json /
# compression-v2/state.json 一并挡在外面。
PROJECTS_DIR = Path.home() / ".qoder-cn" / "projects"
SOURCE = "qoder_cn"
TZ = timezone(timedelta(hours=8))  # Asia/Shanghai

# 单个会话合并后的转写上限，与 codex / claude_code / qoder 插件保持一致
MAX_SESSION_CHARS = 200000

# 工具报错正文的截断长度（与 codex / qoder 一致）
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
    里混排，所以先判类型。没有时间戳的 1466 条 bookkeeping 记录走 None。
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
    """只取 type=text 的块：thinking / image / tool_use 都不是正文。"""
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

    失败判据（实测 77 条）：`toolUseResult.isError` 为真，或 `exitCode` 是非零
    整数。与 codex 不同，这里不需要从输出正文里**找**失败信号，标志位是显式给
    出的，所以正文为空也照样记账（kimi_code / qoder 同此：失败已知，就不该因为
    没抓到输出而把信号丢掉）。

    实测另有 2 条 `toolUseResult` 是字符串——没有标志位即没有失败证据，返回 ''。
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
        logger.info("qoder_cn: %s missing, no-op", PROJECTS_DIR)
        return []
    refs = []
    for path in sorted(PROJECTS_DIR.glob("*/*.jsonl")):
        try:
            if _file_hits_day(path, day):
                refs.append(SourceRef(source=SOURCE, ref=str(path), day=day))
        except OSError as e:
            logger.warning("qoder_cn: cannot read %s: %s", path, e)
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
            continue  # 没有时间戳的是 bookkeeping（1466 条），本就不产素材
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
