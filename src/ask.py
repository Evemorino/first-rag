"""带过滤的语义检索与引用式回答（T026 / FR-018 / FR-019）。

链路：问题嵌入 → similarity.search（payload 过滤：type/date/project）
→ 引用式上下文（`[日期] 类型: 摘要`）→ chat 生成回答。
CLI：python -m src.ask Q="…" [--type X] [--project X]
     [--since 7d|YYYY-MM-DD] [--until …] [--no-expand]（FR-025 面）。
"""

from __future__ import annotations

import logging
import math
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Sequence

from qdrant_client import QdrantClient
from qdrant_client.models import DatetimeRange, FieldCondition, Filter, MatchValue

from src import config, similarity
from src.ark_client import chat, embed

logger = logging.getLogger(__name__)


def _client() -> QdrantClient:
    """Local Qdrant client for expansion fetches (similarity.py 保持 ★ 不动)."""
    return QdrantClient(url=config.QDRANT_URL, trust_env=False)

_NO_HITS_GUIDANCE = (
    "库中还没有可回答的内容。先运行 `make sync` 摄入当日素材，"
    "或放宽过滤条件（类型/日期/项目）后再试。"
)

_RELATIVE = re.compile(r"^(\d+)d$")


@dataclass(frozen=True)
class Citation:
    """一条可追溯引用（FR-019：日期、类型、摘要、来源）。"""

    id: str
    date: str
    type: str
    text: str
    source: str
    source_refs: list[str]
    score: float


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


def _citations(hits: list[similarity.Hit]) -> list[Citation]:
    return [
        Citation(
            id=h.id,
            date=h.payload.get("date", ""),
            type=h.payload.get("type", ""),
            text=h.payload.get("text", ""),
            source=h.payload.get("source", ""),
            source_refs=h.payload.get("source_refs", []),
            score=h.score,
        )
        for h in hits
    ]


def _context_lines(citations: list[Citation], marker: str = "") -> list[str]:
    """`[日期] 类型: 摘要` context lines; marker like 关联补充 tags expansion."""
    tag = f"（{marker}）" if marker else ""
    return [f"[{c.date}] {c.type}{tag}: {c.text}" for c in citations]


# --- 关联扩展（T029 / FR-020 / FR-021，AC-005）---


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _expand_neighbors(
    hits: list[similarity.Hit],
    question_vector: Sequence[float],
    *,
    mode: str | float | None = None,
    client: QdrantClient | None = None,
    collection_name: str | None = None,
) -> list[Citation]:
    """一跳关联扩展：主命中的 related 邻居作为「关联补充」进入上下文。

    三模式（FR-020）：off=不扩展；all=全量（受 neighbor_limit_per_hit /
    context_cap 约束）；数值=阈值，邻居与问题的 cosine ≥ 阈值才纳入。
    """
    expand = config.load_schema()["retrieval"]["expand"]
    effective = expand["mode"] if mode is None else mode
    if effective == "off" or not hits:
        return []

    threshold = None
    if isinstance(effective, (int, float)):
        threshold = float(effective)
    elif expand.get("neighbor_min_score") is not None:
        threshold = float(expand["neighbor_min_score"])

    seen = {h.id for h in hits}
    queue: list[str] = []
    for h in hits:
        for neighbor_id in h.payload.get("related", [])[: expand["neighbor_limit_per_hit"]]:
            neighbor_id = str(neighbor_id)
            if neighbor_id not in seen:
                seen.add(neighbor_id)
                queue.append(neighbor_id)
    queue = queue[: expand["context_cap"]]
    if not queue:
        return []

    qdrant = client or _client()
    # with_vector 仅阈值模式需要；本地（:memory:）模式不支持该参数
    # （即便传 False 也会报 Unknown arguments），所以按需注入。
    retrieve_kwargs = {"with_vector": True} if threshold is not None else {}
    records = qdrant.retrieve(
        collection_name=collection_name or config.COLLECTION,
        ids=queue,
        with_payload=True,
        **retrieve_kwargs,
    )
    expanded = []
    for record in records:
        score = 1.0
        if threshold is not None:
            score = _cosine(question_vector, record.vector or [])
            if score < threshold:
                continue
        expanded.append(_citation_from_record(record, score))
    return expanded


def _citation_from_record(record, score: float) -> Citation:
    payload = record.payload or {}
    return Citation(
        id=str(record.id),
        date=payload.get("date", ""),
        type=payload.get("type", ""),
        text=payload.get("text", ""),
        source=payload.get("source", ""),
        source_refs=payload.get("source_refs", []),
        score=score,
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
    schema = config.load_schema()
    top_k = schema["retrieval"]["top_k"]
    filters = build_filters(
        type=type, project=project, since=since, until=until)

    vector = embed([question])[0]
    hits = similarity.search(vector, k=top_k, filters=filters)
    if not hits:
        return Answer(question=question, text=_NO_HITS_GUIDANCE)

    citations = _citations(hits)
    expanded_mode = "off" if expand is False else ("all" if expand is True else None)
    try:
        expanded = _expand_neighbors(hits, vector, mode=expanded_mode)
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
    answer_text = chat(messages)
    return Answer(
        question=question, text=answer_text,
        citations=citations, expanded=expanded)


# --- CLI ---


def _parse_args(argv: list[str]) -> dict:
    """Parse `Q=…` plus --type/--project/--since/--until/--no-expand."""
    opts: dict = {"Q": None, "type": None, "project": None,
                  "since": None, "until": None, "no-expand": False}
    i = 1
    while i < len(argv):
        arg = argv[i]
        if arg.startswith("Q="):
            opts["Q"] = arg[2:]
        elif arg == "--no-expand":
            opts["no-expand"] = True
        elif arg in ("--type", "--project", "--since", "--until"):
            i += 1
            opts[arg[2:]] = argv[i] if i < len(argv) else None
        i += 1
    return opts


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    opts = _parse_args(sys.argv if argv is None else argv)
    if not opts["Q"]:
        print('usage: make ask Q="…" [--type X] [--project X] '
              '[--since 7d|YYYY-MM-DD] [--until …] [--no-expand]',
              file=sys.stderr)
        return 2
    try:
        answer = query(
            opts["Q"],
            type=opts["type"],
            project=opts["project"],
            since=opts["since"],
            until=opts["until"],
            expand=False if opts["no-expand"] else None,
        )
    except Exception:
        logger.exception("ask failed")
        print("检索失败：请确认 `make up`（Qdrant）与 .env（Ark）就绪后重试。",
              file=sys.stderr)
        return 1

    print(answer.text)
    if answer.citations or answer.expanded:
        print("\n引用：")
        for c in answer.citations:
            print(f"- [{c.date}] {c.type}: {c.text[:80]}")
        for c in answer.expanded:
            print(f"- [{c.date}] {c.type}（关联补充）: {c.text[:80]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
