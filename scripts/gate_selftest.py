#!/usr/bin/env python3
"""门禁自检：给每个 pre-commit 钩子植入一个已知违规，看它到底红不红。

为什么需要它
------------
门禁是这样死掉的：某天有人改了 scripts/lint_layers.py，把检查循环写错了
（比如异常吞掉了所有问题）；或者改 .pre-commit-config.yaml 时把 files 过滤器
写宽了，该管的文件被排除掉。之后每次提交钩子都绿着 —— 绿不是因为代码没问题，
是因为门禁已经瞎了。而它自己的单元测试测的是函数，测不到"钩子在真实仓库里
跑起来到底拦不拦得住人"这件事。

我们这套门禁里已经发生过两次同类事故（mutmut 的 only_mutate 加了不生效、
post-commit 提醒开了 verbose 才看得见），每一次都是"配置写了，但没生效"。

这个脚本就是补最后一层：不测门禁内部逻辑，而是真的建一个临时仓库、真的植入
违规、真的跑 `pre-commit run <hook>`，然后断言它退出码非 0。每个钩子配一个
"必须红"的用例，再配一组"干净仓库必须绿"的对照组 —— 只测红不测绿，等于允许
门禁靠一直报错来假装自己在工作。

用法::

    python scripts/gate_selftest.py              # 跑全部用例
    python scripts/gate_selftest.py --hook crap  # 只跑某个钩子
    python scripts/gate_selftest.py --keep       # 保留临时仓库便于排查

退出码：0 全部符合预期；1 有用例不符；2 配置本身有问题。

注意：临时仓库建在 .gate-selftest/ 下（已 gitignore），跑完自动删。
必须建在仓库内部 —— 钩子的 entry 是 `uv run --no-sync python ...`，
uv 要向上找到 pyproject.toml 才能用项目的 .venv，丢到 /tmp 里就跑不起来了。
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG = REPO_ROOT / ".pre-commit-config.yaml"
GITIGNORE = REPO_ROOT / ".gitignore"
SCRIPTS = REPO_ROOT / "scripts"
TMP_ROOT = REPO_ROOT / ".gate-selftest"

# 临时仓库自带 pytest 配置，两个原因：
# 1. 不写它，pytest 会顺着目录往上找到本项目的 pyproject.toml，那份配置里的
#    testpaths = ["tests"] 会生效吗？不会 —— rootdir 变了，约束力也变了，
#    到时候钩子去收集 scripts/ 下的文件，报出一堆和用例无关的错误。
#    所以这里必须把本项目的 pytest 口径抄过来。
# 2. no:cacheprovider 省掉 .pytest_cache，临时仓库跑完就删，没必要留。
PYTEST_INI = "[pytest]\ntestpaths = tests\naddopts = -p no:cacheprovider\n"

# --- 夹具 ---

# 超过 --maxkb=512 的门槛即可，用可重复的字符省得造随机文件
BIG_FILE = "x" * (600 * 1024)

CONFLICT = "x = 1\n<<<<<<< HEAD\ny = 2\n=======\ny = 3\n>>>>>>> feature\n"

# 拆成两截拼：整段写出来会被密钥扫描器当成真私钥拦下，而这里只需要一个
# 能触发 detect-private-key 的文本形状。
FAKE_PEM = (
    "-----BEGIN RSA PRIVATE " + "KEY-----\n"
    "MIIBOgIBAAJBAKj34GkxFhD90vcNLYLInFEX6Ppy1tPf9Cnzj4p4WGeKLs1Pt8Qu\n"
    "-----END RSA PRIVATE " + "KEY-----\n"
)

# 同样拼接构造，而且必须拼：这个文件自己是本仓库扫描的对象，写全了等于自杀。
# 触发的是赋值式规则（配置/文档形态），不是 PEM。
LEAKY_CONFIG = "ARK_API_KEY: " + ("ar" + "k-") + "7" * 40 + "\n"

# 复杂度 12 左右 → CRAP = 12²×(1-0)³+12 = 156，远超阈值 30。
# 关键是"复杂"和"零覆盖"必须同时成立：复杂度 1 的函数哪怕零覆盖，
# CRAP 也只有 2，这个夹具就白造了（那正是 CRAP 的盲区）。
CRAPPY_SRC = """def grade(score, attend, late, extra, bonus, penalty, flag):
    if score > 90:
        base = "A"
    elif score > 80:
        base = "B"
    elif score > 70:
        base = "C"
    elif score > 60:
        base = "D"
    else:
        base = "F"
    if attend < 0.5:
        base = "F"
    if late > 3:
        base = "F"
    if extra:
        base = "A"
    if bonus:
        base = "A"
    if penalty:
        base = "F"
    if flag:
        base = "?"
    return base
"""

BIG_SRC = "\n".join(f"value_{i} = {i}" for i in range(320)) + "\n"

BOOM_TEST = "def test_boom():\n    assert False, '植入的失败用例'\n"


def coverage_xml(filename: str, lines: range | list[int], hits: int) -> str:
    """造一份最小 coverage.xml：只有一个模块，指定行都是同一个命中数。"""
    body = "\n".join(f'        <line number="{n}" hits="{hits}"/>' for n in lines)
    return (
        '<?xml version="1.0" ?>\n'
        '<coverage version="7.16.1" line-rate="0">\n'
        "  <sources><source>src</source></sources>\n"
        '  <packages><package name="." line-rate="0">\n'
        "    <classes>\n"
        f'      <class name="{filename}" filename="{filename}" line-rate="0">\n'
        "        <methods/>\n"
        f"        <lines>\n{body}\n        </lines>\n"
        "      </class>\n"
        "    </classes>\n"
        "  </package>\n"
        "</packages>\n</coverage>\n"
    )


# 对照组：一个什么毛病都没有的小仓库。它必须让所有钩子都绿 ——
# 只验证"该红时红"的话，一个永远报错的钩子也能通过自检。
CLEAN_FILES = {
    "src/ok.py": "VALUE = 1\n\n\ndef add(a, b):\n    return a + b\n",
    "tests/test_ok.py": "def test_ok():\n    assert True\n",
    "good.json": '{"a": 1}\n',
    "good.yaml": "a: 1\n",
    "coverage.xml": coverage_xml("ok.py", [1, 2, 4, 5], 1),
}

# 参与对照的钩子。fixer 类（end-of-file-fixer / trailing-whitespace）也在内：
# 干净文件它们不该动手，动手了说明规则写错了。
CLEAN_HOOKS = [
    "check-added-large-files",
    "check-merge-conflict",
    "check-json",
    "check-yaml",
    "check-ast",
    "end-of-file-fixer",
    "trailing-whitespace",
    "detect-private-key",
    "secret-scan",
    "lint-layers",
    "size-guard",
    "write-boundary",
    "pytest",
    "crap",
    "orphans",
]

# 钩子脚本对本仓库 src/ 模块的依赖，建临时仓库时要一起带过去。
# 只给需要的钩子复制，别一把全拷：临时仓库里多出一个零覆盖的 src/ 模块，
# orphans 那道对照实验就会红得莫名其妙。
SCRIPT_DEPS = {"secret-scan": ("src/secret_patterns.py",)}


@dataclass(frozen=True)
class Case:
    """一个自检用例：在某个钩子上植入违规，断言它会红（或对照：必须绿）。"""

    hook: str
    title: str
    files: dict[str, str] = field(default_factory=dict)
    expect: str = "red"          # "red" 期望非 0；"green" 期望 0
    why: str = ""                # 为什么这样能触发它 —— 半年后看的人会问
    in_merge: bool = False       # 造一个"正在 merge"的 git 状态（见下）
    # 钩子的脚本 import 了本仓库的哪些模块，就得一起带进临时仓库。
    # 不声明的话临时仓库里它会直接 ImportError，而"跑不起来"在非 0 退出码上
    # 长得和"拦住了"一模一样 —— 干净对照组会因此假绿失败（secret-scan 第一版
    # 就是这么暴露出依赖 src/secret_patterns.py 的）。
    repo_files: tuple[str, ...] = ()

    @property
    def slug(self) -> str:
        # 带上 title 的短哈希：同一个钩子可以有多条同期望的用例（lint-layers
        # 就有两条「必须红」——一条测跨层 import，一条测未登记的新目录）。
        # 只用 hook-expect 当目录名的话，它们会共用同一个临时仓库，第二条把
        # 第一条覆盖掉；--keep 时更糟，只能看到最后一条。
        digest = hashlib.sha1(self.title.encode("utf-8")).hexdigest()[:6]
        return f"{self.hook}-{self.expect}-{digest}"


CASES: list[Case] = [
    Case("check-added-large-files", "大文件混进提交",
         {"blob.bin": BIG_FILE}, "red",
         "--maxkb=512：600KB 必须被拦下。注意这个钩子只查**新增**（staged as A）"
         "的文件，已提交的老文件它不管 —— 所以临时仓库只 add 不 commit"),
    Case("check-merge-conflict", "冲突标记没清",
         {"src/conflicted.py": CONFLICT}, "red", in_merge=True,
         why="带 <<<<<<< 的文件提交上去等于把仓库弄坏。这个钩子只在 .git 里存在 "
             "MERGE_MSG + MERGE_HEAD 时才干活（即 merge/rebase 中），平时永远绿 "
             "—— 所以用例必须伪造出 merge 状态，否则测的是空气"),
    Case("check-json", "JSON 语法错",
         {"bad.json": '{"a": 1,\n'}, "red", "缺右括号"),
    Case("check-yaml", "YAML 语法错",
         {"bad.yaml": "a: [1, 2\n"}, "red", "缺右中括号"),
    Case("check-ast", "Python 语法错",
         {"src/broken.py": "def f(:\n    pass\n"}, "red", "根本 parse 不过"),
    Case("end-of-file-fixer", "文件末尾缺换行",
         {"src/nonewline.py": "x = 1"}, "red",
         "POSIX 里这种文件会让 diff 和 cat 串味"),
    Case("trailing-whitespace", "行尾空格",
         {"src/ws.py": "x = 1   \n"}, "red", "无意义的行尾空白"),
    Case("detect-private-key", "私钥进库",
         {"key.pem": FAKE_PEM}, "red",
         "宪法 V 唯一能自动守住的一环：密钥 MUST NOT 出现在任何提交物里"),
    Case("secret-scan", "API 令牌写进了配置模板",
         {"config/leaky.yaml": LEAKY_CONFIG}, "red",
         repo_files=SCRIPT_DEPS["secret-scan"],
         why="上一个钩子守不住的那一半：detect-private-key 只认 PEM 私钥，看不见 "
             "API 令牌。2026-09-24 真发生过一把 46 字符的方舟密钥躺在被跟踪的 "
             ".env.example 里、14 个钩子全绿。夹具必须拼接构造 —— 写全了这段，"
             "这个文件自己就会被这道钩子扫出来"),
    Case("lint-layers", "插件 import 编排层",
         {"src/plugins/demo.py": "from src import distill\n"}, "red",
         "插件只允许 src.plugins 与 src.config，反向依赖会让改一处全库回归"),
    Case("lint-layers", "新目录没登记就免检",
         {"src/retrieval/rerank.py": "x = 1\n"}, "red",
         why="位置登记的兜底必须是「拒绝」，不能是「编排层」—— 编排层的 ALLOWED "
             "是 None（不限制），把最宽松的那一层当兜底，等于每个新目录默认免检，"
             "而「新建目录」恰恰是唯一需要门禁的时刻。夹具故意用一个零 import 的"
             "文件：它登记过的话必然绿，所以红只能来自位置。这条钉的就是那个默认"
             "值 —— 把 layer_of() 的兜底改回 return \"编排层\"，它必须变绿"),
    Case("size-guard", "单文件超 300 SLOC",
         {"src/big.py": BIG_SRC}, "red",
         "320 行代码 > 300 SLOC 上限（用 SLOC 是为了不罚注释写得多的文件）"),
    Case("write-boundary", "往产品源目录写东西",
         {"src/plugins/demo.py": '"""A collector that forgot it is read-only."""\n'
                                 "from pathlib import Path\n"
                                 "OUT = Path.home() / '.codex' / 'sessions'\n"
                                 "\n"
                                 "\n"
                                 "def tidy() -> None:\n"
                                 '    (OUT / "rollout.jsonl").write_text("boom")\n'},
         "red",
         why="宪法 V 标了 NON-NEGOTIABLE 却没有机械门禁的那条，而且违规不可恢复"
             "—— 产品目录里是用户真实的会话记录。形状很稳定：路径来自 Path.home()"
             "，动作是个写调用。登记豁免救不了它（硬法），所以这条红了就只能改代码"),
    Case("write-boundary", "绕过登记好的配置写入点",
         {"src/scope.py": '"""The sanctioned config writer."""\n'
                          "from pathlib import Path\n"
                          "\n"
                          "\n"
                          "def save(matrix: dict) -> Path:\n"
                          "    path = Path('config') / 'scope.json'\n"
                          '    path.write_text("{}", encoding="utf-8")\n'
                          "    return path\n",
          "src/sneaky.py": '"""A pipeline that reaches past the CLI."""\n'
                           "from src.scope import save as write_scope\n"
                           "\n"
                           "\n"
                           "def run() -> None:\n"
                           "    write_scope({})\n"},
         "red",
         why="登记表钉的是「写入点在 src/scope.py:save」，光这样挡不住别的模块 "
             "import 这个函数去写 config/ —— 写入点名字都没变，门禁照样绿。"
             "这条用例走的是带 as 别名的导入形状，最容易漏认的一种"),
    Case("pytest", "测试挂了",
         {"tests/test_boom.py": BOOM_TEST}, "red",
         "一个必失败的断言；这条挂了等于提交门禁的底座没了"),
    Case("crap", "复杂函数零覆盖",
         {"src/messy.py": CRAPPY_SRC,
          "coverage.xml": coverage_xml("messy.py", range(2, 23), 0)}, "red",
         "复杂度 ~12 且零覆盖 → CRAP 156，远超阈值 30"),
    Case("orphans", "模块一行都没跑过",
         {"src/orphan.py": "x = 1\n",
          "coverage.xml": coverage_xml("orphan.py", [1], 0)}, "red",
         "CRAP 抓不到它（简单函数零覆盖 CRAP 只有 2），只有这个钩子会喊"),
] + [
    Case(hook, "干净仓库不该报", CLEAN_FILES, "green",
         "对照组：什么都不违规时，这个钩子必须放行",
         # 同样的理由：不在 merge 中的时候 check-merge-conflict 直接返回 0，
         # 那样的"绿"是假的，证明不了任何事。
         in_merge=(hook == "check-merge-conflict"),
         repo_files=SCRIPT_DEPS.get(hook, ()))
    for hook in CLEAN_HOOKS
]


@dataclass
class Result:
    case: Case
    state: str          # "red" / "green" / "missing" / "error"
    ok: bool
    detail: str = ""


# --- 临时仓库 ---


def _git(dest: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(dest), *args],
        check=True, capture_output=True, text=True,
    )


def build_repo(dest: Path, files: dict[str, str], in_merge: bool = False,
               repo_files: tuple[str, ...] = ()) -> None:
    """建一个能跑 pre-commit 的最小 git 仓库，并把夹具写进去。

    只 `git add` 不 commit，是故意的：真实提交时钩子面对的就是暂存区，
    而 check-added-large-files 只查"新增"文件 —— 一旦 commit 过，
    `git diff --cached --diff-filter=A` 就空了，那个钩子会（正确地）放行。
    """
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)

    shutil.copy(CONFIG, dest / ".pre-commit-config.yaml")
    # .gitignore 必须一起带过来：pytest 钩子会往工作区写 coverage.xml，
    # 而 pre-commit 只要发现"钩子改了被跟踪的文件"就判失败。真实仓库之所以
    # 一直是绿的，全靠 coverage.xml 在 .gitignore 里 —— 这条依赖从来没写在
    # 任何地方，是这次自检撞出来的。
    if GITIGNORE.is_file():
        shutil.copy(GITIGNORE, dest / ".gitignore")
    # 复制而不是软链：软链指向目录时，detect-private-key 之类的钩子会尝试
    # 按文件读它，炸在 IsADirectoryError 上，报出一堆和用例无关的错误。
    shutil.copytree(SCRIPTS, dest / "scripts",
                    ignore=shutil.ignore_patterns("__pycache__"))
    # 钩子的脚本 import 了本仓库的模块时，得按声明一起带过来（见 SCRIPT_DEPS）：
    # 少了它，脚本以 ImportError 非 0 退出，而非 0 在非 0 上长得和"拦住了"一样。
    for rel in repo_files:
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO_ROOT / rel, target)
    (dest / "pytest.ini").write_text(PYTEST_INI, encoding="utf-8")

    for rel, text in files.items():
        path = dest / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    _git(dest, "init", "-q")
    _git(dest, "add", "-A")

    if in_merge:
        # check-merge-conflict 靠这两个文件的存在判断"是不是在 merge 中"
        git_dir = dest / ".git"
        (git_dir / "MERGE_MSG").write_text("Merge branch 'feature'\n",
                                           encoding="utf-8")
        (git_dir / "MERGE_HEAD").write_text("0" * 40 + "\n", encoding="utf-8")


def run_hook(dest: Path, hook: str) -> tuple[str, str]:
    """在临时仓库里真跑一次钩子，返回 (状态, 输出)。"""
    env = dict(_CLEAN_ENV)
    proc = subprocess.run(
        [sys.executable, "-m", "pre_commit", "run", hook, "--all-files"],
        cwd=dest, capture_output=True, text=True, env=env,
    )
    out = f"{proc.stdout}\n{proc.stderr}"
    # 配置里没有这个钩子时，pre-commit 也是非 0 退出 —— 那不是"门禁生效了"，
    # 是门禁没了。必须和真正的拦截区分开，否则删掉钩子也能自检通过。
    if "No hook with id" in out:
        return "missing", out
    if proc.returncode == 0:
        return "green", out
    return "red", out


_CLEAN_ENV = {
    key: value
    for key, value in __import__("os").environ.items()
    if key not in ("SKIP", "PYTEST_ADDOPTS")
} | {"PYTHONDONTWRITEBYTECODE": "1"}


def run_case(case: Case, keep: bool = False) -> Result:
    dest = TMP_ROOT / case.slug
    try:
        build_repo(dest, case.files, case.in_merge, case.repo_files)
        state, out = run_hook(dest, case.hook)
        ok = state == case.expect
        return Result(case, state, ok, "" if ok else out.strip())
    finally:
        if not keep and dest.exists():
            shutil.rmtree(dest, ignore_errors=True)


# --- 配置体检 ---


def check_config(config: Path | None = None) -> list[str]:
    """配置里的 local 钩子，entry 指向的脚本真的存在吗。

    脚本改名或挪走之后，钩子会以一个没人注意的方式失效 —— 它依然列在配置里，
    只是再也拦不住任何东西。

    config 留空时用模块级的 CONFIG。注意不能写成默认参数 `config=CONFIG`：
    那样会在 import 时就绑定死，测试没法把它指到临时文件上（orphan_check 踩过）。
    """
    problems: list[str] = []
    config = config if config is not None else CONFIG
    if not config.is_file():
        return [f"找不到 {config}"]

    data = yaml.safe_load(config.read_text(encoding="utf-8")) or {}
    for repo in data.get("repos", []):
        for hook in repo.get("hooks", []):
            entry = str(hook.get("entry", ""))
            if not entry.endswith(".py"):
                continue
            script = entry.split()[-1]
            if not (REPO_ROOT / script).is_file():
                problems.append(
                    f"钩子 {hook.get('id')} 的 entry 指向 {script}，但这个文件不存在"
                )
    return problems


# --- 报告 ---


def render(results: list[Result]) -> str:
    lines: list[str] = []
    for result in results:
        mark = "✓" if result.ok else "✗"
        want = "红" if result.case.expect == "red" else "绿"
        got = {"red": "红", "green": "绿", "missing": "配置里没有这个钩子",
               "error": "跑不起来"}[result.state]
        lines.append(f"{mark} {result.case.title:<20} {result.case.hook:<26} "
                     f"期望{want} 实际{got}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--hook", action="append", metavar="ID",
                        help="只跑指定钩子（可重复）")
    parser.add_argument("--keep", action="store_true",
                        help=f"保留临时仓库（在 {TMP_ROOT.name}/ 下）")
    parser.add_argument("--why", action="store_true",
                        help="打印每个用例为什么这样设计")
    args = parser.parse_args(argv)

    problems = check_config()
    if problems:
        print("✗ 配置体检没过：", file=sys.stderr)
        for problem in problems:
            print(f"    {problem}", file=sys.stderr)
        return 2

    cases = CASES
    if args.hook:
        wanted = set(args.hook)
        cases = [case for case in CASES if case.hook in wanted]
        unknown = wanted - {case.hook for case in CASES}
        if unknown:
            print(f"没有这些钩子的用例：{', '.join(sorted(unknown))}",
                  file=sys.stderr)
            return 2

    if args.why:
        for case in cases:
            print(f"{case.hook:<26} {case.title}\n    {case.why}")

    # 上一次 --keep 留下的残留会污染这次的判断，先清干净
    if not args.keep and TMP_ROOT.exists():
        shutil.rmtree(TMP_ROOT, ignore_errors=True)

    print(f"门禁自检：{len(cases)} 个用例（临时仓库在 {TMP_ROOT.name}/）\n")
    results = [run_case(case, keep=args.keep) for case in cases]
    print(render(results))

    failed = [r for r in results if not r.ok]
    print()
    if failed:
        print(f"✗ {len(failed)}/{len(results)} 个用例不符预期：", file=sys.stderr)
        for result in failed:
            print(f"\n--- {result.case.hook} / {result.case.title} ---",
                  file=sys.stderr)
            print(result.detail or "（无输出）", file=sys.stderr)
        print(
            "\n门禁如果连植入的违规都抓不到，它平时那些绿灯就没有意义了。",
            file=sys.stderr,
        )
        return 1

    print(f"✓ {len(results)} 个用例全部符合预期：该红的都红了，该绿的都绿了。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
