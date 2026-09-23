"""scripts/write_boundary_check.py 的单元测试。

它守的是宪法 V 那条 NON-NEGOTIABLE：运行时只准写 data/ 与 notes/，产品源目录
（~/.claude、~/.codex、~/.kimi-code、~/.trae-cn）严格只读。

为什么静态查而不是跑一遍看有没有写：真跑一次 sync 要 .env + Qdrant + 真调
Ark，进不了毫秒级的提交门禁；而违规的形状其实非常稳定 —— 一个写调用，路径
落在产品根下。AST 扫就够，且**绝不执行被测代码**，所以这个门禁本身也不会写坏
任何人的会话记录。

两级规则，照 lint_layers 的路子来（规则覆盖全体、默认拒绝、例外显式带理由）：

  1. 产品根写入 = 硬违规，登记也救不了。
  2. src/ 里任何写入点都必须登记过；没登记就是没想过。

注意：这些用例只 import 脚本本身，**不 import src/**，所以不需要 .env 和 Qdrant。
"""

from pathlib import Path
import sys

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import write_boundary_check as wbc  # noqa: E402  (先补 sys.path 才能导入)

REPO_ROOT = Path(__file__).resolve().parents[2]

PRODUCT_WRITE = '''\
"""A plugin that forgot it is only supposed to read."""

from pathlib import Path

LOG = Path.home() / ".codex" / "sessions"


def tidy() -> None:
    (LOG / "rollout-1.jsonl").write_text("rewritten")
'''


def make_tree(tmp_path: Path, monkeypatch, files: dict[str, str]) -> None:
    """在临时 cwd 里造出一棵 src/ 树。

    门禁吃的是相对路径（跟 lint_layers 一样），所以必须真的 chdir 过去。
    """
    monkeypatch.chdir(tmp_path)
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def test_writing_into_a_product_dir_is_a_violation(tmp_path, monkeypatch):
    """往 ~/.codex 写 = 硬违规，而且要指名道姓说是哪个文件。"""
    make_tree(tmp_path, monkeypatch, {"src/plugins/demo/__init__.py": PRODUCT_WRITE})

    problems = wbc.scan(Path("src"), registry={})

    assert [(p.file, p.rule) for p in problems] == [
        ("src/plugins/demo/__init__.py", "product-root")]
    assert problems[0].lineno == 9  # fixture 里那句 write_text 所在的行


def test_the_real_repo_passes_with_the_real_registry(monkeypatch):
    """仓库现状必须过它自己的门禁，且登记表里没有空条目。

    断言扫的是真 src/（不是临时树）：这是唯一能在 `make test` 阶段就说「你刚
    加的写入点没登记」的地方，不用等一次 commit 才被钩子打回来。
    registry=None 让 scan 用模块里那份 WRITE_SITES，顺手也证明 _stale 抓不到
    东西 —— 登记的 5 个写入点确实都还在写。
    """
    monkeypatch.chdir(REPO_ROOT)

    assert wbc.scan(Path("src")) == []


def test_list_shows_every_registered_write_site(capsys, monkeypatch):
    """`--list` 要能把「哪些写入点被免检、凭什么」摊开给人看。

    登记表这种东西，看不看得见决定了会不会有人往里乱加。报错信息只说"你没登记"，
    而这行让人看到别人的登记长什么样 —— 理由那一栏就是门槛。
    """
    monkeypatch.chdir(REPO_ROOT)

    assert wbc.main(["--list"]) == 0

    out = capsys.readouterr().out
    for site, entry in wbc.WRITE_SITES.items():
        assert site in out
        assert entry.target in out
    assert "config/scope.json" in out  # 那个唯一的例外也得看得见


SNAPSHOT_WRITE = '''\
"""Saves a day snapshot. Legitimate-looking, but nobody registered it."""

from pathlib import Path


def save(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
'''


def test_an_unregistered_write_site_is_a_violation(tmp_path, monkeypatch):
    """默认拒绝：src/ 里每个写入点都得登记过。

    产品根那条只能抓住「写到 ~/.codex」这种明显犯规的。真正会悄悄发生的是
    第三种写入根 —— 今天往 config/ 写一个 json，明天往仓库根写一个 .bak，
    每处单看都无害，攒起来就是「运行时到底改了哪些文件」没人说得清。
    所以登记才是覆盖全体的那条。
    """
    make_tree(tmp_path, monkeypatch, {"src/collect.py": SNAPSHOT_WRITE})

    problems = wbc.scan(Path("src"), registry={})

    assert [(p.rule, p.site) for p in problems] == [
        ("unregistered", "src/collect.py:save")]


def test_a_product_root_write_is_not_also_reported_as_unregistered(
        tmp_path, monkeypatch):
    """登记也洗不白：写进产品根，哪怕这个点登记过了，照样报。

    登记表是「第三种写入根」的出口，不是硬法的出口 —— 如果登记能把产品根写入
    一起免掉，那 WRITE_SITES 就成了宪法的后门。这条断言的是登记**不能**做到什么。
    """
    make_tree(tmp_path, monkeypatch, {"src/plugins/demo/__init__.py": PRODUCT_WRITE})

    problems = wbc.scan(
        Path("src"),
        registry={"src/plugins/demo/__init__.py:tidy": wbc.Site("wherever", "x")})

    assert [p.rule for p in problems] == ["product-root"]


APPEND_WRITE = '''\
"""Appends one line to the inbox."""

from pathlib import Path


def log(inbox: Path, line: str) -> None:
    with open(inbox, "a", encoding="utf-8") as f:
        f.write(line + "\\n")
'''

READ_WRITE = '''\
"""Reads a snapshot back."""

from pathlib import Path


def load(path: Path) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def rewrite(path: Path, text: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
'''


def test_open_in_append_mode_counts_as_a_write(tmp_path, monkeypatch):
    """`open(p, "a")` 是个写入点。

    只认 write_text/mkdir 那套方法名的话，src/log.py 就整个漏掉了 —— 它追加
    快记走的正是 `open(inbox, "a")`。而 `f.write()` 本身不算一个**新**写入点：
    句柄哪来的才是关键，不然一个 with 块报两条。
    """
    make_tree(tmp_path, monkeypatch, {"src/log.py": APPEND_WRITE})

    problems = wbc.scan(Path("src"), registry={})

    assert [p.site for p in problems] == ["src/log.py:log"]


def test_open_for_reading_is_not_a_write_site(tmp_path, monkeypatch):
    """默认读模式不是写入点，显式 "w" 才是。

    反过来搞错的话，全库每个读文件的地方都会报错，门禁第一天就会被要求关掉。
    """
    make_tree(tmp_path, monkeypatch, {"src/collect.py": READ_WRITE})

    problems = wbc.scan(Path("src"), registry={})

    assert [p.site for p in problems] == ["src/collect.py:rewrite"]


def test_a_stale_registration_is_reported(tmp_path, monkeypatch):
    """登记还在、写入点已经没了 = 登记已死，要报。

    没有这条的话，重构掉一个写入函数而登记留在原地，那个 target 就等于被
    永久免检 —— 门禁不会变宽，但会悄悄放宽。lint_layers 用同一招对付它自己
    的 EXCEPTIONS 与空层。
    """
    make_tree(tmp_path, monkeypatch, {"src/collect.py": SNAPSHOT_WRITE})

    problems = wbc.scan(Path("src"), registry={
        "src/collect.py:save": wbc.Site("data/raw/<day>.json", "还在用"),
        "src/collect.py:save_v1": wbc.Site("data/raw/<day>.json", "函数早就改名了")})

    assert [(p.rule, p.site) for p in problems] == [
        ("stale-registry", "src/collect.py:save_v1")]


def test_a_registration_for_an_absent_file_is_not_stale(tmp_path, monkeypatch):
    """整棵树里没这个文件，不算登记已死。

    门禁扫的是「手上这棵 src/ 树」：gate_selftest 的对照组只放一个 src/ok.py，
    任何局部扫描同理。把缺文件也报成 stale 会让对照组永远红 —— 而恰恰是对照组
    在证明这个钩子会放行，它比这条重要得多。
    """
    make_tree(tmp_path, monkeypatch, {"src/ok.py": "VALUE = 1\n"})

    problems = wbc.scan(
        Path("src"), registry={"src/gone.py:save": wbc.Site("data/", "文件不在树里")})

    assert problems == []


SCOPE_MODULE = '''\
"""The picker: this repo's only sanctioned config writer."""

from pathlib import Path


def save(matrix: dict) -> Path:
    path = Path("config") / "scope.json"
    path.write_text("{}", encoding="utf-8")
    return path


def main() -> int:
    save({})
    return 0
'''

SYNC_REACHES_IN = '''\
"""A pipeline that quietly borrows the config writer."""

from src.scope import save


def run() -> None:
    save({"tools": {}})
'''


BYPASS_REGISTRY = {
    "src/scope.py:save": wbc.Site(
        "config/scope.json", "宪法 V 的唯一例外",
        only_from=("src/scope.py:main",)),
}


def test_a_sanctioned_writer_called_from_elsewhere_is_a_bypass(
        tmp_path, monkeypatch):
    """宪法 V 第②条（"只由人显式调用的入口触发"）的可执行版本。

    登记表钉住的是「写入点在 src/scope.py:save」；光这样挡不住 sync.py 直接
    import 这个函数来写 config/ —— 写入点还是那个已登记的名字，门禁照样绿。
    所以要查调用方：谁调它，调用的位置必须在 only_from 里。
    """
    make_tree(tmp_path, monkeypatch, {
        "src/scope.py": SCOPE_MODULE,
        "src/sync.py": SYNC_REACHES_IN,
    })

    problems = wbc.scan(Path("src"), registry=BYPASS_REGISTRY)

    assert [(p.rule, p.file, p.site) for p in problems] == [
        ("writer-bypassed", "src/sync.py", "src/scope.py:save")]


def test_the_sanctioned_caller_may_still_call_it(tmp_path, monkeypatch):
    """对照组：只有 scope.py 自己在 main 里调，就该放行。

    只测"抓到绕过"的话，一个逢调用必报的实现也能过 —— 那这条门禁会变成
    拆掉 scope 功能的原因之一，而不是保护它的手段。
    """
    make_tree(tmp_path, monkeypatch, {"src/scope.py": SCOPE_MODULE})

    assert wbc.scan(Path("src"), registry=BYPASS_REGISTRY) == []
