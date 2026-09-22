"""提醒：刚提交的改动落在变异覆盖范围内 —— 记得重跑 make mutation。

为什么需要它
------------
变异测试跑一遍要半小时（483 个变异体、串行），进不了 pre-commit，
只能靠手动 `make mutation`。于是"改了核心代码却没重跑"就全靠人记得。

本项目真踩过两次：
* 把 `distill.py` 从 417 行拆到 241 行，README 里的变异分数是拆之前的数，
  过了好几轮才被想起来；
* 加了脚本单测之后 `make mutation` 整个跑不起来（`also_copy` 缺 scripts/），
  而 mutmut 不在 pre-commit 里，没有任何门禁报警。

所以这里做一件很轻的事：提交之后比对"改了哪些文件"和"only_mutate 覆盖
哪些文件"，有交集就提醒一句。**只提醒，不拦人** —— 它跑在 post-commit，
这时候提交已经发生了，拦也拦不住，而且该不该重跑是人来判断的。

用法::

    python scripts/mutation_reminder.py              # 看刚提交的那次
    python scripts/mutation_reminder.py --since HEAD~3
    python scripts/mutation_reminder.py --json

退出码恒为 0（提醒而已）。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = REPO_ROOT / "pyproject.toml"


def only_mutate(pyproject: Path = PYPROJECT) -> list[str]:
    """读 pyproject 里 [tool.mutmut] 的 only_mutate。"""
    with pyproject.open("rb") as handle:
        data = tomllib.load(handle)
    return list(data.get("tool", {}).get("mutmut", {}).get("only_mutate") or [])


def changed_files(since: str) -> list[str]:
    """since..HEAD 之间改动过的文件；git 报错时返回空列表。"""
    result = subprocess.run(
        ["git", "diff", "--name-only", since, "HEAD"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def find_hits(changed: list[str], targets: list[str]) -> list[str]:
    """改动文件与变异覆盖名单的交集，按名单顺序返回。"""
    changed_paths = {Path(name).as_posix() for name in changed}
    return [t for t in targets if Path(t).as_posix() in changed_paths]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--since", default="HEAD~1", help="与谁比对（默认 HEAD~1）")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args(argv)

    targets = only_mutate()
    hits = find_hits(changed_files(args.since), targets)

    if args.json:
        print(json.dumps({"covered_by_mutation": targets, "changed_and_covered": hits}))
        return 0

    if not hits:
        return 0

    print("提醒：这次改动碰到了变异测试覆盖的文件 ——")
    for name in hits:
        print(f"    {name}")
    print("\nREADME 里那个变异分数现在是旧的了。等有空时跑一遍：")
    print("    make mutation      # 约 30 分钟，末尾会自动核对并刷新 README 基线")
    print("只想先看看基线还准不准：make baseline")
    return 0


if __name__ == "__main__":
    sys.exit(main())
