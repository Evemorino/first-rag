"""T038/T039 integration: 历史补跑（AC-009）+ retention（AC-008）。

内嵌 Qdrant；Ark 侧 fake。AC-009 已由 test_sync_four_sources（固定过去
日 2026-09-18 跑通全链）覆盖，这里补 retention 语义的实证。
"""

import json
from datetime import date, datetime
from uuid import uuid5, NAMESPACE_URL

import pytest
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

from src import collect, config, distill, ingest, sync

TEST_COLLECTION = "learning_memory_test_t039"
DAY = date(2026, 9, 18)
TODAY = date(2026, 9, 20)


@pytest.fixture
def world(tmp_data_dir, monkeypatch):
    # 库里预置 DAY 的两个条目（模拟已同步的历史日）
    client = QdrantClient(":memory:")
    client.create_collection(
        collection_name=TEST_COLLECTION,
        vectors_config=VectorParams(size=2, distance=Distance.COSINE))
    points = [
        PointStruct(
            id=str(uuid5(NAMESPACE_URL, f"t039|{i}")), vector=[float(i), 1.0],
            payload={"text": f"entry {i}", "date": DAY.isoformat(),
                     "type": "progress", "source": "manual", "project": None,
                     "created_at": "2026-09-18T22:00:00+08:00",
                     "source_refs": [f"r{i}"], "distill_version": "t+r@a",
                     "related": []})
        for i in range(2)
    ]
    client.upsert(collection_name=TEST_COLLECTION, wait=True, points=points)

    # raw 快照：过期日 DAY + 今日空快照
    config.RAW_DIR.mkdir(parents=True, exist_ok=True)
    for day, status in ((DAY, "ok"), (TODAY, "noop")):
        (config.RAW_DIR / f"{day.isoformat()}.json").write_text(json.dumps({
            "date": day.isoformat(),
            "collected_at": f"{day.isoformat()}T22:00:00+08:00",
            "distill_run": {"status": status}, "materials": []}),
            encoding="utf-8")

    # 当日无素材（gather 为空）+ retention 0
    monkeypatch.setattr(config, "COLLECTION", TEST_COLLECTION)
    monkeypatch.setattr(sync.collect, "gather",
                        lambda day, scope=None, **kw: collect.DayRaw(
                            day=day, collected_at=datetime.now(tz=config.TZ)))
    monkeypatch.setattr(sync.distill, "distill", lambda day_raw: [])
    monkeypatch.setattr(sync.ingest, "upsert", lambda entries: ingest.Report(0))
    real_schema = config.load_schema()

    def schema_retention_zero():
        merged = dict(real_schema)
        merged["raw_retention_days"] = 0
        return merged

    monkeypatch.setattr(config, "load_schema", schema_retention_zero)

    # 时间冻结：retention 以"真实今天"为基准（补跑历史时也应清过期快照），
    # 若测试沿用真实时钟，日历一翻页 cutoff 就变，断言会从 1 漂到 2。
    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(TODAY.year, TODAY.month, TODAY.day, 22, 0,
                       tzinfo=tz or config.TZ)

    monkeypatch.setattr(sync, "datetime", _FrozenDateTime)

    yield client
    client.close()


def test_retention_zero_cleans_expired_raw_but_keeps_entries(world):
    summary = sync.run(TODAY)

    # 过期 raw（DAY）被清理，今日快照保留
    assert summary["raw_removed"] == 1
    assert not (config.RAW_DIR / f"{DAY.isoformat()}.json").exists()
    assert (config.RAW_DIR / f"{TODAY.isoformat()}.json").exists()

    # 库内条目不受影响（AC-008）
    count = world.count(collection_name=TEST_COLLECTION, exact=True).count
    assert count == 2
