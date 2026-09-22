#!/usr/bin/env python3
"""变异自检（canary）：确认「改坏源码 → 测试会红」这条链路真的通。

为什么需要它
------------
变异分数是个代理指标 —— 它测的是「测试看起来有多严」，而不是「测试真的严」。
如果工具因为配置或包名问题压根没把变异体套上去，它照样会给你一个看起来
正常的分数。本项目踩过一次：28.8% 是假分数，修掉转发 bug 之后真实是 75.8%。
这类失效不报错、不崩溃，只安静地给个数字，所以只能靠一个已知答案来校验。

自检做什么
----------
1. 先确认基线是绿的（本来就红的测试，变异后红了也不算数）
2. 往源码塞一个已知必死的改动
3. 期望测试变红 —— 抓不住就说明测试是摆设
4. 无论结果如何都还原源码（备份 + finally，不走 git，避免吃掉未提交改动）

用法
----
    uv run python scripts/mutation_selfcheck.py            # 跑全部内置 canary
    uv run python scripts/mutation_selfcheck.py --list      # 看内置 canary 有哪些
    uv run python scripts/mutation_selfcheck.py \
        --file src/similarity.py --find 'top_k=5' --replace 'top_k=4' \
        --tests tests/unit/test_similarity.py               # 临时自定义一个

退出码：0 = 全通过；1 = 有 canary 没被抓住（测试太松）；2 = 基线本来就是红的。
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PYTEST_ARGS = ["-x", "-q", "--no-header", "-p", "no:cacheprovider"]


@dataclass(frozen=True)
class Canary:
    """一个已知答案的改动，用来校验「变异 → 测试变红」这条链路。

    expect="caught"：改了必须红。不红 = 断言是摆设（假阴性）。
    expect="missed"：改了不该红（代码在当前平台执行不到）。红了 = 环境在假杀
      （假阳性）—— 比如 pytest 的 tmp 目录属主冲突、或 --basetemp 被并发
      rmtree，都会让测试莫名其妙变红，把变异分数顶到接近 100%。
    """

    name: str
    file: str
    find: str
    replace: str
    tests: tuple[str, ...]
    why: str = ""
    expect: str = "caught"


@dataclass
class Result:
    canary: Canary
    baseline_ok: bool
    caught: bool
    detail: str = ""
    restored: bool = True
    error: str | None = None

    @property
    def ok(self) -> bool:
        if self.error or not self.baseline_ok or not self.restored:
            return False
        return self.caught if self.canary.expect == "caught" else not self.caught


# 内置 canary：挑的是「有独立重算、不依赖实现细节」的契约型断言。
# 别挑那种测试里直接抄了实现写法的（例如 assert len(x) == 16 写死常量），
# 那种属于把实现抄进测试，杀了也没意义。
CANARIES: tuple[Canary, ...] = (
    Canary(
        name="ids.content_hash 截断长度 16 → 15",
        file="src/ids.py",
        find="[:16]",
        replace="[:15]",
        tests=("tests/unit/test_ids.py",),
        why="16 位是 data-model 的硬契约；测试独立重算 sha256，改一位必红",
    ),
    Canary(
        name="ids.point_id 命名空间 URL → DNS",
        file="src/ids.py",
        find="uuid.NAMESPACE_URL",
        replace="uuid.NAMESPACE_DNS",
        tests=("tests/unit/test_ids.py",),
        why="换命名空间会换掉整个 ID 空间，测试用 NAMESPACE_URL 独立推导期望值",
    ),
    Canary(
        name="sync.__try_lock 的 Windows 分支（不该被抓住）",
        file="src/sync.py",
        find="os.lseek(fd, 0, os.SEEK_SET)",
        replace="os.lseek(None, 0, os.SEEK_SET)",
        tests=("tests/unit/test_sync.py",),
        expect="missed",
        why="这段在 except ImportError 里，macOS 走 fcntl 分支，根本执行不到。"
        "抓住它就是假杀 —— 通常是 pytest 临时目录出问题，分数会虚高到接近 100%",
    ),
)


def _run_pytest(targets: list[str]) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, "-m", "pytest", *PYTEST_ARGS, *targets]
    return subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )


def _tail(text: str, limit: int = 12) -> str:
    lines = [line for line in text.strip().splitlines() if line.strip()]
    if not lines:
        return "（无输出）"
    return "\n".join(lines[-limit:])


def run_canary(canary: Canary) -> Result:
    target = REPO_ROOT / canary.file
    result = Result(canary=canary, baseline_ok=False, caught=False)

    if not target.exists():
        result.error = f"找不到源码文件：{target}"
        return result
    if canary.find not in target.read_text(encoding="utf-8"):
        result.error = f"{canary.file} 里已经找不到 {canary.find!r}，canary 失效了，请更新"
        return result

    tests = [str(REPO_ROOT / t) for t in canary.tests]

    # --- 1. 基线必须是绿的 ---
    baseline = _run_pytest(tests)
    if baseline.returncode != 0:
        result.detail = _tail(baseline.stdout)
        result.error = "基线测试本来就是红的，先修好再来自检"
        return result
    result.baseline_ok = True

    # --- 2. 变异 + 还原，finally 兜底 ---
    backup = Path(tempfile.mkdtemp(prefix="mutation-selfcheck-")) / target.name
    shutil.copy2(target, backup)
    try:
        original = target.read_text(encoding="utf-8")
        target.write_text(original.replace(canary.find, canary.replace, 1), encoding="utf-8")

        mutated = _run_pytest(tests)
        result.caught = mutated.returncode != 0
        result.detail = _tail(mutated.stdout)
    finally:
        shutil.copy2(backup, target)
        shutil.rmtree(backup.parent, ignore_errors=True)

    result.restored = target.read_text(encoding="utf-8") == original
    return result


def render(result: Result) -> None:
    canary = result.canary
    mark = "PASS" if result.ok else "FAIL"
    print(f"\n[{mark}] {canary.name}")
    print(f"       {canary.file}: {canary.find!r} → {canary.replace!r}")
    if canary.why:
        print(f"       为什么必死：{canary.why}")

    if result.error:
        print(f"       ✗ {result.error}")
    elif not result.restored:
        print("       ✗ 源码没能还原，请手工检查（备份已尽力恢复）")
    elif result.canary.expect == "missed":
        if result.caught:
            print("       ✗ 不该抓住却抓住了 —— 环境在假杀，分数会虚高到接近 100%")
            print("         查：pytest 临时目录属主冲突、--basetemp 被并发重建")
        else:
            print("       ✓ 正确地没被抓住（这段代码在当前平台执行不到）")
    elif result.caught:
        print("       ✓ 基线绿 → 变异后变红 → 源码已还原")
    else:
        print("       ✗ 改坏之后测试还是绿的 —— 这条路径上的断言是摆设")

    if result.detail and not result.ok:
        print("       --- pytest 输出尾部 ---")
        for line in result.detail.splitlines():
            print(f"       {line}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="验证「改坏源码 → 测试会红」这条链路真的通",
    )
    parser.add_argument("--list", action="store_true", help="列出内置 canary 后退出")
    parser.add_argument("--file", help="自定义：要改的源码文件（相对仓库根）")
    parser.add_argument("--find", help="自定义：要替换的原文")
    parser.add_argument("--replace", help="自定义：替换成什么")
    parser.add_argument("--tests", nargs="+", help="自定义：跑哪些测试")
    parser.add_argument(
        "--expect",
        choices=["caught", "missed"],
        default="caught",
        help="自定义：期望被抓住（默认），还是期望抓不住（反向对照）",
    )
    args = parser.parse_args(argv)

    if args.list:
        for canary in CANARIES:
            print(f"{canary.file}: {canary.find!r} → {canary.replace!r}")
            print(f"    {canary.name} — {canary.why}")
        return 0

    if args.file:
        missing = [n for n in ("find", "replace", "tests") if not getattr(args, n)]
        if missing:
            parser.error(f"自定义 canary 缺少参数：{', '.join('--' + m for m in missing)}")
        canaries: tuple[Canary, ...] = (
            Canary(
                "自定义 canary",
                args.file,
                args.find,
                args.replace,
                tuple(args.tests),
                expect=args.expect,
            ),
        )
    else:
        canaries = CANARIES

    print(f"变异自检：{len(canaries)} 个 canary"
          f"（双向：该抓住的必须抓住，不该抓住的必须放过）")
    results = [run_canary(c) for c in canaries]
    for result in results:
        render(result)

    passed = sum(1 for r in results if r.ok)
    print(f"\n{passed}/{len(results)} 通过")

    if any(r.error and not r.baseline_ok for r in results):
        print("结论：基线是红的，先把主流程修绿 —— 现在的变异分数不可信。")
        return 2
    if passed != len(results):
        if any(r.canary.expect == "missed" and r.caught for r in results):
            print("结论：环境在假杀 —— 变异分数会虚高到接近 100%，先修环境再谈分数。")
        else:
            print("结论：有改动没被抓住。变异分数会虚高，别信那个数字。")
        return 1

    print("结论：链路是通的。变异分数可以作为信号使用（但仍不是真理）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
