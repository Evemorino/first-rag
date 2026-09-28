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

import re
import shlex
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = REPO_ROOT / "Makefile"
README = REPO_ROOT / "README.md"
AGENTS = REPO_ROOT / "AGENTS.md"

# 目标 → 为什么它可以不出现在 README / AGENTS.md 里。
# 判据不是"重要不重要"，而是"人会不会主动去敲它"：给人敲的入口要写进文档，
# 参数变体、内部件、一次性工具不必（写进去反而诱导重跑），但理由要留在这里。
# 与 write_boundary 的写入点登记同构：**没登记就红**，兜底是拒绝而不是放行。
INTERNAL_TARGETS = {
    "crap-observe": "`crap` 的观察模式（--observe --top 20），排查时才用",
    "migrate-trae": "一次性存量迁移（语义已在 AC-016 写清），列进文档会诱导重跑",
    "orphans-top": "`orphans` 的 --top 10 变体",
    "schema-check": "单文件校验助手（要 `F=` 参数），给改 schema 的流程用，不是日常入口",
}

MAKE_MENTION_RE = re.compile(r"\bmake\s+([a-z][a-z0-9-]*)")

# 目标 → README 里写明可用的参数变量名。加目标时两边一起加。
FORWARDED = {
    "log": ("m", "t"),
    "sync": ("D",),
    "redistill": ("D", "APPLY"),
    "ask": ("Q", "ARGS"),
}

# 目标 → (配方里该出现的锚点, README 里写明可用的参数变量名)。
# 脚本型目标的配方跑的是 `scripts/*.py`，锚点不是 `src.<target>`；判据与上面
# 那张表完全一样 —— "README 写了可用，配方就得转发"，只是锚点换了个字符串。
FORWARDED_SCRIPTS = {
    "rerun-overlap": ("scripts/rerun_overlap_report.py", ("D", "ARGS")),
    "batch-edges": ("scripts/batch_edge_probe.py", ("D", "ARGS")),
    "probe": ("scripts/collect_probe.py", ("D", "S")),
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
        # 点开头的行（.PHONY 之类的特殊目标）不是常规目标，别当成一个来数。
        if head and not head.startswith(".") and " " not in head and "=" not in head:
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
    assert ".PHONY" not in recipes


def test_every_target_is_documented_or_registered_as_internal(recipes):
    """每个目标要么写进 README / AGENTS.md，要么在 INTERNAL_TARGETS 里登记理由。

    为什么需要它：这一跳一直没人管，实测漏了 **7 个**——`down`（`up` 的反操作）、
    `hooks-run`、`baseline-update`，以及 `schema-check` / `migrate-trae` /
    `crap-observe` / `orphans-top`。`down` 这种一眼就该在文档里的都能漏整整一周，
    说明"靠人记得"不成立；判据必须是机械的，跟 `lint_layers` 的目录登记一个路子。
    """
    documented = {
        match.group(1)
        for text in (
            README.read_text(encoding="utf-8"),
            AGENTS.read_text(encoding="utf-8"),
        )
        for match in MAKE_MENTION_RE.finditer(text)
    }
    undocumented = sorted(set(recipes) - documented - set(INTERNAL_TARGETS))
    assert not undocumented, (
        f"这些目标既没写进 README/AGENTS.md，也没在 INTERNAL_TARGETS 里登记理由："
        f"{undocumented}"
    )
    # 登记表自己也要维护：指着不存在的目标，说明写它的人看的是旧 Makefile。
    stale = sorted(set(INTERNAL_TARGETS) - set(recipes))
    assert not stale, f"INTERNAL_TARGETS 里有已不存在的目标：{stale}"


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


@pytest.mark.parametrize(
    "target,anchor,flags", [(t, *v) for t, v in sorted(FORWARDED_SCRIPTS.items())]
)
def test_script_target_forwards_its_documented_flags(
    target, anchor, flags, recipes, readme_lines
):
    recipe = recipes[target]

    # 锚点换成脚本路径：同样是为了防止"解析跑偏时断言空转"。
    assert anchor in recipe, f"{target} 的配方解析错了：{recipe!r}"
    # 表里写着"README 里可用"，README 里就真得有这个目标的示例 ——
    # 否则这张表保护的是一份不存在的文档。
    assert any(line.strip().startswith(f"make {target}") for _, line in readme_lines), (
        f"README 的 ```sh 示例里没有 `make {target}`，"
        f"这条转发断言保护的是不存在的文档"
    )
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
