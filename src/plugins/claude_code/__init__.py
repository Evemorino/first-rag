"""claude_code plugin: parse ~/.claude/projects/<project>/*.jsonl (raw path).

Each line is one event: user/assistant messages, tool_use, tool_result
with is_error (research.md). Source dir is strictly read-only (constitution V).
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from src.plugins import Plugin, RawMaterial, SourceRef

logger = logging.getLogger(__name__)

SESSIONS_DIR = Path.home() / ".claude" / "projects"
CLAUDE_HOME = Path.home() / ".claude"
TZ = timezone(timedelta(hours=8))  # Asia/Shanghai


def _as_shanghai(ts: str) -> datetime:
    """Parse an ISO timestamp (UTC 'Z' or with offset) into Asia/Shanghai."""
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(TZ)


def discover(day: date) -> list[SourceRef]:
    """One SourceRef per session file touched on `day`. [] if nothing."""
    if not SESSIONS_DIR.is_dir():
        if CLAUDE_HOME.is_dir():
            # 装了 claude 但会话不在 projects/ 布局下——版本漂移嫌疑，
            # 提醒排查而不是静默吞掉（research.md 格式漂移风险）。
            logger.warning(
                "claude_code: %s missing while %s exists — session layout "
                "may have drifted, verify the real path; no-op this run",
                SESSIONS_DIR, CLAUDE_HOME)
        else:
            logger.info("claude_code: %s missing, no-op", SESSIONS_DIR)
        return []
    refs = []
    for path in SESSIONS_DIR.rglob("*.jsonl"):
        try:
            if _file_hits_day(path, day):
                refs.append(SourceRef(source="claude_code", ref=str(path), day=day))
        except OSError as e:
            logger.warning("claude_code: cannot read %s: %s", path, e)
    return refs


def _file_hits_day(path: Path, day: date) -> bool:
    """Cheap mtime prefilter, then exact timestamp check on the lines."""
    day_start = datetime.combine(day, datetime.min.time(), tzinfo=TZ)
    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    if mtime < day_start:
        return False  # file untouched since before the day: skip reading it
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                ts = json.loads(line).get("timestamp")
            except json.JSONDecodeError:
                continue
            if ts and _as_shanghai(ts).date() == day:
                return True
    return False


def parse(ref: SourceRef) -> RawMaterial:
    """Extract user/assistant text and tool errors for ref.day from one session file."""
    materials: list[RawMaterial] = []
    meta_base = {"cwd": None, "session_id": Path(ref.ref).stem}
    error_run = 0
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
            if event.get("cwd"):
                meta_base["cwd"] = event["cwd"]

            entry_type = event.get("type")
            if entry_type == "user":
                new_mats, had_error, saw_ok = _user_materials(
                    event, ref, ts, meta_base, error_run)
                # 只有工具真的跑通才算挣扎结束；一次失败的 user 回合把连击 +1，
                # 纯文本的用户发言则是中性的（换个话题不等于把坑填了）。
                if had_error:
                    error_run += 1
                elif saw_ok:
                    error_run = 0
                materials.extend(new_mats)
            elif entry_type == "assistant":
                materials.extend(
                    _assistant_materials(event, ref, ts, meta_base))
                # 助手叙述不打断失败连击：真实会话里每次工具报错后面必然跟着一条
                # assistant 消息，在这里归零等于把 struggle_rounds 永远压成 1。
            elif entry_type in ("tool_result", "toolUseResult"):
                if _is_error(event):
                    error_run += 1
                    materials.append(RawMaterial(
                        source="claude_code", ref=ref.ref, ts=ts, kind="error",
                        text=_error_text(event),
                        meta={**meta_base, "struggle_rounds": error_run}))
    return _merged(ref, materials)


def _user_materials(event, ref, ts, meta_base, error_run: int):
    """User turns; tool_result blocks with is_error become kind='error'.

    Returns (materials, had_error, saw_ok) so the caller can track consecutive
    error runs (struggle evidence, FR-008): `had_error` on a failed tool round,
    `saw_ok` when a tool_result came back clean — the only thing that ends a
    struggle. A plain user message is neither, so it neither extends nor ends
    the run.
    """
    message = event.get("message", {})
    content = message.get("content")
    out: list[RawMaterial] = []
    had_error = False
    saw_ok = False
    if isinstance(content, str) and content.strip():
        out.append(RawMaterial(source="claude_code", ref=ref.ref, ts=ts,
                               kind="message", text=content,
                               meta=dict(meta_base)))
    elif isinstance(content, list):
        for block in content:
            if block.get("type") == "tool_result" and block.get("is_error"):
                had_error = True
                out.append(RawMaterial(
                    source="claude_code", ref=ref.ref, ts=ts, kind="error",
                    text=_truncate(_block_text(block.get("content"))),
                    meta={**meta_base, "struggle_rounds": error_run + 1}))
            elif block.get("type") == "tool_result":
                saw_ok = True
            elif block.get("type") == "text" and block.get("text", "").strip():
                out.append(RawMaterial(source="claude_code", ref=ref.ref, ts=ts,
                                       kind="message", text=block["text"],
                                       meta=dict(meta_base)))
    return out, had_error, saw_ok


def _block_text(content) -> str:
    """tool_result content: str, or a block list like [{'type':'text','text':…}]."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content
                        if isinstance(b, dict))
    return str(content)


def _assistant_materials(event, ref, ts, meta_base) -> list[RawMaterial]:
    """Assistant turns: keep text blocks only, drop tool_use JSON."""
    content = event.get("message", {}).get("content", [])
    out = []
    if isinstance(content, list):
        for block in content:
            if block.get("type") == "text" and block.get("text", "").strip():
                out.append(RawMaterial(source="claude_code", ref=ref.ref, ts=ts,
                                       kind="message", text=block["text"],
                                       meta=dict(meta_base)))
    return out


def _is_error(event: dict) -> bool:
    if event.get("is_error"):
        return True
    result = event.get("toolUseResult")
    if isinstance(result, dict):
        return bool(result.get("is_error") or result.get("stderr"))
    return False


def _error_text(event: dict) -> str:
    content = event.get("content")
    if not content:
        result = event.get("toolUseResult", {})
        content = result.get("stderr") or result.get("stdout") or str(result)
    return _truncate(content if isinstance(content, str) else json.dumps(content, ensure_ascii=False))


def _truncate(text: str, limit: int = 2000) -> str:
    text = text.strip()
    return text[:limit] + "…" if len(text) > limit else text


def _merged(ref: SourceRef, materials: list[RawMaterial]) -> RawMaterial:
    """parse() contract returns ONE RawMaterial per SourceRef.

    A session may yield many pieces; merge into one material whose text is
    the joined transcript (collect stores one material per session per day).
    """
    if not materials:
        return RawMaterial(source="claude_code", ref=ref.ref,
                           ts=datetime.combine(ref.day, datetime.min.time(), tzinfo=TZ),
                           kind="message", text="", meta={})
    parts = [m.text for m in materials if m.text]
    errors = [m for m in materials if m.kind == "error"]
    struggle = max((m.meta.get("struggle_rounds", 0) for m in errors), default=0)
    return RawMaterial(
        source="claude_code", ref=ref.ref, ts=materials[0].ts,
        kind="message",
        text="\n---\n".join(parts)[:200000],
        meta={**materials[0].meta, "struggle_rounds": struggle,
              "error_count": len(errors)})


PLUGIN = Plugin(name="claude_code", discover=discover, parse=parse)
