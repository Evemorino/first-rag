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

import shlex
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = REPO_ROOT / "Makefile"
README = REPO_ROOT / "README.md"

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


def _readme_shell_lines(text: str) -> list[tuple[int, str]]:
    """取出 README 里 ```sh 围栏内的命令示例，附行号。

    只认围栏，**不认引用块** —— README 的 blockquote 里故意写着
    `make ask Q=… --type error` 当反例，把它当示例扫进来就成了自己打自己。
    """
    out: list[tuple[int, str]] = []
    inside = False
    for lineno, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("```"):
            inside = stripped in ("```sh", "```bash", "```console")
            continue
        if inside:
            out.append((lineno, line))
    return out


@pytest.fixture(scope="module")
def readme_lines() -> list[tuple[int, str]]:
    return _readme_shell_lines(README.read_text(encoding="utf-8"))


def test_readme_parser_finds_the_examples(readme_lines):
    """先检查检查器自己：围栏标记一改，下面那条断言就会变成空转。"""
    assert len(readme_lines) >= 5, readme_lines
    assert any(line.strip().startswith("make ask ") for _, line in readme_lines)


def test_readme_examples_never_pass_bare_flags_to_make(readme_lines):
    """README 示例里不许出现裸的 `--flag`。

    这条是本轮 converge 的产物。README 原先写着 `make ask Q=… --type error`，
    而配方只转发 $(Q) 和 $(ARGS) —— 真跑起来 make 会把 `--type` 当成自己的
    未知选项，以退出码 2 停下（实测：`make -n ask Q=x --type error` →
    `unrecognized option '--type'`）。

    上面那条按目标核对的断言拦不住它：它只看 Makefile 配方里有没有 $(VAR)，
    而 $(ARGS) 确实在配方里，所以 README 怎么写都绿。差别在**谁读文档** ——
    那条读 Makefile，这条读 README。

    正确写法是经变量传：`make ask Q="…" ARGS="--type error --since 7d"`。
    """
    offenders: list[str] = []
    for lineno, line in readme_lines:
        try:
            tokens = shlex.split(line, comments=True)
        except ValueError as e:  # 引号没配平：这行自己就有问题，照样报出来
            offenders.append(f"README.md:{lineno} 引号没配平（{e}）：{line.strip()}")
            continue
        if len(tokens) < 2 or tokens[0] != "make":
            continue
        rest = tokens[1:]
        # make 自己的选项（`make -j4 ask`）可以出现在目标之前，放行。
        while rest and rest[0].startswith("-"):
            rest.pop(0)
        for token in rest[1:]:  # 目标之后的一切参数
            if token.startswith("-"):
                offenders.append(
                    f"README.md:{lineno} 裸选项 {token!r} 会被 make 当成未知选项"
                    f"（退出码 2），应经变量传：{line.strip()}"
                )
    assert not offenders, "\n".join(offenders)
