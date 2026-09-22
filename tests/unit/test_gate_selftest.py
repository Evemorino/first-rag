"""scripts/gate_selftest.py 的单元测试。

它守的是"守门的人"：13 个 pre-commit 钩子到底还会不会拦人。所以这里的测试
只检查用例表本身的结构与判定逻辑，**不真跑钩子**（那要 9 秒，是 `make
gate-selftest` 的事）。重点防的是用例表和真实配置之间各走各的：
配置里加了钩子、用例表没跟上，自检会安静地少测一个门禁。
"""

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

import sys

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import gate_selftest  # noqa: E402  (先补 sys.path 才能导入)

REPO_ROOT = Path(__file__).resolve().parents[2]


def hook_ids_in_config() -> set[str]:
    """从真实的 .pre-commit-config.yaml 里读出所有钩子 id。"""
    import yaml

    data = yaml.safe_load((REPO_ROOT / ".pre-commit-config.yaml").read_text(
        encoding="utf-8")) or {}
    return {
        hook["id"]
        for repo in data.get("repos", [])
        for hook in repo.get("hooks", [])
    }


RED_CASES = [c for c in gate_selftest.CASES if c.expect == "red"]
GREEN_CASES = [c for c in gate_selftest.CASES if c.expect == "green"]


# --- 用例表与配置的同步 ---


def test_every_hook_in_config_has_a_case():
    """配置里每个会拦人的钩子都得有用例，否则自检会漏掉它。"""
    covered = {case.hook for case in gate_selftest.CASES}
    # post-commit 的提醒钩子不拦人（退出码恒 0），不在这个自检的范围内
    missing = hook_ids_in_config() - covered - {"mutation-reminder"}
    assert not missing, f"这些钩子没有任何自检用例：{sorted(missing)}"


def test_every_case_hook_exists_in_config():
    """反过来：用例里写的钩子必须在配置里，否则永远只能测到"配置里没有"。 """
    known = hook_ids_in_config()
    unknown = {case.hook for case in gate_selftest.CASES} - known
    assert not unknown, f"用例引用了配置里不存在的钩子：{sorted(unknown)}"


def test_every_hook_has_both_a_red_and_a_green_case():
    """只测红不测绿，等于允许钩子靠一直报错来假装自己在工作。"""
    red = {case.hook for case in RED_CASES}
    green = {case.hook for case in GREEN_CASES}
    assert red == green, (
        f"红/绿用例不配对：只有红的 {sorted(red - green)}，"
        f"只有绿的 {sorted(green - red)}"
    )


def test_slugs_are_unique():
    """slug 是临时仓库的目录名 —— 撞了就互相覆盖，结果不可信。"""
    slugs = [case.slug for case in gate_selftest.CASES]
    assert len(slugs) == len(set(slugs))


# --- 用例本身的质量 ---


def test_every_case_says_why():
    """半年后看的人会问"为什么这样能触发它"，没写就等于没留。"""
    for case in gate_selftest.CASES:
        assert case.why.strip(), f"{case.hook} 的用例没写 why"


def test_every_red_case_plants_something():
    for case in RED_CASES:
        assert case.files, f"{case.hook} 的红灯用例没有植入任何文件"


def test_green_cases_share_one_clean_fixture():
    """对照组必须完全一致，否则"绿"的解释会随用例漂移。"""
    assert len({id(case.files) for case in GREEN_CASES}) == 1


# --- 夹具 ---


def test_coverage_fixture_is_parseable():
    xml = gate_selftest.coverage_xml("demo.py", [1, 2], 0)
    root = ET.fromstring(xml)
    lines = root.findall(".//class/lines/line")
    assert [line.get("number") for line in lines] == ["1", "2"]
    assert all(line.get("hits") == "0" for line in lines)


def test_crappy_fixture_really_is_complex():
    """夹具要是只有复杂度 1，CRAP 就是 2，那个红灯用例等于白造。"""
    from radon.complexity import cc_visit

    blocks = cc_visit(gate_selftest.CRAPPY_SRC)
    assert blocks and blocks[0].complexity >= 6, (
        "复杂度不够，CRAP = comp²+comp 达不到阈值 30"
    )


def test_big_src_fixture_exceeds_the_limit():
    """300 是 size_guard.py 里的 DEFAULT_MAX_FILE_SLOC。这里不 import 它是
    有意的：改了那个默认值却不改夹具，这条会红，正好提醒两边要一起动。"""
    from radon.raw import analyze

    assert analyze(gate_selftest.BIG_SRC).sloc > 300


# --- 配置体检 ---


def test_check_config_is_clean_on_the_real_config():
    assert gate_selftest.check_config() == []


def test_check_config_flags_a_missing_script(tmp_path):
    config = tmp_path / ".pre-commit-config.yaml"
    config.write_text(
        "repos:\n"
        "  - repo: local\n"
        "    hooks:\n"
        "      - id: gone\n"
        "        entry: uv run --no-sync python scripts/does_not_exist.py\n",
        encoding="utf-8",
    )
    problems = gate_selftest.check_config(config)
    assert len(problems) == 1
    assert "does_not_exist.py" in problems[0]
    assert "gone" in problems[0]


def test_check_config_reports_a_missing_config(tmp_path):
    problems = gate_selftest.check_config(tmp_path / "nope.yaml")
    assert problems and "找不到" in problems[0]


# --- 报告与判定 ---


def _result(case, state, ok):
    return gate_selftest.Result(case=case, state=state, ok=ok)


def test_render_shows_hook_and_state():
    case = RED_CASES[0]
    out = gate_selftest.render([_result(case, "green", False)])
    assert case.hook in out
    assert "期望红" in out and "实际绿" in out


def test_render_marks_a_missing_hook_clearly():
    """钩子从配置里消失了，不能显示成普通的"没拦住"。 """
    case = RED_CASES[0]
    out = gate_selftest.render([_result(case, "missing", False)])
    assert "配置里没有这个钩子" in out


@pytest.mark.parametrize("state,expect,ok", [
    ("red", "red", True),
    ("green", "red", False),
    ("green", "green", True),
    ("red", "green", False),
    ("missing", "red", False),
    ("missing", "green", False),
])
def test_state_matches_expectation(state, expect, ok):
    """missing 无论期望什么都算失败 —— 钩子没了不是"没拦住"，是门禁没了。"""
    case = gate_selftest.Case(hook="x", title="t", expect=expect)
    assert (state == case.expect) is ok
