"""Makefile 目标必须把它文档化的参数转发给 CLI。

为什么需要它：Makefile 是 CLI 外面薄薄一层，而**这一跳一直没有测试盯着**。
它坏掉的方式恰好是最安静的那种 —— 实测过：`make log m="…" t=error` 少了
`t` 转发时，快记照样写进 `notes/inbox.md`，只是类型退回 reflection
（collect 读不到 marker 时的默认值）。README 一直写着 `t=error` 能用，
`test_log.py::test_main_parses_m_and_t` 也一直绿 —— 因为它测的是
`log.main(["log", "m=…", "t=…"])`，直接跳过了 make 这一层。

同一类"两层之间那一跳没人管"的坑本仓库踩过不止一次（README 里记着
变异提醒漏过两次、`also_copy` 漏文件两次），所以这里按目标建表逐条核对。
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = REPO_ROOT / "Makefile"

# 目标 → README 里写明可用的参数变量名。加目标时两边一起加。
FORWARDED = {
    "log": ("m", "t"),
    "sync": ("D",),
    "redistill": ("D", "APPLY"),
    "ask": ("Q", "ARGS"),
}


def _recipes(text: str) -> dict[str, str]:
    """把 Makefile 拆成 {目标名: 配方}，配方是紧跟目标行、以 Tab 开头的那些行。"""
    recipes: dict[str, str] = {}
    current: str | None = None
    for line in text.splitlines():
        if line.startswith("\t"):
            if current is not None:
                recipes[current] += line + "\n"
            continue
        current = None
        if not line or line.lstrip().startswith("#"):
            continue
        head = line.split(":", 1)[0].strip() if ":" in line else ""
        if head and " " not in head and "=" not in head:
            current = head
            recipes.setdefault(current, "")
    return recipes


@pytest.fixture(scope="module")
def recipes() -> dict[str, str]:
    return _recipes(MAKEFILE.read_text(encoding="utf-8"))


def test_parser_finds_the_real_targets(recipes):
    """先检查检查器自己：解析器瞎了的话，下面那些断言会变成空转。"""
    assert len(recipes) >= 20
    assert {"up", "sync", "ask", "log", "serve"} <= set(recipes)


@pytest.mark.parametrize("target,flags", sorted(FORWARDED.items()))
def test_target_forwards_its_documented_flags(target, flags, recipes):
    recipe = recipes[target]

    # 锚一下"确实拿到了这个目标的配方"，否则解析跑偏时断言可能空转。
    assert f"src.{target}" in recipe, f"{target} 的配方解析错了：{recipe!r}"
    for flag in flags:
        assert f"$({flag})" in recipe, (
            f"`make {target}` 没有转发 {flag}：参数会被静默丢掉，"
            f"而 README 写着它可用。配方：{recipe.strip()!r}"
        )
