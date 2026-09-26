"""hermes plugin: raw 会话蒸馏路径（FR-002a），读 `~/.hermes/state.db`。

**⚠️ 未经真实数据验证**（AC-017③ / FR-002a）。

PRD 原文：hermes「该源当前零真实数据（库建于 2026-09-24，`messages` / `sessions`
均 0 行，`freelist=0` 确认为真空库），按 schema 实现。其验收依据**仅为构造的
fixture，未经真实数据验证**；待该源产生数据后补验，在此之前不得据其声称任何 AC
通过。」本模块按 schema 与产品自身写库代码实现，**未经任何真实记录验证**。

只读两张表：

    sessions(id, source, cwd, started_at, …)
    messages(id, session_id, role, content, tool_name, tool_call_id, timestamp, …)

写入统一走 `hermes_state_messages._INSERT_MESSAGE_SQL`（hermes-agent 0.21.5）。

**时间戳是 epoch 秒**（REAL）：`_coerce_timestamp` 的兜底是 `time.time()` 而非
毫秒；按毫秒解会得到 year 58684。

**注入物过滤：FR-002a 对本源没有给规则**（zcode 有 `synthetic`、workbuddy_ai 有
`<user_query>`，hermes 只有「零数据、按 schema 实现」）。因此本插件**不施加任何
注入物判据**，只按 role 与空值过滤——挑哪条判据属于需求决定，须回 PRD 变更控制。
库里确有候选列（`_compressed_summary` 是产品自己的压缩摘要、`active`/`compacted`
标注被取代的行、`observed`、`display_kind`），是否排除留待补验时一并定。

FR-008 的失败判据取「`role='tool'` 且 content 解析出真值 `error` 键」：产品的
`tools/registry.tool_error()` 写出的正是 `{"error": …}`，而工具异常经
`model_tools._sanitize_tool_error` 包成 `"[TOOL_ERROR] …"` 再塞进该字段。
**已知缺口**：`agent/display._detect_tool_failure` 另有一张更细的判据表
（terminal 的 `exit_code`、`success is False`、degraded、guardrail refusal），
本插件**不**复制它——零数据下无法验证哪几条真的会落库。

`meta.cwd` 取自 `sessions.cwd`（真实列）。

凭据边界（NFR-001）：表允许列表只有 `messages` + `sessions`。库内另有
`system_prompts`（系统提示词全文）`state_meta`、`gateway_routing`、
`session_model_usage`（计费）等表一律不读；凭据文件 `~/.hermes/.env` 与
`auth.json` 在库外（PRD §NFR-001 点名），本插件只开库，不碰文件系统其余部分。

源目录只读（宪法 V）：一律 `file:…?mode=ro` + `uri=True`（NFR-001 机械拦截）。
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from src.plugins import Plugin, RawMaterial, SourceRef

logger = logging.getLogger(__name__)

DB_PATH = Path.home() / ".hermes/state.db"
SOURCE = "hermes"
TZ = timezone(timedelta(hours=8))  # Asia/Shanghai

# 表允许列表：只读这两张。system_prompts / state_meta / gateway_routing /
# session_model_usage 等不在其中。
ALLOWED_TABLES = frozenset({"messages", "sessions"})

MAX_ERROR_CHARS = 2000
MAX_SESSION_CHARS = 200000

# 结构化（多模态）内容的哨兵前缀（`hermes_state._CONTENT_JSON_PREFIX`）：这类
# content 不是纯文本，本插件不解析（缺口记在 docstring）。
_CONTENT_JSON_PREFIX = "\x00json:"


def _connect() -> sqlite3.Connection:
    """只读连接：不改 `db` 文件本身、不写产品数据。

    注意别把它读成「绝不落 sidecar」——2026-09-26 实测：sidecar 不存在时只读连接
    会自己造出 `-wal`/`-shm`；源目录不可写时则直接打不开（靠 `_query` 的
    `except sqlite3.Error` 降级为空数据）。边界全文见 PRD NFR-001「已知边界」。
    """
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _day_bounds_sec(day: date) -> tuple[float, float]:
    """上海时区当天 [起, 止) 的 epoch **秒**。"""
    start = datetime.combine(day, time.min, tzinfo=TZ)
    return start.timestamp(), (start + timedelta(days=1)).timestamp()


def _sec_to_dt(value) -> datetime | None:
    if isinstance(value, bool):  # bool 是 int 的子类，但不是时间戳
        return None
    if not isinstance(value, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(value, tz=timezone.utc).astimezone(TZ)
    except (OverflowError, OSError, ValueError):
        return None


def _text_of(content) -> str | None:
    """纯文本内容才算素材；空串、空白、多模态哨兵串都返回 None。"""
    if not isinstance(content, str):
        return None
    if content.startswith(_CONTENT_JSON_PREFIX):
        return None
    return content if content.strip() else None


def _error_of(content) -> str | None:
    """`role='tool'` 行的失败正文；不是产品的 `{"error": …}` 形状就返回 None。"""
    if not isinstance(content, str) or not content.strip():
        return None
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    error = parsed.get("error")
    if not error:
        return None
    if isinstance(error, str):
        return f"[error] {error[:MAX_ERROR_CHARS]}"
    return f"[error] {json.dumps(error, ensure_ascii=False)[:MAX_ERROR_CHARS]}"


def _event(role: str, content) -> tuple[str, str] | None:
    """一行消息摊成零或一个事件：`text` 素材 / `error` 失败 / `tool_ok` 成功。"""
    if role == "tool":
        error = _error_of(content)
        if error is not None:
            return ("error", error)
        return ("tool_ok", "")
    if role in ("user", "assistant"):
        text = _text_of(content)
        if text is not None:
            return ("text", text)
    return None  # system / 空内容 / 多模态 / 未知 role 都不产素材


def _material(role: str, content) -> bool:
    event = _event(role, content)
    return event is not None and event[0] in ("text", "error")


def _query(sql: str, params: tuple) -> list[sqlite3.Row]:
    """查询失败一律降级为「没有数据」：库坏了也不许把异常抛进 sync（NFR-004）。"""
    try:
        with _connect() as conn:
            return conn.execute(sql, params).fetchall()
    except sqlite3.Error as e:
        logger.warning("hermes: query failed: %s", e)
        return []


def discover(day: date) -> list[SourceRef]:
    """One SourceRef per session that has material on this day."""
    if not DB_PATH.is_file():
        logger.info("hermes: %s missing, no-op", DB_PATH)
        return []
    start, end = _day_bounds_sec(day)
    seen: set[str] = set()
    for row in _query(
            "SELECT session_id, role, content FROM messages "
            "WHERE timestamp >= ? AND timestamp < ? ORDER BY session_id, id",
            (start, end)):
        session_id = row["session_id"]
        if session_id in seen:
            continue
        if _material(row["role"], row["content"]):
            seen.add(session_id)
    return [SourceRef(source=SOURCE, ref=f"{DB_PATH}#{sid}", day=day)
            for sid in sorted(seen)]


def _session_id(ref: str) -> str:
    return ref.rpartition("#")[2]


def _cwd(session_id: str) -> str | None:
    rows = _query("SELECT cwd FROM sessions WHERE id = ?", (session_id,))
    if not rows:
        return None
    cwd = rows[0]["cwd"]
    return cwd if isinstance(cwd, str) and cwd.strip() else None


def parse(ref: SourceRef) -> RawMaterial:
    """Merge this day's user text, assistant text and tool failures for one session."""
    session_id = _session_id(ref.ref)
    start, end = _day_bounds_sec(ref.day)
    parts: list[str] = []
    error_count = 0
    error_run = 0
    struggle = 0
    first_ts: datetime | None = None

    for row in _query(
            "SELECT role, content, timestamp FROM messages "
            "WHERE session_id = ? AND timestamp >= ? AND timestamp < ? ORDER BY id",
            (session_id, start, end)):
        event = _event(row["role"], row["content"])
        if event is None:
            continue
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
                first_ts = _sec_to_dt(row["timestamp"])

    if first_ts is None:
        first_ts = datetime.combine(ref.day, time.min, tzinfo=TZ)
    return RawMaterial(
        source=SOURCE,
        ref=ref.ref,
        ts=first_ts,
        kind="message",
        text="\n---\n".join(parts)[:MAX_SESSION_CHARS],
        meta={"cwd": _cwd(session_id), "session_id": session_id,
              "struggle_rounds": struggle, "error_count": error_count},
    )


PLUGIN = Plugin(name=SOURCE, discover=discover, parse=parse)
