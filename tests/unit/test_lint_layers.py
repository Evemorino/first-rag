"""scripts/lint_layers.py 的单元测试。

它守的是两件事：**文件该放在哪**（位置登记）和**谁能 import 谁**（分层依赖）。
这两件事的失败模式都特别安静：

- 兜底一旦写回「编排层」，新目录就默认拿到不限制的权限（fail-open），而所有
  已有文件照样全绿 —— 门禁看着在工作，其实只对**新结构**失效，那恰恰是它唯一
  该工作的时刻（旧结构早就被人 review 过了）。
- 规则靠一份手工名单枚举（比如曾经的 `PLUGIN_SUBPACKAGES`），新插件忘了加
  进去就静默失效。

`gate_selftest` 能证明"钩子在真实仓库里会红"，但它只走一条路径、还要 9 秒。
这里用毫秒级的方式把上面这些不变量钉住，并把报错提前到测试阶段 —— 钩子的报错
要等一次 commit 才看得到，而且不会指名道姓说是哪个文件。

注意：这些用例只 import 脚本本身，**不 import src/**，所以不需要 .env 和 Qdrant。
"""

from pathlib import Path
import sys

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import lint_layers  # noqa: E402  (先补 sys.path 才能导入)

REPO_ROOT = Path(__file__).resolve().parents[2]


def make_tree(tmp_path: Path, monkeypatch, files: dict[str, str]) -> None:
    """在临时 cwd 里造出一棵 src/ 树。

    必须真的把 cwd 挪过去：`check_file()` 与 `main()` 吃的都是相对路径
    （`layer_of()` 直接看 `src/...` 前缀），用绝对路径调它们等于测另一件事。
    """
    monkeypatch.chdir(tmp_path)
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


# --- 兜底必须是「拒绝」---


# 这四个路径都是实测过的：改造前它们全被兜底成「编排层」，而编排层的
# ALLOWED 是 None（不限制）—— 也就是说，新建一个目录就等于默认拿到最大权限。
UNREGISTERED = [
    "src/retrieval/rerank.py",
    "src/api2/handler.py",
    "src/plugins_v2/helper.py",
    "src/deep/nested/module.py",
]


@pytest.mark.parametrize("rel", UNREGISTERED)
def test_unregistered_location_has_no_layer(rel):
    """没登记过就是没登记过，不能兜底成最宽松的那一层。"""
    assert lint_layers.layer_of(rel) is None


def test_the_permissive_layer_is_not_reachable_by_fallback():
    """「编排层」只能从登记过的 `src/` 前缀拿到，不能是"谁都没匹配上"的产物。"""
    assert lint_layers.layer_of("src/anything.py") == "编排层"
    assert lint_layers.layer_of("src/sync.py") == "编排层"
    # 同一个层名，换一个没登记的目录就必须落空 —— 否则上面两行说明不了什么
    assert lint_layers.layer_of("src/sub/anything.py") is None


REGISTERED = [
    ("src/sync.py", "编排层"),
    ("src/distill_candidates.py", "编排层"),
    ("src/api/app.py", "接口层 api/"),
    ("src/plugins/__init__.py", "插件层 plugins/"),
    ("src/plugins/codex/__init__.py", "插件层 plugins/"),
    ("src/plugins/_template/__init__.py", "插件层 plugins/"),
    ("src/similarity.py", "核心层（宪法 VII）"),
    ("src/distill_prompt.py", "核心层（宪法 VII）"),
    ("src/config.py", "基础设施 config"),
]


@pytest.mark.parametrize("rel,layer", REGISTERED)
def test_registered_locations_keep_their_layer(rel, layer):
    """fail-closed 不能顺手把已经登记好的路径也一起关掉。"""
    assert lint_layers.layer_of(rel) == layer


# --- 登记表与现实同步 ---


def real_src_files() -> list[str]:
    return sorted(
        path.relative_to(REPO_ROOT).as_posix()
        for path in (REPO_ROOT / "src").rglob("*.py")
        if "__pycache__" not in path.parts
    )


def test_every_real_src_file_has_a_layer():
    """现实里有、表里没有 = 门禁会红。把红提前到测试阶段并指名道姓。

    （反过来"表里有、现实里没有"是小事：一个空的登记条目不会拦任何人，
    `--list` 里那一层会显示 0 个文件，一眼能看出是死的。）
    """
    orphans = [rel for rel in real_src_files() if lint_layers.layer_of(rel) is None]
    assert not orphans, (
        f"这些文件的位置没有登记，提交时会被 lint-layers 拦下：{orphans}"
    )


def test_every_layer_in_the_table_has_import_rules():
    """DIRECTORIES 里的层名必须在 ALLOWED 里有对应规则。

    没有的话 `check_file()` 拿到的是一个"没有规则的层"—— 那时候"确实不限制"
    和"层名打错了"看起来一模一样，而后者会让这一层悄悄免检。
    """
    assert lint_layers.registry_problems() == []


def test_registry_problems_flags_an_unknown_layer(monkeypatch):
    monkeypatch.setitem(
        lint_layers.DIRECTORIES,
        "src/http/",
        lint_layers.Place("接口层 http/", "手滑打错层名"),
    )
    problems = lint_layers.registry_problems()
    assert any("接口层 http/" in problem for problem in problems)


def test_registry_problems_flags_a_key_without_trailing_slash(monkeypatch):
    """登记的是目录，键少了结尾的斜杠就永远匹配不上 —— 静默失效的经典形态。"""
    monkeypatch.setitem(
        lint_layers.DIRECTORIES,
        "src/http",
        lint_layers.Place("接口层 api/", "忘了写结尾的斜杠"),
    )
    problems = lint_layers.registry_problems()
    assert any("src/http" in problem for problem in problems)


# --- check_file ---


def test_check_file_reports_instead_of_crashing_on_unregistered(tmp_path, monkeypatch):
    """改造前 `layer_of()` 永远返回一个 ALLOWED 里有的层，所以这里不会出事；
    现在它可能返回 None，忘了处理就是 KeyError —— 钩子崩掉和钩子放行一样糟。"""
    make_tree(tmp_path, monkeypatch, {"src/retrieval/rerank.py": "import qdrant_client\n"})
    problems = lint_layers.check_file(Path("src/retrieval/rerank.py"))
    assert len(problems) == 1
    assert "没有登记" in problems[0]
    # 报错要给下一步做什么，不能只说"你错了"
    assert "DIRECTORIES" in problems[0]


# --- 插件不许互相依赖 ---


def test_cross_plugin_import_is_flagged(tmp_path, monkeypatch):
    make_tree(tmp_path, monkeypatch, {
        "src/plugins/codex/__init__.py": "from src.plugins.claude_code import x\n",
    })
    problems = lint_layers.check_file(Path("src/plugins/codex/__init__.py"))
    assert any("另一个插件" in problem for problem in problems)


def test_plugin_may_import_its_own_submodule(tmp_path, monkeypatch):
    make_tree(tmp_path, monkeypatch, {
        "src/plugins/codex/parse.py": "VALUE = 1\n",
        "src/plugins/codex/__init__.py": "from src.plugins.codex import parse\n",
    })
    assert lint_layers.check_file(Path("src/plugins/codex/__init__.py")) == []


def test_plugin_may_import_the_shared_registry(tmp_path, monkeypatch):
    """`src.plugins` 本身是共用契约，每个插件都要 import 它。"""
    make_tree(tmp_path, monkeypatch, {
        "src/plugins/codex/__init__.py": "from src.plugins import Plugin, RawMaterial\n",
    })
    assert lint_layers.check_file(Path("src/plugins/codex/__init__.py")) == []


def test_plugin_registry_may_not_depend_on_a_specific_plugin(tmp_path, monkeypatch):
    """插件注册表自己不能依赖某个具体插件 —— 那会让"插件可插拔"名存实亡。"""
    make_tree(tmp_path, monkeypatch, {
        "src/plugins/__init__.py": "from src.plugins.codex import x\n",
    })
    problems = lint_layers.check_file(Path("src/plugins/__init__.py"))
    assert any("另一个插件" in problem for problem in problems)


def test_cross_plugin_rule_does_not_need_a_name_list(tmp_path, monkeypatch):
    """这条钉的是"覆盖全体，而不是枚举已知的"。

    夹具里两个插件名都是新的（`alpha` / `zed`），任何手工名单都不会包含它们 ——
    改造前靠 `PLUGIN_SUBPACKAGES` 枚举，这种 import 会静默放行，而新插件恰恰是
    唯一需要它生效的时候。

    第一版夹具写的是 `zed` import `trae`，结果它在"退回手工名单"的 canary 下
    照样绿 —— 因为 `trae` 本来就在名单里。**用名单里的名字测"不依赖名单"，
    等于什么都没测。**
    """
    make_tree(tmp_path, monkeypatch, {
        "src/plugins/alpha/__init__.py": "VALUE = 1\n",
        "src/plugins/zed/__init__.py": "from src.plugins.alpha import VALUE\n",
    })
    problems = lint_layers.check_file(Path("src/plugins/zed/__init__.py"))
    assert any("另一个插件" in problem for problem in problems), (
        "两个插件名都不在任何名单里，规则也应该照样生效"
    )


# --- 例外不能是死的 ---


def test_stale_exception_is_flagged(tmp_path, monkeypatch):
    """例外是给读者的信号。文件还在、但那条跨层 import 早就没了，例外就成了
    误导 —— 它让人以为这里还有约束，而实际上它已经不挡任何东西。"""
    make_tree(tmp_path, monkeypatch, {"src/whatever.py": "VALUE = 1\n"})
    monkeypatch.setattr(
        lint_layers, "EXCEPTIONS", {"src/whatever.py -> src.collect": "早就失效了"}
    )
    problems = lint_layers.stale_exceptions()
    assert len(problems) == 1
    assert "src.collect" in problems[0]


def test_live_exception_is_not_flagged(tmp_path, monkeypatch):
    make_tree(tmp_path, monkeypatch, {
        "src/whatever.py": "from src.collect import DayRaw\n",
    })
    monkeypatch.setattr(
        lint_layers, "EXCEPTIONS", {"src/whatever.py -> src.collect": "还在用"}
    )
    assert lint_layers.stale_exceptions() == []


def test_stale_exception_check_skips_missing_files(tmp_path, monkeypatch):
    """文件整个没了就不管。

    这条不是"顺手宽松"，是硬要求：`gate_selftest` 的临时仓库里只有夹具文件，
    真去要求 `src/distill_prompt.py` 存在，那个"干净仓库必须绿"的对照组会红。
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        lint_layers, "EXCEPTIONS", {"src/not_here.py -> src.collect": "文件都没了"}
    )
    assert lint_layers.stale_exceptions() == []


def test_the_real_exception_is_still_live():
    """真仓库那条例外必须还成立 —— 否则它已经从"约束"退化成"噪声"。"""
    assert lint_layers.EXCEPTIONS, "例外表空了，这条用例就失去意义了"
    assert lint_layers.stale_exceptions() == []


# --- main 的退出码 ---


def test_main_returns_2_when_src_is_missing(tmp_path, monkeypatch):
    """空目录上 rglob 返回空列表，然后打印「✓ 通过（0 个文件）」——
    那是门禁瞎了还在报平安。指错了目录、或者在错误的 cwd 下跑，必须是 2。"""
    monkeypatch.chdir(tmp_path)
    assert lint_layers.main([]) == 2


def test_main_passes_a_clean_tree(tmp_path, monkeypatch):
    make_tree(tmp_path, monkeypatch, {"src/ok.py": "VALUE = 1\n"})
    assert lint_layers.main([]) == 0


def test_main_fails_on_an_unregistered_directory(tmp_path, monkeypatch):
    make_tree(tmp_path, monkeypatch, {"src/retrieval/rerank.py": "VALUE = 1\n"})
    assert lint_layers.main([]) == 1


def test_list_mode_prints_every_registered_prefix(capsys):
    assert lint_layers.main(["--list"]) == 0
    out = capsys.readouterr().out
    for prefix in lint_layers.DIRECTORIES:
        assert prefix in out
