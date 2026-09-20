"""codex plugin: parse ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl (raw path).

真实格式（research.md 实测）：每行 {timestamp, ordinal, type, payload}。
- session_meta / turn_context: cwd、session_id（项目归属）
- response_item.payload.type=message: role user/assistant 的对话文本
  （role=developer 是系统指令，量大利低，直接丢弃）
- event_msg: task_complete 带 error、turn_aborted → 工具/任务报错素材
时间戳为 UTC，统一折算 Asia/Shanghai 后判定归属日。源目录只读（宪法 V）。
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from src.plugins import Plugin, RawMaterial, SourceRef

logger = logging.getLogger(__name__)

SESSIONS_DIR = Path.home() / ".codex" / "sessions"
TZ = timezone(timedelta(hours=8))  # Asia/Shanghai

# 单个会话合并后的转写上限，与 claude_code 插件保持一致
MAX_SESSION_CHARS = 200000


def _as_shanghai(ts: str) -> datetime:
    """Parse a codex UTC/offset timestamp into Asia/Shanghai."""
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(TZ)


def discover(day: date) -> list[SourceRef]:
    """One SourceRef per rollout file with events on `day`. [] if nothing.

    本地日 D 覆盖 UTC D-1 16:00 → D 16:00，目录名是 UTC 日期，
    所以不能按目录名过滤，逐文件按事件时间戳判定（mtime 预筛）。
    """
    if not SESSIONS_DIR.is_dir():
        logger.info("codex: %s missing, no-op", SESSIONS_DIR)
        return []
    refs = []
    for path in SESSIONS_DIR.rglob("rollout-*.jsonl"):
        try:
            if _file_hits_day(path, day):
                refs.append(SourceRef(source="codex", ref=str(path), day=day))
        except OSError as e:
            logger.warning("codex: cannot read %s: %s", path, e)
    return refs


def _file_hits_day(path: Path, day: date) -> bool:
    """Cheap mtime prefilter, then exact timestamp check on the lines."""
    day_start = datetime.combine(day, datetime.min.time(), tzinfo=TZ)
    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    if mtime < day_start:
        return False  # 最后写入都在当天开始前：不可能含当天事件
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                ts = json.loads(line).get("timestamp")
            except json.JSONDecodeError:
                continue
            if ts and _as_shanghai(ts).date() == day:
                return True
    return False


def _content_text(content) -> str:
    """message.content: [{'type': 'input_text'|'output_text', 'text': …}]"""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "\n".join(
        block.get("text", "")
        for block in content
        if isinstance(block, dict) and block.get("text")
    )


# codex 以 user 角色注入的模板文本（实测开头标记），不是真实用户发言
_INJECTED_USER_PREFIXES = (
    "# AGENTS.md instructions",
    "<skill>",
    "<environment_context>",
    "<user_instructions>",
)


def _is_injected_user_text(text: str) -> bool:
    return text.startswith(_INJECTED_USER_PREFIXES)


def parse(ref: SourceRef) -> RawMaterial:
    """Extract user/assistant text and task errors for ref.day, merged."""
    meta: dict = {"cwd": None, "session_id": Path(ref.ref).name}
    parts: list[str] = []
    error_count = 0
    error_run = 0
    struggle = 0
    first_ts: datetime | None = None

    with open(ref.ref, encoding="utf-8") as f:
        for line in f:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts_field = event.get("timestamp")
            ts = _as_shanghai(ts_field) if ts_field else None
            if ts is None or ts.date() != ref.day:
                continue
            if first_ts is None:
                first_ts = ts

            etype = event.get("type")
            payload = event.get("payload", {})
            if etype in ("session_meta", "turn_context"):
                if payload.get("cwd"):
                    meta["cwd"] = payload["cwd"]
                if payload.get("session_id"):
                    meta["session_id"] = payload["session_id"]
            elif etype == "response_item" and payload.get("type") == "message":
                role = payload.get("role")
                if role == "developer":
                    continue  # 系统指令：体量大、无学习价值
                text = _content_text(payload.get("content")).strip()
                if text and not (role == "user" and _is_injected_user_text(text)):
                    parts.append(text)
                    error_run = 0 if role == "assistant" else error_run
            elif etype == "event_msg":
                message = _event_error_text(payload)
                if message:
                    error_count += 1
                    error_run += 1
                    struggle = max(struggle, error_run)
                    parts.append(message)

    if first_ts is None:
        first_ts = datetime.combine(ref.day, datetime.min.time(), tzinfo=TZ)
    return RawMaterial(
        source="codex",
        ref=ref.ref,
        ts=first_ts,
        kind="message",
        text="\n---\n".join(parts)[:MAX_SESSION_CHARS],
        meta={**meta, "struggle_rounds": struggle, "error_count": error_count},
    )


def _event_error_text(payload: dict) -> str:
    """task_complete.error / turn_aborted → one error text, else ''."""
    ptype = payload.get("type")
    if ptype == "task_complete":
        error = payload.get("error")
        if isinstance(error, dict) and error.get("message"):
            return f"[error] {error['message'][:2000]}"
    elif ptype == "turn_aborted":
        reason = payload.get("reason") or "turn aborted"
        return f"[error] turn aborted: {str(reason)[:2000]}"
    return ""


PLUGIN = Plugin(name="codex", discover=discover, parse=parse)
