"""带过滤的语义检索与引用式回答（T026 / FR-018 / FR-019）。

链路：问题嵌入 → similarity.search（payload 过滤：type/date/project）
→ 引用式上下文（`[日期] 类型: 摘要`）→ chat 生成回答。
引用构建与一跳关联扩展在 `src/ask_expand.py`（本模块把它俩再导出）。
CLI：python -m src.ask Q="…" [--type X] [--project X]
     [--since 7d|YYYY-MM-DD] [--until …] [--no-expand]（FR-025 面）。
"""

from __future__ import annotations

import logging
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Iterator

from qdrant_client.models import DatetimeRange, FieldCondition, Filter, MatchValue

from src import config, similarity
from src.ark_client import chat, chat_stream, embed
from src.ask_expand import Citation, citations_from_hits, expand_neighbors

logger = logging.getLogger(__name__)

_NO_HITS_GUIDANCE = (
    "库中还没有可回答的内容。先运行 `make sync` 摄入当日素材，"
    "或放宽过滤条件（类型/日期/项目）后再试。"
)

_RELATIVE = re.compile(r"^(\d+)d$")


@dataclass
class Answer:
    question: str
    text: str
    citations: list[Citation] = field(default_factory=list)
    expanded: list[Citation] = field(default_factory=list)  # 关联补充（T029）


def _parse_date(value: str | None, *, today: date | None = None) -> date | None:
    """Accept `7d`-style relative days or an ISO date. None → None."""
    if value is None:
        return None
    today = today or datetime.now(tz=config.TZ).date()
    if (m := _RELATIVE.match(value.strip())):
        return today - timedelta(days=int(m.group(1)))
    return date.fromisoformat(value.strip())


def build_filters(
    *,
    type: str | None = None,
    project: str | None = None,
    since: str | None = None,
    until: str | None = None,
    today: date | None = None,
) -> Filter | None:
    """Build the Qdrant payload filter (FR-018). None = no filtering."""
    conditions = []
    if type:
        conditions.append(
            FieldCondition(key="type", match=MatchValue(value=type)))
    if project:
        conditions.append(
            FieldCondition(key="project", match=MatchValue(value=project)))
    date_range: dict[str, str] = {}
    if (d := _parse_date(since, today=today)) is not None:
        date_range["gte"] = f"{d.isoformat()}T00:00:00Z"
    if (d := _parse_date(until, today=today)) is not None:
        date_range["lte"] = f"{d.isoformat()}T23:59:59Z"
    if date_range:
        conditions.append(FieldCondition(key="date", range=DatetimeRange(**date_range)))
    if not conditions:
        return None
    return Filter(must=conditions)


def _context_lines(citations: list[Citation], marker: str = "") -> list[str]:
    """`[日期] 类型: 摘要` context lines; marker like 关联补充 tags expansion."""
    tag = f"（{marker}）" if marker else ""
    return [f"[{c.date}] {c.type}{tag}: {c.text}" for c in citations]


@dataclass(frozen=True)
class _Prepared:
    """一次提问在"该发给 LLM 什么"这一步的全部结果。

    抽出来是为了让 query() 与 query_stream() 共用同一份上下文构建：过滤、
    关联扩展、引用行格式这三处逻辑一旦复制成两份，改动只会在一条路径上
    生效，表现为"流式和不流式答得不一样"，且不会有测试变红。
    """

    messages: list[dict[str, str]]
    citations: list[Citation]
    expanded: list[Citation]
    max_tokens: int
    thinking: bool


def _prepare(
    question: str,
    *,
    type: str | None = None,
    project: str | None = None,
    since: str | None = None,
    until: str | None = None,
    expand: bool | None = None,
) -> _Prepared | None:
    """检索 + 过滤 + 拼 prompt；None 表示无命中（调用方别去问 LLM）。"""
    retrieval = config.load_schema()["retrieval"]
    filters = build_filters(
        type=type, project=project, since=since, until=until)

    vector = embed([question])[0]
    hits = similarity.search(vector, k=retrieval["top_k"], filters=filters)
    if not hits:
        return None

    citations = citations_from_hits(hits)
    expanded_mode = "off" if expand is False else ("all" if expand is True else None)
    try:
        expanded = expand_neighbors(hits, vector, mode=expanded_mode)
    except Exception as e:  # noqa: BLE001 — 扩展失败不拖垮主回答
        logger.warning("ask: neighbor expansion failed, skipped: %s", e)
        expanded = []
    context = "\n".join(
        _context_lines(citations) + _context_lines(expanded, marker="关联补充"))
    messages = [
        {
            "role": "system",
            "content": (
                "你是个人学习记忆的检索助手。只依据下方给出的学习条目回答；"
                "每条结论后必须附引用标记 [YYYY-MM-DD]。条目不足以回答时"
                "直接说明，不要编造。标有（关联补充）的条目是与命中内容"
                "语义相关的补充背景。"
            ),
        },
        {
            "role": "user",
            "content": f"问题：{question}\n\n学习条目：\n{context}",
        },
    ]
    return _Prepared(
        messages=messages,
        citations=citations,
        expanded=expanded,
        # 缺键时兜底：校验层已保证存在，这里只是不让 .get 的默认值成为
        # 唯一防线（schema 被绕过加载时仍能跑）。
        max_tokens=retrieval.get("answer_max_tokens", 600),
        thinking=not retrieval.get("disable_thinking", True),
    )


def query(
    question: str,
    *,
    type: str | None = None,
    project: str | None = None,
    since: str | None = None,
    until: str | None = None,
    expand: bool | None = None,
) -> Answer:
    """检索 + 过滤 + 引用式回答（FR-018/019）。expand=None 走配置默认。"""
    prepared = _prepare(
        question, type=type, project=project,
        since=since, until=until, expand=expand)
    if prepared is None:
        return Answer(question=question, text=_NO_HITS_GUIDANCE)

    answer_text = chat(
        prepared.messages, max_tokens=prepared.max_tokens,
        thinking=prepared.thinking)
    return Answer(
        question=question, text=answer_text,
        citations=prepared.citations, expanded=prepared.expanded)


@dataclass
class StreamedAnswer:
    """流式回答的句柄：引用先给（它们来自检索，不必等生成），文本再逐块消费。"""

    question: str
    citations: list[Citation]
    expanded: list[Citation]
    chunks: Iterator[str]


def query_stream(
    question: str,
    *,
    type: str | None = None,
    project: str | None = None,
    since: str | None = None,
    until: str | None = None,
    expand: bool | None = None,
) -> StreamedAnswer:
    """query() 的流式版：同样的检索与 prompt，只是文本逐块产出。

    chunks 是惰性的 —— 调用本函数不发任何生成请求，第一次消费才开始。
    无命中时不碰 LLM，直接给引导语。
    """
    prepared = _prepare(
        question, type=type, project=project,
        since=since, until=until, expand=expand)
    if prepared is None:
        return StreamedAnswer(
            question=question, citations=[], expanded=[],
            chunks=iter([_NO_HITS_GUIDANCE]))

    return StreamedAnswer(
        question=question,
        citations=prepared.citations,
        expanded=prepared.expanded,
        chunks=chat_stream(
            prepared.messages, max_tokens=prepared.max_tokens,
            thinking=prepared.thinking),
    )


# --- CLI ---


def _parse_args(argv: list[str]) -> dict:
    """Parse `Q=…` plus --type/--project/--since/--until/--no-expand/--stream."""
    opts: dict = {"Q": None, "type": None, "project": None,
                  "since": None, "until": None, "no-expand": False,
                  "stream": False}
    i = 1
    while i < len(argv):
        arg = argv[i]
        if arg.startswith("Q="):
            opts["Q"] = arg[2:]
        elif arg in ("--no-expand", "--stream"):
            opts[arg[2:]] = True
        elif arg in ("--type", "--project", "--since", "--until"):
            i += 1
            opts[arg[2:]] = argv[i] if i < len(argv) else None
        i += 1
    return opts


def _filters(opts: dict) -> dict:
    """CLI 选项 → query/query_stream 的关键字参数（两条路径共用一份）。"""
    return {
        "type": opts["type"],
        "project": opts["project"],
        "since": opts["since"],
        "until": opts["until"],
        "expand": False if opts["no-expand"] else None,
    }


def _print_citations(citations: list[Citation], expanded: list[Citation]) -> None:
    if not (citations or expanded):
        return
    print("\n引用：")
    for c in citations:
        print(f"- [{c.date}] {c.type}: {c.text[:80]}")
    for c in expanded:
        print(f"- [{c.date}] {c.type}（关联补充）: {c.text[:80]}")


def _run_stream(question: str, filters: dict) -> None:
    """流式打印正文（边到边打），再补引用清单。

    flush=True 是流式的关键：Python 的 stdout 接管道/重定向时是块缓冲的，
    不 flush 的话"逐块产出"会被攒成一大块最后才出现 —— 终端里看着像流式，
    接 `| head` 或写日志时完全不是。
    """
    streamed = query_stream(question, **filters)
    for chunk in streamed.chunks:
        print(chunk, end="", flush=True)
    print()
    _print_citations(streamed.citations, streamed.expanded)


def main(argv: list[str] | None = None) -> int:
    config.load_env()
    # CLI 入口自己 bootstrap 环境：config.env() 只读 os.environ，忘了这一步
    # 就会在第一次读环境变量时炸掉（test_config 守着这条）。
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    opts = _parse_args(sys.argv if argv is None else argv)
    if not opts["Q"]:
        print('usage: make ask Q="…" [--type X] [--project X] '
              '[--since 7d|YYYY-MM-DD] [--until …] [--no-expand] [--stream]',
              file=sys.stderr)
        return 2
    try:
        if opts["stream"]:
            _run_stream(opts["Q"], _filters(opts))
        else:
            answer = query(opts["Q"], **_filters(opts))
            print(answer.text)
            _print_citations(answer.citations, answer.expanded)
    except Exception:
        logger.exception("ask failed")
        print("检索失败：请确认 `make up`（Qdrant）与 .env（Ark）就绪后重试。",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
