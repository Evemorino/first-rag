"""zcode plugin: raw 会话蒸馏路径（FR-002 / FR-002a），读 `~/.zcode/cli/db/db.sqlite`。

只读两张表：

    message(id, session_id, time_created, time_updated, data, sequence)
    part(id, message_id, session_id, time_created, time_updated, data, sequence)

正文在 `part` 里，按 `message_id` 挂回 `message`（2026-09-25 只读勘察，全库
1863 条消息 / 5894 个零件）。`part.data.type` 实测 text 1003 / tool 1560 /
reasoning 515 / step-start 1405 / step-finish 1398 / timeline 13 —— 只有 `text`
进正文，其余是簿记与思考过程。

**注入物过滤（FR-002a 的核心）**：`message.data.synthetic` 为真的整条消息
（连同它的全部零件）一律不产素材。实测 343 条 user 消息里 115 条 synthetic
（fork 提示与工具结果回填），真用户输入只剩 228 条——过滤判据是**标签本身**，
不是计数比例，所以数据涨了也不会失效。

FR-008 的失败判据取 `part.data.state.status == "error"`（实测 completed 1531 /
error 29），正文写 `[error] {state.error}`——注意 `state.error` 在这里是**字符串**，
而 opencode 那边是 `{type, message}` 字典：两族各自实现的意义正在这里，共用
一个 helper 反而要把两套形状塞进同一个分支。

`meta.cwd` 取自 `message.data.path`（实测是 `{"cwd": …, "root": …}` 字典，
1429 条有），**不需要**再读 `session` 表——允许列表因此能只留两张表。

凭据边界（NFR-001）：表允许列表只有 `message` + `part`。库内另有
`input_history`(100) / `local_setting`(6) / `permission` 等表一律不读；凭据文件
`~/.zcode/v2/credentials*.json` 在库外，本插件只开库，不碰文件系统其余部分。

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

DB_PATH = Path.home() / ".zcode/cli/db/db.sqlite"
SOURCE = "zcode"
TZ = timezone(timedelta(hours=8))  # Asia/Shanghai

# 表允许列表：只读这两张。input_history / local_setting / permission 等不在其中。
ALLOWED_TABLES = frozenset({"message", "part"})

MAX_ERROR_CHARS = 2000
MAX_SESSION_CHARS = 200000

# 一天的消息 + 它的零件，按（消息序号, 零件序号）排——实测两者各自在
# 会话/消息内唯一递增，能稳定复现转录顺序。天按 `message.time_created` 切：
# 一条消息整体归属它开始的那天，synthetic 的判定也才跟着消息走。
_ROWS_SQL = """
SELECT m.session_id AS session_id, m.time_created AS mtime,
       m.data AS mdata, p.data AS pdata
FROM message m JOIN part p ON p.message_id = m.id
WHERE m.time_created >= ? AND m.time_created < ? {session}
ORDER BY m.session_id, m.sequence, p.sequence
"""


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
    """上海时区当天 [起, 止) 的 epoch 毫秒。"""
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


def _is_injected(message: dict) -> bool:
    """实测取值只有 None 与 True 两种；按 PRD 的「为真」语义判真值。"""
    return bool(message.get("synthetic"))


def _roles_and_path(message: dict) -> tuple[str, str | None]:
    """从消息 JSON 里取角色与 cwd。"""
    role = message.get("role")
    path = message.get("path")
    cwd = None
    if isinstance(path, dict):
        raw = path.get("cwd")
        if isinstance(raw, str) and raw.strip():
            cwd = raw
    return (role if isinstance(role, str) else ""), cwd


def _error_text(state: dict) -> str:
    """`state.error` 实测是**字符串**（opencode 那边是字典——两族各自实现）。"""
    error = state.get("error")
    if isinstance(error, str) and error.strip():
        return f"[error] {error[:MAX_ERROR_CHARS]}"
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str) and message.strip():
            return f"[error] {message[:MAX_ERROR_CHARS]}"
        return f"[error] {json.dumps(error, ensure_ascii=False)[:MAX_ERROR_CHARS]}"
    return "[error]"


def _event(message: dict, part: dict) -> tuple[str, str] | None:
    """一个零件摊成零或一个事件：`text` 素材 / `error` 失败 / `tool_ok` 成功。"""
    if _is_injected(message):
        return None  # FR-002a：整条消息连零件一起丢
    ptype = part.get("type")
    if ptype == "text":
        if message.get("role") not in ("user", "assistant"):
            return None
        text = part.get("text")
        if isinstance(text, str) and text.strip():
            return ("text", text)
        return None
    if ptype == "tool":
        state = part.get("state")
        if not isinstance(state, dict):
            return None
        if state.get("status") == "error":
            return ("error", _error_text(state))
        return ("tool_ok", "")
    return None  # reasoning / step-start / step-finish / timeline 都是簿记


def _query(sql: str, params: tuple) -> list[sqlite3.Row]:
    """查询失败一律降级为「没有数据」：库坏了也不许把异常抛进 sync（NFR-004）。"""
    try:
        with _connect() as conn:
            return conn.execute(sql, params).fetchall()
    except sqlite3.Error as e:
        logger.warning("zcode: query failed: %s", e)
        return []


def discover(day: date) -> list[SourceRef]:
    """One SourceRef per session that has material on this day."""
    if not DB_PATH.is_file():
        logger.info("zcode: %s missing, no-op", DB_PATH)
        return []
    start_ms, end_ms = _day_bounds_ms(day)
    seen: set[str] = set()
    for row in _query(_ROWS_SQL.format(session=""), (start_ms, end_ms)):
        session_id = row["session_id"]
        if session_id in seen:
            continue
        event = _event(_as_dict(row["mdata"]), _as_dict(row["pdata"]))
        if event is not None and event[0] in ("text", "error"):
            seen.add(session_id)
    return [SourceRef(source=SOURCE, ref=f"{DB_PATH}#{sid}", day=day)
            for sid in sorted(seen)]


def _session_id(ref: str) -> str:
    return ref.rpartition("#")[2]


def parse(ref: SourceRef) -> RawMaterial:
    """Merge this day's user text, assistant text and tool failures for one session."""
    session_id = _session_id(ref.ref)
    start_ms, end_ms = _day_bounds_ms(ref.day)
    parts: list[str] = []
    cwd: str | None = None
    error_count = 0
    error_run = 0
    struggle = 0
    first_ts: datetime | None = None

    sql = _ROWS_SQL.format(session="AND m.session_id = ?")
    for row in _query(sql, (start_ms, end_ms, session_id)):
        message = _as_dict(row["mdata"])
        event = _event(message, _as_dict(row["pdata"]))
        if event is None:
            continue
        if cwd is None:
            _, cwd = _roles_and_path(message)
        kind, text = event
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
                first_ts = _ms_to_dt(row["mtime"])

    if first_ts is None:
        first_ts = datetime.combine(ref.day, time.min, tzinfo=TZ)
    return RawMaterial(
        source=SOURCE,
        ref=ref.ref,
        ts=first_ts,
        kind="message",
        text="\n---\n".join(parts)[:MAX_SESSION_CHARS],
        meta={"cwd": cwd, "session_id": session_id,
              "struggle_rounds": struggle, "error_count": error_count},
    )


PLUGIN = Plugin(name=SOURCE, discover=discover, parse=parse)
