"""`scripts/mutant_recheck.py` 的测试。

这个工具会**改写 src/ 再还原**，所以测法跟着分两半：
- 纯函数半边（读 .meta、算真实改动、归类、锚点唯一性）用临时构造的 mutants/ 副本测；
- 危险半边只测它的**闸门**：脏树拒绝跑、还原不干净就停 —— 不去驱动真 pytest
  （那会套娃跑全量测试）。
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import mutant_recheck as mr  # noqa: E402  (先补 sys.path 才能导入)

ORIG = '''"""instrumented copy."""


def x_foo__mutmut_orig(text, max_chars):
    total = len(text)
    if total <= max_chars:
        return text
    return text[:max_chars]


def x_foo__mutmut_1(text, max_chars):
    total = len(text)
    if total < max_chars:
        return text
    return text[:max_chars]


def x_bar__mutmut_orig():
    key = "collected_at"
    return key


def x_bar__mutmut_2():
    key = "COLLECTED_AT"
    return key


def x_name__mutmut_orig():
    path = config.CONFIG_DIR / "scope.json"
    return path


def x_name__mutmut_3():
    path = config.CONFIG_DIR / "SCOPE.JSON"
    return path


def x_say__mutmut_orig():
    return "你是个人学习记忆的检索助手。只依据条目回答；"


def x_say__mutmut_4():
    return "XX你是个人学习记忆的检索助手。只依据条目回答；XX"

'''

REAL = '''import config


def foo(text, max_chars):
    total = len(text)
    if total <= max_chars:
        return text
    return text[:max_chars]


def bar():
    key = "collected_at"
    return key


def name():
    path = config.CONFIG_DIR / "scope.json"
    return path
'''

META = {"exit_code_by_key": {
    "demo.x_foo__mutmut_1": 0,       # survived
    "demo.x_bar__mutmut_2": 0,       # survived
    "demo.x_name__mutmut_3": 0,      # survived
    "demo.x_say__mutmut_4": 0,       # survived：给人读的文案
    "demo.x_foo__mutmut_5": 1,       # killed -> 不该出现在结果里
    "demo.x_foo__mutmut_6": 33,      # no tests -> 也不该出现
}}


@pytest.fixture
def mutants(tmp_path):
    src = tmp_path / "mutants" / "src"
    src.mkdir(parents=True)
    (src / "demo.py").write_text(ORIG, encoding="utf-8")
    (src / "demo.py.meta").write_text(json.dumps(META), encoding="utf-8")
    return tmp_path / "mutants"


def test_only_survivors_are_loaded(mutants):
    items = mr.load_survivors(mutants)

    assert sorted(i.mutant for i in items) == [
        "demo.x_bar__mutmut_2", "demo.x_foo__mutmut_1",
        "demo.x_name__mutmut_3", "demo.x_say__mutmut_4"]


def test_diff_is_computed_on_the_body_not_the_def_line(mutants):
    """def 行带着 mutmut 的改名，拿它当锚点永远对不上真实源码。"""
    item = next(i for i in mr.load_survivors(mutants)
                if i.mutant == "demo.x_foo__mutmut_1")

    assert item.hunks == [("    if total <= max_chars:", "    if total < max_chars:")]
    assert all("def x_foo" not in r for r, _a in item.hunks)


def test_families_separate_boundary_key_filename_and_prose(mutants):
    families = {i.mutant: mr.classify(i) for i in mr.load_survivors(mutants)}

    assert families["demo.x_foo__mutmut_1"] == "边界与比较符"
    assert families["demo.x_bar__mutmut_2"] == "程序读的字符串被改（键名/枚举值）"
    assert families["demo.x_name__mutmut_3"] == "数据文件名大小写"
    # 给人读的那条必须单独成族：上一轮把 84 条一锅端进"文案"，其中约七成是键名。
    # 没有这一族，"纯文案到底几条"这句话就只有我那个一次性探针能复现。
    assert families["demo.x_say__mutmut_4"] == "纯文案（人读的字符串：提示语/usage/日志）"


def test_human_prose_needs_cjk_or_spaced_words():
    assert mr.human_prose('return "你是检索助手，只依据条目回答；"',
                          'return "XX你是检索助手，只依据条目回答；XX"') is True
    assert mr.human_prose('return "usage: make ask Q=…"', 'x') is True
    assert mr.human_prose('key = "collected_at"', 'key = "COLLECTED_AT"') is False


def test_program_read_only_looks_at_the_changed_literal():
    """`"content": ("你是助手")` 里改的是文案，没动的 `content` 不能算键名变异。

    第一版扫整段 diff，于是 33 条纯文案被归进"键名被改"，60/33 这两个数字都是假的。
    """
    assert mr.program_read('key = "collected_at"', 'key = "XXcollected_atXX"') == "collected_at"
    assert mr.program_read('return "你是检索助手，只依据条目回答"',
                           'return "XX你是检索助手，只依据条目回答XX"') is None
    assert set(mr.changed_literals('a = "one"', 'a = "two"')) == {"one", "two"}
    assert mr.changed_literals('x = "content"', 'x = "content"') == []


def test_apply_hunks_refuses_an_ambiguous_anchor(mutants):
    item = next(i for i in mr.load_survivors(mutants)
                if i.mutant == "demo.x_bar__mutmut_2")
    ambiguous = 'a = "collected_at"\nb = "collected_at"\n'

    assert mr.apply_hunks(ambiguous, item) is None      # 判不了 ≠ 没问题
    # 函数体块带着真实缩进，所以这里的夹具也必须带缩进
    single = 'def bar():\n    key = "collected_at"\n    return key\n'
    mutated = mr.apply_hunks(single, item)
    assert mutated == 'def bar():\n    key = "COLLECTED_AT"\n    return key\n'


def test_mutating_a_body_line_works_against_real_source(mutants):
    item = next(i for i in mr.load_survivors(mutants)
                if i.mutant == "demo.x_foo__mutmut_1")

    assert "if total < max_chars:" in mr.apply_hunks(REAL, item)


def test_dirty_tree_is_refused(monkeypatch):
    """它会改写 src/，所以工作区不干净时必须拒跑，而不是"顺手还原掉你的改动"。"""

    def fake_run(cmd, **kwargs):
        class R:
            returncode = 0
            stdout = " M src/collect.py\n"
        return R()

    monkeypatch.setattr(mr.subprocess, "run", fake_run)

    clean, dirty = mr.tree_is_clean()
    assert clean is False and "collect.py" in dirty


def test_main_reports_without_touching_source(monkeypatch, mutants, capsys):
    monkeypatch.setattr(mr, "MUTANTS", mutants)
    called = []
    monkeypatch.setattr(mr.subprocess, "run",
                        lambda *a, **k: called.append(a) or type(
                            "R", (), {"returncode": 0, "stdout": ""})())

    assert mr.main(["--summary"]) == 0
    out = capsys.readouterr().out

    assert "存活变异体 4 条" in out
    assert "边界与比较符" in out and "纯文案" in out and called == []   # 不跑测试


def test_main_missing_mutants_is_two(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(mr, "MUTANTS", tmp_path / "nope")

    assert mr.main([]) == 2
    assert "make mutation" in capsys.readouterr().err
