#!/usr/bin/env python3
"""分层依赖门禁：用 AST 检查 src/ 里的 import 有没有跨层。

层级（依赖只能向下，不能反向）：

    api/         接口薄壳 —— 只调编排层，不碰采集细节
    编排层        sync / redistill / distill / ingest / ask / collect / scope / log
    核心层        similarity / ids / ark_client / distill_prompt（宪法 VII 保护）
    基础设施      config
    plugins/      采集插件 —— 自成一体，只认 plugins 包和 config

为什么需要它：写下这份规则时，所有约束都已经被满足（零违规）。但没人守着的话，
半年内一定会出现"插件图省事直接 import 编排层"这类退化 —— 依赖方向一旦乱了，
改一个模块就得全库回归，而这类错误在 code review 里极容易漏掉
（import 一行，看着无害）。

例外写在 EXCEPTIONS 里并注明理由。规则有例外不可怕，可怕的是例外没理由 ——
没理由的例外过半年就变成"反正一直这样"。

用法：
    python scripts/lint_layers.py            # 只检查
    python scripts/lint_layers.py --list     # 打印层级与规则
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

SOURCE_ROOT = Path("src")

# 宪法 VII 点名的手写核心（AI 仅 review）。单独列出来是为了让提示更明确。
CORE_MODULES = {
    "src/similarity.py",
    "src/ids.py",
    "src/ark_client.py",
    "src/distill_prompt.py",
}

# 具体插件子包（用于判断"插件 import 了另一个插件"）
PLUGIN_SUBPACKAGES = {"codex", "claude_code", "kimi_code", "trae", "_template"}


def layer_of(rel: str) -> str:
    """按路径判断一个模块属于哪一层。"""
    if rel.startswith("src/api/"):
        return "接口层 api/"
    if rel.startswith("src/plugins/"):
        return "插件层 plugins/"
    if rel in CORE_MODULES:
        return "核心层（宪法 VII）"
    if rel == "src/config.py":
        return "基础设施 config"
    return "编排层"


# 每层允许 import 的 src 前缀。None 表示不限制（编排层本来就要互相调用）。
ALLOWED: dict[str, set[str] | None] = {
    "插件层 plugins/": {"src.plugins", "src.config"},
    "接口层 api/": {"src.api", "src.config", "src.sync", "src.ask", "src.log"},
    "核心层（宪法 VII）": {"src.config", "src.collect"},
    "基础设施 config": set(),
    "编排层": None,
}

# 规则之外的放行，每条都必须写清理由。
EXCEPTIONS: dict[str, str] = {
    "src/distill_prompt.py -> src.collect": (
        "只为取 DayRaw —— 它是数据契约，只是恰好住在 collect.py。"
        "把 DayRaw 挪去独立的 models 模块更干净，但那是另一个重构。"
    ),
}


def imported_src_modules(tree: ast.AST) -> list[str]:
    """收集一个模块里所有指向 src 的 import，展开成 `src.xxx` 形式。

    `from src import config, distill` 会被展开成 src.config 和 src.distill ——
    不展开的话这类写法会绕过检查，而它恰恰是本项目最常见的写法。
    """
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "src" or alias.name.startswith("src."):
                    found.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "src":
                found.extend(f"src.{alias.name}" for alias in node.names)
            elif module.startswith("src."):
                found.append(module)
    return found


def allowed_for(layer: str, module: str) -> bool:
    allowed = ALLOWED[layer]
    if allowed is None:
        return True
    return any(module == a or module.startswith(a + ".") for a in allowed)


def check_file(path: Path) -> list[str]:
    """返回这个文件里的违规描述（空列表 = 干净）。"""
    rel = path.as_posix()
    layer = layer_of(rel)
    if ALLOWED[layer] is None:
        return []

    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
    except SyntaxError as exc:  # 语法错误有 check-ast 钩子管，这里别重复报
        return [f"{rel}: 无法解析（{exc.msg}）"]

    problems: list[str] = []
    for module in sorted(set(imported_src_modules(tree))):
        # 插件之间不许互相依赖：src.plugins 可以，src.plugins.codex 不行
        if layer == "插件层 plugins/":
            parts = module.split(".")
            if len(parts) >= 3 and parts[1] == "plugins" and parts[2] in PLUGIN_SUBPACKAGES:
                problems.append(
                    f"{rel}: 插件不该依赖另一个插件（{module}）—— "
                    "插件之间应当完全独立，共用逻辑提到 src/plugins/__init__.py"
                )
                continue

        if allowed_for(layer, module):
            continue

        reason = EXCEPTIONS.get(f"{rel} -> {module}")
        if reason:
            continue

        problems.append(
            f"{rel}（{layer}）不该 import {module}\n"
            f"      允许：{sorted(ALLOWED[layer] or []) or '无'}"
        )
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="检查 src/ 的分层依赖方向")
    parser.add_argument("--list", action="store_true", help="打印层级与规则后退出")
    args = parser.parse_args(argv)

    if args.list:
        for layer, allowed in ALLOWED.items():
            rule = "不限制" if allowed is None else ", ".join(sorted(allowed)) or "无"
            print(f"{layer:<20} 允许 import：{rule}")
        print("\n例外：")
        for key, why in EXCEPTIONS.items():
            print(f"  {key}\n    {why}")
        return 0

    files = sorted(
        p for p in SOURCE_ROOT.rglob("*.py") if "__pycache__" not in p.parts
    )
    problems: list[str] = []
    for path in files:
        problems.extend(check_file(path))

    if not problems:
        print(f"✓ 分层依赖检查通过（{len(files)} 个文件）")
        return 0

    print(f"✗ 分层依赖违规 {len(problems)} 处：", file=sys.stderr)
    for problem in problems:
        print(f"  {problem}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
