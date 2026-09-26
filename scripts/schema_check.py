#!/usr/bin/env python3
"""schema_check —— 拿真实文件照一眼结构，写 parser 之前先验格式。

用途（contracts/plugin-contract.md:42）：接一个新插件时，先用它看真实文件的
字段长什么样，再动手写 `parse()`。它不判断对错，只打印事实——所以别把它当
回归门禁用（那是插件 fixture 测试的活）。

两种输入：

- **JSONL / JSON**：先当作整份 JSON 试一次，失败再按行解析。嵌套结构压成
  点分路径 + 类型集合，列表折叠成 `path[]`。同一路径在不同记录里类型不同会
  并列显示（`str/int`）——那通常正是格式漂移的第一现场。缺键不会抹掉已经
  见到过的路径：会话里字段本来就时有时无。
- **SQLite**：打印表、行数、列。

**只读，无副作用，不碰 data/。** SQLite 一律走 `mode=ro` URI——普通
`connect()` 会在产品源目录里建 `-wal`/`-journal`，那是实打实的写入（宪法 V）。
不带 `mode=ro` 的改动都算破坏「源目录只读」。

退出码：
  0  正常打印完
  1  文件不存在 / 解析不了

用法：
  python scripts/schema_check.py <path> [--max-depth N] [--limit N]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

# 素材正文动辄几千字，例值截到这个长度才看得下去
MAX_EXAMPLE = 60

SQLITE_MAGIC = b"SQLite format 3\x00"


class SchemaCheckError(RuntimeError):
    """Raised when a file cannot be read as JSON at all."""


def type_name(value) -> str:
    """类型名 —— bool 必须先于 int：Python 里 isinstance(True, int) 为真。"""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "dict"
    return type(value).__name__


def is_sqlite(path: Path) -> bool:
    """按文件头判断，不看扩展名。"""
    try:
        with path.open("rb") as handle:
            return handle.read(len(SQLITE_MAGIC)) == SQLITE_MAGIC
    except OSError:
        return False


def parse_records(text: str) -> list:
    """整份 JSON 优先（快照文件是缩进过的），否则按 JSONL 逐行解析。"""
    stripped = text.strip()
    if not stripped:
        return []
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        pass
    else:
        return payload if isinstance(payload, list) else [payload]

    records = []
    for lineno, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise SchemaCheckError(f"line {lineno}: not JSON: {exc}") from exc
    return records


def _excerpt(value):
    """标量给个例值；容器不给（dict/list 的 repr 只是噪声）。"""
    if isinstance(value, (dict, list)):
        return None
    text = repr(value)
    if len(text) > MAX_EXAMPLE:
        return text[:MAX_EXAMPLE] + "…"
    return text


def _record(shapes: dict, path: str, value) -> None:
    slot = shapes.setdefault(path, {"types": set(), "example": None})
    slot["types"].add(type_name(value))
    if slot["example"] is None and _excerpt(value) is not None:
        slot["example"] = _excerpt(value)


def _walk(value, prefix: str, level: int, max_depth: int, shapes: dict) -> None:
    """把一个节点的下一层记进 shapes；超过 max_depth 就不再往下。"""
    if level > max_depth:
        return
    if isinstance(value, dict):
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            _record(shapes, path, item)
            if isinstance(item, (dict, list)):
                _walk(item, path, level + 1, max_depth, shapes)
    elif isinstance(value, list):
        path = f"{prefix}[]"
        for item in value:
            _record(shapes, path, item)
            if isinstance(item, (dict, list)):
                _walk(item, path, level + 1, max_depth, shapes)


def merge_shapes(records: list, max_depth: int = 2) -> dict:
    """把多条记录合并成 `路径 -> {types, example}`。"""
    shapes: dict = {}
    for record in records:
        _walk(record, "", 0, max_depth, shapes)
    return shapes


def render(shapes: dict) -> str:
    lines = []
    for path in sorted(shapes):
        slot = shapes[path]
        types = "/".join(sorted(slot["types"]))
        example = slot["example"]
        lines.append(f"{path}: {types}" + (f"   e.g. {example}" if example else ""))
    return "\n".join(lines)


def describe_sqlite(path: Path) -> str:
    """表 / 行数 / 列。mode=ro —— 源目录只读（宪法 V）。"""
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        tables = [
            row[0]
            for row in con.execute(
                "select name from sqlite_master where type='table' order by name"
            )
        ]
        if not tables:
            return f"{path}: no tables"
        lines = [f"{path}:"]
        for table in tables:
            count = con.execute(f'select count(*) from "{table}"').fetchone()[0]
            lines.append(f"  {table}  ({count} rows)")
            for column in con.execute(f'pragma table_info("{table}")'):
                lines.append(f"    {column[1]} {column[2]}")
        return "\n".join(lines)
    finally:
        con.close()


def _parse_args(argv):
    parser = argparse.ArgumentParser(
        description="Print the real structure of a source file (read-only)."
    )
    parser.add_argument("path", type=Path)
    parser.add_argument(
        "--max-depth", type=int, default=2,
        help="嵌套到第几层就不再展开（默认 2）",
    )
    parser.add_argument(
        "--limit", type=int, default=0,
        help="只取前 N 条记录（0 = 全部；大文件的采样用）",
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)
    path = args.path
    if not path.exists():
        print(f"schema_check: no such file: {path}", file=sys.stderr)
        return 1
    try:
        if is_sqlite(path):
            print(describe_sqlite(path))
            return 0

        text = path.read_text(encoding="utf-8", errors="replace")
        records = parse_records(text)
        if not records:
            print(f"{path}: no records")
            return 0
        total = len(records)
        sample = records[: args.limit] if args.limit else records
        label = f"{total} record(s)" if total == len(sample) else (
            f"{total} record(s), showing first {len(sample)}"
        )
        print(f"{path}: {label}")
        print(render(merge_shapes(sample, max_depth=args.max_depth)))
    except (SchemaCheckError, sqlite3.Error, OSError) as exc:
        print(f"schema_check: {path}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
