"""条目写面契约测试（T099 / T100；ADR-18、ADR-19）。

写面有四条不能退化的性质：**不改 ID**、**先算向量再落库**（Ark 挂了不留半截）、
**版本不符就拒绝**、**留痕齐全**。逐条钉住。
"""

from datetime import datetime
from types import SimpleNamespace

import pytest

from src import entries

TYPE_ENUM = ["error", "progress", "idea"]


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


class FakeQdrant:
    """带状态的假客户端：写进去的东西在 points 里看得见。"""

    def __init__(self, points):
        self.points = {str(k): dict(v) for k, v in points.items()}
        self.upserts = []
        self.set_payloads = []
        self.delete_payloads = []

    def retrieve(self, **kwargs):
        return [
            SimpleNamespace(id=ident, payload=dict(self.points[ident]))
            for ident in (str(i) for i in kwargs["ids"])
            if ident in self.points
        ]

    def upsert(self, **kwargs):
        self.upserts.append(kwargs)
        for point in kwargs["points"]:
            self.points[str(point.id)] = dict(point.payload)
        return SimpleNamespace(operation_id=1)

    def set_payload(self, **kwargs):
        self.set_payloads.append(kwargs)
        for ident in (str(i) for i in kwargs["points"]):
            self.points[ident].update(kwargs["payload"])

    def delete_payload(self, **kwargs):
        self.delete_payloads.append(kwargs)
        for ident in (str(i) for i in kwargs["points"]):
            for key in kwargs["keys"]:
                self.points[ident].pop(key, None)


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setattr(
        entries.config, "load_schema", lambda: {"types": [{"name": t} for t in TYPE_ENUM]}
    )
    monkeypatch.setattr(entries, "_now", lambda: "2026-09-28T12:00:00+00:00")
    return FakeQdrant({"abc": make_payload()})


@pytest.fixture
def embedded(monkeypatch):
    recorded = []

    def fake_embed(texts):
        recorded.append(list(texts))
        return [[0.5] * 4 for _ in texts]

    monkeypatch.setattr(entries, "embed", fake_embed)
    return recorded


# --- 编辑 ---


def test_edit_writes_override_and_trail(fake, embedded):
    view = entries.edit_entry("abc", rev=0, text="人工改过的正文", client=fake)
    stored = fake.points["abc"]

    assert view["id"] == "abc"      # 返回值也要带对 id（T111 判决里的真存活）
    assert view["rev"] == 1         # ……以及推进后的版本号
    assert stored["override"] == {"text": "人工改过的正文"}
    assert stored["original_text"] == "原始蒸馏正文"
    assert stored["edited_prev_text"] == "原始蒸馏正文"
    assert stored["edited_at"] == "2026-09-28T12:00:00+00:00"
    assert stored["rev"] == 1
    assert view["text"] == "人工改过的正文"
    assert view["original_text"] == "原始蒸馏正文"
    assert view["edited"] is True


def test_edit_keeps_the_point_id(fake, embedded):
    entries.edit_entry("abc", rev=0, text="改了", client=fake)
    assert str(fake.upserts[0]["points"][0].id) == "abc"
    assert list(fake.points) == ["abc"]  # 没有写出第二个点


def test_edit_embeds_the_effective_text(fake, embedded):
    """改了正文就必须用改后的正文算向量，否则显示新文字、命中靠旧语义。"""
    entries.edit_entry("abc", rev=0, text="改成可检索的说法", client=fake)
    assert embedded == [["改成可检索的说法"]]


def test_edit_without_text_keeps_embedding_on_current_text(fake, embedded):
    entries.edit_entry("abc", rev=0, type="progress", client=fake)
    assert embedded == [["原始蒸馏正文"]]
    assert fake.points["abc"]["override"] == {"type": "progress"}


def test_edit_rejects_type_outside_config(fake, embedded):
    with pytest.raises(entries.ValidationError, match="类型"):
        entries.edit_entry("abc", rev=0, type="not-a-type", client=fake)
    assert fake.upserts == [] and embedded == []


def test_edit_rejects_empty_text(fake, embedded):
    with pytest.raises(entries.ValidationError):
        entries.edit_entry("abc", rev=0, text="   ", client=fake)
    assert fake.upserts == []


def test_edit_rejects_stale_rev_without_writing(fake, embedded):
    with pytest.raises(entries.ConflictError, match="版本"):
        entries.edit_entry("abc", rev=7, text="改了", client=fake)
    assert fake.upserts == [] and embedded == []


def test_edit_does_not_write_when_embedding_fails(fake, monkeypatch):
    def boom(_texts):
        raise RuntimeError("Ark 502")

    monkeypatch.setattr(entries, "embed", boom)
    with pytest.raises(RuntimeError):
        entries.edit_entry("abc", rev=0, text="改了", client=fake)
    assert fake.points["abc"] == make_payload()  # 一个字节都没动


def test_edit_normalizes_tags(fake, embedded):
    entries.edit_entry("abc", rev=0, tags=["  b ", "a", "b", ""], client=fake)
    assert fake.points["abc"]["override"]["tags"] == ["b", "a"]


def test_edit_can_clear_project(fake, embedded):
    entries.edit_entry("abc", rev=0, project="   ", client=fake)
    assert fake.points["abc"]["override"]["project"] is None
    assert entries.to_view("abc", fake.points["abc"])["project"] is None


def test_edit_stores_a_real_project_value(fake, embedded):
    """清空与写入是两条路：只测清空会让"永远写 None"的变异体活下来（T111）。"""
    entries.edit_entry("abc", rev=0, project="  new-project  ", client=fake)
    assert fake.points["abc"]["override"]["project"] == "new-project"


def test_second_edit_keeps_first_original_text(fake, embedded):
    entries.edit_entry("abc", rev=0, text="第一版", client=fake)
    entries.edit_entry("abc", rev=1, text="第二版", client=fake)
    stored = fake.points["abc"]
    assert stored["original_text"] == "原始蒸馏正文"
    assert stored["edited_prev_text"] == "第一版"
    assert stored["rev"] == 2


def test_edit_missing_entry_raises_not_found(fake, embedded):
    with pytest.raises(entries.NotFoundError):
        entries.edit_entry("nope", rev=0, text="x", client=fake)


# --- 软删除 / 恢复 ---


def test_delete_marks_and_bumps_rev(fake):
    view = entries.delete_entry("abc", rev=0, reason="  蒸馏错了  ", client=fake)
    assert view["id"] == "abc"
    assert view["rev"] == 1
    assert fake.points["abc"]["deleted_at"] == "2026-09-28T12:00:00+00:00"
    assert fake.points["abc"]["deleted_reason"] == "蒸馏错了"
    assert fake.points["abc"]["rev"] == 1
    assert view["deleted_at"] is not None


def test_delete_does_not_touch_vectors(fake):
    """软删只是标记：一不重算向量、二不 upsert（ADR-19）。"""
    entries.delete_entry("abc", rev=0, client=fake)
    assert fake.upserts == []


def test_delete_twice_is_rejected(fake):
    entries.delete_entry("abc", rev=0, client=fake)
    with pytest.raises(entries.ValidationError, match="已是删除状态"):
        entries.delete_entry("abc", rev=1, client=fake)


def test_restore_removes_the_keys_and_bumps_rev(fake):
    entries.delete_entry("abc", rev=0, reason="错了", client=fake)
    view = entries.restore_entry("abc", rev=1, client=fake)

    assert view["id"] == "abc"
    assert view["rev"] == 2     # 返回的视图不能落后于库里写的版本
    assert "deleted_at" not in fake.points["abc"]
    assert "deleted_reason" not in fake.points["abc"]
    assert fake.points["abc"]["rev"] == 2
    assert view["deleted_at"] is None
    assert fake.delete_payloads[0]["keys"] == ["deleted_at", "deleted_reason"]


def test_restore_when_not_deleted_is_rejected(fake):
    with pytest.raises(entries.ValidationError, match="不在删除状态"):
        entries.restore_entry("abc", rev=0, client=fake)


# --- 供 ingest 复用的两个纯函数 ---


def test_human_fields_skips_empty_values():
    payload = make_payload(rev=0, override=None, deleted_at=None, edited_at=None)
    assert entries.human_fields(payload) == {}
    kept = entries.human_fields(make_payload(rev=2, deleted_at="t", override={"text": "x"}))
    assert set(kept) == {"rev", "deleted_at", "override"}


def test_embedding_text_prefers_override_text():
    human = {"override": {"text": "人工版"}}
    assert entries.embedding_text("自动版", human) == "人工版"
    assert entries.embedding_text("自动版", {}) == "自动版"


def test_now_returns_timezone_aware_iso():
    """`_now()` 写进 payload 的必须是带时区的 ISO 时间戳 —— 留痕要能跨时区读。

    这条是为了让 `_now` 有真覆盖：变异测试此前判它"整个函数无测试"（因为别的
    用例把它 monkeypatch 掉了）。补测试比登记豁免更诚实。
    """
    stamp = entries._now()
    parsed = datetime.fromisoformat(stamp)
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() is not None
