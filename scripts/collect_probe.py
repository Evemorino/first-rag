"""只读采集探针：量「某个源在某天有多少素材」，**不写任何文件**。

为什么需要它
------------
AC-015 的验证要回答两件事：「这个源今天有素材吗」与「入库路径通不通」。第二件必须
真跑 `make sync`（它本来就要往真库 upsert），但第一件**不需要写盘** —— 而在
v0.7.11 之前，能问第一件的唯一办法就是跑一次 sync，那会写 `data/raw/<day>.json`，
也就是 `make redistill` 的**重放基线**。这条基线在本仓库已经被弄丢过（v0.7.4，
其中几天不可恢复），所以「为了验证而写盘」这个形状必须断掉（tasks.md T083）。

于是 `collect.gather` 多了 `persist=False`（`src/collect.py`）：照常采集、照常返回
`DayRaw`，只是不落盘。本脚本是那个开关的唯一使用者。

自校
----
这条路径**没有门禁盯着**（`scripts/` 不在写入边界的扫描面内，写入边界扫的是
`src/`），所以本脚本自己证：跑前跑后比对 `data/raw/<day>.json` 的存在性与
大小/mtime，变了就报错退出 1。也就是说「探针不写盘」不是靠注释承诺的。

用法::

    python scripts/collect_probe.py --day 2026-09-25              # 全源，逐源计数
    python scripts/collect_probe.py --day 2026-09-25 --source qoder   # 只看一个源

`--source X` 的"只看"是**合计口径**上的只看：X 之外的插件用 scope 关掉（省时间），
而 scope 关不掉的两类素材 —— git 提交（`projects` 节）与 `notes/` 手动快记（`gather`
里没有对应开关）—— 仍然会被采到，于是单独列在一条分隔线下并标明"未计入合计"。
不这么做的话，`--source qoder` 报出的"素材合计"里会混着 git 提交，而它读起来像是
qoder 有素材。

只读的含义：不碰 `data/raw/`、不需要 Qdrant、不调用任何 LLM。采集插件对产品源目录
一律只读（宪法 V；三个 SQLite 源一律 `mode=ro`），git 源只做 `git log`。

退出码：0 采集完成（**素材 0 条也是成功**）；1 探针发现自己动了盘（bug）；2 用法错误。
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter
from datetime import date
from pathlib import Path

# 直接运行脚本时 sys.path[0] 是 scripts/ 而非仓库根，补上项目根
# （与 embed_test.py / rerun_overlap_report.py / secret_scan.py 同一写法）。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import collect, config  # noqa: E402  (先补 sys.path 才能导入)
from src.plugins import iter_plugins  # noqa: E402


def snapshot_fingerprint(day: date) -> tuple[int, int] | None:
    """快照的存在性指纹：不在就 None，在就给 (字节数, mtime_ns)。

    只比"存在性 + 大小 + mtime"而不用哈希：探针的承诺是"一次都不写"，而任何写入
    都会碰 mtime —— 哈希能多抓的只有"内容变了但大小与 mtime 都没变"，那需要刻意
    伪造时间戳，不在本工具的威胁模型里（它防的是自己手滑，不是防对手）。
    """
    path = collect.snapshot_path(day)
    if not path.is_file():
        return None
    stat = path.stat()
    return (stat.st_size, stat.st_mtime_ns)


def scope_for_source(source: str | None) -> dict | None:
    """只让 `source` 一个**插件**参与采集；`None` 表示全源（返回 None）。

    `gather` 的 scope 语义是**缺键 = 启用**，所以"只看一个源"必须把其余源显式写成
    false —— 写成 `{"tools": {source: True}}` 会静默退化成全源，那正是本探针最不该
    犯的错（它会让人以为"这个源有素材"，其实数的是所有源）。

    这层 scope **关不掉**另外两类素材：`projects`（git 提交）与 `notes/`（手动快记，
    `gather` 里根本没有对应开关）。所以"只看一个源"由 `render` 的合计口径兑现 ——
    它只把 `source` 算进合计，其余来源另列并标明未计入。scope 在这里省的是时间
    （不去扫其他 10 个插件），不是口径。

    源名不认识时抛 `KeyError`，由调用方转成退出码 2 并列出已知源。
    """
    if source is None:
        return None
    names = [plugin.name for plugin in iter_plugins()]
    if source not in names:
        raise KeyError(source)
    return {"tools": {name: name == source for name in names}}


def _by_count(counts: Counter) -> list[tuple[str, int]]:
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


def render(day: date, day_raw: collect.DayRaw, before, after,
           source: str | None = None) -> list[str]:
    """逐源计数。给了 `source` 就只把它算进合计，其余来源另列。

    `source` 前的合计是**这个源报的那个数**；未被选中的来源不是被丢掉，而是列在
    一条分隔线下面 —— 它们当天确实被采到了，只是不回答"这个源有素材吗"。
    """
    counts = Counter(material.source for material in day_raw.materials)
    lines = [f"{day.isoformat()} 只读采集探针（persist=False，不写任何文件）"]
    width = max((len(name) for name in counts), default=0)

    def row(name: str, count: int) -> str:
        return f"    {name:<{width}}  {count}"

    if source is None:
        lines.append(f"  素材合计 {len(day_raw.materials)} 条")
        lines.extend(row(name, count) for name, count in _by_count(counts))
        if not counts:
            lines.append("    （今天没有素材 —— 这是采集的正常结论，不是故障）")
    else:
        lines.append(f"  素材合计 {counts.get(source, 0)} 条"
                     f"（--source {source}；全源 {len(day_raw.materials)} 条）")
        if counts.get(source):
            lines.append(row(source, counts[source]))
        else:
            lines.append(f"    （{source} 今天没有素材 —— 这是采集的正常结论，不是故障）")
        others = Counter({n: c for n, c in counts.items() if n != source})
        if others:
            lines.append(f"    ── 下面几行不属于 {source}，是同一天顺带采到的，"
                         "未计入合计 ──")
            lines.extend(row(name, count) for name, count in _by_count(others))
    path = collect.snapshot_path(day)
    if before == after:
        state = "不存在，也没被创建" if after is None else "未被改动"
        lines.append(f"  快照 {path.name}：{state} ✓")
    else:
        lines.append(f"  快照 {path.name}：**被改动了** ✗（{before} → {after}）")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="只读采集探针（不写任何文件）")
    parser.add_argument("--day", required=True, help="YYYY-MM-DD")
    parser.add_argument("--source", help="只看这一个源（默认全部）")
    args = parser.parse_args(argv)

    config.load_env()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    try:
        day = date.fromisoformat(args.day)
    except ValueError:
        print(f"--day 要的是 YYYY-MM-DD，收到 {args.day!r}", file=sys.stderr)
        return 2
    try:
        scope = scope_for_source(args.source)
    except KeyError:
        known = ", ".join(plugin.name for plugin in iter_plugins())
        print(f"没有名为 {args.source!r} 的源。已知：{known}", file=sys.stderr)
        return 2

    before = snapshot_fingerprint(day)
    day_raw = collect.gather(day, scope, persist=False)
    after = snapshot_fingerprint(day)

    for line in render(day, day_raw, before, after, args.source):
        print(line)
    if before != after:
        print(
            "\n探针写了盘 —— 这是 bug：`gather(persist=False)` 必须一次都不写。"
            "请把它报给维护者，并检查 src/collect.py 的 persist 分支。",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
