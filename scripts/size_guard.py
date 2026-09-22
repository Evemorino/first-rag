#!/usr/bin/env python3
"""规模门禁：单个文件和单个函数的长度上限。

只查 src/ —— 测试函数天然长（一堆断言排下来），工具脚本同理，
拿同一把尺子去量只会逼人把测试拆碎，那不是我们想要的。

为什么用 SLOC 而不是物理行数：注释和空行写得多的文件不该被罚。
radon 的 SLOC 已经排掉了空行与注释行，量的是真正的代码。

为什么两个都管：文件太大说明职责太多（该拆模块），函数太长说明逻辑没分层
（该拆函数）。但两者并不等价 —— 见过 116 行却很清晰的函数（顺序编排），
也见过 66 行就绕成一团的函数。所以这个门禁只是下限保障，
真正衡量改动风险的还是 CRAP（复杂度 × 未覆盖度）。

用法：
    python scripts/size_guard.py                    # 默认 300 / 80
    python scripts/size_guard.py --max-file 200 --max-func 50
    python scripts/size_guard.py --top 10           # 看最长的 10 个（摸底用）
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from radon.complexity import cc_visit
from radon.raw import analyze

SOURCE_ROOT = Path("src")
DEFAULT_MAX_FILE_SLOC = 300
DEFAULT_MAX_FUNC_LINES = 80


def collect(root: Path) -> tuple[list[tuple[int, str]], list[tuple[int, str, int]]]:
    """返回 (文件 SLOC 列表, 函数行数列表)，都按从大到小排。"""
    files: list[tuple[int, str]] = []
    funcs: list[tuple[int, str, int]] = []

    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = path.as_posix()
        source = path.read_text(encoding="utf-8")
        files.append((analyze(source).sloc, rel))
        for func in cc_visit(source):
            funcs.append((func.endline - func.lineno + 1, rel, func.name))

    files.sort(reverse=True)
    funcs.sort(reverse=True)
    return files, funcs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="检查 src/ 的文件与函数规模")
    parser.add_argument("--max-file", type=int, default=DEFAULT_MAX_FILE_SLOC,
                        help=f"单文件 SLOC 上限（默认 {DEFAULT_MAX_FILE_SLOC}）")
    parser.add_argument("--max-func", type=int, default=DEFAULT_MAX_FUNC_LINES,
                        help=f"单函数行数上限（默认 {DEFAULT_MAX_FUNC_LINES}）")
    parser.add_argument("--top", type=int, default=0,
                        help="只打印最长的 N 个，且不判失败（摸底用）")
    args = parser.parse_args(argv)

    files, funcs = collect(SOURCE_ROOT)

    if args.top:
        print(f"文件 SLOC（前 {args.top}）：")
        for sloc, rel in files[:args.top]:
            print(f"  {sloc:>5}  {rel}")
        print(f"\n函数行数（前 {args.top}）：")
        for lines, rel, name in funcs[:args.top]:
            print(f"  {lines:>5}  {rel}:{name}")
        return 0

    too_big_files = [(s, r) for s, r in files if s > args.max_file]
    too_long_funcs = [(n, r, f) for n, r, f in funcs if n > args.max_func]

    if not too_big_files and not too_long_funcs:
        print(
            f"✓ 规模检查通过（{len(files)} 个文件，{len(funcs)} 个函数；"
            f"文件 ≤{args.max_file} SLOC，函数 ≤{args.max_func} 行）"
        )
        return 0

    print("✗ 规模超限：", file=sys.stderr)
    for sloc, rel in too_big_files:
        print(f"  文件 {rel}: {sloc} SLOC > {args.max_file}", file=sys.stderr)
    for lines, rel, name in too_long_funcs:
        print(f"  函数 {rel}:{name}: {lines} 行 > {args.max_func}", file=sys.stderr)
    print(
        "\n  拆之前先看 CRAP（make crap）：复杂度低的长函数拆了收益有限，\n"
        "  复杂度高的短函数才是真该动的地方。",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
