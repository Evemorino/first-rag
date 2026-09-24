"""kimi_code plugin: parse ~/.kimi-code/sessions/wd_<ws>/session_<id>/ (raw path).

每个会话目录含 state.json（cwd / title / createdAt）与 agents/main/wire.jsonl
事件流。事件形态按本机真实数据实测（2026-09-24，11 个会话 / 39206 个事件）：

- ``turn.prompt``：人说的话在 ``input[]`` 的 ``{type:"text"}`` 块里；``origin.kind``
  实测有三种——user 358 / task 64（子代理回报）/ skill_activation 4，只有
  "user" 算人开口（本机 426 条 turn.prompt 的分布）。
- ``agent.message.appended``：``message.message`` 才是那条消息，``content[]`` 里
  ``type:"text"`` 是对外文本、``type:"think"`` 是思维链（体量最大，不入素材）。
  ``role`` 为 tool/user 的是上下文回灌，与"人开口""工具报错"都不同源，跳过。
- ``context.append_loop_event``：``event.type`` 里 3669 条 ``tool.result``，
  报错看 ``result.isError``（真实数据 108 条为真）。

时间戳统一是 **int 毫秒**（state.json 的 createdAt 也是），折算 Asia/Shanghai
判归属日。源目录严格只读（宪法 V）。

这里栽过一次：三套 shape 是照别家产品**猜**的，而真实数据一直在本机，于是
``_classify`` 认出 0 条、int 毫秒又让归属日永不命中，采集源静默空转、测试全绿。
闸口是 tests/unit/test_kimi_code.py::test_real_sessions_are_recognised。
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
    """kimi 的时间戳是 int 毫秒 epoch；也接受带时区的 ISO 串。

    单位是契约的一部分：按秒解释会落到 1970 年，按微秒解释会落到 5 万年后，
    两种都"解析成功"，于是归属日永远不命中、还不报错——正是本模块栽过的坑。
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value / 1000, tz=TZ)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(
                value.replace("Z", "+00:00")).astimezone(TZ)
        except ValueError:
            return None
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


def _block_text(blocks, want: str = "text") -> str:
    """拼出 content/input 里指定类型的文本块；其余（think 思维链）丢掉。

    真实数据里 think 块比 text 块还多，全收进素材既泄露不该外流的思维链，
    又会把 MAX_SESSION_CHARS 的额度先吃光。
    """
    if not isinstance(blocks, list):
        return ""
    return "\n".join(
        str(block["text"])
        for block in blocks
        if isinstance(block, dict)
        and block.get("type") == want and block.get("text")
    )


def _classify(event: dict) -> tuple[str, str]:
    """Return (kind, text) for one wire event: user/assistant/error/ok/''.

    'ok' 表示一次干净的工具成功，它只用来归零失败连击、不进素材。FR-008 的
    struggle_rounds 数的是"同一问题连续没解决"，所以结束信号只能是**真成功**：
    助手叙述是每次报错之后的必然产物，人的下一句往往只是"又试一次"，两者都
    不该打断连击（与 claude_code / codex 同一条语义）。
    """
    etype = event.get("type")

    if etype == "turn.prompt":
        origin = event.get("origin")
        if not isinstance(origin, dict) or origin.get("kind") != "user":
            return "", ""          # task / skill_activation 都不是人开口
        text = _block_text(event.get("input")).strip()
        return ("user", text) if text else ("", "")

    if etype == "agent.message.appended":
        message = event.get("message")
        inner = message.get("message") if isinstance(message, dict) else None
        # role=tool 的回灌与 tool.result 同源，role=user 的回灌与 turn.prompt
        # 同源，都只取一次：这里只认助手对外说的文本。
        if not isinstance(inner, dict) or inner.get("role") != "assistant":
            return "", ""
        text = _block_text(inner.get("content")).strip()
        return ("assistant", text) if text else ("", "")

    if etype == "context.append_loop_event":
        inner = event.get("event")
        if not isinstance(inner, dict) or inner.get("type") != "tool.result":
            return "", ""
        result = inner.get("result")
        if not isinstance(result, dict):
            return "", ""
        if result.get("isError"):
            return "error", f"[error] {str(result.get('output', ''))[:2000]}"
        return "ok", ""

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
                elif kind == "error":
                    error_count += 1
                    error_run += 1
                    struggle = max(struggle, error_run)
                    parts.append(text)
                elif kind == "ok":
                    error_run = 0  # 工具真成功：这一轮挣扎到这儿结束了

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
