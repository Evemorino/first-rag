"""T036 unit tests: 重蒸馏对照编排（FR-023）。

对齐规则本体是 ★ T037（用户手写在 similarity.py），这里只验证编排：
快照加载 → 重跑蒸馏 → 新旧 diff（注入 fake 对齐器）→ 先删后插整组替换。
"""

import json
from datetime import date, datetime
from types import SimpleNamespace

import pytest

from src import config, distill, ingest, redistill
from src.collect import DayRaw
from src.ingest import Entry, Report
from src.plugins import RawMaterial

DAY = date(2026, 9, 18)


def entry(text: str, *, refs=("s1",), source="claude_code") -> Entry:
    return Entry(
        text=text, date=DAY.isoformat(), type="progress", tags=["t"],
        source=source, project=None,
        created_at=datetime(2026, 9, 18, 12, 0, 0),
        source_refs=list(refs), distill_version="m+rubric@abcd1234")


@pytest.fixture
def raw_snapshot(tmp_data_dir, monkeypatch):
    config.RAW_DIR.mkdir(parents=True, exist_ok=True)
    snapshot = {
        "date": DAY.isoformat(),
        "collected_at": "2026-09-18T22:00:00+08:00",
        "distill_run": {"model": "old", "rubric_hash": "h", "status": "ok"},
        "materials": [
            {"source": "claude_code", "ref": "s1", "ts": None,
             "kind": "message", "text": "old material", "meta": {}},
        ],
    }
    (config.RAW_DIR / f"{DAY.isoformat()}.json").write_text(
        json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
    return snapshot


class FakeQdrant:
    def __init__(self, payloads):
        self.payloads = payloads
        self.deleted = []
        self.scrolls = []

    def scroll(self, *, collection_name, scroll_filter=None,
               limit=100, with_payload=True):
        self.scrolls.append({
            "collection_name": collection_name,
            "scroll_filter": scroll_filter,
            "limit": limit,
            "with_payload": with_payload,
        })
        return ([SimpleNamespace(id=f"id-{i}", payload=p)
                 for i, p in enumerate(self.payloads)], None)

    def delete(self, *, collection_name, points_selector):
        self.deleted.append(points_selector)


def _entry_from_payload(entry_obj) -> Entry:
    return entry_obj


@pytest.fixture
def alignment_seam(monkeypatch):
    """Fake the ★ T037 alignment rule living in similarity.align_redistill."""
    calls = {}

    def fake_align(old, new):
        calls["old"] = old
        calls["new"] = new
        paired = [(old[0], new[0])] if old and new else []
        return {"rewritten": paired, "unchanged": [],
                "added": new[1:], "removed": old[1:]}

    monkeypatch.setattr(redistill.similarity, "align_redistill",
                        fake_align, raising=False)
    return calls


def test_load_day_raw_from_snapshot(raw_snapshot):
    day_raw = redistill.load_day_raw(DAY)

    assert isinstance(day_raw, DayRaw)
    assert day_raw.day == DAY
    assert day_raw.materials[0].ref == "s1"


def test_load_day_raw_missing_snapshot_fails(tmp_data_dir):
    with pytest.raises(redistill.RedistillError) as excinfo:
        redistill.load_day_raw(DAY)

    # 中间夹的是 OS 给的 errno 文本（路径也随 tmp 变），整串比会很脆；
    # 所以锁住首尾结构：谁的快照、为什么拿不到、用户该怎么办。
    message = str(excinfo.value)
    assert message.startswith(f"raw snapshot for {DAY} not available (")
    assert "No such file or directory" in message
    assert message.endswith(
        "); redistill needs the day's snapshot within its retention window"
    )


def test_redistill_produces_diff(raw_snapshot, alignment_seam, monkeypatch):
    old_entries = [entry("old text")]
    new_entries = [entry("rewritten text"), entry("brand new", refs=("s2",))]
    monkeypatch.setattr(
        redistill, "_fetch_day_entries", lambda day: old_entries)
    monkeypatch.setattr(redistill.distill, "distill", lambda dr: new_entries)

    diff, new_out = redistill.redistill(DAY)

    assert alignment_seam["old"] == old_entries
    assert alignment_seam["new"] == new_entries
    assert new_out == new_entries  # diff 与替换共用同一批新条目
    assert diff["rewritten"] == [(old_entries[0], new_entries[0])]
    assert [e.text for e in diff["added"]] == ["brand new"]


def test_apply_deletes_day_then_upserts(raw_snapshot, monkeypatch):
    order = []
    fake_qdrant = FakeQdrant([])
    monkeypatch.setattr(redistill, "_client", lambda: fake_qdrant)
    new_entries = [entry("rewritten")]
    monkeypatch.setattr(
        redistill.ingest, "upsert",
        lambda entries: order.append("upsert") or Report(upserted=len(entries)))

    report = redistill.apply_replace(DAY, new_entries)

    assert order == ["upsert"]
    assert len(fake_qdrant.deleted) == 1  # 先删该日全部
    assert fake_qdrant.deleted[0].filter.must[0].key == "date"
    assert report.upserted == 1


def test_main_without_apply_only_prints_diff(
        raw_snapshot, alignment_seam, monkeypatch, capsys):
    monkeypatch.setattr(
        redistill, "_fetch_day_entries", lambda day: [entry("old")])
    monkeypatch.setattr(
        redistill.distill, "distill", lambda dr: [entry("new")])
    applied = []
    monkeypatch.setattr(redistill, "apply_replace",
                        lambda day, entries: applied.append(day))

    code = redistill.main(["redistill", f"D={DAY.isoformat()}"])

    assert code == 0
    assert applied == []  # 未确认：只打印 diff，不替换
    out = capsys.readouterr().out
    assert "改写" in out


def test_main_apply_flag_replaces(
        raw_snapshot, alignment_seam, monkeypatch):
    fake_qdrant = FakeQdrant([])
    monkeypatch.setattr(redistill, "_client", lambda: fake_qdrant)
    monkeypatch.setattr(
        redistill, "_fetch_day_entries", lambda day: [entry("old")])
    monkeypatch.setattr(
        redistill.distill, "distill", lambda dr: [entry("new")])
    monkeypatch.setattr(redistill.ingest, "upsert",
                        lambda e: Report(upserted=len(e)))

    code = redistill.main(["redistill", f"D={DAY.isoformat()}", "--apply"])

    assert code == 0
    assert len(fake_qdrant.deleted) == 1


def test_main_reports_pending_alignment_rule(raw_snapshot, monkeypatch):
    """对齐规则是 ★ T037（用户手写）；未就位时给明确指引而非裸崩。"""
    monkeypatch.delattr(redistill.similarity, "align_redistill", raising=False)
    monkeypatch.setattr(
        redistill, "_fetch_day_entries", lambda day: [entry("old")])
    monkeypatch.setattr(
        redistill.distill, "distill", lambda dr: [entry("new")])

    code = redistill.main(["redistill", f"D={DAY.isoformat()}"])

    assert code == 1


# --- _fetch_day_entries：库里条目回读成 Entry，字段一个都不能走样 ---


def test_fetch_day_entries_maps_full_payload_to_entry():
    client = FakeQdrant([{
        "text": "old entry", "date": DAY.isoformat(), "type": "error",
        "tags": ["qdrant"], "source": "codex", "project": "first-rag",
        "created_at": "2026-09-18T12:00:00",
        "source_refs": ["s1", "c1"],
        "distill_version": "m+rubric@abcd1234",
        "related": ["00000000-0000-0000-0000-000000000001"],
    }])

    entries = redistill._fetch_day_entries(DAY, client=client)

    assert len(entries) == 1
    got = entries[0]
    assert got.text == "old entry"
    assert got.type == "error"
    assert got.tags == ["qdrant"]
    assert got.source == "codex"
    assert got.project == "first-rag"
    assert got.created_at == datetime(2026, 9, 18, 12, 0, 0)
    assert got.source_refs == ["s1", "c1"]
    assert got.distill_version == "m+rubric@abcd1234"
    assert [str(r) for r in got.related] == [
        "00000000-0000-0000-0000-000000000001"
    ]


def test_fetch_day_entries_falls_back_to_safe_defaults():
    """Qdrant 里可能有老数据缺字段；回读不能炸，且要能看出缺了什么。"""
    client = FakeQdrant([{}])

    (got,) = redistill._fetch_day_entries(DAY, client=client)

    assert got.text == ""
    assert got.date == DAY.isoformat()   # 兜底用查询的那一天
    assert got.type == ""
    assert got.tags == []
    assert got.source == ""
    assert got.project is None
    assert got.source_refs == []
    assert got.distill_version == ""
    assert got.related == []


def test_fetch_day_entries_scrolls_only_the_requested_day():
    client = FakeQdrant([{}])

    redistill._fetch_day_entries(DAY, client=client)

    call = client.scrolls[0]
    assert call["collection_name"] == config.COLLECTION
    assert call["limit"] == 256
    assert call["with_payload"] is True
    condition = call["scroll_filter"].must[0]
    assert condition.key == "date"
    assert condition.match.value == DAY.isoformat()


def test_fetch_day_entries_returns_empty_list_when_day_is_absent():
    client = FakeQdrant([])

    assert redistill._fetch_day_entries(DAY, client=client) == []
