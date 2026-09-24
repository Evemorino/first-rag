#!/usr/bin/env python3
"""把"存活的变异体"手工打进真实源码重跑测试，按族报告到底有没有测试抓得住。

为什么需要它
------------
mutmut 报"存活"只说明一件事：**在它自己的 trampoline 机制下**没有测试响。那个机制
不给任何测试制造区分机会 —— 它给每个变异体生成整份函数副本，原字面量一直留在文件
里，所以"读源码文本断言字面量"这类测试对它结构性无效。于是"这条已被杀掉了"这句话
必须先说清用的是哪个 oracle，否则就是在写一条下一个人会当真的假话。

这个工具就是另一个 oracle：把变异**真的**打进 src/（就像人或 AI 改错那样），跑测试，
看响不响，然后按字节还原。两者结论不一致的条目就是"机制差异"，单独成族列出。

安全
----
它会写 src/，所以：跑之前要求 `src/` 工作区干净（有未提交改动就可能被还原动作覆盖
掉）；每条跑完立刻按字节还原并核验，不匹配就当场停。产品源目录（宪法 V）一个字节都
不碰。

用法::

    python scripts/mutant_recheck.py --summary          # 只分类，不改源码（默认）
    python scripts/mutant_recheck.py --family 程序读的字符串 --run
    python scripts/mutant_recheck.py --run --limit 20
    python scripts/mutant_recheck.py --json

退出码：0 正常；2 前置条件不满足（mutants/ 不在、工作区脏）。
"""

from __future__ import annotations

import argparse
import atexit
import difflib
import json
import re
import signal
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MUTANTS = REPO_ROOT / "mutants"

# 判据来自 mutmut 自己，不在本地重抄一套退出码含义。
from mutmut.stats import status_by_exit_code  # noqa: E402

FILENAME_STRING = re.compile(r"[\"'][^\"']*\.(json|md|txt|csv|lock)[\"']")


@dataclass
class Survivor:
    mutant: str
    module: str
    func: str
    hunks: list[tuple[str, str]] = field(default_factory=list)

    @property
    def pair(self) -> str:
        return "\n".join(r + a for r, a in self.hunks)


def bodies(text: str) -> dict[str, list[str]]:
    """{函数名: 去掉 def 行的函数体行}。

    def 行必须丢掉：插桩副本里的名字带着 mutmut 的改名（x_foo__mutmut_orig），
    跟真实源码的 `def foo(` 永不相等 —— 上一版探针就是因为这个全军覆没。
    """
    out: dict[str, list[str]] = {}
    for match in re.finditer(r"^[ \t]*def ([^\s(]+)\(", text, re.M):
        rest = text[match.start():]
        end = re.search(r"\n[ \t]*(?:def |mutants_\w+\[)", rest[1:])
        block = rest[:end.start() + 1] if end else rest
        out[match.group(1)] = block.splitlines()[1:]
    return out


def hunks_between(orig_lines, variant_lines) -> list[tuple[str, str]]:
    pairs = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
            None, orig_lines, variant_lines).get_opcodes():
        if tag == "equal":
            continue
        removed = "\n".join(orig_lines[i1:i2])
        added = "\n".join(variant_lines[j1:j2])
        if removed.strip() or added.strip():
            pairs.append((removed, added))
    return pairs


def load_survivors(mutants_dir: Path | None = None) -> list[Survivor]:
    """读 .meta 判定 + 插桩副本，算出每条存活变异体的真实改动。

    默认值不能在签名里写死 `= MUTANTS`：那会在 import 时绑死，测试没法把
    它指到临时目录上（`gate_selftest.check_config` 踩过同一条）。
    """
    mutants_dir = MUTANTS if mutants_dir is None else mutants_dir
    metas = sorted((mutants_dir / "src").rglob("*.meta"))
    survivors: list[Survivor] = []
    for meta_path in metas:
        module = meta_path.name[: -len(".meta")]
        code_path = meta_path.with_suffix("")
        if not code_path.is_file():
            continue
        verdicts = json.loads(meta_path.read_text(encoding="utf-8"))
        bs = bodies(code_path.read_text(encoding="utf-8"))
        for mutant, code in verdicts.get("exit_code_by_key", {}).items():
            if status_by_exit_code.get(code) != "survived":
                continue
            stem, num = mutant.split(".", 1)[1].rsplit("__mutmut_", 1)
            orig, variant = bs.get(f"{stem}__mutmut_orig"), bs.get(f"{stem}__mutmut_{num}")
            if orig is None or variant is None:
                survivors.append(Survivor(mutant, module, stem, []))
                continue
            survivors.append(Survivor(mutant, module, stem, hunks_between(orig, variant)))
    return survivors


def changed_literals(removed: str, added: str) -> list[str]:
    """只看**真的变了**的引号串。

    不能扫整段 diff：`"content": ("你是检索助手…")` 这种行里，`"content"` 是
    没动的键名，扫全段就会把一条纯文案变异归进"键名被改" —— 第一版就是这么把
    33 条文案错算进 60 条键名的。
    """
    def literals(text: str) -> set[str]:
        return set(re.findall(r"[\"']([^\"']{2,})[\"']", text or ""))
    return list(literals(removed) ^ literals(added))


def program_read(removed: str, added: str) -> str | None:
    """变了的那个字符串是不是**程序读的**（键名、枚举值、分隔符）。

    判据是形状不是位置：标识符形状（无空格无中文）就是程序在读。mutmut 会把字符串
    变异写成 "XXcollected_atXX"，所以先剥掉 XX 包装再看内核。
    """
    for token in changed_literals(removed, added):
        core = re.sub(r"(?i)^xx|xx$", "", token)
        if core and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", core):
            return core
    return None


def human_prose(removed: str, added: str) -> bool:
    """变了的那个字符串是不是**给人读的**：带空格或中日韩文字。

    上一轮分诊把 84 条一锅端进"文案，不值得杀"，其中约七成其实是程序读的键名。
    所以这一族必须由工具自己判出来，否则"纯文案有多少条"只有我那个一次性探针
    能复现 —— 那等于没有来源。
    """
    for token in changed_literals(removed, added):
        core = re.sub(r"(?i)^xx|xx$", "", token)
        if re.search(r"[\u4e00-\u9fff]", core):
            return True
        if " " in core.strip() and len(core) > 8:
            return True
    return False


def classify(item: Survivor) -> str:
    if not item.hunks:
        return "探针取不到改动（未判）"
    pair = item.pair
    if re.search(r"[\x1e\x00]", pair):
        return "git 控制字符（记录/字段分隔符）"
    if re.search(r"max_chars|remaining|used \+=|total <=", pair) or (
            re.search(r"[<>]=?", pair) and re.search(r"max|limit|>=|<=|>|<", pair)):
        return "边界与比较符"
    if "ensure_ascii" in pair:
        return "ensure_ascii（数据相关）"
    if FILENAME_STRING.search(pair):
        return "数据文件名大小写"
    if any(program_read(removed, added) for removed, added in item.hunks):
        return "程序读的字符串被改（键名/枚举值）"
    if re.search(r"=\s*(None|False)\b|,\s*None[,)]|,\s*\)", pair) and re.search(
            r"=\s*(True|False|\d+|f?\"|config|\{|\[)|\{\}", pair):
        return "关键字参数/默认值/计算值被动过"
    if re.search(r"indent=|encoding=", pair):
        return "快照排版与编码"
    if re.search(r"msvcrt|except ImportError", pair):
        return "平台不可达（Windows 分支）"
    if any(human_prose(removed, added) for removed, added in item.hunks):
        return "纯文案（人读的字符串：提示语/usage/日志）"
    return "没抓住形状（未判）"


def apply_hunks(text: str, item: Survivor) -> str | None:
    """按块替换。锚点在文件里不唯一时返回 None —— 那不能算"判过了"。"""
    out = text
    for removed, added in item.hunks:
        if not removed.strip():
            continue
        if out.count(removed) != 1:
            return None
        out = out.replace(removed, added, 1)
    return out if out.encode() != text.encode() else None


def tree_is_clean() -> tuple[bool, str]:
    proc = subprocess.run(["git", "status", "--porcelain", "src/"],
                          cwd=REPO_ROOT, capture_output=True, text=True)
    return proc.returncode == 0 and not proc.stdout.strip(), proc.stdout.strip()


_PENDING: tuple[Path, bytes] | None = None


def restore_pending() -> None:
    """把"正被改写的那个文件"还原。中断（Ctrl-C / kill）时也必须做这件事。"""
    global _PENDING
    if _PENDING is None:
        return
    path, original = _PENDING
    _PENDING = None
    if path.read_bytes() != original:
        path.write_bytes(original)
        print(f"!! 中断在 {path}，已按字节还原", file=sys.stderr)


def recheck(item: Survivor, pytest_args: list[str]) -> str:
    src = REPO_ROOT / "src" / item.module
    original = src.read_bytes()
    mutated = apply_hunks(original.decode("utf-8"), item)
    if mutated is None:
        return "锚点不唯一（未判）"
    src.write_text(mutated, encoding="utf-8")
    global _PENDING
    _PENDING = (src, original)
    try:
        proc = subprocess.run(
            ["uv", "run", "--no-sync", "python", "-m", "pytest", *pytest_args],
            cwd=REPO_ROOT, capture_output=True, text=True)
        verdict = "手工可杀" if proc.returncode else "手工也杀不掉"
    finally:
        src.write_bytes(original)
        _PENDING = None
    if src.read_bytes() != original:
        print(f"!! {src} 还原失败，立刻停在这里", file=sys.stderr)
        raise SystemExit(2)
    return verdict


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run", action="store_true",
                        help="真打源码跑测试（默认只分类，不改任何文件）")
    parser.add_argument("--family", action="append", metavar="族名",
                        help="只处理这些族（可重复；子串匹配）")
    parser.add_argument("--limit", type=int, help="最多处理多少条")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--summary", action="store_true", help="只打印分族计数")
    parser.add_argument("pytest_args", nargs="*", default=[],
                        help="传给 pytest 的额外参数（放在 -- 之后）")
    args = parser.parse_args(argv)

    if not (MUTANTS / "src").is_dir():
        print("没有 mutants/src/ —— 先跑 make mutation", file=sys.stderr)
        return 2
    items = load_survivors()
    counts = Counter(classify(i) for i in items)
    if args.json:
        print(json.dumps({"survivors": len(items),
                          "families": dict(counts)}, ensure_ascii=False))
        return 0
    print(f"存活变异体 {len(items)} 条，按族：")
    for name in sorted(counts):
        print(f"  {counts[name]:4d}  {name}")
    if args.summary or not args.run:
        print("\n（只分类，没动源码。要真打源码跑测试：--run）")
        return 0

    wanted = args.family or []
    selected = [i for i in items
                if not wanted or any(w in classify(i) for w in wanted)]
    if args.limit:
        selected = selected[:args.limit]
    clean, dirty = tree_is_clean()
    if not clean:
        print(f"src/ 有未提交改动，拒绝跑（它会改写源码）：\n{dirty}", file=sys.stderr)
        return 2
    # 改写源码的工具必须假设自己会被打断：atexit + 信号各留一道还原。
    atexit.register(restore_pending)
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_a: (restore_pending(), sys.exit(130)))

    results = Counter()
    by_family: dict[str, Counter] = defaultdict(Counter)
    pytest_args = args.pytest_args or ["-x", "-q", "--no-header", "-p", "no:cacheprovider"]
    for item in selected:
        verdict = recheck(item, pytest_args)
        family = classify(item)
        results[verdict] += 1
        by_family[family][verdict] += 1
        print(f"  {item.mutant:52s} {family:24s} {verdict}", flush=True)

    print(f"\n共 {len(selected)} 条：{dict(results)}")
    print("按族：")
    for family, counter in sorted(by_family.items()):
        print(f"  {family:26s} {dict(counter)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
