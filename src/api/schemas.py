"""接口的**响应**模型（T129）。

为什么要它们：前端 `web/src/api/client.ts` 的视图类型过去是手抄的一份，两边会各自
漂移，而漂移只在运行时以"某个字段 undefined"的形式露出来 —— 类型检查一句话都不说。
给路由挂上 `response_model` 之后，`make ui-types` 生成的 `web/src/api/schema.d.ts`
里就有具名模型，前端直接引它，两边只剩一个真相（ADR-21 决策 8）。

三种形状不能合并成一个模型：

* 列表行与写接口返回的是 `entries.to_view`（**没有**同日邻居字段）；
* 详情多两个 `prev_id` / `next_id`，由 `entries._neighbors` 补上。

合成一个模型的话，列表响应会凭空多出两个 `null` 字段 —— 那是改契约，不是补类型。

字段一律**必填但可为 null**（`x: str | None`，不给默认值），这样生成的 TS 类型里
`rev` 这类是 `number` 而不是 `number | undefined`，而 `text` 老实写成
`string | null` —— 它确实可能为 null（`effective()` 取的是 payload 原值）。手写那份
把 `text` 写成 `string` 是句假话：越界的那天它不会报错，只会渲染出空白。

顺带一层作用：`response_model` 是**白名单**。以后 payload 里多出字段，接口不会自动
把它漏出去，除非这里也登记 —— 对"别把内部 payload 暴露给页面"这件事，这个默认值
是安全的（fail closed）。
"""

from __future__ import annotations

from pydantic import BaseModel


class EntryView(BaseModel):
    """一条条目的生效视图（列表行 + 三个写接口的返回）。"""

    id: str
    text: str | None
    type: str | None
    tags: list[str] | None
    project: str | None
    date: str | None
    source: str | None
    created_at: str | None
    source_refs: list[str]
    distill_version: str | None
    related: list[str]
    original_text: str | None
    edited: bool
    edited_at: str | None
    deleted_at: str | None
    deleted_reason: str | None
    rev: int


class EntryDetailView(EntryView):
    """详情：在视图之上多两个同日邻居（当日范围没有邻居时是 null）。"""

    prev_id: str | None
    next_id: str | None


class EntryListView(BaseModel):
    """分页列表。`total` 是**过滤后**的总数（不是本页条数）。"""

    total: int
    page: int
    page_size: int
    entries: list[EntryView]


class TypesView(BaseModel):
    """配置里的类型枚举（供页面筛选与编辑下拉，FR-016）。"""

    types: list[str]
