"""重蒸馏对照编排（T036 / FR-023）。

流程：加载当日 raw 快照 → 按当前配置重跑蒸馏 → 拉取库中该日旧条目 →
对齐 diff（★ T037 用户手写在 similarity.align_redistill：共享
source_refs 且相似度 ≥0.85 配对；配对有差异=改写；仅新=新增；仅旧=消失）
→ 用户确认后整组替换（先删该日全部，再插新组，PRD §9 事务性说明）。

CLI：python -m src.redistill D=YYYY-MM-DD [--apply]
     不带 --apply 只打印 diff；带 --apply 执行替换。
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import date, datetime
from uuid import UUID

from qdrant_client import QdrantClient
from qdrant_client.models import FieldCondition, Filter, FilterSelector, MatchValue

from src import config, distill, ingest, similarity
from src.collect import DayRaw
from src.ingest import Entry
from src.plugins import RawMaterial

logger = logging.getLogger(__name__)


class RedistillError(RuntimeError):
    """Raised when the day's raw snapshot is missing or unreadable."""


def _client() -> QdrantClient:
    return QdrantClient(url=config.QDRANT_URL, trust_env=False)


def load_day_raw(day: date) -> DayRaw:
    """Rebuild a DayRaw from data/raw/YYYY-MM-DD.json（重蒸馏基线）。"""
    path = config.RAW_DIR / f"{day.isoformat()}.json"
    try:
        snapshot = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError) as exc:
        raise RedistillError(
            f"raw snapshot for {day} not available ({exc}); "
            "redistill needs the day's snapshot within its retention window"
        ) from exc
    materials = [
        RawMaterial(
            source=m["source"], ref=m["ref"],
            ts=datetime.fromisoformat(m["ts"]) if m.get("ts")
            else datetime.combine(day, datetime.min.time(), tzinfo=config.TZ),
            kind=m["kind"], text=m.get("text", ""), meta=m.get("meta", {}))
        for m in snapshot.get("materials", [])
    ]
    return DayRaw(day=day,
                  collected_at=datetime.fromisoformat(snapshot["collected_at"]),
                  materials=materials)


def _fetch_day_entries(day: date, *, client: QdrantClient | None = None) -> list[Entry]:
    """Pull the day's current entries from Qdrant (payload → Entry)."""
    qdrant = client or _client()
    points, _ = qdrant.scroll(
        collection_name=config.COLLECTION,
        scroll_filter=Filter(must=[
            FieldCondition(key="date", match=MatchValue(value=day.isoformat()))]),
        limit=256,
        with_payload=True,
    )
    entries = []
    for point in points:
        payload = point.payload or {}
        entries.append(Entry(
            text=payload.get("text", ""),
            date=payload.get("date", day.isoformat()),
            type=payload.get("type", ""),
            tags=payload.get("tags", []),
            source=payload.get("source", ""),
            project=payload.get("project"),
            created_at=datetime.fromisoformat(payload.get(
                "created_at", datetime.now(tz=config.TZ).isoformat())),
            source_refs=payload.get("source_refs", []),
            distill_version=payload.get("distill_version", ""),
            related=[UUID(r) for r in payload.get("related", [])],
        ))
    return entries


def redistill(day: date) -> tuple[dict, list[Entry]]:
    """重跑当日蒸馏并产出（对照 diff, 新条目集）。不改库。

    diff 与 apply 共用同一批 new_entries：蒸馏是显式非确定的外部调用
    （宪法 IV），重跑两次可能产出不同条目集，确认的必须就是替换的。
    """
    day_raw = load_day_raw(day)
    old_entries = _fetch_day_entries(day)
    new_entries = distill.distill(day_raw)

    aligner = getattr(similarity, "align_redistill", None)
    if aligner is None:
        raise NotImplementedError(
            "alignment rule not implemented yet (T037, 用户手写："
            "similarity.align_redistill)")
    return aligner(old_entries, new_entries), new_entries


def apply_replace(day: date, new_entries: list[Entry]) -> ingest.Report:
    """确认后整组替换：先删该日全部条目，再插入新组（FR-023）。"""
    qdrant = _client()
    qdrant.delete(
        collection_name=config.COLLECTION,
        points_selector=FilterSelector(filter=Filter(must=[
            FieldCondition(key="date",
                           match=MatchValue(value=day.isoformat()))])),
    )
    logger.info("redistill: deleted all %s entries before re-insert", day)
    return ingest.upsert(new_entries)


def _format_diff(diff: dict) -> str:
    lines = []
    for old, new in diff.get("rewritten", []):
        lines.append(f"改写：{old.text[:60]!r} -> {new.text[:60]!r}")
    for old, new in diff.get("unchanged", []):
        lines.append(f"未变：{old.text[:60]!r}")
    for e in diff.get("added", []):
        lines.append(f"新增：{e.text[:80]!r}")
    for e in diff.get("removed", []):
        lines.append(f"消失：{e.text[:80]!r}")
    return "\n".join(lines) or "（无差异）"


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    argv = sys.argv if argv is None else argv
    day_value = next((a[2:] for a in argv[1:] if a.startswith("D=")), None)
    apply = "--apply" in argv[1:]
    if not day_value:
        print("usage: make redistill D=YYYY-MM-DD [APPLY=1]", file=sys.stderr)
        return 2
    day = date.fromisoformat(day_value)

    try:
        diff, new_entries = redistill(day)
    except (RedistillError, NotImplementedError) as exc:
        print(f"redistill failed: {exc}", file=sys.stderr)
        return 1
    except Exception:
        logging.getLogger(__name__).exception("redistill failed")
        return 1

    print(f"{day} 重蒸馏对照：")
    print(_format_diff(diff))
    if not apply:
        print("\n确认无误后执行：make redistill D=%s APPLY=1" % day_value)
        return 0

    try:
        report = apply_replace(day, new_entries)
    except Exception:
        logging.getLogger(__name__).exception("apply failed")
        return 1
    print(f"\nreplaced: upserted={report.upserted}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
