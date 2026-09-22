"""孤儿模块检查：找出"一行都没被测试跑过"的 src/ 模块。

为什么 CRAP 抓不到它
--------------------
CRAP = 复杂度² × (1-覆盖)³ + 复杂度。一个只有简单函数（复杂度 1）的模块，
哪怕一行测试都没有，CRAP 也只有 1×(1-0)³+1 = **2**，离 30 的阈值远得很。
于是"新加了一个模块、一个测试都没写"这件事可以悄无声息地溜过门禁：
整体覆盖率会被稀释一点点，但没人报警，CRAP 也不吭声。

这个脚本就是补那个洞：直接看每个模块的**被执行行数**，一行都没跑过的
就是孤儿。它和 CRAP 看的是两件事 —— CRAP 问"复杂的代码测够了吗"，
这里问"这个模块有人碰过吗"。

用法::

    python scripts/orphan_check.py            # 检查（默认吃 coverage.xml）
    python scripts/orphan_check.py --top 10   # 摸底：覆盖率最低的 10 个，不判失败

退出码：0 没有孤儿；1 有孤儿；2 没有 coverage.xml（先跑 make cov）。
"""

from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

# 相对路径，故意的：门禁自检（scripts/gate_selftest.py）会在一个临时仓库里
# 造一份假的 src/ 和 coverage.xml 来确认"这个钩子到底还会不会红"。写死成
# 脚本所在目录的绝对路径，那个自检就只能去动真实仓库的 coverage.xml 了。
# 代价是必须从仓库根调用（make orphans 和 pre-commit 钩子都是这么跑的）。
SOURCE_ROOT = Path("src")
DEFAULT_XML = Path("coverage.xml")

# 豁免：`plugins/_template/` 是给新插件照着抄的骨架，两个函数都直接
# `raise NotImplementedError` —— 它从来就不是要被跑的逻辑，没有测试是对的。
EXEMPT_PREFIXES = ("plugins/_template/",)


def load_coverage(xml_path: Path = DEFAULT_XML) -> dict[str, tuple[int, int]]:
    """读 coverage.xml，返回 {模块相对 src/ 的路径: (命中行数, 可执行行数)}。"""
    root = ET.parse(xml_path).getroot()
    result: dict[str, tuple[int, int]] = {}
    for cls in root.iter("class"):
        lines = cls.find("lines")
        hit = total = 0
        for line in lines if lines is not None else []:
            total += 1
            if int(line.get("hits") or 0) > 0:
                hit += 1
        result[cls.get("filename")] = (hit, total)
    return result


def is_exempt(rel: str) -> bool:
    return rel.startswith(EXEMPT_PREFIXES)


def find_orphans(
    coverage: dict[str, tuple[int, int]],
    source_root: Path | None = None,
) -> list[tuple[str, str]]:
    """返回 [(模块路径, 为什么算孤儿)]，按路径排序。

    source_root 留空时用模块级 SOURCE_ROOT —— 注意不能在签名里直接写默认值，
    那样会在 import 时就绑定死，测试就没法把它指到临时目录上了。
    """
    source_root = source_root if source_root is not None else SOURCE_ROOT
    orphans: list[tuple[str, str]] = []
    for path in sorted(source_root.rglob("*.py")):
        rel = path.relative_to(source_root).as_posix()
        if is_exempt(rel):
            continue
        if rel not in coverage:
            orphans.append((f"src/{rel}", "压根没出现在 coverage.xml 里（连 import 都没有）"))
            continue
        hit, total = coverage[rel]
        # total == 0：空文件（比如只有一个 docstring 的 __init__.py），不算孤儿
        if total and hit == 0:
            orphans.append((f"src/{rel}", f"{total} 行可执行代码，一行都没被执行过"))
    return orphans


def render_bottom(
    coverage: dict[str, tuple[int, int]], top: int
) -> list[tuple[str, float, int, int]]:
    """覆盖率最低的若干模块，摸底用。"""
    rows = []
    for rel, (hit, total) in coverage.items():
        if total == 0 or is_exempt(rel):
            continue
        rows.append((f"src/{rel}", hit / total * 100, hit, total))
    rows.sort(key=lambda row: row[1])
    return rows[:top]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cov", type=Path, default=DEFAULT_XML, help="coverage.xml 路径")
    parser.add_argument("--top", type=int, metavar="N", help="只看覆盖率最低的 N 个，不判失败")
    args = parser.parse_args(argv)

    if not args.cov.exists():
        print(f"找不到 {args.cov} —— 先跑 make cov（孤儿检查吃的是它的输出）", file=sys.stderr)
        return 2

    coverage = load_coverage(args.cov)

    if args.top:
        print(f"覆盖率最低的 {args.top} 个模块（摸底，不判失败）：")
        print(f"{'模块':<36}{'覆盖':>7}{'命中/总数':>12}")
        for name, pct, hit, total in render_bottom(coverage, args.top):
            print(f"  {name:<34}{pct:>6.1f}%{hit:>7}/{total:<6}")
        return 0

    orphans = find_orphans(coverage)
    checked = sum(
        1
        for path in SOURCE_ROOT.rglob("*.py")
        if not is_exempt(path.relative_to(SOURCE_ROOT).as_posix())
    )
    if orphans:
        print(f"✗ {len(orphans)} 个模块没有任何测试跑过它：")
        for name, why in orphans:
            print(f"    {name} — {why}")
        print("\n这些模块可能一行测试都没有。CRAP 抓不到它们（简单函数的 CRAP 很低），")
        print("只有这里会喊 —— 要么补测试，要么确认它是模板并加进 EXEMPT_PREFIXES。")
        return 1

    print(f"✓ 没有孤儿模块（{checked} 个 src/ 模块都至少被执行过一行）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
