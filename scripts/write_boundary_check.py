#!/usr/bin/env python3
"""写入边界门禁：宪法 V 的 NON-NEGOTIABLE 那条。

运行时只准写 data/ 与 notes/；产品源目录（~/.claude、~/.codex、~/.kimi-code、
~/.trae-cn）**严格只读**。这条约束违规的代价是「把用户真实的会话记录改了」，
而它恰好是本项目里唯一没有机械门禁的硬约束 —— 全靠人记得。

静态 AST 扫 src/，绝不执行被测代码（跑一遍 sync 要 .env + Qdrant + 真调 Ark，
进不了毫秒级提交门禁；而且门禁自己不该有能力写坏东西）。

用法：
    python scripts/write_boundary_check.py            # 只检查
    python scripts/write_boundary_check.py --list     # 打印登记与规则
"""

from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass
from pathlib import Path

SOURCE_ROOT = Path("src")

# 形如 <path 表达式>.write_text(...) / .mkdir() / .unlink() 的写操作
WRITE_METHODS = {"write_text", "write_bytes", "mkdir", "touch", "unlink"}
# 形如 os.remove(...) / shutil.rmtree(...) 的写操作
WRITE_MODULE_FUNCTIONS = {"os": {"remove", "rename", "replace"},
                          "shutil": {"rmtree", "move", "copy", "copy2"}}


@dataclass(frozen=True)
class Site:
    """一个登记过的写入点：它实际会碰到哪里，以及凭什么允许。"""

    target: str
    why: str


@dataclass(frozen=True)
class Problem:
    """一处违规：文件、写入点、行号、违反了哪条规则、以及怎么说才够用人。"""

    file: str
    lineno: int
    rule: str
    detail: str
    site: str = ""


def scan(root: Path, registry: dict | None = None) -> list[Problem]:
    """扫一棵 src/ 树，返回全部违规。

    `registry` 是写入点登记表；默认用本模块那份（真实仓库的那份）。
    做成参数是为了让单测能喂一棵临时树进来。
    """
    entries = WRITE_SITES if registry is None else registry
    problems: list[Problem] = []
    sites: set[str] = set()
    files: set[str] = set()
    for path in sorted(root.rglob("*.py")):
        rel = path.as_posix()
        files.add(rel)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        except (OSError, SyntaxError) as e:
            problems.append(Problem(rel, 0, "unreadable", f"读不了/解析不了：{e}"))
            continue
        found = write_sites(tree, rel)
        sites.update(site for site, _ in found)
        products = _product_violations(rel, found, tree)
        problems.extend(products)
        # 产品根那条是硬法，登记也不许 —— 同一个点就别再报一次「未登记」，
        # 否则等于给出一条根本不该被采纳的修复建议。
        hard = {p.site for p in products}
        problems.extend(p for p in _unregistered(found, entries)
                        if p.site not in hard)
    problems.extend(_stale(sites, files, entries))
    return problems


def _stale(sites: set[str], files: set[str], registry: dict) -> list[Problem]:
    """登记里有、但那个文件里已经没有这个写入点了。

    只看**这棵树里存在的文件**：整文件不在（对照组只有一两个夹具、或只扫一个
    子目录）不代表登记死了，报出来会让门禁在任何非全量扫描上都永远红。
    """
    return [Problem(site.split(":")[0], 0, "stale-registry",
                    "登记还在，但这个文件里已经没有这个写入点了 —— 留着它等于"
                    "给那个 target 发了张永久的免检条。删掉，或改回实际的样子",
                    site=site)
            for site in sorted(registry)
            if site not in sites and site.split(":")[0] in files]


def product_roots(tree: ast.AST) -> set[str]:
    """模块级常量中，值由 `Path.home()` / `expanduser` 派生的那些名字。

    插件们都是「顶层算一个常量、函数里用它」的写法（`SESSIONS_DIR =
    Path.home() / ".codex" / "sessions"`），所以认名字就够了 —— 也正好是
    产品根的实际形状。
    """
    roots: set[str] = set()
    for node in ast.iter_child_nodes(tree):
        if not isinstance(node, ast.Assign):
            continue
        dumped = ast.dump(node.value)
        if "home" not in dumped and "expanduser" not in dumped:
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                roots.add(target.id)
    return roots


def _path_expression(node: ast.Call) -> ast.expr | None:
    """这个写调用里，代表「写到哪」的那一段表达式。"""
    func = node.func
    if isinstance(func, ast.Attribute) and func.attr in WRITE_METHODS:
        return func.value
    if node.args:
        return node.args[0]
    return None


def _is_write(node: ast.Call) -> bool:
    func = node.func
    if isinstance(func, ast.Name) and func.id == "open":
        return _writes(_mode_of(node, 1))
    if not isinstance(func, ast.Attribute):
        return False
    if func.attr in WRITE_METHODS:
        return True
    if func.attr == "open":
        # Path.open("a+") / io.open(p, "w")：mode 是第一个位置参数
        return _writes(_mode_of(node, 0))
    receiver = func.value
    return (isinstance(receiver, ast.Name)
            and func.attr in WRITE_MODULE_FUNCTIONS.get(receiver.id, set()))


def _mode_of(node: ast.Call, index: int) -> str | None:
    """open() 的 mode：位置参数或 `mode=`，拿不到字面量时返回 None。

    None 一律按「会写」处理。门禁宁可错报一次让人说清楚，也不要静默放过
    `open(p, mode)` 这种看不懂的写法 —— 放过一次，就是宪法 V 少一道闸。
    """
    for kw in node.keywords or ():
        if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
            return str(kw.value.value)
    if len(node.args) > index:
        arg = node.args[index]
        # 有这一位但不是字面量（`open(p, mode)`）：看不懂，按最危险的算
        return str(arg.value) if isinstance(arg, ast.Constant) else None
    return "r"  # 压根没写 mode —— 内置 open 与 Path.open 的默认都是读


def _writes(mode: str | None) -> bool:
    return mode is None or any(c in mode for c in "wax+")


def _names(expr: ast.expr) -> set[str]:
    return {n.id for n in ast.walk(expr) if isinstance(n, ast.Name)}


def _product_violations(rel: str, found: list[tuple[str, ast.Call]],
                        tree: ast.AST) -> list[Problem]:
    roots = product_roots(tree)
    problems = []
    for site, node in found:
        expr = _path_expression(node)
        if expr is None or not (_names(expr) & roots):
            continue
        problems.append(Problem(
            file=rel, lineno=node.lineno, rule="product-root", site=site,
            detail="产品源目录严格只读（宪法 V）。这里往由 Path.home() 派生的"
                   f"路径里写：{_describe(expr)}"))
    return problems


def _write_calls(node: ast.AST) -> list[ast.Call]:
    """`node` 子树里的写调用，但不穿过嵌套函数。

    不穿过去有两个原因：写入点归属到**最近**的函数才有用（`save_snapshot`
    和它内部的东西不是一回事），以及穿过去会双计 —— 模块级作用域会把每个函数
    里的写调用再收一遍。
    """
    out: list[ast.Call] = []
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue  # 它自己会作为一个作用域被扫到
        if isinstance(child, ast.Call) and _is_write(child):
            out.append(child)
        out.extend(_write_calls(child))
    return out


def write_sites(tree: ast.AST, rel: str) -> list[tuple[str, ast.Call]]:
    """这棵树里的写入点，按 `文件:函数` 归组（模块级用 `-`）。

    键里不放行号：行号会随着上面加一行注释就漂掉，登记表会变成天天要改的
    摆设 —— 那就没人改了。
    """
    scopes: list[tuple[str, ast.AST]] = [("-", tree)]
    scopes += [(n.name, n) for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    return [(f"{rel}:{name}", call)
            for name, scope in scopes for call in _write_calls(scope)]


def _describe(expr: ast.expr) -> str:
    try:
        return ast.unparse(expr)
    except Exception:  # pragma: no cover — unparse 失败不该让门禁整个崩掉
        return "<表达式>"


def _unregistered(found: list[tuple[str, ast.Call]],
                  registry: dict) -> list[Problem]:
    problems = []
    for site, call in found:
        if site in registry:
            continue
        rel = site.split(":")[0]
        problems.append(Problem(
            file=rel, lineno=call.lineno, rule="unregistered", site=site,
            detail="未登记的写入点。宪法 V 只认 data/ 与 notes/ 两个写入根，"
                   f"要放开就在 WRITE_SITES 里登记「{site}」，写清允许写到哪、"
                   "为什么"))
    return problems


WRITE_SITES = {
    # 键 = "文件:函数"（模块级用 "-"）。值里的 target 是「这一处实际会碰到谁」，
    # 登记的意义全在理由：半年后看到这行还能知道当初为什么允许。
    "src/collect.py:save_snapshot": Site(
        "data/raw/<day>.json",
        "FR-006 当日快照，宪法 V 明给的写入根"),
    "src/log.py:log": Site(
        "notes/inbox.md",
        "FR-004 手动快记，宪法 V 明给的写入根"),
    "src/sync.py:_lock": Site(
        "data/.sync.lock",
        "FR-022 并发互斥；锁文件本身不删，所以没有 stale-lock 窗口"),
    "src/sync.py:cleanup_raw": Site(
        "data/raw/<day>.json（删除）",
        "FR-022 到期清理；只碰 raw/，永不碰库内条目（AC-008）"),
    # 第三个写入根。宪法 V 那句「运行时只写 data/、notes/」并没有把它算进去，
    # 但 FR-005 的 `make scope` 就是要落一个 config/scope.json —— 交互式选择
    # 的结果总得有个地方存。这里显式登记而不是装作看不见：例外要有理由，
    # 没理由的例外过半年就成了「反正一直这样」。
    "src/scope.py:save": Site(
        "config/scope.json",
        "FR-005 采集范围选择的持久化；唯一一处写到 data//notes/ 之外"),
}


def print_registry() -> None:
    """把登记表摊开：免检了哪些写入点、各自碰到哪里、凭什么。"""
    print("已登记的写入点（键 = 文件:函数，模块级写作 -）：")
    for site, entry in sorted(WRITE_SITES.items()):
        print(f"  {site:<28} -> {entry.target}")
        print(f"  {'':<28}    {entry.why}")
    print("\n硬法（登记也不能豁免）：产品源目录 ~/.claude、~/.codex、"
          "~/.kimi-code、~/.trae-cn 严格只读（宪法 V）")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="write_boundary_check")
    parser.add_argument("--list", action="store_true", dest="list_registry",
                        help="打印写入点登记表与规则，不做检查")
    args = parser.parse_args(argv)
    if args.list_registry:
        print_registry()
        return 0

    problems = scan(SOURCE_ROOT)
    for p in problems:
        print(f"{p.file}:{p.lineno}: [{p.rule}] {p.detail}", file=sys.stderr)
    if problems:
        print(f"\n✗ 写入边界违规 {len(problems)} 处"
              "（宪法 V：运行时只写 data/ 与 notes/，产品源目录只读）",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
