"""条目读取面契约测试（T095 / PRD FR-026、FR-027；ADR-18、ADR-19）。

只测**读**：生效值解析（覆写优先、缺省回落）、可见性（软删默认隐藏）、过滤、
排序分页、详情字段。写能力（编辑/软删/恢复）在片 2 落地，本文件刻意不覆盖 ——
读了能过、写了还没写，两边不能混着测。
"""

from datetime import date
from types import SimpleNamespace

from qdrant_client.models import FieldCondition, IsEmptyCondition

from src import entries


def make_payload(**overrides):
    payload = {
        "text": "原始蒸馏正文",
        "date": "2026-09-20",
        "type": "error",
        "tags": ["qdrant"],
        "source": "claude_code",
        "project": "first-rag",
        "created_at": "2026-09-20T10:00:00+08:00",
        "source_refs": ["session-a"],
        "distill_version": "mock+rubric@abcd1234",
        "related": [],
    }
    payload.update(overrides)
    return payload


class FakeQdrantClient:
    """只实现读取面用到的两个方法；调用别的方法说明实现跑偏了。"""

    def __init__(self, points):
        self.points = points  # [(id, payload), ...]
        self.scrolls = []
        self.counts = []

    def scroll(self, **kwargs):
        self.scrolls.append(kwargs)
        return ([SimpleNamespace(id=i, payload=dict(p)) for i, p in self.points], None)

    def count(self, **kwargs):
        self.counts.append(kwargs)
        return SimpleNamespace(count=len(self.points))

    def retrieve(self, **kwargs):
        wanted = {str(i) for i in kwargs["ids"]}
        return [
            SimpleNamespace(id=i, payload=dict(p))
            for i, p in self.points
            if str(i) in wanted
        ]


def filter_keys(kwargs):
    """取出 scroll 收到的过滤器里的字段条件名，便于断言。"""
    flt = kwargs["scroll_filter"]
    return [c.key for c in flt.must if isinstance(c, FieldCondition)]


def has_emptiness_guard(kwargs):
    return any(isinstance(c, IsEmptyCondition) for c in kwargs["scroll_filter"].must)


# --- 生效值：人工覆写优先，缺省回落原始字段 ---


def test_effective_without_override_returns_original_values():
    payload = make_payload()
    assert entries.effective(payload) == {
        "text": "原始蒸馏正文",
        "type": "error",
        "tags": ["qdrant"],
        "project": "first-rag",
    }


def test_effective_prefers_override_per_field():
    payload = make_payload(override={"text": "人工改过的正文", "tags": ["人工"]})
    assert entries.effective(payload) == {
        "text": "人工改过的正文",   # 覆写
        "type": "error",            # 未覆写 → 回落
        "tags": ["人工"],           # 覆写
        "project": "first-rag",     # 未覆写 → 回落
    }


def test_effective_ignores_uneditable_override_keys():
    """`date`/`source` 参与 ID 构造，覆写层里出现也不算数（ADR-18）。"""
    payload = make_payload(override={"date": "1999-01-01", "source": "hacked"})
    assert entries.effective(payload)["text"] == "原始蒸馏正文"
    assert "date" not in entries.effective(payload)


# --- 可见性：软删默认隐藏 ---


def test_list_hides_soft_deleted_by_default():
    fake = FakeQdrantClient([("a", make_payload())])
    entries.list_entries(client=fake)
    assert has_emptiness_guard(fake.scrolls[0]), "默认查询必须带「deleted_at 为空」条件"


def test_list_include_deleted_drops_the_guard():
    fake = FakeQdrantClient([("a", make_payload())])
    entries.list_entries(include_deleted=True, client=fake)
    assert not has_emptiness_guard(fake.scrolls[0])


# --- 过滤条件 ---


def test_list_builds_conditions_for_each_filter():
    fake = FakeQdrantClient([("a", make_payload())])
    entries.list_entries(
        date_from="2026-09-01",
        date_to="2026-09-30",
        type="error",
        project="first-rag",
        source="claude_code",
        client=fake,
    )
    assert sorted(filter_keys(fake.scrolls[0])) == [
        "date",
        "project",
        "source",
        "type",
    ]


def test_list_without_filters_still_applies_visibility():
    fake = FakeQdrantClient([("a", make_payload())])
    entries.list_entries(client=fake)
    assert filter_keys(fake.scrolls[0]) == []
    assert has_emptiness_guard(fake.scrolls[0])


# --- 排序与分页 ---


def test_list_sorts_by_date_desc_then_paginates():
    fake = FakeQdrantClient(
        [
            ("old", make_payload(date="2026-09-01")),
            ("new", make_payload(date="2026-09-25")),
            ("mid", make_payload(date="2026-09-10")),
        ]
    )
    first = entries.list_entries(page=1, page_size=2, client=fake)
    second = entries.list_entries(page=2, page_size=2, client=fake)

    assert first["total"] == 3
    assert [e["id"] for e in first["entries"]] == ["new", "mid"]
    assert [e["id"] for e in second["entries"]] == ["old"]
    assert first["page"] == 1 and second["page"] == 2


def test_list_same_day_breaks_ties_by_created_at():
    fake = FakeQdrantClient(
        [
            ("early", make_payload(created_at="2026-09-20T09:00:00+08:00")),
            ("late", make_payload(created_at="2026-09-20T18:00:00+08:00")),
        ]
    )
    listed = entries.list_entries(client=fake)
    assert [e["id"] for e in listed["entries"]] == ["late", "early"]


def test_list_caps_page_size():
    fake = FakeQdrantClient([("a", make_payload())])
    listed = entries.list_entries(page_size=9999, client=fake)
    assert listed["page_size"] == entries.MAX_PAGE_SIZE


def test_list_scans_up_to_the_documented_cap():
    """排序在 Python 侧做，所以扫描面必须一次覆盖全部匹配点 —— 上限写在常量里。"""
    fake = FakeQdrantClient([("a", make_payload())])
    entries.list_entries(client=fake)
    assert fake.scrolls[0]["limit"] == entries.MAX_SCAN


def test_list_rejects_bad_page():
    fake = FakeQdrantClient([])
    for page in (0, -1):
        try:
            entries.list_entries(page=page, client=fake)
        except ValueError as exc:
            assert "page" in str(exc)
        else:  # pragma: no cover
            raise AssertionError(f"page={page} 应当被拒绝")


# --- 详情 ---


def test_get_entry_returns_view_with_trail_and_original_text():
    payload = make_payload(
        override={"text": "人工版"},
        original_text="原始蒸馏正文",
        edited_at="2026-09-28T10:00:00+08:00",
        deleted_at=None,
        rev=2,
    )
    fake = FakeQdrantClient([("abc", payload)])
    view = entries.get_entry("abc", client=fake)

    assert view["id"] == "abc"
    assert view["text"] == "人工版"           # 生效值
    assert view["original_text"] == "原始蒸馏正文"  # 原文永在
    assert view["edited"] is True
    assert view["rev"] == 2
    assert view["source_refs"] == ["session-a"]
    assert view["related"] == []


def test_get_entry_without_edit_is_not_marked_edited():
    fake = FakeQdrantClient([("abc", make_payload())])
    view = entries.get_entry("abc", client=fake)
    assert view["edited"] is False
    assert view["original_text"] == payload_text(fake)


def payload_text(fake):
    return fake.points[0][1]["text"]


def test_get_entry_missing_returns_none():
    fake = FakeQdrantClient([("abc", make_payload())])
    assert entries.get_entry("nope", client=fake) is None


def test_get_entry_uses_retrieve_not_scroll():
    """按 ID 取一条：实现若退化成扫全库，点上就会体现（慢且口径不同）。"""
    payload = make_payload()
    fake = FakeQdrantClient([("abc", payload)])
    entries.get_entry("abc", client=fake)
    # get_entry 会为"顺藤摸瓜"顺带查一次同日邻居，但不该为取这条本身而扫全库。
    assert all(kw["scroll_filter"] is not None for kw in fake.scrolls)
    assert any(
        c.key == "date"
        for kw in fake.scrolls
        for c in kw["scroll_filter"].must
        if isinstance(c, FieldCondition)
    )


# --- 顺藤摸瓜：同日前后翻（FR-025 / PRD FR-027）---


def day_entries(*triples):
    return [
        (ident, make_payload(date=day, created_at=created))
        for ident, day, created in triples
    ]


def test_get_entry_reports_same_day_neighbours():
    fake = FakeQdrantClient(
        day_entries(
            ("a", "2026-09-20", "2026-09-20T09:00:00+08:00"),
            ("b", "2026-09-20", "2026-09-20T10:00:00+08:00"),
            ("c", "2026-09-20", "2026-09-20T11:00:00+08:00"),
        )
    )
    # 顺序与列表一致：日期倒序 + created_at 倒序 → c, b, a
    assert entries.get_entry("b", client=fake)["prev_id"] == "c"
    assert entries.get_entry("b", client=fake)["next_id"] == "a"


def test_get_entry_neighbours_are_none_at_the_edges():
    fake = FakeQdrantClient(
        day_entries(
            ("a", "2026-09-20", "2026-09-20T09:00:00+08:00"),
            ("b", "2026-09-20", "2026-09-20T10:00:00+08:00"),
        )
    )
    first, last = entries.get_entry("b", client=fake), entries.get_entry("a", client=fake)
    assert first["prev_id"] is None and first["next_id"] == "a"
    assert last["next_id"] is None and last["prev_id"] == "b"


def test_get_entry_neighbours_ignore_other_days():
    """"当日前后翻"就只在当日里翻 —— 跨天是列表的活儿，不是详情导航的。"""
    fake = FakeQdrantClient(
        day_entries(
            ("a", "2026-09-19", "2026-09-19T10:00:00+08:00"),
            ("b", "2026-09-20", "2026-09-20T10:00:00+08:00"),
        )
    )
    # 假客户端不过滤，所以这里直接核对筛选条件里带了当天日期区间。
    entries.get_entry("b", client=fake)
    condition = next(
        c for c in fake.scrolls[-1]["scroll_filter"].must if isinstance(c, FieldCondition)
    )
    assert condition.key == "date"


def test_visible_helper_matches_the_filter_semantics():
    assert entries.visible(make_payload()) is True
    assert entries.visible(make_payload(deleted_at="2026-09-28T12:00:00+00:00")) is False


# --- T111：把"手工也杀不掉"的那批逐条判过之后补的覆盖 ---
# 每条都对应 T110 判决里 `entries` 的一个真存活变异体，注释写明它钉的是什么。


def test_view_contract_carries_every_field():
    """整份视图契约（不是抽几个字段）—— 钉住 to_view 里每个 `payload.get(<键>)`。

    T111 判决里 12 条真存活都是这类：把某个键改成 `payload.get(None)`、把
    `related` 的 `or` 改成 `and`（永远返回 []）、把 `rev` 的兜底 0 改成 1……
    没有一个测试整体看过返回值，所以它们都"没人抓"。
    """
    payload = make_payload(
        source="codex",
        distill_version="m+rubric@deadbeef",
        related=["11111111-1111-1111-1111-111111111111"],
        original_text="原文",
        edited_at="2026-09-28T12:00:00+00:00",
        deleted_reason="错了",
        rev=3,
    )
    view = entries.to_view("abc", payload)

    assert view == {
        "id": "abc",
        "text": "原始蒸馏正文",
        "type": "error",
        "tags": ["qdrant"],
        "project": "first-rag",
        "date": "2026-09-20",
        "source": "codex",
        "created_at": "2026-09-20T10:00:00+08:00",
        "source_refs": ["session-a"],
        "distill_version": "m+rubric@deadbeef",
        "related": ["11111111-1111-1111-1111-111111111111"],
        "original_text": "原文",
        "edited": False,
        "edited_at": "2026-09-28T12:00:00+00:00",
        "deleted_at": None,
        "deleted_reason": "错了",
        "rev": 3,
    }


def test_view_defaults_are_zero_and_empty():
    """没写过的字段要有稳定默认（`rev` 兜底 0、`related` 兜底 []）。"""
    view = entries.to_view("abc", make_payload())
    assert view["rev"] == 0
    assert view["related"] == []


# --- 过滤器：键里的值与边界（不只是"有哪些键"）---


def test_build_filter_pins_match_values():
    query = entries.build_filter(type="error", project="p", source="codex")
    matches = {
        c.key: c.match.value for c in query.must if isinstance(c, FieldCondition)
    }
    assert matches == {"type": "error", "project": "p", "source": "codex"}


def test_build_filter_pins_both_date_bounds():
    query = entries.build_filter(date_from="2026-09-01", date_to="2026-09-30")
    condition = next(c for c in query.must if isinstance(c, FieldCondition))
    # DatetimeRange 会把 ISO 串解析成 datetime，所以比日期部分。
    assert condition.range.gte.date() == date(2026, 9, 1)
    assert condition.range.lte.date() == date(2026, 9, 30)


def test_build_filter_accepts_a_single_bound():
    """只给一端也要生效 —— `or` 被改成 `and` 时这里会红。"""
    only_from = next(
        c for c in entries.build_filter(date_from="2026-09-01").must
        if isinstance(c, FieldCondition)
    )
    only_to = next(
        c for c in entries.build_filter(date_to="2026-09-30").must
        if isinstance(c, FieldCondition)
    )
    assert only_from.range.gte.date() == date(2026, 9, 1)
    assert only_from.range.lte is None
    assert only_to.range.lte.date() == date(2026, 9, 30)
    assert only_to.range.gte is None


def test_list_entries_forwards_date_bounds():
    fake = FakeQdrantClient([("a", make_payload())])
    entries.list_entries(date_from="2026-09-01", date_to="2026-09-30", client=fake)
    condition = next(
        c for c in fake.scrolls[0]["scroll_filter"].must if isinstance(c, FieldCondition)
    )
    assert condition.range.gte.date() == date(2026, 9, 1)
    assert condition.range.lte.date() == date(2026, 9, 30)


def test_neighbors_pin_the_day_window():
    fake = FakeQdrantClient([("abc", make_payload())])
    entries.get_entry("abc", client=fake)
    condition = next(
        c for c in fake.scrolls[-1]["scroll_filter"].must if isinstance(c, FieldCondition)
    )
    assert condition.range.gte.date() == date(2026, 9, 20)
    assert condition.range.lte.date() == date(2026, 9, 20)


# --- 类型枚举：dict 与裸字符串两种形状 ---


def test_configured_types_handles_dicts_and_bare_strings(monkeypatch):
    monkeypatch.setattr(
        entries.config, "load_schema",
        lambda: {"types": [{"name": "error"}, "idea"]},
    )
    assert entries.configured_types() == ["error", "idea"]
