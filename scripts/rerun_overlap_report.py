"""同日重跑的重叠度报告：把「重跑多出来的条目算不算近似重复」从推定变成实测。

为什么需要它
------------
PRD §9 的待澄清项（FR-007 同日重跑累加）卡在一句**推定**上：第二次、第三次重跑
会新增与前面**近似重复**的条目。这句推定的后果很重 —— 它同时压着 AC-003
（「同一日期重复 `make sync`：points 数不变」，`PRD.md:257`）；而宪法 IV 又把
LLM 蒸馏显式豁免在幂等承诺之外（`.specify/memory/constitution.md:35-37`）。
所以「多出来的到底是什么」决定了该改哪一边。

但这句话从来没被量过。`filter_novel` 没拦住有两种可能，修法完全不同：

  - 它们**不够像**（阈值没问题，重跑确实产出了不同内容）→ 要改的是 AC 口径；
  - 阈值**定得太高**（内容近似、判据失效）→ 要改的是判据或阈值。

本脚本量这一件事，而且判据与代码同源：阈值读 `distill.novelty_threshold`
（不抄一份硬编码），比较的是「每条条目与**本簇之外**最近邻的 cosine」——
这正是 `similarity.filter_novel` 要看的那个量（它把本批 ID 排除在比较之外）。

只读
----
只做 Qdrant 的 `scroll` / `query_points`，不写任何地方：不碰 `data/raw/`、
不需要 `ALLOW_SHRINK`、不落任何文件。写入边界门禁扫的是 `src/` 树，本脚本不在其列。

用法::

    python scripts/rerun_overlap_report.py                     # 按日的最近邻总览
    python scripts/rerun_overlap_report.py --day 2026-09-24    # 另出该日的分次运行明细
    python scripts/rerun_overlap_report.py --threshold 0.80    # 换阈值试算（不改配置）

退出码：0 出报告；2 连不上 Qdrant（先 `make up`）。

数怎么看
--------
表里每天给两对数字，缺一不可：

  **IN**（同一次运行内部）—— 对照组：这些条目必须**共存**，它们是同一次蒸馏产出的
  不同条目。理论上界就是判据不该碰的地方。
  **EX**（跨运行）—— `filter_novel` 要看的量：本批之外的最近邻。

读法是**先看 IN 再看 EX**：

  - EX 整片在阈值以下 → 判据没拦住是对的（同日的多次运行产出的确实是不同内容）；
  - EX 集中在阈值以上 → 才是近似重复；
  - **IN max 高过 EX max** → 阈值不是问题所在。这时降阈值**先误伤真条目**，
    而且对真正冗余的那一对（同一次运行内部的）依然无能为力 —— 因为
    `filter_novel` 把本批 ID 排除在外，同批之间根本不比。2026-09-26 实测就是
    这个样子（IN max 0.906 vs EX max 0.813），详见
    `specs/001-learning-memory-rag/fr007-rerun-options.md`。

余量也要看：贴着阈值的最大值说明结论对阈值敏感。
"""

from __future__ import annotations

import argparse
import collections
import math
import sys
from pathlib import Path
from typing import Any, Iterable, Sequence

from qdrant_client import QdrantClient

# 直接运行脚本时 sys.path[0] 是 scripts/ 而非仓库根，补上项目根
# （与 embed_test.py / secret_scan.py / migrate_trae_source.py 同一写法）。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config  # noqa: E402  (先补 sys.path 才能导入)


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """两个向量的 cosine 相似度；任一为零向量时返回 0.0。

    与 Qdrant 的 `Distance.COSINE` 分数是同一个量（归一化点积，量纲 [-1, 1]）。
    这一点不靠"定义如此"来相信：`cross_check` 会拿本机的 Qdrant 实测比对，
    把偏差打出来。
    """
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / math.sqrt(na * nb)


def internal_nn_scores(
    vectors: Sequence[Sequence[float]], groups: dict[str, list[int]]
) -> list[float]:
    """每条向量到**本簇之内**其它向量的最大 cosine —— `external` 的对照组。

    这一组是"必须共存"的条目：同一次蒸馏写出的不同条目，任何判据都不该把它们
    判成重复。所以它是阈值的**下界约束** —— 只有当 IN 明显低于 EX 时，
    "把阈值调低"才是个安全的旋钮。
    """
    member: dict[int, str] = {}
    for key, indices in groups.items():
        for index in indices:
            member[index] = key
    out: list[float] = []
    for i, a in enumerate(vectors):
        best = None
        for j, b in enumerate(vectors):
            if i == j or member.get(i) != member.get(j):
                continue
            score = cosine(a, b)
            if best is None or score > best:
                best = score
        if best is not None:
            out.append(best)
    return out


def percentile(xs: Sequence[float], q: float) -> float:
    """排序后取 `int(q * (n-1))` 处（向下取整，不用 round——避免银行家舍入的意外）。

    空列表返回 nan。分位数的取值口径只有这一处，报告里的数字都由它出。
    """
    if not xs:
        return float("nan")
    ordered = sorted(xs)
    return ordered[min(len(ordered) - 1, int(q * (len(ordered) - 1)))]


def run_key(payload: dict[str, Any]) -> str:
    """一次运行的指纹：`created_at` 截到秒。

    同一次 `distill()` 调用写出的条目共享同一个 `created_at` 取值，所以秒级
    前缀能把同一批和不同批分开。缺字段时归到 "<unknown>" 一簇（不静默丢掉）。
    """
    created = payload.get("created_at")
    return str(created)[:19] if created is not None else "<unknown>"


def group_runs(payloads: Sequence[dict[str, Any]]) -> dict[str, list[int]]:
    """按 `run_key` 把下标分簇（保序：簇内下标升序，簇间按首次出现）。"""
    groups: dict[str, list[int]] = {}
    for index, payload in enumerate(payloads):
        groups.setdefault(run_key(payload), []).append(index)
    return groups


def external_nn_scores(
    vectors: Sequence[Sequence[float]], groups: dict[str, list[int]]
) -> list[float]:
    """每条向量到**本簇之外**向量的最大 cosine —— `filter_novel` 要看的就是这个。

    `filter_novel` 把"本批"的 ID 全部排除在比较之外（同日重跑要能到达 upsert），
    所以"这次运行会不会被判重复"取决于它与**其它运行**的最近邻分数。
    """
    member: dict[int, str] = {}
    for key, indices in groups.items():
        for index in indices:
            member[index] = key
    out: list[float] = []
    for i, a in enumerate(vectors):
        best = None
        for j, b in enumerate(vectors):
            if member.get(i) == member.get(j):
                continue
            score = cosine(a, b)
            if best is None or score > best:
                best = score
        if best is not None:
            out.append(best)
    return out


def summarize(scores: Sequence[float], threshold: float) -> dict[str, Any]:
    """一组分数的摘要（渲染与测试共用这一处口径）。"""
    return {
        "n": len(scores),
        "min": min(scores) if scores else float("nan"),
        "p25": percentile(scores, 0.25),
        "p50": percentile(scores, 0.50),
        "p75": percentile(scores, 0.75),
        "max": max(scores) if scores else float("nan"),
        "above": sum(1 for s in scores if s > threshold),
    }


def max_abs_delta(pairs: Iterable[tuple[float, float]]) -> float:
    """两组分数的最大绝对偏差（`cross_check` 用它报"本地与 Qdrant 差多少"）。"""
    deltas = [abs(a - b) for a, b in pairs]
    return max(deltas) if deltas else 0.0


def collect_points(client: QdrantClient, collection: str) -> list[Any]:
    """读全库（含向量）。只读的 `scroll`，分批取完。"""
    points: list[Any] = []
    offset = None
    while True:
        batch, offset = client.scroll(
            collection, limit=1000, offset=offset, with_payload=True, with_vectors=True
        )
        points.extend(batch)
        if offset is None:
            return points


def cross_check(
    client: QdrantClient, collection: str, points: Sequence[Any], sample: int = 3
) -> float:
    """拿 Qdrant 自己的分数校一次：本模块的 cosine 与它差多少。

    比的是同一对向量 —— 用第 i 条的向量去查，取命中第 j 条时的服务端分数，
    再与本地 `cosine` 对照。偏差为 0 量级说明"两者的尺子是同一条"。
    """
    if len(points) < 2:
        return 0.0
    pairs: list[tuple[float, float]] = []
    for i in range(min(sample, len(points))):
        target = points[i]
        response = client.query_points(
            collection_name=collection,
            query=target.vector,
            limit=2,
            with_payload=False,
        )
        for hit in response.points:
            if str(hit.id) == str(target.id):
                continue
            other = next((p for p in points if str(p.id) == str(hit.id)), None)
            if other is not None:
                pairs.append((cosine(target.vector, other.vector), float(hit.score)))
    return max_abs_delta(pairs)


def _fmt(value: float) -> str:
    return "  n/a" if value != value else f"{value:>6.3f}"


def _summary_line(summary: dict[str, Any], threshold: float) -> str:
    """一组分数的单行明细（`render_day` 的 IN 与 EX 两行共用这一处格式）。"""
    return (
        f"  n={summary['n']} min={_fmt(summary['min']).strip()}"
        f" p25={_fmt(summary['p25']).strip()} p50={_fmt(summary['p50']).strip()}"
        f" p75={_fmt(summary['p75']).strip()} max={_fmt(summary['max']).strip()}"
        f"  >{threshold}: {summary['above']}"
    )


def render_overview(rows: list[dict[str, Any]], threshold: float) -> list[str]:
    lines = [
        f"同日重跑重叠度报告（阈值 novelty_threshold = {threshold}）",
        "",
        # 分数列宽 6 与 `_fmt` 一致（`f"{x:>6.3f}"`），列间留一格免得两个
        # 6 字表头粘成 "IN p50IN max"（表头与数据必须同宽同隔）
        f"{'date':<12}{'pts':>4}{'runs':>5}  |{'IN p50':>6} {'IN max':>6}  |"
        f"{'EX p50':>6} {'EX max':>6} {'EX>thr':>6}",
    ]
    for row in rows:
        internal = row["internal"]
        external = row["external"]
        lines.append(
            f"{row['date']:<12}{row['points']:>4}{row['runs']:>5}  |"
            f"{_fmt(internal['p50'])} {_fmt(internal['max'])}  |"
            f"{_fmt(external['p50'])} {_fmt(external['max'])} {external['above']:>6}"
        )
    lines.append("")
    lines.append(
        "（IN = 同一次运行内部最近邻：必须共存的对照组；"
        "EX = 跨运行最近邻：filter_novel 要看的量；"
        "单次运行的日子没有 EX，记 n/a）"
    )
    lines.append("先看 IN 再看 EX：IN max 不低于 EX max 时，降阈值只会先误伤真条目。")
    return lines


def render_day(
    day: str,
    groups: dict[str, list[int]],
    internal: Sequence[float],
    external: Sequence[float],
    threshold: float,
) -> list[str]:
    lines = [f"=== {day}：{len(groups)} 次运行 ==="]
    for key, indices in groups.items():
        lines.append(f"  run {key}  n={len(indices)}")
    lines.append(f"=== {day} 同一次运行内部最近邻（对照组：必须共存）===")
    lines.append(_summary_line(summarize(internal, threshold), threshold))
    lines.append(f"=== {day} 跨运行最近邻（filter_novel 要看的量）===")
    lines.append(_summary_line(summarize(external, threshold), threshold))
    return lines


def analyze(
    points: Sequence[Any], threshold: float, day: str | None = None
) -> tuple[list[dict[str, Any]], str | None, list[str]]:
    """把点集算成报告（纯函数：不碰网络、不写文件，测试直接喂假点）。

    返回 (总览行, 明细的日子, 明细行)。`day=None` 时挑点数最多的那天出明细 ——
    那是"同日重跑"最可能留下痕迹的一天。
    """
    by_date: dict[str, list[Any]] = {}
    for point in points:
        by_date.setdefault(str(point.payload.get("date")), []).append(point)

    rows: list[dict[str, Any]] = []
    for date_key, group in by_date.items():
        vectors = [p.vector for p in group]
        groups = group_runs([p.payload for p in group])
        rows.append(
            {
                "date": date_key,
                "runs": len(groups),
                "points": len(group),
                "internal": summarize(internal_nn_scores(vectors, groups), threshold),
                "external": summarize(external_nn_scores(vectors, groups), threshold),
            }
        )

    if day is None and rows:
        day = max(rows, key=lambda r: r["points"])["date"]
    if day is None or day not in by_date:
        return rows, None, []

    group = by_date[day]
    groups = group_runs([p.payload for p in group])
    vectors = [p.vector for p in group]
    detail = render_day(
        day,
        groups,
        internal_nn_scores(vectors, groups),
        external_nn_scores(vectors, groups),
        threshold,
    )
    return rows, day, detail


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="同日重跑的重叠度报告（只读）")
    parser.add_argument("--day", help="另出这一天的分次运行明细（默认取点数最多的那天）")
    parser.add_argument("--threshold", type=float, help="试算用的阈值（不改配置）")
    parser.add_argument("--collection", default=config.COLLECTION)
    args = parser.parse_args(argv)

    schema = config.load_schema()
    threshold = (
        args.threshold
        if args.threshold is not None
        else float(schema["distill"]["novelty_threshold"])
    )

    # trust_env=False：宿主上配了代理，不隔离会把 localhost 也走代理（仓库既有做法）。
    client = QdrantClient(url=config.QDRANT_URL, timeout=30, trust_env=False)
    try:
        points = collect_points(client, args.collection)
    except Exception as exc:  # noqa: BLE001 — 连不上就是"还没起"，给出路而不是栈
        print(f"连不上 Qdrant（{config.QDRANT_URL}）：{exc}", file=sys.stderr)
        print("先 `make up` 起容器，再重跑本脚本。", file=sys.stderr)
        return 2

    if not points:
        print(f"集合 {args.collection} 里没有点 —— 先 `make sync` 再来看。")
        return 0

    rows, day, detail = analyze(points, threshold, args.day)
    for line in render_overview(rows, threshold):
        print(line)
    if detail:
        print()
        for line in detail:
            print(line)
    try:
        delta = cross_check(client, args.collection, points)
        print(f"\nQdrant 分数自校：抽样比对本模块 cosine 与服务端 COSINE，最大偏差 {delta:.2e}")
    except Exception as exc:  # noqa: BLE001 — 自校失败不该毁掉报告
        print(f"\nQdrant 分数自校失败（不影响上面的数字）：{exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
