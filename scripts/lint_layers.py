#!/usr/bin/env python3
"""位置门禁：既管「谁可以 import 谁」，也管「文件该放在哪」。

规则一：分层依赖（依赖只能向下，不能反向）

    api/         接口薄壳 —— 只调编排层，不碰采集细节
    编排层        sync / redistill / distill / ingest / ask / collect / scope / log
    核心层        similarity / ids / ark_client / distill_prompt（宪法 VII 保护）
    基础设施      config
    plugins/      采集插件 —— 自成一体，只认 plugins 包和 config

规则二：位置登记（文件该放在哪）

    src/ 下每一个「装着 .py 的目录」都必须在 DIRECTORIES 里登记过属于哪一层。
    没登记就报错，不给默认值。

为什么位置也要管，以及为什么默认值必须是「拒绝」
------------------------------------------------
这份脚本原本只查规则一，`layer_of()` 的最后一行是 `return "编排层"` 兜底，
而 `ALLOWED["编排层"] = None` 表示不限制。于是：

    src/retrieval/rerank.py   -> 编排层 -> 不限制
    src/api2/handler.py       -> 编排层 -> 不限制

新建一个目录，就等于**默认拿到最大权限**。这是典型的 fail-open：门禁对「已经
存在、已经被人 review 过的旧结构」严格，对「还没人想过的新结构」放行 —— 而
后者恰恰是唯一需要门禁的时刻（旧结构早就不会出问题了）。

行业里做得好的方案，兜底一律是拒绝：

  - Go 的 `internal/`：目录名是编译期约束，规则外的路径根本无法被外部 import
  - Bazel `visibility`：默认 private。官方最佳实践原文是「避免把
    default_visibility 设为 public……随着代码库增长，无意中创建公共目标的风险
    会上升」
  - Nx 的 project tag：官方文档一句「Projects without any tags cannot depend
    on any other projects」—— 没打 tag 就等于谁都不能依赖
  - tach：允许放行，但必须在 tach.toml 里显式写 `unchecked: true`，
    所以逃逸是显式的、可审计的

共同点不是「用哪个工具」，而是：**规则覆盖全体、默认拒绝、例外显式写出来。**
本脚本照这个来 —— 新增目录时你会被拦住，然后必须在 DIRECTORIES 里写一行
「它属于哪一层、为什么」。

例外写在 EXCEPTIONS 里并注明理由。规则有例外不可怕，可怕的是例外没理由 ——
没理由的例外过半年就变成「反正一直这样」。

用法：
    python scripts/lint_layers.py            # 只检查
    python scripts/lint_layers.py --list     # 打印层级、位置登记与例外
"""

from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass
from pathlib import Path

SOURCE_ROOT = Path("src")

# 宪法 VII 点名的手写核心（AI 仅 review）。单独列出来是为了让提示更明确。
CORE_MODULES = {
    "src/similarity.py",
    "src/ids.py",
    "src/ark_client.py",
    "src/distill_prompt.py",
}


@dataclass(frozen=True)
class Place:
    """一个已登记的位置：它属于哪一层，以及为什么这样分。"""

    layer: str
    why: str


# 已登记的目录。键必须以 / 结尾；值是「层 + 理由」，理由不是装饰 ——
# 没有理由的登记过半年就变成「反正一直这样」。
#
# 登记单位是**目录**而不是文件，因为「新增一个文件」和「新增一个目录」是两种
# 量级的动作：前者是给已有结构添砖，后者是在引入一个新的架构单元 —— 只有后者
# 值得先停下来想一步。平铺在 src/ 根的文件算编排层，`src/` 这一条就是给它们的。
DIRECTORIES: dict[str, Place] = {
    "src/": Place(
        "编排层",
        "平铺在 src/ 根的是编排层：sync / redistill / distill / ingest / ask / "
        "collect / scope / log 等。它们互相调用是常态，所以这一层的 import 不设限"
        "（ALLOWED 里是 None）—— 代价是在这里新加一个文件几乎零阻力，所以"
        "「新增目录」才必须登记，那是唯一需要停下来想一步的地方。",
    ),
    "src/api/": Place(
        "接口层 api/",
        "HTTP 薄壳，只准调编排层。这里不该有业务逻辑，逻辑下沉到编排层 —— "
        "否则同一件事会有两份实现，改一份漏一份。",
    ),
    "src/plugins/": Place(
        "插件层 plugins/",
        "采集插件，自成一体，只认 src.plugins 与 src.config。子目录不单独登记："
        "插件是开放式扩展点，多一个少一个不改变分层判断，真正要守的是"
        "「插件之间不许互相依赖」，那条按插件名判，不需要维护名单。",
    ),
}

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


def layer_of(rel: str) -> str | None:
    """按路径判断一个模块属于哪一层；没登记过就返回 None。

    兜底是 None 而不是「编排层」：默认值给最宽松的那一层，等于给所有新目录
    发一张不限制的通行证（见模块 docstring）。
    """
    if rel in CORE_MODULES:
        return "核心层（宪法 VII）"
    if rel == "src/config.py":
        return "基础设施 config"

    parent = rel.rpartition("/")[0] + "/"
    place = DIRECTORIES.get(parent)
    if place is not None:
        return place.layer
    if parent.startswith("src/plugins/"):
        # 插件是开放式扩展点：src/plugins/ 下再开一层子目录不用单独登记，
        # 因为「它属于哪一层」在 src/plugins/ 这里就已经有答案了。
        return "插件层 plugins/"
    return None


def plugin_name(rel: str) -> str | None:
    """这个文件属于哪个插件子包；src/plugins/__init__.py 本身返回 None。"""
    parts = rel.split("/")
    if len(parts) >= 4 and parts[0] == "src" and parts[1] == "plugins":
        return parts[2]
    return None


def unregistered(rel: str) -> str:
    """未登记位置的报错。要给出「下一步做什么」，而不只是「你错了」。"""
    parent = rel.rpartition("/")[0] + "/"
    return (
        f"{rel}：{parent} 这个目录没有登记过，判断不出它属于哪一层。\n"
        f"      新增目录时，请到 scripts/lint_layers.py 的 DIRECTORIES 里加一条，"
        f"写明属于哪一层、为什么；\n"
        f"      只是新增一个模块的话，放进已登记的目录就行"
        f"（平铺在 src/ 根 = 编排层）。"
    )


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
    if layer is None:
        return [unregistered(rel)]

    if layer not in ALLOWED:
        # DIRECTORIES 里登记了一个 ALLOWED 没有的层名（多半是手滑打错）。
        # 这种时候绝不能默默放行 —— 那正是这次要消灭的毛病。
        return [
            f"{rel}：登记成了「{layer}」这一层，但 ALLOWED 里没有这一层的规则。"
            f"（DIRECTORIES 和 ALLOWED 对不上账）"
        ]
    if ALLOWED[layer] is None:
        return []

    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
    except SyntaxError as exc:  # 语法错误有 check-ast 钩子管，这里别重复报
        return [f"{rel}: 无法解析（{exc.msg}）"]

    problems: list[str] = []
    own_plugin = plugin_name(rel)
    for module in sorted(set(imported_src_modules(tree))):
        # 插件之间不许互相依赖：src.plugins 可以，src.plugins.codex 不行。
        # 判据是「这个文件自己属于哪个插件」，不是一张插件名单 —— 名单要手工
        # 维护，新插件忘了加进去规则就静默失效（枚举「已知的」而不是覆盖
        # 「全体的」，正是这次修掉的那类毛病）。
        if layer == "插件层 plugins/":
            parts = module.split(".")
            if len(parts) >= 3 and parts[1] == "plugins" and parts[2] != own_plugin:
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


def registry_problems() -> list[str]:
    """登记表自身的体检。只看常量、不看仓库内容 —— 所以在任何仓库里结论一样。

    刻意不去检查「登记的目录必须存在」：那条在 gate_selftest 的临时仓库里
    必然失败（那里只有夹具文件，没有 src/api/ 和 src/plugins/），而它拦不住
    任何真问题 —— 目录改名后忘了改登记表，新位置会以「未登记」被拦下，这条
    已经够了。死条目用 `--list` 看每个目录的现有文件数即可（显示 0 就是可疑）。
    """
    problems: list[str] = []
    for prefix, place in DIRECTORIES.items():
        if not prefix.endswith("/"):
            problems.append(
                f"DIRECTORIES 的键 {prefix!r} 必须以 / 结尾（登记的是目录，不是文件）"
            )
        if place.layer not in ALLOWED:
            problems.append(
                f"DIRECTORIES 里 {prefix} 登记成了「{place.layer}」，"
                f"但 ALLOWED 里没有这一层 —— 两边对不上账"
            )
    return problems


def stale_exceptions() -> list[str]:
    """EXCEPTIONS 里的例外还成立吗。

    只在「文件在、但已经不再 import 那个模块」时报。文件整个没了就不管：
    那种情况下例外自然失效，不值得拦一次提交，而且 gate_selftest 的临时仓库
    里只有夹具文件，真去要求 src/distill_prompt.py 存在会把对照组弄红。
    """
    problems: list[str] = []
    for key in EXCEPTIONS:
        rel, _, module = key.partition(" -> ")
        path = Path(rel)
        if not path.is_file():
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        except SyntaxError:
            continue
        if module not in imported_src_modules(tree):
            problems.append(
                f"{rel} 已经不再 import {module}，EXCEPTIONS 里这条可以删了"
                f"（失效的例外会误导后面读的人，以为这里还有约束）"
            )
    return problems


def print_registry() -> None:
    """--list：把「规则是什么」和「现状长什么样」摆在一起。

    按**层**统计文件数，而不是按目录 rglob —— 后者会把嵌套目录重复算进去
    （src/ 那一条会显示全部 24 个，看着像有 24 个文件平铺在根上）。
    按层统计还有个好处：某个层显示 0，就说明那条登记大概率已经死了。
    """
    counts: dict[str, int] = {}
    for path in SOURCE_ROOT.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        layer = layer_of(path.as_posix())
        counts[layer] = counts.get(layer, 0) + 1

    print("已登记的位置（src/ 下每个装着 .py 的目录都必须在这里）：")
    for prefix, place in DIRECTORIES.items():
        print(f"  {prefix:<14} -> {place.layer}")
        print(f"  {'':<14}    {place.why}")

    print("\n按文件登记的位置（层的边界穿过某个目录时，只能按文件登记）：")
    for rel in sorted(CORE_MODULES):
        print(f"  {rel:<24} 核心层（宪法 VII）")
    print(f"  {'src/config.py':<24} 基础设施 config")

    print("\n每层允许 import 的 src 前缀（末尾是这一层现有文件数，0 = 空层，"
          "登记可能已死）：")
    for layer, allowed in ALLOWED.items():
        rule = "不限制" if allowed is None else ", ".join(sorted(allowed)) or "无"
        print(f"  {layer:<18} {rule:<32} {counts.get(layer, 0)} 个")

    print("\n例外：")
    for key, why in EXCEPTIONS.items():
        print(f"  {key}\n    {why}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="检查 src/ 的位置与分层依赖")
    parser.add_argument("--list", action="store_true", help="打印层级与规则后退出")
    args = parser.parse_args(argv)

    if args.list:
        print_registry()
        return 0

    problems = registry_problems()
    if problems:
        print("✗ 位置登记表自身有问题：", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 2

    if not SOURCE_ROOT.is_dir():
        # 空目录上 rglob 返回空列表，然后打印「通过」—— 那是门禁瞎了还在报平安。
        print(f"✗ 找不到 {SOURCE_ROOT}/，没有可检查的对象", file=sys.stderr)
        return 2

    files = sorted(
        p for p in SOURCE_ROOT.rglob("*.py") if "__pycache__" not in p.parts
    )
    violations: list[str] = []
    for path in files:
        violations.extend(check_file(path))
    violations.extend(stale_exceptions())

    if not violations:
        print(f"✓ 位置与分层检查通过（{len(files)} 个文件）")
        return 0

    print(f"✗ 位置/分层违规 {len(violations)} 处：", file=sys.stderr)
    for violation in violations:
        print(f"  {violation}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
