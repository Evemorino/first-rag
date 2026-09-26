"""跨批关联边的观测（只读诊断，T087）。

为什么需要
----------
ADR-12（按输出切批）把一条副作用记为已知：切批后 `source_refs` 只能引用**本批**
素材，同一事件分在相邻两批时蒸馏期就断链，只能等库内相似度补 —— 而"库内能不能
补回来"当时**没有观测数据**。2026-09-27 量了一次，读数是**能，但有条件**
（落进 `docs/adr/0012-batch-by-output.md` 的「观测记录」一节）。本脚本把那次量法
固定下来：以后再有多批日期，一条命令复现同一个读数。

它看**同一天内部**的配对，按两个变量分三组：

  - `same-run/same-batch`  —— 对照组：同一次运行的同一批，同样吃了 in-run 排除
  - `same-run/cross-batch` —— **本例要看的量**：ADR-12 的那条副作用
  - `cross-run`            —— 已知会建边的那组（补回机制的参照）

批次归属不来自存储（`distill` 不记批次），而是**重建**：读 `data/raw/<day>.json`，
用 `distill_batches.split_for_batches` 按 `distill.batch_max_chars` 重新装箱，
再把每条条目的 `source_refs` 映射到批次序号。快照里的文本就是当时脱敏后的那份，
所以重装箱的边界与当时一致。快照被覆盖过的日期（见 ADR-14）算不出批次，单列成
`same-run/unknown-batch` —— 不静默丢掉："算不出来"本身就是那天的读数。

只读
----
只做 Qdrant 的 `scroll`，不写任何地方：不碰 `data/raw/`、不落文件。写入边界门禁
扫的是 `src/` 树，本脚本不在其列 —— 所以它的只读承诺由单测钉住（假 client 只实现
`scroll`，调别的方法直接炸）。

用法::

    python scripts/batch_edge_probe.py                    # 全部日期：只给三组统计
    python scripts/batch_edge_probe.py --day 2026-09-25   # 另出该日跨批对明细
    python scripts/batch_edge_probe.py --threshold 0.80   # 换阈值试算（不改代码）

退出码：0 出报告；2 连不上 Qdrant（先 `make up`）。
"""

from __future__ import annotations

import argparse
import collections
import inspect
import itertools
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from qdrant_client import QdrantClient

# 直接运行脚本时 sys.path[0] 是 scripts/ 而非仓库根，补上项目根
# （与 embed_test.py / rerun_overlap_report.py 同一写法）。
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config, similarity  # noqa: E402  (先补 sys.path 才能导入)
from src.distill_batches import split_for_batches  # noqa: E402

# cosine / run_key / collect_points 从同日重跑报告复用：两个脚本量的是同一件事的
# 两半（它给 EX 组，本脚本给按批分组），公式抄第二份就会漂 —— 与"阈值只定义一次"
# 是同一条规矩。
from rerun_overlap_report import collect_points, cosine, run_key  # noqa: E402

# 建边阈值不读配置（它本来就不是配置项）：直接取 `build_related_edges` 的形参默认值，
# 这样 src 改了默认值本脚本自动跟上，不会留第二份硬编码。
EDGE_THRESHOLD: float = float(
    inspect.signature(similarity.build_related_edges).parameters["threshold"].default
)

CROSS_BATCH = "same-run/cross-batch"
SAME_BATCH = "same-run/same-batch"
CROSS_RUN = "cross-run"
UNKNOWN_BATCH = "same-run/unknown-batch"


class _Material:
    """`split_for_batches` 只读 `.text`；带上 `ref` 才能在装箱后反查批次序号。"""

    __slots__ = ("ref", "text")

    def __init__(self, ref: str, text: str) -> None:
        self.ref = ref
        self.text = text


def load_batch_index(day: str, budget: int, raw_dir: Path) -> dict[str, Any] | None:
    """重建 `素材 ref -> 批次序号`；快照缺失或读不出来时返回 None。

    返回 None 不是错误：那天就是算不出批次（快照被窄 scope 重跑覆盖过），
    报告里会把它单列，而不是假装批次可算。
    """
    path = raw_dir / f"{day}.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    materials = payload.get("materials") or []
    if not materials:
        return None
    boxes = split_for_batches(
        [_Material(str(m["ref"]), str(m.get("text", ""))) for m in materials], budget
    )
    ref_batch: dict[str, int] = {}
    for index, box in enumerate(boxes):
        for material in box:
            ref_batch[material.ref] = index
    return {"n_batches": len(boxes), "n_materials": len(materials), "ref_batch": ref_batch}


def batches_of(payload: dict[str, Any], index: dict[str, Any] | None) -> frozenset[int]:
    """一条条目落在哪几批：把 `source_refs` 逐个映射过去，对不上的不算。

    全部对不上（快照被覆盖的日期）→ 空集，调用方按 unknown 处理。
    """
    if not index:
        return frozenset()
    return frozenset(
        index["ref_batch"][ref]
        for ref in payload.get("source_refs") or []
        if ref in index["ref_batch"]
    )


def pair_class(
    left: dict[str, Any], right: dict[str, Any], index: dict[str, Any] | None
) -> str:
    """给同一天的一对条目分类（三组 + unknown，见模块头部）。"""
    if run_key(left) != run_key(right):
        return CROSS_RUN
    left_batches, right_batches = batches_of(left, index), batches_of(right, index)
    if not left_batches or not right_batches:
        return UNKNOWN_BATCH
    return SAME_BATCH if left_batches == right_batches else CROSS_BATCH


def edge_set(points: Sequence[Any]) -> set[tuple[str, str]]:
    """把 payload 里的 `related` 展成双向 id 对（一律 str，与 similarity 同口径）。"""
    edges: set[tuple[str, str]] = set()
    for point in points:
        for neighbour in point.payload.get("related") or []:
            edges.add((str(point.id), str(neighbour)))
            edges.add((str(neighbour), str(point.id)))
    return edges


def analyze(
    points: Sequence[Any],
    indexes: dict[str, dict[str, Any] | None],
    *,
    edge_threshold: float = EDGE_THRESHOLD,
) -> list[dict[str, Any]]:
    """按天统计三组配对 + 跨批明细（纯函数：不碰网络、不写文件）。"""
    by_date: dict[str, list[Any]] = collections.defaultdict(list)
    for point in points:
        by_date[str(point.payload.get("date"))].append(point)
    edges = edge_set(points)

    rows: list[dict[str, Any]] = []
    for day in sorted(by_date):
        group = by_date[day]
        index = indexes.get(day)
        stats: dict[str, dict[str, float]] = collections.defaultdict(
            lambda: {"pairs": 0, "over": 0, "linked": 0, "max": 0.0}
        )
        detail: list[dict[str, Any]] = []
        for left, right in itertools.combinations(group, 2):
            score = cosine(left.vector, right.vector)
            kind = pair_class(left.payload, right.payload, index)
            bucket = stats[kind]
            bucket["pairs"] += 1
            bucket["max"] = max(bucket["max"], score)
            # 与 `build_related_edges` 的判据同口径：严格大于才建边。
            if score <= edge_threshold:
                continue
            bucket["over"] += 1
            linked = (str(left.id), str(right.id)) in edges
            bucket["linked"] += 1 if linked else 0
            if kind == CROSS_BATCH:
                detail.append(
                    {
                        "score": score,
                        "linked": linked,
                        "run": run_key(left.payload),
                        "source": str(left.payload.get("source")),
                        "left": str(left.id),
                        "right": str(right.id),
                        "left_batches": sorted(batches_of(left.payload, index)),
                        "right_batches": sorted(batches_of(right.payload, index)),
                        "left_refs": _basenames(left.payload),
                        "right_refs": _basenames(right.payload),
                    }
                )
        unmatched = sum(1 for p in group if not batches_of(p.payload, index))
        rows.append(
            {
                "day": day,
                "points": len(group),
                "batches": index["n_batches"] if index else None,
                "materials": index["n_materials"] if index else None,
                "unmatched": unmatched,
                "stats": dict(stats),
                "cross_batch": sorted(detail, key=lambda d: d["score"], reverse=True),
            }
        )
    return rows


def _basenames(payload: dict[str, Any]) -> list[str]:
    """`source_refs` 只留最后一段：报告是给人看的，全路径太长。"""
    return [str(ref).rsplit("/", 1)[-1] for ref in payload.get("source_refs") or []]


def render(rows: Sequence[dict[str, Any]], *, day: str | None, threshold: float) -> list[str]:
    """报告文本。`day` 命中时附上那天的跨批明细。"""
    lines = [f"建边阈值 {threshold:.3f}（取自 build_related_edges 的默认值）"]
    for row in rows:
        batches = row["batches"] if row["batches"] is not None else "?"
        materials = row["materials"] if row["materials"] is not None else "?"
        lines.append(
            f"\n== {row['day']}: points={row['points']} batches={batches} "
            f"materials={materials} refs_unmatched={row['unmatched']}"
        )
        for kind in (SAME_BATCH, CROSS_BATCH, CROSS_RUN, UNKNOWN_BATCH):
            bucket = row["stats"].get(kind)
            if bucket is None:
                continue
            lines.append(
                f"   {kind:24s} pairs={bucket['pairs']:5d} > {threshold:.3f}: "
                f"{bucket['over']:3d} linked={bucket['linked']:3d} max={bucket['max']:.3f}"
            )
        if day is None or row["day"] != day:
            continue
        for item in row["cross_batch"]:
            lines.append(
                f"   * cos={item['score']:.3f} linked={item['linked']} run={item['run']} "
                f"source={item['source']} batches={item['left_batches']}->{item['right_batches']}"
            )
            lines.append(f"     A {item['left'][:12]} refs={item['left_refs']}")
            lines.append(f"     B {item['right'][:12]} refs={item['right_refs']}")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="跨批关联边观测（只读）")
    parser.add_argument("--day", help="另出这一天的跨批对明细")
    parser.add_argument("--threshold", type=float, help="试算用的建边阈值（不改代码）")
    parser.add_argument("--collection", default=config.COLLECTION)
    parser.add_argument("--raw-dir", default=str(config.DATA_DIR / "raw"))
    args = parser.parse_args(argv)

    schema = config.load_schema()
    budget = int(schema["distill"]["batch_max_chars"])
    threshold = args.threshold if args.threshold is not None else EDGE_THRESHOLD

    try:
        client = QdrantClient(url=config.QDRANT_URL)
        points = collect_points(client, args.collection)
    except Exception as exc:  # noqa: BLE001  —— 连不上就给路，不给栈
        print(f"连不上 Qdrant（{config.QDRANT_URL}）：{exc}", file=sys.stderr)
        print("先 `make up` 把 Qdrant 起起来。", file=sys.stderr)
        return 2

    raw_dir = Path(args.raw_dir)
    indexes = {
        str(p.payload.get("date")): load_batch_index(
            str(p.payload.get("date")), budget, raw_dir
        )
        for p in points
    }
    if args.day is not None and args.day not in indexes:
        print(f"库里没有 {args.day} 的点。", file=sys.stderr)
        return 2

    rows = analyze(points, indexes, edge_threshold=threshold)
    print("\n".join(render(rows, day=args.day, threshold=threshold)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
