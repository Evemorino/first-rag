"""钉住数据文件名的字面量 —— 大小写在这台机器上"测不出来的东西"。

为什么不用行为断言：本机 APFS 默认大小写不敏感，`inbox.md` 改成 `INBOX.MD`
照样读到同一个文件，于是 4 个变异体在这里永久存活（README 分诊表里那 4 条：
`collect.x__note_materials__mutmut_5` / `__mutmut_35`、
`collect.x__git_materials__mutmut_4`、`sync.x__load_scope__mutmut_4`）。
任何"读读看能不能读到"的测试都区分不了它们 —— 在这台机器上恒真。

所以只能换个与被测文件系统无关的判据：**字面量本身是契约的一部分**。
`repos.txt` / `inbox.md` / `schema.json` 都写在 PRD 的 FR 与 §6 目录表里
（`scope.json` 只在 AGENTS.md 与宪法里出现，PRD 没提它），改大小写就是改契约，
哪怕在 mac 上"还能跑"。

这些测试还有一层作用：CI 从不跑 `make mutation`，所以指望"换台大小写敏感的
机器就会被杀"是空的（这一点 README 上原先写错了，已改）。钉住字面量是唯一
在**任何**平台都成立的杀法。
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _source(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


def test_collect_reads_exactly_these_lowercase_file_names():
    """collect 用的两个数据文件名必须是小写原样。"""
    source = _source("src/collect.py")

    assert '"repos.txt"' in source
    assert '"inbox.md"' in source


def test_sync_reads_scope_json_with_the_pinned_name():
    """sync 读 scope.json 的名字同样钉死。"""
    source = _source("src/sync.py")

    assert '"scope.json"' in source


def test_the_pinned_names_also_appear_in_the_prd():
    """PRD §6 那张目录表点名的这几个文件，代码里必须用同一个名字。

    只钉代码里的字符串不够：PRD 写 `repos.txt`、代码读 `REPOS.txt` 这种分叉
    没人会发现（mac 上还"能跑"）。这条把代码和 PRD 拉回同一个来源。
    """
    prd = (REPO_ROOT / "PRD.md").read_text(encoding="utf-8")

    for name in ("repos.txt", "inbox.md", "schema.json"):
        assert name in prd, f"PRD 里不再提到 {name}，代码里的字面量就成了无源之水"
