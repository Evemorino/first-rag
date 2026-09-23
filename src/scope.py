"""采集范围交互选择器（T034 / FR-005）。

工具×项目 矩阵（含最近会话日期与近 7 天估算条数），勾选结果写入
config/scope.json——纯文本配置是唯一事实来源，选择器只是它的编辑器
（spec US-4）。估算只用插件契约（discover 窗口扫描），不触碰插件内部。

交互命令：`t <名>` 切换勾选；`a` 全选；`n` 全不选；`s` 保存退出；
`q` 不保存退出。
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from src import config
from src.plugins import iter_plugins

LOOKBACK_DAYS = 7


def _today() -> date:
    return datetime.now(tz=config.TZ).date()


def _repo_list() -> list[str]:
    repos_file = config.CONFIG_DIR / "repos.txt"
    if not repos_file.is_file():
        return []
    return [line.strip() for line in
            repos_file.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")]


def build_matrix(*, lookback_days: int = LOOKBACK_DAYS) -> dict:
    """工具×项目 矩阵：每行含 selected / latest_day / estimated_items。"""
    matrix: dict = {"tools": {}, "projects": {}}
    today = _today()

    for plugin in iter_plugins():
        per_day_counts: dict[date, int] = {}
        for offset in range(lookback_days):
            day = today - timedelta(days=offset)
            try:
                per_day_counts[day] = len(plugin.discover(day))
            except Exception:  # noqa: BLE001 — 估算失败不影响选择器
                per_day_counts[day] = 0
        days_with_items = [d for d, n in per_day_counts.items() if n > 0]
        matrix["tools"][plugin.name] = {
            "selected": True,
            "latest_day": max(days_with_items).isoformat() if days_with_items else None,
            "estimated_items": sum(per_day_counts.values()),
        }

    for repo in _repo_list():
        matrix["projects"][repo] = {
            "selected": True,
            "latest_day": None,
            "estimated_items": None,  # git 行数估算留给采集时自然发生
        }
    return matrix


def toggle(matrix: dict, section: str, name: str) -> None:
    row = matrix[section].get(name)
    if row is None:
        raise KeyError(f"unknown {section}: {name}")
    row["selected"] = not row["selected"]


def set_all(matrix: dict, selected: bool) -> None:
    for section in ("tools", "projects"):
        for row in matrix[section].values():
            row["selected"] = selected


def render(matrix: dict) -> str:
    lines = ["采集范围（t <名> 切换 / a 全选 / n 全不选 / s 保存 / q 退出）", "工具："]
    for name, row in matrix["tools"].items():
        mark = "x" if row["selected"] else " "
        meta = (f"最近 {row['latest_day']}，近 {LOOKBACK_DAYS} 天约 "
                f"{row['estimated_items']} 条" if row["latest_day"] else "近 7 天无素材")
        lines.append(f"  [{mark}] {name}  — {meta}")
    if matrix["projects"]:
        lines.append("项目（git 仓库）：")
        for name, row in matrix["projects"].items():
            mark = "x" if row["selected"] else " "
            lines.append(f"  [{mark}] {name}")
    return "\n".join(lines)


def save(matrix: dict) -> Path:
    """Write config/scope.json. 未勾选的工具/项目在采集期被跳过（AC-007）。"""
    payload = {
        section: {name: row["selected"]
                  for name, row in matrix[section].items()}
        for section in ("tools", "projects")
    }
    path = config.CONFIG_DIR / "scope.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    return path


def main(argv: list[str] | None = None, *, stdin_lines: list[str] | None = None) -> int:
    config.load_env()
    del argv  # 目前无位置参数
    matrix = build_matrix()
    source = iter(stdin_lines) if stdin_lines is not None else None

    while True:
        print(render(matrix))
        command = (next(source) if source is not None else input("> ")).strip()
        if command == "s":
            path = save(matrix)
            print(f"saved -> {path}")
            return 0
        if command == "q":
            print("quit without saving")
            return 0
        if command == "a":
            set_all(matrix, True)
        elif command == "n":
            set_all(matrix, False)
        elif command.startswith("t "):
            target = command[2:].strip()
            section = "projects" if target in matrix["projects"] else "tools"
            try:
                toggle(matrix, section, target)
            except KeyError:
                print(f"unknown name: {target}")
        else:
            print("unknown command")


if __name__ == "__main__":
    sys.exit(main())
