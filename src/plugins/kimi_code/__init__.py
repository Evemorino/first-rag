"""kimi_code plugin: parse ~/.kimi-code/sessions/wd_<ws>/session_<id>/ (raw path).

结构（research.md 实测）：每个会话目录含
- state.json：cwd / title / createdAt（项目归属与元信息）
- agents/main/wire.jsonl：事件流

本机无 kimi-code 真实数据，wire 事件按常见形态容错解析（三种形态
在 fixture 单测中固化）；时间戳为 UTC，折算 Asia/Shanghai 判定归属日。
源目录只读（宪法 V）。装了 kimi-code 的机器上应先跑一次真实数据
冒烟（plugin-contract 的 schema_check 步骤）再信任解析结果。
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from src.plugins import Plugin, RawMaterial, SourceRef

logger = logging.getLogger(__name__)

SESSIONS_DIR = Path.home() / ".kimi-code" / "sessions"
TZ = timezone(timedelta(hours=8))  # Asia/Shanghai

MAX_SESSION_CHARS = 200000

# 可能承载时间戳的顶层键，按优先级尝试
_TS_KEYS = ("timestamp", "ts", "time", "created_at")


def _as_shanghai(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(
            value.replace("Z", "+00:00")).astimezone(TZ)
    except ValueError:
        return None


def _event_ts(event: dict) -> datetime | None:
    for key in _TS_KEYS:
        ts = _as_shanghai(event.get(key))
        if ts:
            return ts
    return None


def discover(day: date) -> list[SourceRef]:
    """One SourceRef per session dir whose wire.jsonl has events on `day`."""
    if not SESSIONS_DIR.is_dir():
        logger.info("kimi_code: %s missing, no-op", SESSIONS_DIR)
        return []
    refs = []
    for wire in sorted(SESSIONS_DIR.glob("wd_*/session_*/agents/main/wire.jsonl")):
        try:
            if _file_hits_day(wire, day):
                refs.append(SourceRef(
                    source="kimi_code", ref=str(wire.parent.parent.parent), day=day))
        except OSError as e:
            logger.warning("kimi_code: cannot read %s: %s", wire, e)
    return refs


def _file_hits_day(wire: Path, day: date) -> bool:
    day_start = datetime.combine(day, datetime.min.time(), tzinfo=TZ)
    mtime = datetime.fromtimestamp(wire.stat().st_mtime, tz=timezone.utc)
    if mtime < day_start:
        return False
    with open(wire, encoding="utf-8") as f:
        for line in f:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            ts = _event_ts(event)
            if ts and ts.date() == day:
                return True
    return False


def _read_state(session_dir: Path) -> dict:
    try:
        state = json.loads(
            (session_dir / "state.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return state if isinstance(state, dict) else {}


def _block_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("text")
        )
    return ""


def _classify(event: dict) -> tuple[str, str]:
    """Return (kind, text) for one wire event: error / user / assistant / ''.

    user 与 assistant 必须分开：连续失败轮次只在「人重新发了一句话」时归零，
    助手的叙述是每次报错之后的必然产物，拿它当结束信号等于永远只数到 1
    （FR-008 的 struggle_rounds 阈值就是这么废掉的）。与 claude_code / codex 同一
    条语义。wire 里没有 tool 结果事件，所以这是能用的最强信号。
    """
    etype = event.get("type")

    if etype == "error" or event.get("is_error") or event.get("level") == "error":
        text = (event.get("message") or event.get("text")
                or _block_text(event.get("content")) or json.dumps(
                    event, ensure_ascii=False)[:2000])
        return "error", f"[error] {str(text)[:2000]}"

    role = event.get("role")
    if role in ("user", "assistant"):
        text = _block_text(event.get("content")) or str(
            event.get("text") or "")
        if text.strip():
            return role, text.strip()

    # {type: user_message / assistant_message, text}
    if etype in ("user_message", "assistant_message") and event.get("text"):
        return etype.removesuffix("_message"), str(event["text"]).strip()

    return "", ""


def parse(ref: SourceRef) -> RawMaterial:
    """Extract messages and errors for ref.day, merged into one material."""
    session_dir = Path(ref.ref)
    state = _read_state(session_dir)
    parts: list[str] = []
    error_count = 0
    error_run = 0
    struggle = 0
    first_ts: datetime | None = _as_shanghai(state.get("createdAt"))

    wire = session_dir / "agents" / "main" / "wire.jsonl"
    try:
        f = open(wire, encoding="utf-8")
    except OSError as e:
        logger.warning("kimi_code: cannot read %s: %s", wire, e)
        f = None

    if f is not None:
        with f:
            for line in f:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict):
                    continue
                ts = _event_ts(event)
                if ts is None or ts.date() != ref.day:
                    continue
                if first_ts is None:
                    first_ts = ts
                kind, text = _classify(event)
                if kind in ("user", "assistant"):
                    parts.append(text)
                    if kind == "user":
                        error_run = 0  # 人重新开口：这一轮挣扎过去了
                elif kind == "error":
                    error_count += 1
                    error_run += 1
                    struggle = max(struggle, error_run)
                    parts.append(text)

    if first_ts is None:
        first_ts = datetime.combine(ref.day, datetime.min.time(), tzinfo=TZ)
    return RawMaterial(
        source="kimi_code",
        ref=ref.ref,
        ts=first_ts,
        kind="message",
        text="\n---\n".join(parts)[:MAX_SESSION_CHARS],
        meta={
            "cwd": state.get("cwd"),
            "title": state.get("title"),
            "session_id": session_dir.name,
            "struggle_rounds": struggle,
            "error_count": error_count,
        },
    )


PLUGIN = Plugin(name="kimi_code", discover=discover, parse=parse)
