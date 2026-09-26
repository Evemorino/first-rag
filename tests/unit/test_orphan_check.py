"""scripts/orphan_check.py 的单元测试。

它守的是 CRAP 的盲区：一个只有简单函数的模块，哪怕零测试，CRAP 也只有 2，
离 30 的阈值远得很。所以"新模块没写测试"必须有人专门盯着 —— 而盯的人自己
得先是对的。
"""

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import orphan_check  # noqa: E402  (先补 sys.path 才能导入)

COVERAGE_XML = """<?xml version="1.0" ?>
<coverage line-rate="0.5">
  <sources><source>src</source></sources>
  <packages>
    <package name="src">
      <classes>
        <class filename="covered.py" line-rate="1">
          <lines>
            <line number="1" hits="3"/>
            <line number="2" hits="1"/>
          </lines>
        </class>
        <class filename="orphan.py" line-rate="0">
          <lines>
            <line number="1" hits="0"/>
            <line number="2" hits="0"/>
          </lines>
        </class>
        <class filename="empty.py" line-rate="0">
          <lines/>
        </class>
      </classes>
    </package>
  </packages>
</coverage>
"""


@pytest.fixture
def src_tree(tmp_path):
    """造一个 src/：已覆盖的、零覆盖的、空文件、模板骨架、报告里没有的。"""
    src = tmp_path / "src"
    (src / "plugins" / "_template").mkdir(parents=True)
    for name in ("covered.py", "orphan.py", "empty.py", "not_in_report.py"):
        (src / name).write_text("x = 1\n", encoding="utf-8")
    (src / "plugins" / "_template" / "__init__.py").write_text(
        "raise NotImplementedError\n", encoding="utf-8"
    )
    return src


@pytest.fixture
def cov(tmp_path):
    xml = tmp_path / "coverage.xml"
    xml.write_text(COVERAGE_XML, encoding="utf-8")
    return orphan_check.load_coverage(xml)


# --- 解析 coverage.xml ---


def test_load_coverage_counts_hits(cov):
    assert cov["covered.py"] == (2, 2)
    assert cov["orphan.py"] == (0, 2)
    assert cov["empty.py"] == (0, 0)


# --- 判定孤儿 ---


def test_find_orphans_flags_zero_hit_modules(cov, src_tree):
    names = {name for name, _ in orphan_check.find_orphans(cov, src_tree)}
    assert "src/orphan.py" in names


def test_find_orphans_flags_modules_missing_from_report(cov, src_tree):
    reasons = dict(orphan_check.find_orphans(cov, src_tree))
    assert "src/not_in_report.py" in reasons
    assert "coverage.xml" in reasons["src/not_in_report.py"]


def test_find_orphans_ignores_covered_and_empty(cov, src_tree):
    names = {name for name, _ in orphan_check.find_orphans(cov, src_tree)}
    assert "src/covered.py" not in names
    # 空文件（只有 docstring 的 __init__.py）没有可执行行，不算孤儿
    assert "src/empty.py" not in names


def test_find_orphans_exempts_the_plugin_template(cov, src_tree):
    names = {name for name, _ in orphan_check.find_orphans(cov, src_tree)}
    assert "src/plugins/_template/__init__.py" not in names


def test_exempt_prefix_is_checked_by_prefix():
    assert orphan_check.is_exempt("plugins/_template/__init__.py")
    assert not orphan_check.is_exempt("plugins/trae_work_cn/__init__.py")


def test_find_orphans_reports_why(cov, src_tree):
    reasons = dict(orphan_check.find_orphans(cov, src_tree))
    assert "一行都没被执行过" in reasons["src/orphan.py"]


# --- CLI 退出码 ---
# main() 用的是模块级的 SOURCE_ROOT，所以这里把它指到造出来的 src/ 上。


@pytest.fixture
def cli_env(tmp_path, src_tree, monkeypatch):
    monkeypatch.setattr(orphan_check, "SOURCE_ROOT", src_tree)
    xml = tmp_path / "coverage.xml"
    xml.write_text(COVERAGE_XML, encoding="utf-8")
    return xml


def test_main_with_orphans_is_one(cli_env, capsys):
    assert orphan_check.main(["--cov", str(cli_env)]) == 1
    out = capsys.readouterr().out
    assert "src/orphan.py" in out
    assert "src/not_in_report.py" in out


def test_main_clean_is_zero(cli_env, capsys, src_tree):
    (src_tree / "orphan.py").unlink()
    (src_tree / "not_in_report.py").unlink()
    assert orphan_check.main(["--cov", str(cli_env)]) == 0
    assert "没有孤儿模块" in capsys.readouterr().out


def test_main_without_coverage_xml_is_two(tmp_path, capsys):
    assert orphan_check.main(["--cov", str(tmp_path / "nope.xml")]) == 2
    assert "先跑 make cov" in capsys.readouterr().err


def test_top_mode_does_not_fail(cli_env, capsys):
    assert orphan_check.main(["--cov", str(cli_env), "--top", "3"]) == 0
    assert "不判失败" in capsys.readouterr().out
