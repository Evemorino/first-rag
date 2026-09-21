"""Daily sync orchestration: collect -> distill -> ingest -> retention.

The lock is acquired before any work starts. A second process fails
immediately instead of competing with the first one (FR-022, F2 fix).
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from src import collect, config, distill, ingest

logger = logging.getLogger(__name__)

_RAW_SNAPSHOT_NAME = re.compile(r"^(?P<day>\d{4}-\d{2}-\d{2})\.json$")


class SyncLockError(RuntimeError):
    """Raised when another sync process already owns the lock."""


@dataclass(frozen=True)
class SyncReport:
    """Summary returned by one sync run."""

    day: date
    materials: int
    entries: int
    upserted: int
    cleaned: int


@contextmanager
def _sync_lock() -> Iterator[None]:
    """Hold an exclusive process lock for the full sync pipeline."""
    path = config.SYNC_LOCK_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise SyncLockError(
            f"sync already running (lock: {path}); "
            "remove the lock if the previous process is no longer running"
        ) from None

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(f"pid={os.getpid()}\n")
            handle.write(f"started_at={datetime.now(tz=config.TZ).isoformat()}\n")
        yield
    finally:
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def cleanup_raw(day: date, retention_days: int | None) -> int:
    """Delete raw snapshots older than the configured retention window."""
    if retention_days is None:
        return 0

    cutoff = day - timedelta(days=retention_days)
    cleaned = 0
    for path in sorted(config.RAW_DIR.glob("*.json")):
        match = _RAW_SNAPSHOT_NAME.fullmatch(path.name)
        if match is None or not path.is_file():
            continue
        try:
            snapshot_day = date.fromisoformat(match.group("day"))
        except ValueError:
            logger.warning("sync: ignoring malformed raw snapshot name %s", path)
            continue
        if snapshot_day >= cutoff:
            continue
        try:
            path.unlink()
        except FileNotFoundError:
            continue
        cleaned += 1
        logger.info("sync: removed expired raw snapshot %s", path)
    return cleaned


def run(day: date | None = None) -> SyncReport:
    """Run the full pipeline for one day under the sync lock."""
    target_day = day or datetime.now(tz=config.TZ).date()
    with _sync_lock():
        schema = config.load_schema()
        day_raw = collect.gather(target_day)
        entries = distill.distill(day_raw)
        result = ingest.upsert(entries)
        cleaned = cleanup_raw(target_day, schema["raw_retention_days"])

    report = SyncReport(
        day=target_day,
        materials=len(day_raw.materials),
        entries=len(entries),
        upserted=result.upserted,
        cleaned=cleaned,
    )
    logger.info(
        "sync: day=%s materials=%d entries=%d upserted=%d cleaned=%d",
        report.day,
        report.materials,
        report.entries,
        report.upserted,
        report.cleaned,
    )
    return report


def parse_day(value: str) -> date:
    """Parse the CLI date as strict YYYY-MM-DD."""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is None:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD") from exc


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point for ``make sync [D=YYYY-MM-DD]`` (FR-025)."""
    parser = argparse.ArgumentParser(description="Run one daily first-rag sync")
    parser.add_argument(
        "day",
        nargs="?",
        type=parse_day,
        help="day to sync in YYYY-MM-DD format (default: today in Asia/Shanghai)",
    )
    args = parser.parse_args(argv)
    target_day = args.day or datetime.now(tz=config.TZ).date()

    try:
        report = run(target_day)
    except SyncLockError as exc:
        print(f"sync: {exc}", file=sys.stderr)
        return 2
    except Exception:
        logger.exception("sync: failed for %s", target_day)
        return 1

    print(
        f"sync {report.day}: materials={report.materials} "
        f"entries={report.entries} upserted={report.upserted} "
        f"cleaned={report.cleaned}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
