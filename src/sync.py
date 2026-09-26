"""Daily sync orchestration: collect → distill → ingest (T019).

设计要点：
1. 整个运行期间持有 data/.sync.lock 文件锁，cron 与 API 壳并发触发时
   后到者立即报错退出（F2 修复），避免同日双写。
2. 运行结束清理到期 raw 快照（FR-022）；库内条目永不因保留期被删。
3. CLI：python -m src.sync [D=YYYY-MM-DD]（FR-025），默认今天；
   任何阶段失败以非零码退出，可安全重跑（宪法 IV）。
"""

from __future__ import annotations

import json
import logging
import re
import sys
from collections import Counter
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import IO, Iterator

from src import collect, config, distill, ingest

logger = logging.getLogger(__name__)

_DATE_ARG = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class SyncInProgressError(RuntimeError):
    """Another sync already holds data/.sync.lock (F2)."""


def _try_lock(fd: int) -> None:
    """Cross-platform non-blocking exclusive lock on one open file."""
    try:
        import fcntl
    except ImportError:  # Windows
        import msvcrt
        import os

        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    else:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


@contextmanager
def _lock(path: Path) -> Iterator[None]:
    """Hold an exclusive lock on `path`; closing the handle releases it.

    The lock file itself is never deleted, so there is no stale-lock
    window: a crashed process releases its handle at exit (OS-level).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle: IO = path.open("a+")
    try:
        _try_lock(handle.fileno())
    except OSError as exc:
        handle.close()
        raise SyncInProgressError(
            f"another sync is already running (lock: {path})"
        ) from exc
    try:
        yield
    finally:
        handle.close()


def cleanup_raw(
    now: date | None = None,
    *,
    retention_days: int | None = None,
    raw_dir: Path | None = None,
) -> list[str]:
    """Delete raw snapshots older than the retention window (FR-022).

    raw_retention_days = null means "keep forever" (PRD FR-022).
    Qdrant entries are never touched here (AC-008).
    """
    if retention_days is None:
        retention_days = config.load_schema().get("raw_retention_days")
    if retention_days is None:
        return []
    directory = raw_dir or config.RAW_DIR
    if not directory.is_dir():
        return []

    now = now or datetime.now(tz=config.TZ).date()
    cutoff = now - timedelta(days=retention_days)
    removed: list[str] = []
    for path in sorted(directory.glob("????-??-??.json")):
        try:
            snapshot_day = date.fromisoformat(path.stem)
        except ValueError:
            continue  # not a day snapshot; leave it alone
        if snapshot_day < cutoff:
            path.unlink()
            removed.append(path.name)
            logger.info("sync: removed expired raw snapshot %s", path.name)
    return removed


def _load_scope() -> dict | None:
    """Read config/scope.json (FR-005). Broken file → None (sync all)."""
    path = config.CONFIG_DIR / "scope.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("sync: cannot read %s, ignoring scope: %s", path, exc)
        return None


def run(day: date | None = None, *, allow_shrink: bool = False) -> dict:
    """One full sync for `day` (default today). Returns a summary dict.

    `allow_shrink` 允许覆盖一份素材数更多的同日快照，默认**不允许**；
    `ALLOW_SHRINK=1` 是明路，理由见 `collect.save_snapshot`。
    """
    day = day or datetime.now(tz=config.TZ).date()
    with _lock(config.SYNC_LOCK_PATH):
        day_raw = collect.gather(day, _load_scope(), allow_shrink=allow_shrink)
        entries = distill.distill(day_raw)
        report = ingest.upsert(entries)
        removed = cleanup_raw(now=datetime.now(tz=config.TZ).date())
    summary = {
        "date": day.isoformat(),
        "materials": len(day_raw.materials),
        "entries": len(entries),
        "upserted": report.upserted,
        "skipped_by_source": report.skipped_by_source,
        "raw_removed": len(removed),
        "materials_by_source": _by_source(day_raw.materials),
        "entries_by_source": _by_source(entries),
    }
    logger.info("sync done: %s", summary)
    return summary


def _by_source(items: list[collect.RawMaterial | ingest.Entry]) -> dict[str, int]:
    """按 `source` 计数，条数多的在前（同数按名字升序）。

    只对账"采到了"与"进了库"：两个键取自同一次运行的同一批对象，所以
    `entries_by_source` 里少掉的源就是被熔断砍掉的那个。缺键表示该源当天
    为零，不补零 —— 真要逐个源点名得靠 `iter_plugins()` 的全量清单，那是
    另一件事，别在这里假装知道。
    """
    counts = Counter(item.source for item in items)
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def _parse_day(argv: list[str]) -> date | None:
    """Accept `D=YYYY-MM-DD` (Makefile form) or a bare date argument."""
    for arg in argv[1:]:
        value = arg[2:] if arg.startswith("D=") else arg
        if _DATE_ARG.match(value):
            return date.fromisoformat(value)
        if arg.startswith("D="):
            raise ValueError(f"invalid date argument: {arg}")
    return None


def _parse_allow_shrink(argv: list[str]) -> bool:
    """`ALLOW_SHRINK=1`（Makefile 形式）。见 `collect.save_snapshot`。"""
    return any(arg == "ALLOW_SHRINK=1" for arg in argv[1:])


def main(argv: list[str] | None = None) -> int:
    """CLI entry. 0 on success, non-zero on any failure (spec Edge Cases)."""
    config.load_env()
    # CLI 入口自己 bootstrap 环境：config.env() 只读 os.environ，忘了这一步
    # 就会在第一次读环境变量时炸掉（test_config 守着这条）。
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    try:
        argv = sys.argv if argv is None else argv
        day = _parse_day(argv)
        summary = run(day, allow_shrink=_parse_allow_shrink(argv))
    except SyncInProgressError as exc:
        logger.error("%s", exc)
        return 2
    except Exception:
        logger.exception("sync failed")
        return 1
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
