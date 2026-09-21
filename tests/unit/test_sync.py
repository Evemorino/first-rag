"""Unit tests for the T019 sync orchestration."""

import argparse
from datetime import date, datetime

import pytest

from src import sync
from src.collect import DayRaw
from src.ingest import Entry, Report


DAY = date(2026, 9, 20)


@pytest.fixture
def sync_paths(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    raw_dir = data_dir / "raw"
    lock_path = data_dir / ".sync.lock"
    raw_dir.mkdir(parents=True)
    monkeypatch.setattr(sync.config, "DATA_DIR", data_dir)
    monkeypatch.setattr(sync.config, "RAW_DIR", raw_dir)
    monkeypatch.setattr(sync.config, "SYNC_LOCK_PATH", lock_path)
    monkeypatch.setattr(
        sync.config,
        "load_schema",
        lambda: {"raw_retention_days": 90},
    )
    return raw_dir, lock_path


def make_entry() -> Entry:
    return Entry(
        text="Sync pipeline entry",
        date=DAY.isoformat(),
        type="progress",
        tags=["sync", "progress"],
        source="manual",
        project=None,
        created_at=datetime(2026, 9, 20, 14, 0, 0),
        source_refs=["inbox.md"],
        distill_version="direct+rubric@test",
    )


def test_run_chains_pipeline_and_releases_lock(sync_paths, monkeypatch):
    _, lock_path = sync_paths
    day_raw = DayRaw(
        day=DAY,
        collected_at=datetime(2026, 9, 20, 13, 0, 0),
        materials=[],
    )
    entry = make_entry()
    calls = []

    monkeypatch.setattr(
        sync.collect,
        "gather",
        lambda day: calls.append(("gather", day)) or day_raw,
    )
    monkeypatch.setattr(
        sync.distill,
        "distill",
        lambda raw: calls.append(("distill", raw)) or [entry],
    )
    monkeypatch.setattr(
        sync.ingest,
        "upsert",
        lambda entries: calls.append(("upsert", entries))
        or Report(upserted=len(entries)),
    )
    monkeypatch.setattr(
        sync,
        "cleanup_raw",
        lambda day, retention: calls.append(("cleanup", day, retention)) or 2,
    )

    report = sync.run(DAY)

    assert report == sync.SyncReport(
        day=DAY,
        materials=0,
        entries=1,
        upserted=1,
        cleaned=2,
    )
    assert calls == [
        ("gather", DAY),
        ("distill", day_raw),
        ("upsert", [entry]),
        ("cleanup", DAY, 90),
    ]
    assert not lock_path.exists()


def test_existing_lock_blocks_run_and_is_preserved(sync_paths, monkeypatch):
    _, lock_path = sync_paths
    lock_path.write_text("pid=123\n", encoding="utf-8")
    calls = []
    monkeypatch.setattr(
        sync.collect,
        "gather",
        lambda day: calls.append(day) or DayRaw(DAY, datetime.now()),
    )

    with pytest.raises(sync.SyncLockError, match="already running"):
        sync.run(DAY)

    assert calls == []
    assert lock_path.read_text(encoding="utf-8") == "pid=123\n"


def test_pipeline_failure_releases_lock(sync_paths, monkeypatch):
    _, lock_path = sync_paths

    def fail_gather(day):
        raise RuntimeError("collect failed")

    monkeypatch.setattr(sync.collect, "gather", fail_gather)

    with pytest.raises(RuntimeError, match="collect failed"):
        sync.run(DAY)

    assert not lock_path.exists()


def test_cleanup_raw_removes_only_snapshots_before_cutoff(sync_paths):
    raw_dir, _ = sync_paths
    old = raw_dir / "2026-09-16.json"
    boundary = raw_dir / "2026-09-17.json"
    current = raw_dir / "2026-09-20.json"
    malformed = raw_dir / "not-a-snapshot.json"
    invalid_date = raw_dir / "2026-99-99.json"
    for path in (old, boundary, current, malformed, invalid_date):
        path.write_text("{}", encoding="utf-8")

    cleaned = sync.cleanup_raw(DAY, 3)

    assert cleaned == 1
    assert not old.exists()
    assert boundary.exists()
    assert current.exists()
    assert malformed.exists()
    assert invalid_date.exists()


def test_cleanup_raw_with_zero_days_keeps_target_day(sync_paths):
    raw_dir, _ = sync_paths
    previous = raw_dir / "2026-09-19.json"
    current = raw_dir / "2026-09-20.json"
    previous.write_text("{}", encoding="utf-8")
    current.write_text("{}", encoding="utf-8")

    cleaned = sync.cleanup_raw(DAY, 0)

    assert cleaned == 1
    assert not previous.exists()
    assert current.exists()


def test_cleanup_raw_with_null_retention_is_permanent(sync_paths):
    raw_dir, _ = sync_paths
    old = raw_dir / "2020-01-01.json"
    old.write_text("{}", encoding="utf-8")

    assert sync.cleanup_raw(DAY, None) == 0
    assert old.exists()


def test_parse_day_requires_iso_date():
    assert sync.parse_day("2026-09-17") == date(2026, 9, 17)
    with pytest.raises(argparse.ArgumentTypeError):
        sync.parse_day("2026/09/17")


def test_main_runs_requested_day(monkeypatch, capsys):
    calls = []
    report = sync.SyncReport(
        day=date(2026, 9, 17),
        materials=2,
        entries=1,
        upserted=1,
        cleaned=0,
    )
    monkeypatch.setattr(
        sync,
        "run",
        lambda day: calls.append(day) or report,
    )

    exit_code = sync.main(["2026-09-17"])

    assert exit_code == 0
    assert calls == [date(2026, 9, 17)]
    assert "2026-09-17" in capsys.readouterr().out
