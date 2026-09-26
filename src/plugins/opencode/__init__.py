"""opencode plugin: raw 会话蒸馏路径（FR-002 / FR-002a），读 `~/.local/share/opencode/opencode.db`。

只读 `session_message` 一张表：

    session_message(id, session_id, type, seq, time_created, time_updated, data)

`data` 是 JSON，`type` 决定它的形状（2026-09-25 只读勘察，全库 85 行）：
`user` 的真用户输入在 `data.text`；`assistant` 的在 `data.content` 块列表里
（只有 `type == "text"` 的块进正文）；`idle`（`data.outcome` = succeeded/failed）
与 `model-switched` 是会话簿记，**不是素材**。

FR-008 的失败判据取 `content` 里 `type == "tool"` 且 `state.status == "error"`
的块（实测 63 completed / 5 error），正文写 `[error] {state.error.message}`。
`idle` 的 `outcome == "failed"` **不**计入：那是会话空闲的结束状态，不是工具失败，
把它算进来会把「会话正常结束」误报成挣扎（实测 14 条 idle 里有 failed）。

凭据边界（NFR-001）：同一库内有 `credential`/`account`/`control_account` 三张表，
本插件的**表允许列表只有 `session_message`**，一条都不读（单测用哨兵值钉住）。
`cwd` 只能从 `session_v2.directory` 取，为守住最小允许列表**故意不取**——
实测 `session_message.data` 里没有 `path`/`directory`/`cwd` 任何一键，故
`meta.cwd` 恒为 None，而不是留一个猜出来的值。

源目录只读（宪法 V）：一律 `file:…?mode=ro` + `uri=True`（NFR-001 机械拦截）。
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Iterator

from src.plugins import Plugin, RawMaterial, SourceRef

logger = logging.getLogger(__name__)

DB_PATH = Path.home() / ".local/share/opencode" / "opencode.db"
SOURCE = "opencode"
TZ = timezone(timedelta(hours=8))  # Asia/Shanghai

# 表允许列表：只读这一张。凭据表（credential/account/control_account）不在其中。
ALLOWED_TABLES = frozenset({"session_message"})

MAX_ERROR_CHARS = 2000
MAX_SESSION_CHARS = 200000


def _connect() -> sqlite3.Connection:
    """只读连接：不改 `db` 文件本身、不写产品数据。

    注意别把它读成「绝不落 sidecar」——2026-09-26 实测：sidecar 不存在时只读连接
    会自己造出 `-wal`/`-shm`；源目录不可写时则直接打不开（靠 `_query` 的
    `except sqlite3.Error` 降级为空数据）。边界全文见 PRD NFR-001「已知边界」。
    """
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _day_bounds_ms(day: date) -> tuple[int, int]:
    """上海时区当天 [起, 止) 的 epoch 毫秒——SQL 侧按它切天，不用逐行解 JSON。"""
    start = datetime.combine(day, time.min, tzinfo=TZ)
    return int(start.timestamp() * 1000), int((start + timedelta(days=1)).timestamp() * 1000)


def _ms_to_dt(value) -> datetime | None:
    if isinstance(value, bool):  # bool 是 int 的子类，但不是时间戳
        return None
    if not isinstance(value, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc).astimezone(TZ)
    except (OverflowError, OSError, ValueError):
        return None


def _as_dict(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _error_text(state: dict) -> str:
    """`state.error` 实测是 `{type, message}` 字典；万一换形状也不许抛。"""
    error = state.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str) and message.strip():
            return f"[error] {message[:MAX_ERROR_CHARS]}"
        return f"[error] {json.dumps(error, ensure_ascii=False)[:MAX_ERROR_CHARS]}"
    if isinstance(error, str) and error.strip():
        return f"[error] {error[:MAX_ERROR_CHARS]}"
    return "[error]"


def _events(kind, record: dict) -> Iterator[tuple[str, str]]:
    """把一条记录摊成按转录顺序排列的事件：`text` 素材 / `error` 失败 / `tool_ok` 成功。

    `kind` 取自 `session_message.type` **列**（不在 `data` JSON 里）。

    块一级而不是记录一级，是因为 assistant 一条记录里同时挂着正文与工具调用
    （实测 content 块 reasoning 51 / tool 68 / text 30）。这样「连续失败算一次
    挣扎、成功打断连击」的口径与 qoder/workbuddy 完全一致。
    """
    if kind == "user":
        text = record.get("text")
        if isinstance(text, str) and text.strip():
            yield ("text", text)
        return
    if kind != "assistant":
        return  # idle / model-switched 是簿记
    content = record.get("content")
    if not isinstance(content, list):
        return
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            text = block.get("text")
            if isinstance(text, str) and text.strip():
                yield ("text", text)
        elif btype == "tool":
            state = block.get("state")
            if not isinstance(state, dict):
                continue
            if state.get("status") == "error":
                yield ("error", _error_text(state))
            else:
                yield ("tool_ok", "")


def _material(kind, record: dict) -> bool:
    return any(k in ("text", "error") for k, _ in _events(kind, record))


def _query(sql: str, params: tuple) -> list[sqlite3.Row]:
    """查询失败一律降级为「没有数据」：库坏了也不许把异常抛进 sync（NFR-004）。"""
    try:
        with _connect() as conn:
            return conn.execute(sql, params).fetchall()
    except sqlite3.Error as e:
        logger.warning("opencode: query failed: %s", e)
        return []


def discover(day: date) -> list[SourceRef]:
    """One SourceRef per session that has material on this day."""
    if not DB_PATH.is_file():
        logger.info("opencode: %s missing, no-op", DB_PATH)
        return []
    start_ms, end_ms = _day_bounds_ms(day)
    rows = _query(
        "SELECT session_id, type, data FROM session_message "
        "WHERE time_created >= ? AND time_created < ? ORDER BY session_id, seq",
        (start_ms, end_ms))
    seen: set[str] = set()
    for row in rows:
        if row["session_id"] in seen:
            continue
        if _material(row["type"], _as_dict(row["data"])):
            seen.add(row["session_id"])
    return [SourceRef(source=SOURCE, ref=f"{DB_PATH}#{sid}", day=day)
            for sid in sorted(seen)]


def _session_id(ref: str) -> str:
    return ref.rpartition("#")[2]


def parse(ref: SourceRef) -> RawMaterial:
    """Merge this day's user text, assistant text and tool failures for one session."""
    session_id = _session_id(ref.ref)
    start_ms, end_ms = _day_bounds_ms(ref.day)
    parts: list[str] = []
    error_count = 0
    error_run = 0
    struggle = 0
    first_ts: datetime | None = None

    rows = _query(
        "SELECT time_created, type, data FROM session_message "
        "WHERE session_id = ? AND time_created >= ? AND time_created < ? ORDER BY seq",
        (session_id, start_ms, end_ms))
    for row in rows:
        ts = _ms_to_dt(row["time_created"])
        if ts is None:
            continue
        for kind, text in _events(row["type"], _as_dict(row["data"])):
            if kind == "error":
                error_count += 1
                error_run += 1
                struggle = max(struggle, error_run)
                contribution = text
            elif kind == "tool_ok":
                error_run = 0  # 工具成功：挣扎结束
                contribution = ""
            else:
                contribution = text
            if contribution:
                parts.append(contribution)
                if first_ts is None:
                    first_ts = ts

    if first_ts is None:
        first_ts = datetime.combine(ref.day, time.min, tzinfo=TZ)
    return RawMaterial(
        source=SOURCE,
        ref=ref.ref,
        ts=first_ts,
        kind="message",
        text="\n---\n".join(parts)[:MAX_SESSION_CHARS],
        meta={"cwd": None, "session_id": session_id,
              "struggle_rounds": struggle, "error_count": error_count},
    )


PLUGIN = Plugin(name=SOURCE, discover=discover, parse=parse)
