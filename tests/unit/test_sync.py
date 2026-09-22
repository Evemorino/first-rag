"""T019 unit tests for src/sync.py (collect → distill → ingest + lock + retention).

All external effects are faked: gather/distill/upsert are monkeypatched,
so these tests verify orchestration, locking, and retention only.
"""

import json
from datetime import date, datetime

import pytest

from src import collect, config, distill, ingest, sync
from src.collect import DayRaw
from src.ingest import Entry, Report
from src.plugins import RawMaterial

DAY = date(2026, 9, 20)


@pytest.fixture
def pipeline(tmp_data_dir, monkeypatch):
    """Fake the three pipeline stages and record the call order."""
    calls = []

    def fake_gather(day, scope=None):
        calls.append(("gather", day, scope))
        return DayRaw(
            day=day,
            collected_at=datetime(2026, 9, 20, 12, 0, 0),
            materials=[RawMaterial(
                source="claude_code", ref="s1",
                ts=datetime(2026, 9, 20, 10, 0, 0),
                kind="message", text="hello")],
        )

    def fake_distill(day_raw):
        calls.append(("distill", day_raw.day))
        return [make_entry(day_raw.day)]

    def fake_upsert(entries):
        calls.append(("upsert", len(entries)))
        return Report(upserted=len(entries))

    monkeypatch.setattr(sync.collect, "gather", fake_gather)
    monkeypatch.setattr(sync.distill, "distill", fake_distill)
    monkeypatch.setattr(sync.ingest, "upsert", fake_upsert)
    return calls


def make_entry(day: date) -> Entry:
    return Entry(
        text="distilled lesson",
        date=day.isoformat(),
        type="progress",
        tags=["t"],
        source="claude_code",
        project=None,
        created_at=datetime(2026, 9, 20, 12, 0, 0),
        source_refs=["s1"],
        distill_version="test+rubric@abcd1234",
    )


# --- pipeline orchestration ---


def test_run_chains_gather_distill_ingest_in_order(pipeline):
    summary = sync.run(DAY)

    assert [name for name, *_ in pipeline] == ["gather", "distill", "upsert"]
    assert pipeline[0][1] == DAY
    assert summary == {
        "date": "2026-09-20",
        "materials": 1,
        "entries": 1,
        "upserted": 1,
        "raw_removed": 0,
    }


def test_run_defaults_to_today(pipeline):
    from src import config as cfg

    sync.run(None)
    today = datetime.now(tz=cfg.TZ).date()
    assert pipeline[0][1] == today


# --- file lock (F2) ---


def test_lock_is_held_while_pipeline_runs(pipeline):
    """Inside gather the lock must already block a second acquisition."""
    observed = {}

    def probing_gather(day, scope=None):
        try:
            with sync._lock(config.SYNC_LOCK_PATH):
                observed["second"] = "acquired"
        except sync.SyncInProgressError:
            observed["second"] = "rejected"
        return DayRaw(day=day, collected_at=datetime.now())

    pipeline and None  # keep fixture ordering side effects
    sync.collect.gather = probing_gather
    sync.run(DAY)
    assert observed["second"] == "rejected"


def test_second_sync_rejected_immediately(pipeline):
    with sync._lock(config.SYNC_LOCK_PATH):
        with pytest.raises(sync.SyncInProgressError) as excinfo:
            sync.run(DAY)

    # 锁路径必须写进消息：并发撞锁时，用户得知道去哪个文件删。
    assert str(excinfo.value) == (
        f"another sync is already running (lock: {config.SYNC_LOCK_PATH})"
    )
    # after release the sync can run again
    assert sync.run(DAY)["upserted"] == 1


def test_lock_released_after_failed_pipeline(pipeline, monkeypatch):
    def boom(entries):
        raise RuntimeError("qdrant down")

    monkeypatch.setattr(sync.ingest, "upsert", boom)
    with pytest.raises(RuntimeError) as excinfo:
        sync.run(DAY)

    # 原样向上抛，不许包装成 SyncError —— 真正的故障原因不能被吞掉。
    assert str(excinfo.value) == "qdrant down"
    # lock must not stay behind after a crash inside the pipeline
    with sync._lock(config.SYNC_LOCK_PATH):
        pass


# --- retention (FR-022) ---


@pytest.fixture
def raw_dir(tmp_data_dir):
    raw = config.RAW_DIR
    raw.mkdir(parents=True, exist_ok=True)
    return raw


def write_snapshot(raw_dir, name: str) -> None:
    (raw_dir / name).write_text("{}", encoding="utf-8")


def test_cleanup_removes_only_expired_snapshots(raw_dir, monkeypatch):
    monkeypatch.setattr(sync.config, "load_schema",
                        lambda: {"raw_retention_days": 90})
    write_snapshot(raw_dir, "2026-09-20.json")   # today
    write_snapshot(raw_dir, "2026-07-01.json")   # 81 days old: keep
    write_snapshot(raw_dir, "2026-05-01.json")   # 142 days old: delete
    write_snapshot(raw_dir, "not-a-date.json")   # never touched

    removed = sync.cleanup_raw(now=date(2026, 9, 20))

    assert removed == ["2026-05-01.json"]
    assert (raw_dir / "2026-09-20.json").exists()
    assert (raw_dir / "2026-07-01.json").exists()
    assert (raw_dir / "not-a-date.json").exists()


def test_cleanup_zero_retention_removes_all_past_days(raw_dir, monkeypatch):
    monkeypatch.setattr(sync.config, "load_schema",
                        lambda: {"raw_retention_days": 0})
    write_snapshot(raw_dir, "2026-09-20.json")
    write_snapshot(raw_dir, "2026-09-19.json")

    removed = sync.cleanup_raw(now=date(2026, 9, 20))

    assert removed == ["2026-09-19.json"]
    assert (raw_dir / "2026-09-20.json").exists()


def test_cleanup_none_retention_keeps_everything(raw_dir, monkeypatch):
    monkeypatch.setattr(sync.config, "load_schema",
                        lambda: {"raw_retention_days": None})
    write_snapshot(raw_dir, "2020-01-01.json")

    assert sync.cleanup_raw(now=date(2026, 9, 20)) == []
    assert (raw_dir / "2020-01-01.json").exists()


def test_run_invokes_retention_cleanup(pipeline, raw_dir, monkeypatch):
    monkeypatch.setattr(sync.config, "load_schema",
                        lambda: {"raw_retention_days": 0})
    write_snapshot(raw_dir, "2026-09-19.json")

    summary = sync.run(DAY)

    assert summary["raw_removed"] == 1
    assert not (raw_dir / "2026-09-19.json").exists()


# --- scope.json (FR-005, feeding T035) ---


def test_run_passes_scope_json_to_gather(pipeline, tmp_data_dir, monkeypatch):
    scope = {"tools": {"claude_code": False}, "projects": {}}
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_data_dir / "config")
    (tmp_data_dir / "config").mkdir()
    (tmp_data_dir / "config" / "scope.json").write_text(
        json.dumps(scope), encoding="utf-8")

    sync.run(DAY)

    assert pipeline[0][2] == scope


def test_run_ignores_broken_scope_json(pipeline, tmp_data_dir, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_data_dir / "config")
    (tmp_data_dir / "config").mkdir()
    (tmp_data_dir / "config" / "scope.json").write_text("{oops", encoding="utf-8")

    sync.run(DAY)

    assert pipeline[0][2] is None


def test_run_without_scope_json_passes_none(pipeline):
    sync.run(DAY)
    assert pipeline[0][2] is None


# --- CLI entry (FR-025) ---


def test_main_parses_d_equals_date(pipeline, monkeypatch):
    code = sync.main(["sync", "D=2026-09-18"])
    assert code == 0
    assert pipeline[0][1] == date(2026, 9, 18)


def test_main_parses_plain_date(pipeline):
    assert sync.main(["sync", "2026-09-18"]) == 0
    assert pipeline[0][1] == date(2026, 9, 18)


def test_main_defaults_to_today(pipeline):
    assert sync.main(["sync"]) == 0
    assert pipeline[0][1] == datetime.now(tz=config.TZ).date()


def test_main_exits_nonzero_on_pipeline_failure(pipeline, monkeypatch):
    monkeypatch.setattr(sync.ingest, "upsert",
                        lambda entries: (_ for _ in ()).throw(RuntimeError("x")))
    assert sync.main(["sync"]) == 1


def test_main_exits_nonzero_when_sync_already_running(pipeline):
    # 退出码要能区分"有人在跑"(2) 和"跑失败了"(1)：cron 撞锁是正常情况，
    # 不该和真故障混成一个码。断言写成 != 0 的话，2 改成 3 也没人发现。
    with sync._lock(config.SYNC_LOCK_PATH):
        assert sync.main(["sync"]) == 2


def test_main_prints_summary_json_to_stdout(pipeline, capsys):
    """stdout 是给 shell/Makefile 消费的（make sync | jq），格式就是契约。"""
    assert sync.main(["sync", "D=2026-09-18"]) == 0

    assert json.loads(capsys.readouterr().out) == {
        "date": "2026-09-18",
        "materials": 1,
        "entries": 1,
        "upserted": 1,
        "raw_removed": 0,
    }


def test_main_rejects_invalid_date(pipeline):
    assert sync.main(["sync", "D=not-a-date"]) == 1
