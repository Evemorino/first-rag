"""手动快记（T032 / FR-004）：追加时间戳行到 notes/inbox.md。

一行一条：`- [2026-09-20T22:31:00+08:00 #type] text`。
未标注类型不写 marker——collect 读取时默认归 reflection（§8 v0.4：
轻处理不改类型，要改类型请在快记时标注）。CLI 与 API 壳共用本实现。
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

from src import config


def log(text: str, type_: str | None = None) -> Path:
    """Append one timestamped line to notes/inbox.md (FR-004)."""
    if not text.strip():
        raise ValueError("note text must not be empty")
    ts = datetime.now(tz=config.TZ).isoformat(timespec="seconds")
    marker = f" #{type_}" if type_ else ""
    line = f"- [{ts}{marker}] {text.strip()}"

    config.NOTES_DIR.mkdir(parents=True, exist_ok=True)
    inbox = config.NOTES_DIR / "inbox.md"
    with open(inbox, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    return inbox


def main(argv: list[str] | None = None) -> int:
    config.load_env()
    argv = sys.argv if argv is None else argv
    text = None
    type_ = None
    for arg in argv[1:]:
        if arg.startswith("m="):
            text = arg[2:]
        elif arg.startswith("t="):
            type_ = arg[2:]
    if not text:
        print('usage: make log m="…" [t=type]', file=sys.stderr)
        return 2
    try:
        inbox = log(text, type_)
    except ValueError as exc:
        print(f"log failed: {exc}", file=sys.stderr)
        return 1
    print(f"noted -> {inbox}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
