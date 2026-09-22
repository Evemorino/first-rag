"""CRAP 指标（Change Risk Anti-Patterns）计算器。

公式：

    CRAP(m) = comp(m)^2 * (1 - cov(m))^3 + comp(m)

comp(m) 取 radon 的圈复杂度，cov(m) 取 coverage 报告的行覆盖率（0~1）。
一个数同时编码两件事：越复杂、越没被测到，改动它的风险就越高。
业界常用阈值 30 —— 超过即视为 "crappy"，值得先补测试或先拆函数。

用法（先跑 `make cov` 生成 coverage.xml）：

    python scripts/crap.py                 # 默认阈值 30，有 crappy 就退出码 1
    python scripts/crap.py --observe       # 只出报告，永远退出码 0（摸基线用）
    python scripts/crap.py --threshold 15  # 收紧阈值
    python scripts/crap.py --top 10 --json crap.json

为什么需要它：单看覆盖率会漏掉"复杂却没覆盖"的函数，单看复杂度会漏掉
"简单但没人测"的函数。CRAP 把两者相乘，排序后改哪个函数最危险一目了然。
"""

from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

DEFAULT_THRESHOLD = 30.0


@dataclass
class FuncMetric:
    """一个函数的复杂度 / 覆盖率 / CRAP。"""

    file: str
    name: str
    complexity: int
    lineno: int
    endline: int
    covered: int
    total: int
    crap: float = 0.0

    @property
    def coverage(self) -> float:
        return self.covered / self.total if self.total else 1.0

    @property
    def qualified(self) -> str:
        return f"{self.file}:{self.name}"


def compute_crap(complexity: int, coverage: float) -> float:
    """CRAP 公式本身。coverage 为 0~1。"""
    return complexity**2 * (1.0 - coverage) ** 3 + complexity


def iter_functions(source_root: Path) -> list[FuncMetric]:
    """遍历 source_root 下所有函数/方法，取圈复杂度（radon）。

    类的复杂度不代表其方法，所以只收函数与方法，跳过 class 块本身；
    闭包与父函数行范围重叠，会重复计分，一并跳过。
    """
    from radon.complexity import cc_visit

    metrics: list[FuncMetric] = []
    for path in sorted(source_root.rglob("*.py")):
        rel = path.as_posix()
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        blocks = cc_visit(source)
        for block in _flatten(blocks, prefix="", file=rel):
            metrics.append(block)
    return metrics


def _flatten(blocks, prefix: str, file: str):
    """把 radon 的块树摊平成函数/方法列表。"""
    for block in blocks:
        methods = getattr(block, "methods", None)
        if methods is not None:  # 类：展开它的方法
            yield from _flatten(methods, prefix=f"{block.name}.", file=file)
            continue
        yield FuncMetric(
            file=file,
            name=f"{prefix}{block.name}" if prefix else block.name,
            complexity=block.complexity,
            lineno=block.lineno,
            endline=block.endline,
            covered=0,
            total=0,
        )


def load_coverage(xml_path: Path) -> dict[str, dict[int, int]]:
    """解析 coverage.xml → {文件名: {行号: 命中次数}}。"""
    if not xml_path.is_file():
        raise SystemExit(
            f"找不到 {xml_path} —— 先跑 `make cov` 生成覆盖率报告。"
        )
    root = ET.parse(xml_path).getroot()
    sources = [node.text or "" for node in root.iter("source")]
    result: dict[str, dict[int, int]] = {}
    for cls in root.iter("class"):
        filename = cls.get("filename", "")
        lines = {
            int(line.get("number")): int(line.get("hits", 0))
            for line in cls.iter("line")
        }
        for key in candidate_keys(sources, filename):
            result[key] = lines
    return result


def candidate_keys(sources: list[str], filename: str) -> set[str]:
    """coverage.xml 里的 filename 可能是裸文件名、相对路径或绝对路径。

    靠 <source> 把裸文件名还原成"相对仓库根"的路径；所有候选键都登记一遍，
    这样无论 coverage 怎么配置（source/relative_files）都能对得上。
    """
    keys = {filename.replace("\\", "/").lstrip("./")}
    cwd = Path.cwd().resolve()
    for source in sources:
        if not source:
            continue
        try:
            rel = (Path(source) / filename).resolve().relative_to(cwd)
        except ValueError:
            continue
        keys.add(rel.as_posix())
    return keys


def apply_coverage(metrics: list[FuncMetric],
                   coverage: dict[str, dict[int, int]]) -> None:
    """按行范围统计每个函数的覆盖行数，并算出 CRAP。"""
    for metric in metrics:
        lines = coverage.get(metric.file)
        if lines is None:
            # 该文件完全没被执行过：所有可执行行都算未覆盖。
            metric.total = max(1, metric.endline - metric.lineno + 1)
            metric.covered = 0
        else:
            hits = [
                count
                for number, count in lines.items()
                if metric.lineno <= number <= metric.endline
            ]
            metric.total = len(hits)
            metric.covered = sum(1 for count in hits if count > 0)
        metric.crap = compute_crap(metric.complexity, metric.coverage)


def render(metrics: list[FuncMetric], threshold: float, top: int) -> str:
    """给出人看的表格：按 CRAP 从高到低。"""
    ranked = sorted(metrics, key=lambda m: (-m.crap, m.qualified))
    shown = ranked if top <= 0 else ranked[:top]

    width = max((len(m.qualified) for m in shown), default=10)
    width = min(width, 58)
    out = [
        f"{'函数':<{width}}  {'复杂度':>6}  {'覆盖':>7}  {'CRAP':>8}",
        f"{'-' * width}  {'-' * 6}  {'-' * 7}  {'-' * 8}",
    ]
    for m in shown:
        flag = " !" if m.crap >= threshold else ""
        out.append(
            f"{m.qualified[:width]:<{width}}  {m.complexity:>6}  "
            f"{m.coverage * 100:>6.0f}%  {m.crap:>8.1f}{flag}"
        )
    if top > 0 and len(ranked) > top:
        out.append(f"... 另有 {len(ranked) - top} 个函数（--top 0 看全部）")
    return "\n".join(out)


def summarize(metrics: list[FuncMetric], threshold: float) -> dict:
    crappy = [m for m in metrics if m.crap >= threshold]
    total_stmts = sum(m.total for m in metrics)
    covered_stmts = sum(m.covered for m in metrics)
    return {
        "functions": len(metrics),
        "crappy": len(crappy),
        "threshold": threshold,
        "max_crap": max((m.crap for m in metrics), default=0.0),
        "avg_crap": (
            sum(m.crap for m in metrics) / len(metrics) if metrics else 0.0
        ),
        # 口径：只算函数体内的语句，不含模块级代码。与 pytest 报的
        # "全量行覆盖"不是一个东西，别混着比（键名保留，JSON 输出要兼容）。
        "line_coverage": (covered_stmts / total_stmts if total_stmts else 0.0),
        "worst": [
            {
                "function": m.qualified,
                "complexity": m.complexity,
                "coverage": round(m.coverage, 4),
                "crap": round(m.crap, 2),
            }
            for m in sorted(metrics, key=lambda m: -m.crap)[:5]
        ],
    }


def coverage_age_note(xml_path: Path, source_root: Path) -> str | None:
    """提醒 coverage.xml 比源码或测试旧。

    过期数据算出来的 CRAP 是个安静的假信号：数字看着正常，其实对应的是
    上一版代码的覆盖率。`make crap` 会先跑 cov，所以走 Makefile 不会遇到；
    直接跑本脚本时才需要这条提醒。只警告不拦人 —— 阻断会很烦，
    人看见提示就够了。
    """
    if not xml_path.exists():
        return None

    newest_mtime = 0.0
    newest: Path | None = None
    for root in (source_root, source_root.parent / "tests"):
        if not root.is_dir():
            continue
        for path in root.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            mtime = path.stat().st_mtime
            if mtime > newest_mtime:
                newest_mtime, newest = mtime, path

    if newest is None or newest_mtime <= xml_path.stat().st_mtime:
        return None

    def fmt(ts: float) -> str:
        return datetime.fromtimestamp(ts).strftime("%m-%d %H:%M")

    return (
        f"⚠ coverage.xml（{fmt(xml_path.stat().st_mtime)}）比 "
        f"{newest}（{fmt(newest_mtime)}）旧："
        "这份 CRAP 用的是过期覆盖率，先跑 make cov"
    )


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="计算 CRAP 指标（复杂度 × 未覆盖度）",
    )
    parser.add_argument("--source", default="src", help="源码根目录（默认 src）")
    parser.add_argument("--coverage-xml", default="coverage.xml",
                        help="coverage.xml 路径（默认 coverage.xml）")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD,
                        help="CRAP 阈值，超过即 crappy（默认 30）")
    parser.add_argument("--top", type=int, default=25,
                        help="表格里最多显示几行，0 表示全部（默认 25）")
    parser.add_argument("--max-crappy", type=int, default=0,
                        help="允许的 crappy 函数数量，超过即失败（默认 0）")
    parser.add_argument("--fail-under", type=float, default=None,
                        help="整体行覆盖率下限（百分比），低于即失败")
    parser.add_argument("--observe", action="store_true",
                        help="只报告不拦人，永远退出码 0（摸基线用）")
    parser.add_argument("--json", dest="json_path", default=None,
                        help="把明细写成 JSON 文件")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    source_root = Path(args.source)
    if not source_root.is_dir():
        print(f"源码目录不存在：{source_root}", file=sys.stderr)
        return 2

    metrics = iter_functions(source_root)
    apply_coverage(metrics, load_coverage(Path(args.coverage_xml)))
    summary = summarize(metrics, args.threshold)

    age_note = coverage_age_note(Path(args.coverage_xml), source_root)
    if age_note:
        print(age_note, file=sys.stderr)

    print(render(metrics, args.threshold, args.top))
    print()
    print(
        f"函数 {summary['functions']} 个 · 函数内语句覆盖 "
        f"{summary['line_coverage'] * 100:.1f}% · CRAP 均值 "
        f"{summary['avg_crap']:.1f} · 最高 {summary['max_crap']:.1f} · "
        f"crappy（≥{args.threshold:.0f}）{summary['crappy']} 个"
    )
    # 口径提示：分子分母只有函数体内的语句；pytest 报的"全量行覆盖"还包含
    # 模块级代码（import、常量、类体），后者几乎必被执行，所以数字会略高。
    # 两个都叫"覆盖"，但不是一个东西 —— 别拿这儿的数和 pytest 的对。
    print("（覆盖口径：仅函数体内语句，与 pytest 报的全量行覆盖不同）")

    if args.json_path:
        payload = {
            "summary": summary,
            "functions": [
                {
                    "function": m.qualified,
                    "complexity": m.complexity,
                    "lines": [m.lineno, m.endline],
                    "coverage": round(m.coverage, 4),
                    "crap": round(m.crap, 2),
                }
                for m in sorted(metrics, key=lambda m: -m.crap)
            ],
        }
        Path(args.json_path).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"明细已写入 {args.json_path}")

    if args.observe:
        return 0

    failed = False
    if summary["crappy"] > args.max_crappy:
        print(
            f"✗ crappy 函数 {summary['crappy']} 个，超过上限 "
            f"{args.max_crappy} 个",
            file=sys.stderr,
        )
        failed = True
    if args.fail_under is not None and \
            summary["line_coverage"] * 100 < args.fail_under:
        print(
            f"✗ 函数内语句覆盖 {summary['line_coverage'] * 100:.1f}% 低于下限 "
            f"{args.fail_under:.1f}%",
            file=sys.stderr,
        )
        failed = True
    if not failed:
        print("✓ CRAP 门禁通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
