"""核对文档里写的变异分数基线是否还成立。

为什么需要它：变异分数是跑出来的，一旦写进 README 就变成了静态数字。
只要有改动落在 only_mutate 覆盖的文件上却没重跑，那个数字就开始说谎 ——
而且它说谎的时候不报错、不崩溃，只是安静地待在那里，直到某天有人拿它
做判断（本项目的实际教训：refactor 把 distill.py 从 417 行拆到 241 行，
README 里的 84.4% 是拆之前的数，没人发现）。

所以这里做两件事：

  1. 读 ``mutants/src/*.meta``，统计真实的 killed / survived / no-tests。
     ``exit_code_by_key`` 既是变异体清单也是状态：1=被杀死，0=存活，
     其他（本项目见到的是 33）=没有测试覆盖到它。
  2. 和文档（默认 README.md）里记录的基线比对，不一致就退出码 1，
     并把"应该写成什么"直接打出来。
  3. 找"整个函数的变异体都被判 no tests"的函数，核对 ``KNOWN_NO_TESTS``
     登记表 —— 不在表里的退出码 1（查这个的理由见 ``KNOWN_NO_TESTS`` 上方）。

外加一条过期提醒：源码或测试比 meta 还新，说明上次跑批之后又动过代码，
即便数字暂时没变，也该重跑。

用法::

    python scripts/baseline_check.py            # 只核对
    python scripts/baseline_check.py --update   # 把真实数字写回文档
    python scripts/baseline_check.py --json     # 机器可读

退出码：0 一致（或没有数据可比）；1 文档基线过期；2 环境/用法错误。
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

from mutmut.stats import status_by_exit_code

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DOC = REPO_ROOT / "README.md"
MUTANTS_DIR = REPO_ROOT / "mutants"

# 判定只按 mutmut 自己的映射走：1 与 3 都是"杀死"，0 是"存活"，
# 33=无测试覆盖、-11=段错误、-24/36/152/255=超时。
# 别在本地重抄一套 if code == 1 —— 那正是把三种不同毛病报成一个的原因。


def _verdicts(mutants_dir: Path):
    """逐个产出 (文件, 变异体名, 状态)。没有 mutants/ 时什么都不产。"""
    metas = sorted(glob.glob(str(mutants_dir / "src" / "*.meta")))
    for path in metas:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        for mutant, code in data.get("exit_code_by_key", {}).items():
            yield path, mutant, status_by_exit_code.get(code, f"未知退出码 {code}")

# 「整个函数的变异体都被判 no tests」的登记表：值写清**为什么它真没有测试**。
# 兜底是"报出来"，登记才放行 —— 与仓库里其他门禁同一套规矩。
#
# 为什么非要查这一条：mutmut 只在出现**新测试名**时才增量重采「函数 → 覆盖它的
# 测试」映射。于是"给一个**既有测试**加断言去覆盖一个新函数"这一步不会重学映射，
# 新函数的变异体跑的是空测试集（pytest 退出码 5/33）→ 被记成 no tests。
# 2026-09-26 实测：`ingest._skipped_by_source` 的 13 条就这么被报成"没覆盖"，
# 而它明明有测试 —— `rm mutants/mutmut-stats.json` 重采后 12 杀 1 活。
# 两种可能长得一模一样，所以必须由人判一次：真没覆盖（登记 + 理由）或
# 映射过期（清 stats 重跑）。
KNOWN_NO_TESTS: dict[str, str] = {
    "ask_expand.x__client": "Qdrant client 工厂，测试一律 monkeypatch 掉，真函数体从未被执行",
    "ingest.x__client": "同上（集成测试与单测都用 fake client 替换）",
}

# 变异体名长 `<模块>.<函数>__mutmut_N`，去掉后缀就是分组用的函数名。
_MUTANT_SUFFIX_RE = re.compile(r"__mutmut_\d+$")


def _group_by_function(mutants_dir: Path) -> tuple[dict[str, list[str]], int]:
    """变异体按函数分组；第二个返回值是"名字认不出来"的条数。

    那个计数是给 main 报"这条检查瞎了"用的：mutmut 哪天真换了命名格式，
    分组会静默地全空，于是这条检查永远报"没问题" —— 那正是门禁最坏的死法，
    所以认不出的名字必须变成红，不能变成"跳过"。
    """
    per_function: dict[str, list[str]] = {}
    unrecognised = 0
    for _path, mutant, status in _verdicts(mutants_dir):
        name = _MUTANT_SUFFIX_RE.sub("", mutant)
        if name == mutant:
            unrecognised += 1
            continue
        per_function.setdefault(name, []).append(status)
    return per_function, unrecognised


def no_tests_only_functions(mutants_dir: Path = MUTANTS_DIR) -> dict[str, int]:
    """变异体**全部**落在 no tests 桶里的函数 → 该函数有几条变异体。

    只看"全部"：一个函数只要有任意一条被判定过（杀死/存活），就说明映射是通的，
    它剩下的 no tests 只是"这个函数里有测试没走到的分支"，那是另一回事。
    """
    per_function, _ = _group_by_function(mutants_dir)
    return {
        name: len(statuses)
        for name, statuses in per_function.items()
        if statuses and all(s == "no tests" for s in statuses)
    }


def unrecognised_mutants(mutants_dir: Path = MUTANTS_DIR) -> int:
    """名字不是 `<模块>.<函数>__mutmut_N` 形状的变异体条数（正常恒为 0）。"""
    return _group_by_function(mutants_dir)[1]


def unregistered_no_tests(mutants_dir: Path = MUTANTS_DIR) -> dict[str, int]:
    """``no_tests_only_functions`` 里没在 ``KNOWN_NO_TESTS`` 登记的那些。"""
    return {
        name: count
        for name, count in no_tests_only_functions(mutants_dir).items()
        if name not in KNOWN_NO_TESTS
    }


def report_unregistered(offenders: dict[str, int]) -> None:
    """把未登记的「整函数无测试映射」打成人话，含两条处置路径。"""
    print()
    print(f"✗ 有 {len(offenders)} 个函数的变异体**全部**被判 no tests，"
          "但没在 KNOWN_NO_TESTS 里登记：")
    for name, count in sorted(offenders.items()):
        print(f"    {name}（{count} 条）")
    print("  两种可能，处置完全不同：")
    print("    ① 真没测试覆盖 → 登记进 scripts/baseline_check.py 的 "
          "KNOWN_NO_TESTS，并写清为什么；")
    print("    ② 映射过期（给**既有测试**加了断言去覆盖新函数，mutmut 不会重学映射）→")
    print("       rm mutants/mutmut-stats.json      # 触发一次全量重采，约 5 秒")
    print("       mutmut run <上面这些名字>…        # 点名重跑，拿到真判决")
    print("  这条不是 --update 能清掉的：它说的是映射，不是文档数字。")


def report_unrecognised(count: int) -> None:
    """变异体名字认不出来 —— 这条检查已经瞎了，别让它显示绿灯。"""
    print()
    print(f"✗ 有 {count} 条变异体的名字不是 `<模块>.<函数>__mutmut_N` 形状，"
          "按函数分组会全空、于是永远报「没问题」：")
    print("  多半是 mutmut 换了命名格式。改 scripts/baseline_check.py 的 "
          "_MUTANT_SUFFIX_RE，并同步 tests/unit/test_baseline_check.py 的夹具。")


# 文档里的基线那句话，跨行写成：
#   当前基线：383 个变异体
#     被杀死、71 个存活、5 个无测试覆盖，**变异分数 84.4%**。
# 中间的空白用分组原样保留，--update 时才不会把排版改乱。
DOC_RE = re.compile(
    r"(?P<killed>\d+)(?P<sp1>\s*个变异体\s*)被杀死、"
    r"(?P<survived>\d+)(?P<sp2>\s*个存活、)"
    r"(?P<no_tests>\d+)(?P<sp3>\s*个无测试覆盖[^\n]*?\*\*变异分数\s*)"
    r"(?P<score>\d+(?:\.\d+)?)(?P<sp4>%\*\*)",
    re.S,
)


class Counts:
    """一次跑批的结果。score 只算有测试覆盖的变异体，与 mutmut 一致。"""

    def __init__(self, killed: int, survived: int, no_tests: int) -> None:
        self.killed = killed
        self.survived = survived
        self.no_tests = no_tests

    @property
    def total(self) -> int:
        return self.killed + self.survived + self.no_tests

    @property
    def score(self) -> float:
        graded = self.killed + self.survived
        if graded == 0:
            return 0.0
        return round(self.killed / graded * 100, 1)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Counts):
            return NotImplemented
        return (
            self.killed == other.killed
            and self.survived == other.survived
            and self.no_tests == other.no_tests
        )

    def __str__(self) -> str:
        return f"{self.killed} / {self.survived} / {self.no_tests} → {self.score:.1f}%"


def read_mutants(mutants_dir: Path = MUTANTS_DIR) -> Counts | None:
    """从 mutants/src/*.meta 统计实际结果。没有 mutants/ 时返回 None。"""
    counted = False
    killed = survived = no_tests = 0
    for _path, _mutant, status in _verdicts(mutants_dir):
        counted = True
        if status == "killed":
            killed += 1
        elif status == "survived":
            survived += 1
        else:
            no_tests += 1
    return Counts(killed, survived, no_tests) if counted else None


def unchecked_breakdown(mutants_dir: Path = MUTANTS_DIR) -> Counter:
    """第三桶（既没杀也没活）到底由什么组成。

    这一桶里混着"真没测试覆盖""跑崩了（段错误）""跑太久（超时）"三种完全不同的
    毛病，处置方式也不同：第一种要补测试，第二三种要查环境或改排除。合成一个数字
    报出去，读者就会拿补测试去对付崩溃。
    """
    return Counter(
        status for _p, _m, status in _verdicts(mutants_dir)
        if status not in ("killed", "survived")
    )


def parse_doc(doc_path: Path = DEFAULT_DOC) -> Counts | None:
    """从文档里抓出记录的基线。没写基线就返回 None。"""
    match = DOC_RE.search(doc_path.read_text(encoding="utf-8"))
    if not match:
        return None
    return Counts(
        int(match.group("killed")),
        int(match.group("survived")),
        int(match.group("no_tests")),
    )


def update_doc(counts: Counts, doc_path: Path = DEFAULT_DOC) -> str:
    """把文档里的基线数字改成实际值，返回替换后的那句话（供打印）。"""
    text = doc_path.read_text(encoding="utf-8")

    def repl(match: re.Match[str]) -> str:
        return (
            f"{counts.killed}{match.group('sp1')}被杀死、"
            f"{counts.survived}{match.group('sp2')}"
            f"{counts.no_tests}{match.group('sp3')}"
            f"{counts.score:.1f}{match.group('sp4')}"
        )

    new_text, n = DOC_RE.subn(repl, text)
    if n != 1:
        raise SystemExit(f"在 {doc_path} 里找到 {n} 处基线，期望恰好 1 处，未改动")
    doc_path.write_text(new_text, encoding="utf-8")
    return DOC_RE.search(new_text).group(0).replace("\n", " ")


def stale_sources(mutants_dir: Path = MUTANTS_DIR) -> list[str]:
    """找出比最近一次跑批还新的源码/测试文件 —— 基线可能已经过期。"""
    metas = sorted(glob.glob(str(mutants_dir / "src" / "*.meta")))
    if not metas:
        return []
    last_run = max(os.path.getmtime(p) for p in metas)

    newer: list[str] = []
    for pattern in ("src/**/*.py", "tests/**/*.py"):
        for path in sorted(REPO_ROOT.glob(pattern)):
            if path.stat().st_mtime > last_run:
                newer.append(str(path.relative_to(REPO_ROOT)))
    return newer


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--doc", type=Path, default=DEFAULT_DOC, help=f"要核对的文档（默认 {DEFAULT_DOC.name}）")
    parser.add_argument("--mutants", type=Path, default=MUTANTS_DIR, help="mutants 目录")
    parser.add_argument("--update", action="store_true", help="把实际数字写回文档")
    parser.add_argument("--json", action="store_true", help="输出 JSON，不打印人话")
    args = parser.parse_args(argv)

    actual = read_mutants(args.mutants)
    if actual is None:
        if args.json:
            print(json.dumps({"status": "no-data"}))
        else:
            print(f"没有找到 {args.mutants}/src/*.meta，跳过核对（先跑 make mutation）")
        return 0

    if args.update:
        offenders = unregistered_no_tests(args.mutants)
        unrecognised = unrecognised_mutants(args.mutants)
        if unrecognised:
            report_unrecognised(unrecognised)
            print("  数字先不写：分组都瞎了，写出去的数字没经过核对。")
            return 1
        if offenders:
            report_unregistered(offenders)
            print("  数字先不写：那些变异体还没判定，写出去的分数是误导。")
            return 1
        sentence = update_doc(actual, args.doc)
        print(f"已把 {args.doc.name} 的基线更新为：{sentence}")
        return 0

    recorded = parse_doc(args.doc)
    newer = stale_sources(args.mutants)
    unregistered = unregistered_no_tests(args.mutants)
    unrecognised = unrecognised_mutants(args.mutants)

    if args.json:
        print(
            json.dumps(
                {
                    "status": "ok" if recorded == actual else "stale",
                    "actual": {"killed": actual.killed, "survived": actual.survived, "no_tests": actual.no_tests, "score": actual.score},
                    "recorded": None
                    if recorded is None
                    else {"killed": recorded.killed, "survived": recorded.survived, "no_tests": recorded.no_tests, "score": recorded.score},
                    "stale_sources": newer,
                    "no_tests_only_unregistered": unregistered,
                    "unrecognised_mutants": unrecognised,
                },
                ensure_ascii=False,
            )
        )
        return 0 if (
            recorded == actual and not unregistered and not unrecognised
        ) else 1

    print(f"实际（mutants/）：{actual}")
    breakdown = unchecked_breakdown(args.mutants)
    if breakdown:
        label = {"no tests": "无测试", "segfault": "段错误", "timeout": "超时",
                 "skipped": "跳过", "suspicious": "可疑",
                 "check was interrupted by user": "被中断"}
        parts = "、".join(
            f"{label.get(status, status)} {count}"
            for status, count in sorted(breakdown.items())
        )
        print(f"  第三桶 {sum(breakdown.values())} 条的构成：{parts}"
              "（只有「无测试」是覆盖盲区；崩溃与超时得另查，别拿补测试去对付）")
    if unrecognised:
        report_unrecognised(unrecognised)
    if unregistered:
        report_unregistered(unregistered)
    if recorded is None:
        print(f"{args.doc.name} 里没找到基线那句话 —— 按当前结果应写成：")
        print(f"  当前基线：{actual.killed} 个变异体被杀死、{actual.survived} 个存活、")
        print(f"  {actual.no_tests} 个无测试覆盖，**变异分数 {actual.score:.1f}%**。")
        return 1

    print(f"文档（{args.doc.name}）：{recorded}")
    if recorded == actual:
        print("✓ 基线一致")
    else:
        print("✗ 文档里的基线已过期。应改为：")
        print(
            f"  {actual.killed} 个变异体被杀死、{actual.survived} 个存活、"
            f"{actual.no_tests} 个无测试覆盖，**变异分数 {actual.score:.1f}%**"
        )
        print(f"  改法：python scripts/baseline_check.py --update")

    if newer:
        # mtime 只说明"动过"，不代表内容真变了（git checkout 也会刷新它），
        # 所以这里是提醒不是判据 —— 话要说清楚，免得又制造一个假信号。
        print(
            f"\n⚠ 有 {len(newer)} 个文件的修改时间比上次跑批还新"
            f"（mtime 只说明动过，内容可能没变），想确认就重跑 make mutation："
        )
        for name in newer[:10]:
            print(f"    {name}")
        if len(newer) > 10:
            print(f"    …还有 {len(newer) - 10} 个")
    return 0 if (
        recorded == actual and not unregistered and not unrecognised
    ) else 1


if __name__ == "__main__":
    sys.exit(main())
