#!/usr/bin/env python3
"""写入边界门禁：宪法 V 的 NON-NEGOTIABLE 那条。

运行时只准写 data/ 与 notes/；产品源目录（PRD §6 那张表，v0.7 起 11 个：
~/.claude、~/.codex、~/.kimi-code、~/.trae-cn、~/.trae、~/.qoder、~/.qoder-cn、
~/.workbuddy-ai、~/.local/share/opencode、~/.zcode、~/.hermes）**严格只读**。
这条约束违规的代价是「把用户真实的会话记录改了」，而它恰好是本项目里唯一
没有机械门禁的硬约束 —— 全靠人记得。

三条规则：

  1. 产品根写入 —— 硬违规，登记也救不了（宪法 V）。
  2. SQLite 必须按只读 URI 打开 —— 同样是硬违规：`sqlite3.connect(path)`
     在 AST 里不像写入，却会往源目录里建 -wal/-shm/-journal（NFR-001）。
  3. src/ 里任何写入点都必须登记过；没登记就是没想过。

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
    """一个登记过的写入点：它实际会碰到哪里，以及凭什么允许。

    `only_from` = 允许调用它的位置（`文件:函数`）。写进 data/、notes/ 的那些
    本来就在墙内，留空即不限制调用方；只有宪法 V 那条例外必须钉住调用方，
    否则"登记表钉住写入点"挡不住别人 import 这个函数去写 config/。
    """

    target: str
    why: str
    only_from: tuple[str, ...] = ()


@dataclass(frozen=True)
class Problem:
    """一处违规：文件、写入点、行号、违反了哪条规则、以及怎么说才够用人。"""

    file: str
    lineno: int
    rule: str
    detail: str
    site: str = ""


MUTMUT_MARKER = "__mutmut_"


def mutmut_instrumented(root: Path = SOURCE_ROOT) -> bool:
    """这棵 src/ 是不是正被 mutmut 就地改写。

    mutmut 3.x 的跑法是把变异体**写进源文件**（`x_save__mutmut_1` 这种），
    于是上一刻还干净的树会凭空多出几十个未登记的写入点。任何"扫真树"的断言
    在这种时刻报的都不是人的代码，所以给它一个窄判据 —— 判据本身有测试钉住
    （test_mutmut_instrumentation_is_detected_only_by_its_marker），免得烂成
    永久空转。
    """
    if not root.is_dir():
        return False
    return any(MUTMUT_MARKER in p.read_text(encoding="utf-8", errors="ignore")
               for p in root.rglob("*.py"))


def scan(root: Path, registry: dict | None = None) -> list[Problem]:
    """扫一棵 src/ 树，返回全部违规。

    `registry` 是写入点登记表；默认用本模块那份（真实仓库的那份）。
    做成参数是为了让单测能喂一棵临时树进来。
    """
    entries = WRITE_SITES if registry is None else registry
    problems: list[Problem] = []
    sites: set[str] = set()
    files: set[str] = set()
    trees: dict[str, ast.AST] = {}
    scopes: dict[str, list] = {}
    for path in sorted(root.rglob("*.py")):
        rel = path.as_posix()
        files.add(rel)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        except (OSError, SyntaxError) as e:
            problems.append(Problem(rel, 0, "unreadable", f"读不了/解析不了：{e}"))
            continue
        trees[rel] = tree
        scopes[rel] = _scopes(tree, rel)
        found = write_sites(tree, rel)
        sites.update(site for site, _ in found)
        products = _product_violations(rel, found, tree)
        problems.extend(products)
        # 跟产品根一样是硬法：不并进登记表，也没有"未登记"那条可走。
        problems.extend(_sqlite_violations(rel, tree))
        # 产品根那条是硬法，登记也不许 —— 同一个点就别再报一次「未登记」，
        # 否则等于给出一条根本不该被采纳的修复建议。
        hard = {p.site for p in products}
        problems.extend(p for p in _unregistered(found, entries)
                        if p.site not in hard)
    problems.extend(_bypassed(trees, scopes, entries))
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


def _sqlite_connect_aliases(tree: ast.AST) -> tuple[set[str], set[str]]:
    """本文件里指向 sqlite3.connect 的两种写法：模块名（可带别名）+ 裸 connect 名。

    认 `import sqlite3`、`import sqlite3 as sq`、`from sqlite3 import connect`
    （可带 as）三种。漏掉任何一种，换个导入写法就整条溜过去了 —— 而换写法
    不需要任何理由，写插件的人顺手就写了。
    """
    modules: set[str] = set()
    bare: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "sqlite3":
                    modules.add(alias.asname or "sqlite3")
        elif isinstance(node, ast.ImportFrom) and node.module == "sqlite3":
            bare.update(a.asname or a.name
                        for a in node.names if a.name == "connect")
    return modules, bare


def _is_sqlite_connect(node: ast.Call, modules: set[str],
                       bare: set[str]) -> bool:
    """这是不是一次 sqlite3.connect()。只看名字，不猜对象是不是连接池。"""
    callee = node.func
    if isinstance(callee, ast.Name):
        return callee.id in bare
    if isinstance(callee, ast.Attribute) and callee.attr == "connect":
        head = callee.value
        return isinstance(head, ast.Name) and head.id in modules
    return False


def _literal_text(node: ast.expr) -> str | None:
    """字符串字面量 / f-string 的**静态可读部分**，读不出来则 None。

    f-string 只拼常量段：`f"file:{db}?mode=ro"` 给出 `"file:?mode=ro"`，
    两个标记都还在。插值进来的变量不参与匹配 —— 那正是我们看不出来、
    要按最危险算的地方。
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(part.value for part in node.values
                       if isinstance(part, ast.Constant)
                       and isinstance(part.value, str))
    return None


def _opens_read_only(node: ast.Call) -> bool:
    """这个 connect 是不是**确定**按只读 URI 打开的。

    三件事必须同时成立：`uri=True`、路径里读得到 `file:`、以及 `mode=ro`。
    少任何一样都还留着写的能力，且三种漏法各漏各的：

      - 没 `mode=ro`：`file:` 的默认 mode 是 **rw**；
      - 没 `uri=True`：那个字符串被当成普通文件名，sqlite3 会在 cwd 下
        凭空建一个名叫 `file:...?mode=ro` 的库；
      - 路径读不出字面量（`connect(p)`）：看不懂 —— 跟 `_mode_of` 一个原则，
        按最危险的算，宁可错报一次让人写清楚。
    """
    if not any(kw.arg == "uri" and isinstance(kw.value, ast.Constant)
               and kw.value.value is True for kw in node.keywords or ()):
        return False
    if not node.args:
        return False
    literal = _literal_text(node.args[0])
    return (literal is not None
            and "file:" in literal and "mode=ro" in literal)


def _sqlite_violations(rel: str, tree: ast.AST) -> list[Problem]:
    """src/ 里每个 sqlite3.connect() 都必须按只读 URI 打开（NFR-001 / AC-017）。

    为什么单独一条规则，而不是并进写入点登记：它 AST 形状上**不像写入** ——
    没有 mode 参数、不落在 WRITE_METHODS 里、路径也不是 Path.home() 派生的
    字面量，三条现有规则没一条够得着。但它对源目录做的事和写一样：建
    -wal/-shm/-journal、退出时改 journal mode（会真的写回库头）。所以这条
    是硬法，登记救不了，只能改代码。
    """
    modules, bare = _sqlite_connect_aliases(tree)
    if not modules and not bare:
        return []
    return [Problem(
        file=rel, lineno=node.lineno, rule="sqlite-mode",
        detail="SQLite 源必须按只读打开（NFR-001 / AC-017）。裸 "
               "sqlite3.connect() 会在源目录里建 -wal/-shm/-journal，并可能"
               "改回库头 —— 和写文件是同一件事（宪法 V：产品源目录严格只读）。"
               '改成：sqlite3.connect(f"file:{path}?mode=ro", uri=True)')
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and _is_sqlite_connect(node, modules, bare)
        and not _opens_read_only(node)]


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


def _walk_calls(node: ast.AST, keep) -> list[ast.Call]:
    """`node` 子树里满足 keep 的调用，但不穿过嵌套函数。

    不穿过去有两个原因：调用点归属到**最近**的函数才有用，以及穿过去会双计
    —— 模块级作用域会把每个函数里的调用再收一遍（第一版就栽在这儿，把
    main 里的调用记成了 "-"）。
    """
    out: list[ast.Call] = []
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue  # 它自己会作为一个作用域被扫到
        if isinstance(child, ast.Call) and keep(child):
            out.append(child)
        out.extend(_walk_calls(child, keep))
    return out


def _write_calls(node: ast.AST) -> list[ast.Call]:
    """`node` 子树里的写调用（不穿过嵌套函数）。"""
    return _walk_calls(node, _is_write)


def _scopes(tree: ast.AST, rel: str) -> list[tuple[str, ast.AST]]:
    """一棵文件里的作用域：模块级（记作 "-"）+ 每个函数。"""
    scopes: list[tuple[str, ast.AST]] = [("-", tree)]
    scopes += [(n.name, n) for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    return scopes


def write_sites(tree: ast.AST, rel: str) -> list[tuple[str, ast.Call]]:
    """这棵树里的写入点，按 `文件:函数` 归组（模块级用 `-`）。

    键里不放行号：行号会随着上面加一行注释就漂掉，登记表会变成天天要改的
    摆设 —— 那就没人改了。
    """
    return [(f"{rel}:{name}", call)
            for name, scope in _scopes(tree, rel) for call in _write_calls(scope)]


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
            detail="未登记的写入点。宪法 V 只认 data/ 与 notes/ 两个写入根"
                   "（唯一枚举例外是 config/scope.json，v2.1.0，且不许有第二处），"
                   f"要放开就在 WRITE_SITES 里登记「{site}」并写清允许写到哪、"
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
    # 宪法 V 在 2.1.0 里开了一条**封闭枚举**的例外：config/scope.json。
    # 它是采集范围的唯一事实来源（PRD FR-005），且 config/ 属于换机器要带走的
    # 三样东西之一（PRD NFR-007）。V 同时写死了三条：只覆盖这一个路径、只由人
    # 显式调用触发、全库只能有这一处 —— 想添第二处 MUST 先修宪，而不是往这张表
    # 里加一行。这张表就是那条宪法话的可执行版本。
    "src/scope.py:save": Site(
        "config/scope.json",
        "宪法 V 的唯一例外（v2.1.0）：FR-005 范围选择的持久化",
        only_from=("src/scope.py:main",)),
}


def print_registry() -> None:
    """把登记表摊开：免检了哪些写入点、各自碰到哪里、凭什么。"""
    print("已登记的写入点（键 = 文件:函数，模块级写作 -）：")
    for site, entry in sorted(WRITE_SITES.items()):
        print(f"  {site:<28} -> {entry.target}")
        print(f"  {'':<28}    {entry.why}")
        if entry.only_from:
            print(f"  {'':<28}    只许被这些位置调用：{'、'.join(entry.only_from)}")
    print("\n硬法（登记也不能豁免）：")
    print("  产品源目录严格只读（宪法 V）——~/.claude、~/.codex、~/.kimi-code、"
          "~/.trae-cn、~/.trae、")
    print("    ~/.qoder、~/.qoder-cn、~/.workbuddy-ai、"
          "~/.local/share/opencode、~/.zcode、~/.hermes（PRD §6，v0.7）")
    print("  SQLite 源必须 sqlite3.connect(f\"file:{path}?mode=ro\", uri=True)"
          "（NFR-001）")


def _names_bound_to(tree: ast.AST, module: str, func: str,
                    is_owner: bool) -> tuple[set[str], set[str]]:
    """本文件里指向 `src.<module>.<func>` 的名字：裸函数名 与 模块别名。

    认三种现实里会出现的写法：`from src.scope import save`（可带 as）、
    `from src import scope` / `import src.scope`、以及 owner 文件自己内部
    直接调 `save(...)`。相对导入（`from .scope import save`）也顺手认。
    """
    bare: set[str] = set()
    mods: set[str] = set()
    if is_owner:
        bare.add(func)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            pkg = node.module or ""
            if node.level and not node.module:      # from . import scope
                if module in [a.name for a in node.names]:
                    mods.update(a.asname or a.name for a in node.names
                                if a.name == module)
            elif pkg == f"src.{module}":            # from src.scope import save
                bare.update(a.asname or a.name for a in node.names
                            if a.name == func)
            elif pkg == "src" or (node.level and pkg == ""):
                mods.update(a.asname or a.name for a in node.names
                            if a.name == module)
            elif pkg.endswith(f".{module}"):        # from .scope import save
                bare.update(a.asname or a.name for a in node.names
                            if a.name == func)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == f"src.{module}":
                    mods.add(alias.asname or module)
                elif alias.name == "src":
                    mods.add(alias.asname or "src")
    return bare, mods


def _calls_a_function(tree: ast.AST, rel: str, module: str, func: str,
                      scopes: list[tuple[str, ast.AST]]
                      ) -> list[tuple[str, ast.Call]]:
    """返回 `文件:函数` → 调用节点，凡看起来在调 src.<module>.<func> 的。"""
    bare, mods = _names_bound_to(tree, module, func, rel == f"src/{module}.py")
    hits: list[tuple[str, ast.Call]] = []

    def is_target(node: ast.Call) -> bool:
        callee = node.func
        if isinstance(callee, ast.Name):
            return callee.id in bare
        if isinstance(callee, ast.Attribute) and callee.attr == func:
            head = callee.value
            return isinstance(head, ast.Name) and head.id in mods
        return False

    for name, scope in scopes:
        for node in _walk_calls(scope, is_target):
            hits.append((f"{rel}:{name}", node))
    return hits


def _module_of(site: str) -> tuple[str, str] | None:
    """`src/scope.py:save` -> ("scope", "save")；不在 src/ 下或没有函数则 None。"""
    rel, _, func = site.partition(":")
    if not rel.startswith("src/") or not rel.endswith(".py") or not func:
        return None
    return rel[len("src/"):-len(".py")], func


def _bypassed(trees: dict[str, ast.AST], scopes: dict[str, list],
              registry: dict) -> list[Problem]:
    """登记了 only_from 的写入点，被名单外的位置调用 = 绕过。

    没有这一步，"登记表钉住写入点"只守得住「谁写了文件」，守不住宪法 V
    第②条「只由人显式调用的入口触发」—— 别的模块 import 同一个函数来写
    config/，写入点名字都没变，门禁照样绿。
    """
    problems: list[Problem] = []
    for site, entry in sorted(registry.items()):
        allowed = set(entry.only_from)
        if not allowed:
            continue
        parsed = _module_of(site)
        if not parsed:
            continue
        module, func = parsed
        for rel, tree in trees.items():
            for caller, node in _calls_a_function(
                    tree, rel, module, func, scopes[rel]):
                if caller in allowed or caller == site:
                    continue
                problems.append(Problem(
                    file=rel, lineno=node.lineno, rule="writer-bypassed",
                    site=site,
                    detail=f"被豁免的写入点「{site}」只允许从 "
                           f"{'、'.join(sorted(allowed))} 调用，这里绕过它去写 "
                           f"{entry.target} 了（宪法 V 例外第②条：只由人显式"
                           "调用的入口触发）"))
    return problems


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
