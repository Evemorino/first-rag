"""workbuddy_ai plugin: parse ~/.workbuddy-ai/projects/<slug>/<uuid>.jsonl.

CodeBuddy 系的**独立解析器**（PRD FR-002a）：它与 qoder 系不共用实现，连时间
戳策略都是相反的（qoder 系双编码，这里是单编码）。源目录只读（宪法 V）。

2026-09-25 本机实测 5 会话 / 5326 行。记录类型：`function_call` 1553、
`function_call_result` 1552、`reasoning` 1020、`message` 678、
`file-history-snapshot` 515、`ai-title` 7、`resend-fork-notice` 1。

进素材的只有三件事：

- `type=message` 且 `role=user`：**且正文里带 `<user_query>` 标签**（实测 47 条）
- `type=message` 且 `role=assistant`：`output_text` 块（实测 562 条）
- 失败的工具调用：`function_call_result` 的 `Exit Code` 尾注非零（实测 26 条）

**这个插件的全部难点是注入物。** 678 条 `message` 里 `role=user` 的有 116 条，
其中只有 47 条带 `<user_query>`；另外 69 条是机器物（`<task-notification>` 53、
`<conversation_history_summary>` 6、"Please continue with the conversation…" 6、
`<system-reminder>` 2、`<teammate-message>` 2，后两条的 `content` 是**字符串**
而不是块列表）。"role=user 就取正文"会多收 69 条。而真正的大头在那 47 条内部：
`<user_query>` 之外的正文是全库最大 19429 字、首条样本 12354 字的注入模板——
`## SOUL.md` 人设全文、`<user_info>`、`memory_and_skills_reminder`、
`<system-reminder data-role="user-context">`。标签本身总在整条记录的**末尾**，
且恰好一对。故规则是**正文只取标签内，取不到标签的记录不产生素材**。

user 侧的正标记是**标签本身**，不是块类型：标签是 harness 从真实输入生成的，
比任何块类型名都强，也不怕将来块类型改名。assistant 侧才用块类型允许列表
（只取 `output_text`），因为那边没有同等强度的标记。实测 user 块类型
`input_text` 115 + `image_blob_ref` 1（后者没有 `text` 键，按 text 取正文天然
跳过），assistant 562 条块全是 `output_text` 且没有一条记录带多个块。

**时间戳单编码**：实测 5326 条记录无一例外是 epoch 毫秒整数（与 qoder 系同一
文件内混排 ISO 字符串 + 整数相反）。所以这里不做双编码兜底：非整数时间戳即
"这个源的格式变了"，该记录不产素材。这是**有意的**——为没见过的格式猜一个
编码，猜错就是把素材记到错误的日期上，比少一条更难发现。测试里有一条专门钉住
这个行为（`test_parse_survives_records_without_a_usable_timestamp`）。

**失败信号（FR-008）在工具结果尾部包裹层**：

    Command: <回显的命令>
    Stdout: …
    Stderr: …
    Exit Code: <N>
    Signal: (none|SIGTERM|…)

实测 1552 条结果里 783 条带该尾注（757 条为 0；26 条非零：`137`×12 是 SIGTERM、
`1`×14，全部来自 Bash）。三个假阳性陷阱，都实测过：① 正文里小写 "exit code"
命中 789 次，绝大多数是**被回显的命令原文**（`echo "exit=$?"`），不是失败；
② `is_error` 命中 11 次，全是 pytest 覆盖率报告里我们自己代码的函数名
（`src/plugins/claude_code/__init__.py:_is_error`）；③ `FAILED` 命中 24 次，
与"退出码非零"**零重叠**——它抓到的全是"跑通过的测试里提到了 FAILED 字符串"，
是比退出码严格更差的信号。

所以只认尾注，且认**最后**一条 `Exit Code: N`：包裹层是 harness 追加在末尾的，
而命令自己的输出（`cat docs/format.md`——那份文档讲的正是这个格式）完全可能
印出以 `Exit Code: 1` 开头的行。取第一个匹配就会平白记一次失败。
另有 60 条 Bash 结果是后台启动（"Launched directly in background…"）、
47 条是块列表输出（read_me / show_widget / present_files），**都没有尾注**——
没有证据就不算失败（与 codex 相反：那边要从正文里*找*信号，这里信号是显式
追加的，所以退出码非零但 stdout 为空照样记账）。

struggle 语义（FR-008）：连续失败轮次只在"工具成功输出"时归零；助手插话不
打断连击计数（与 codex / kimi_code / qoder 同一条语义）。"没有尾注的结果"
（后台启动、块列表输出）视同工具成功——它确实跑了，只是这个包裹层没给退出码。
时间戳统一折算 Asia/Shanghai 后判定归属日。

`meta` 只有 `cwd` 和 `session_id`：实测源里**没有** `gitBranch` 键（qoder 系才
有），所以这里不塞 `git_branch`——一个恒为 None 的键等于声称"该源有这个字段
但它是空的"，而事实是这个源没有它。
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from src.plugins import Plugin, RawMaterial, SourceRef

logger = logging.getLogger(__name__)

# 路径允许列表：只读这一个 glob，两层深度把 `subagents/agent-*.jsonl`、
# `<uuid>.meta.json`、`<uuid>.file-rollback.ndjson` 都挡在外面（NFR-001）。
SESSIONS_DIR = Path.home() / ".workbuddy-ai" / "projects"
SOURCE = "workbuddy_ai"
TZ = timezone(timedelta(hours=8))  # Asia/Shanghai

# 单个会话合并后的转写上限，与其它源插件保持一致
MAX_SESSION_CHARS = 200000

# 工具报错正文的截断长度（与 codex / qoder 一致）
MAX_ERROR_CHARS = 2000

# 真实用户输入的唯一标记（PRD FR-002a）。DOTALL 是必需的：标签内容可能跨行。
_USER_QUERY = re.compile(r"<user_query>(.*?)</user_query>", re.DOTALL)

# 工具结果尾注。必须锚定行首行尾，否则回显的命令文本会误命中；取最后一条是
# 因为包裹层由 harness 追加在末尾（见模块 docstring）。
_EXIT_CODE = re.compile(r"^Exit Code:[ \t]*(\d+)[ \t]*$", re.MULTILINE)


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
    """epoch 毫秒 → 上海时间；不是整数毫秒就给 None。

    实测 5326 条记录**全部**是 int 毫秒，没有第二种编码，所以不做字符串兜底：
    读不出来即格式变了，宁可这条不产素材，也不要猜一个编码把素材记到别的日期。
    """
    if isinstance(ts, bool):  # bool 是 int 的子类，但不是时间戳
        return None
    if not isinstance(ts, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(ts / 1000, tz=timezone.utc).astimezone(TZ)
    except (OverflowError, OSError, ValueError):
        return None


def _record_text(record: dict) -> str:
    """记录所有的正文文本（块列表拼起来，或字符串本身）。

    只认带 `text` 的块：实测那 1 条 `image_blob_ref` 块只有
    `blob_id`/`mime`/`size`/`blob_path`/`original_filename`，没有正文可漏。
    """
    content = record.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "\n".join(
        block["text"] for block in content
        if isinstance(block, dict) and isinstance(block.get("text"), str) and block["text"]
    )


def _output_text(record: dict) -> str:
    """`function_call_result.output` 的正文：dict 或块列表两种形状都吃。"""
    output = record.get("output")
    if isinstance(output, dict):
        text = output.get("text")
        return text if isinstance(text, str) else ""
    if isinstance(output, list):
        return "\n".join(
            block["text"] for block in output
            if isinstance(block, dict) and isinstance(block.get("text"), str) and block["text"]
        )
    return ""


def _failure_text(record: dict) -> str:
    """失败的工具调用 → "[error] …"；不是失败返回 ''。

    判据是尾注里**最后**一条 `Exit Code: N`，N 非零即失败。没有尾注 = 没有失败
    证据（后台启动、块列表输出都属此类），不是失败。
    """
    if record.get("type") != "function_call_result":
        return ""
    codes = _EXIT_CODE.findall(_output_text(record))
    if not codes or int(codes[-1]) == 0:
        return ""
    return f"[error] {_output_text(record)[:MAX_ERROR_CHARS]}"


def _material_text(record: dict) -> str:
    """该记录贡献的素材正文；不产素材返回 ''。

    这是"注入物不得产生素材"的唯一出口：user 必须带 `<user_query>` 标签，
    助手只认 `output_text` 块。
    """
    if record.get("type") != "message":
        return ""
    role = record.get("role")
    if role == "user":
        match = _USER_QUERY.search(_record_text(record))
        return match.group(1).strip() if match else ""
    if role == "assistant":
        content = record.get("content")
        if not isinstance(content, list):
            return ""
        return "\n".join(
            block["text"] for block in content
            if isinstance(block, dict) and block.get("type") == "output_text"
            and isinstance(block.get("text"), str) and block["text"]
        )
    return ""


def discover(day: date) -> list[SourceRef]:
    """One SourceRef per session file with material on `day`. [] if nothing.

    只认"当天真能产出素材"的会话：一天里每个跨夜会话都会追加记录，照单全收
    等于往快照里塞一堆空素材。
    """
    if not SESSIONS_DIR.is_dir():
        logger.info("workbuddy_ai: %s missing, no-op", SESSIONS_DIR)
        return []
    refs = []
    for path in sorted(SESSIONS_DIR.glob("*/*.jsonl")):
        try:
            if _file_hits_day(path, day):
                refs.append(SourceRef(source=SOURCE, ref=str(path), day=day))
        except OSError as e:
            logger.warning("workbuddy_ai: cannot read %s: %s", path, e)
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
        if _failure_text(record) or _material_text(record):
            return True
    return False


def parse(ref: SourceRef) -> RawMaterial:
    """Extract user queries, assistant text and tool failures for ref.day."""
    meta: dict = {"cwd": None, "session_id": Path(ref.ref).stem}
    parts: list[str] = []
    error_count = 0
    error_run = 0
    struggle = 0
    first_ts: datetime | None = None

    for record in _iter_records(ref.ref):
        ts = _as_shanghai(record.get("timestamp"))
        if ts is None or ts.date() != ref.day:
            continue
        if meta["cwd"] is None and record.get("cwd"):
            meta["cwd"] = record["cwd"]

        failure = _failure_text(record)
        if failure:
            error_count += 1
            error_run += 1
            struggle = max(struggle, error_run)
            contribution = failure
        elif record.get("type") == "function_call_result":
            error_run = 0  # 工具跑了且没报失败：挣扎结束
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
