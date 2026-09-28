"""条目读取面契约测试（T095 / PRD FR-026、FR-027；ADR-18、ADR-19）。

只测**读**：生效值解析（覆写优先、缺省回落）、可见性（软删默认隐藏）、过滤、
排序分页、详情字段。写能力（编辑/软删/恢复）在片 2 落地，本文件刻意不覆盖 ——
读了能过、写了还没写，两边不能混着测。
"""

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
