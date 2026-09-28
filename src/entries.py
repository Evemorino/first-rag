"""条目读取面（T095 / PRD FR-026、FR-027；ADR-18、ADR-19；宪法 II 核心层）。

读三件事：

1. **生效值** —— 人工覆写优先，缺省回落原始字段（ADR-18）。`date` / `source`
   参与 ID 构造，即使覆写层里出现也不生效。
2. **列表** —— 按日期倒序、可过滤、可分页；默认隐藏软删条目（ADR-19）。
3. **详情** —— 含 `original_text`（原始蒸馏正文）、留痕字段与关联边。

写能力（编辑 / 软删 / 恢复）在片 2 落地，**本模块现在不写库**。

已知边界：排序在 Python 侧做（Qdrant 的 scroll 不保证顺序），因此一次扫描以
`MAX_SCAN` 为上限。条目总量预期千级（plan Technical Context），够用；若要长的
量级，该改成给 `date` 建索引 + 用 `order_by`，那时这条注释就是起点。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.models import (
    DatetimeRange,
    FieldCondition,
    Filter,
    IsEmptyCondition,
    MatchValue,
    PayloadField,
    PointStruct,
)

from src import config
from src.ark_client import embed

# 可人工覆写的字段（ADR-18）。`date` / `source` 不在其中：它们参与 uuid5 构造。
OVERRIDABLE = ("text", "type", "tags", "project")

# 人工留下的字段：重放（`ingest.upsert`）必须原样保留它们（ADR-18 / NFR-011）。
# 这份清单是唯一事实来源 —— ingest 从它取值，不另抄一份。
HUMAN_KEYS = (
    "override",
    "original_text",
    "edited_at",
    "edited_prev_text",
    "deleted_at",
    "deleted_reason",
    "rev",
)

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200
MAX_SCAN = 2000


class NotFoundError(Exception):
    """条目不存在（接口层映射 404）。"""


class ConflictError(Exception):
    """版本不符 —— 多半是另一个标签页改过同一条（接口层映射 409）。"""


class ValidationError(Exception):
    """输入不合法：类型不在配置枚举内、正文为空、没有要改的字段等（映射 400）。"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def configured_types() -> list[str]:
    """配置里的类型枚举（页面下拉与写入校验共用一份）。"""
    types = config.load_schema().get("types", [])
    return [t["name"] if isinstance(t, dict) else str(t) for t in types]


def normalize_tags(tags: list[str]) -> list[str]:
    """trim + 去重（保序）—— 与页面上的提示一致（decisions Q3）。"""
    seen: set[str] = set()
    normalized: list[str] = []
    for tag in tags:
        cleaned = (tag or "").strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            normalized.append(cleaned)
    return normalized


def human_fields(payload: dict[str, Any]) -> dict[str, Any]:
    """取出人工留下的字段。值为空（None / {} / 0 / []）的不算 —— 它们等于没设。"""
    return {
        key: payload[key]
        for key in HUMAN_KEYS
        if payload.get(key) not in (None, {}, 0, [])
    }


def embedding_text(auto_text: str, human: dict[str, Any]) -> str:
    """算向量时该用哪段正文：人工改过就用人工的（否则"显示新文字、命中靠旧语义"）。"""
    return (human.get("override") or {}).get("text") or auto_text


def _client() -> QdrantClient:
    """创建本地 Qdrant client；测试可传入 fake client 替换。"""
    return QdrantClient(url=config.QDRANT_URL, trust_env=False)


def effective(payload: dict[str, Any]) -> dict[str, Any]:
    """把人工覆写叠到原始字段上，返回可编辑字段的**生效值**。"""
    override = payload.get("override") or {}
    return {key: override.get(key, payload.get(key)) for key in OVERRIDABLE}


def visible(payload: dict[str, Any]) -> bool:
    """软删条目对"面向人的读取"不可见（ADR-19）。"""
    return not payload.get("deleted_at")


def visibility_condition() -> IsEmptyCondition:
    """"未软删"的判据。列表与检索共用这一份 —— 抄两份迟早会漂（ADR-19）。"""
    return IsEmptyCondition(is_empty=PayloadField(key="deleted_at"))


def build_filter(
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    type: str | None = None,
    project: str | None = None,
    source: str | None = None,
    include_deleted: bool = False,
) -> Filter:
    """按查询条件拼 Qdrant 过滤器，并默认加上"未软删"这道闸。"""
    must: list[Any] = []
    if date_from or date_to:
        bounds: dict[str, str] = {}
        if date_from:
            bounds["gte"] = date_from
        if date_to:
            bounds["lte"] = date_to
        must.append(FieldCondition(key="date", range=DatetimeRange(**bounds)))
    for key, value in (("type", type), ("project", project), ("source", source)):
        if value:
            must.append(FieldCondition(key=key, match=MatchValue(value=value)))
    if not include_deleted:
        # `IsEmptyCondition` 命中"字段缺失或为空"——正是"没被软删"的定义。
        must.append(visibility_condition())
    return Filter(must=must)


def to_view(point_id: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """把一条 point 转成页面/接口用的视图：生效值 + 原文 + 留痕 + 关联。"""
    values = effective(payload)
    return {
        "id": str(point_id),
        "text": values["text"],
        "type": values["type"],
        "tags": values["tags"],
        "project": values["project"],
        "date": payload.get("date"),
        "source": payload.get("source"),
        "created_at": payload.get("created_at"),
        "source_refs": payload.get("source_refs") or [],
        "distill_version": payload.get("distill_version"),
        "related": payload.get("related") or [],
        "original_text": payload.get("original_text") or payload.get("text"),
        "edited": bool(payload.get("override")),
        "edited_at": payload.get("edited_at"),
        "deleted_at": payload.get("deleted_at"),
        "deleted_reason": payload.get("deleted_reason"),
        "rev": payload.get("rev") or 0,
    }


def list_entries(
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    type: str | None = None,
    project: str | None = None,
    source: str | None = None,
    include_deleted: bool = False,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
    client: QdrantClient | None = None,
) -> dict[str, Any]:
    """列表：过滤 + 日期倒序 + 分页。返回 `total` / `page` / `page_size` / `entries`。"""
    if page < 1:
        raise ValueError("page 必须 ≥ 1")
    if page_size < 1:
        raise ValueError("page_size 必须 ≥ 1")
    page_size = min(page_size, MAX_PAGE_SIZE)
    client = client or _client()
    query_filter = build_filter(
        date_from=date_from,
        date_to=date_to,
        type=type,
        project=project,
        source=source,
        include_deleted=include_deleted,
    )
    points, _offset = client.scroll(
        collection_name=config.COLLECTION,
        scroll_filter=query_filter,
        limit=MAX_SCAN,
        with_payload=True,
        with_vectors=False,
    )
    views = [to_view(point.id, point.payload or {}) for point in points]
    views.sort(key=lambda view: (view["date"] or "", view["created_at"] or ""), reverse=True)
    start = (page - 1) * page_size
    total = client.count(
        collection_name=config.COLLECTION,
        count_filter=query_filter,
        exact=True,
    ).count
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "entries": views[start : start + page_size],
    }


def get_entry(entry_id: str, *, client: QdrantClient | None = None) -> dict[str, Any] | None:
    """按 ID 取一条详情；不存在返回 None。

    顺带算出**同日前后邻居**（`prev_id` / `next_id`）—— 详情页要能"顺藤摸瓜"
    （PRD FR-027），而排序/可见性口径必须与列表一致，所以这段放在核心层而不是
    页面里。软删邻居天然被可见性闸排除。
    """
    client = client or _client()
    records = client.retrieve(
        collection_name=config.COLLECTION,
        ids=[entry_id],
        with_payload=True,
        with_vectors=False,
    )
    if not records:
        return None
    record = records[0]
    payload = record.payload or {}
    view = to_view(record.id, payload)
    view.update(_neighbors(entry_id, payload, client))
    return view


def _neighbors(
    entry_id: str, payload: dict[str, Any], client: QdrantClient
) -> dict[str, str | None]:
    """同日的上一条/下一条（顺序与列表一致：日期倒序 + created_at 倒序）。"""
    day = payload.get("date")
    if not day:
        return {"prev_id": None, "next_id": None}
    query_filter = build_filter(date_from=day, date_to=day)
    points, _offset = client.scroll(
        collection_name=config.COLLECTION,
        scroll_filter=query_filter,
        limit=MAX_SCAN,
        with_payload=True,
        with_vectors=False,
    )
    views = [to_view(point.id, point.payload or {}) for point in points]
    views.sort(key=lambda view: (view["date"] or "", view["created_at"] or ""), reverse=True)
    ids_in_order = [view["id"] for view in views]
    if entry_id not in ids_in_order:
        return {"prev_id": None, "next_id": None}
    index = ids_in_order.index(entry_id)
    return {
        "prev_id": ids_in_order[index - 1] if index > 0 else None,
        "next_id": ids_in_order[index + 1] if index + 1 < len(ids_in_order) else None,
    }


# --- 写面（T099 / T100）：人工编辑、软删除、恢复 ---


def _load_payload(entry_id: str, client: QdrantClient) -> dict[str, Any]:
    records = client.retrieve(
        collection_name=config.COLLECTION,
        ids=[entry_id],
        with_payload=True,
        with_vectors=False,
    )
    if not records:
        raise NotFoundError(entry_id)
    return records[0].payload or {}


def _check_rev(payload: dict[str, Any], rev: int) -> int:
    """乐观并发：版本不符就拒绝，别静默覆盖另一次编辑（decisions R3）。"""
    current = int(payload.get("rev") or 0)
    if current != int(rev):
        raise ConflictError(f"条目已被改动（当前版本 {current}），请刷新后重试")
    return current


def edit_entry(
    entry_id: str,
    *,
    rev: int,
    text: str | None = None,
    type: str | None = None,
    tags: list[str] | None = None,
    project: str | None = None,
    client: QdrantClient | None = None,
) -> dict[str, Any]:
    """人工编辑：写覆写层 + 留痕，并用**生效正文**重算向量（ADR-18）。

    Ark 不可用时整条编辑不生效 —— 先算向量再落库，不做"先存文本、后补向量"。
    """
    client = client or _client()
    payload = _load_payload(entry_id, client)
    current = _check_rev(payload, rev)

    override = dict(payload.get("override") or {})
    if text is not None:
        stripped = text.strip()
        if not stripped:
            raise ValidationError("正文不能为空")
        override["text"] = stripped
    if type is not None:
        if type not in configured_types():
            raise ValidationError(f"类型不在配置枚举内：{type}")
        override["type"] = type
    if tags is not None:
        override["tags"] = normalize_tags(tags)
    if project is not None:
        override["project"] = project.strip() or None
    if not override:
        raise ValidationError("没有要改的字段")

    effective_before = effective(payload)["text"]
    embedding_source = override.get("text") or payload.get("text") or ""
    vector = embed([embedding_source])[0]  # Ark 挂了就在这里抛，库还没动

    new_payload = {
        **payload,
        "override": override,
        "original_text": payload.get("original_text") or payload.get("text") or "",
        "edited_prev_text": effective_before,
        "edited_at": _now(),
        "rev": current + 1,
    }
    client.upsert(
        collection_name=config.COLLECTION,
        points=[PointStruct(id=entry_id, vector=vector, payload=new_payload)],
        wait=True,
    )
    return to_view(entry_id, new_payload)


def delete_entry(
    entry_id: str,
    *,
    rev: int,
    reason: str | None = None,
    client: QdrantClient | None = None,
) -> dict[str, Any]:
    """软删除：写 `deleted_at`（可恢复），不摘向量（ADR-19）。"""
    client = client or _client()
    payload = _load_payload(entry_id, client)
    current = _check_rev(payload, rev)
    if payload.get("deleted_at"):
        raise ValidationError("条目已是删除状态")
    changes: dict[str, Any] = {"deleted_at": _now(), "rev": current + 1}
    if reason and reason.strip():
        changes["deleted_reason"] = reason.strip()
    client.set_payload(
        collection_name=config.COLLECTION, payload=changes, points=[entry_id]
    )
    return to_view(entry_id, {**payload, **changes})


def restore_entry(
    entry_id: str, *, rev: int, client: QdrantClient | None = None
) -> dict[str, Any]:
    """恢复：把两个键**删掉**而不是置 null（可见性判据是"字段为空"）。"""
    client = client or _client()
    payload = _load_payload(entry_id, client)
    current = _check_rev(payload, rev)
    if not payload.get("deleted_at"):
        raise ValidationError("条目不在删除状态")
    client.delete_payload(
        collection_name=config.COLLECTION,
        keys=["deleted_at", "deleted_reason"],
        points=[entry_id],
    )
    client.set_payload(
        collection_name=config.COLLECTION, payload={"rev": current + 1}, points=[entry_id]
    )
    restored = {
        key: value
        for key, value in payload.items()
        if key not in ("deleted_at", "deleted_reason")
    }
    restored["rev"] = current + 1
    return to_view(entry_id, restored)
