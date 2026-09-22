"""scripts/mutation_reminder.py 的单元测试。

它守的是"改了核心代码却没人记得重跑变异测试"这个洞 —— 变异跑一遍半小时，
进不了 pre-commit，所以只能靠提醒。提醒本身必须准：漏报等于没装，
谎报几次之后就会被 ignore，那就比没有更糟（又是一个没人信的信号）。
"""

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import mutation_reminder as mr  # noqa: E402  (先补 sys.path 才能导入)


def test_only_mutate_reads_the_real_config():
    targets = mr.only_mutate()
    assert "src/ids.py" in targets
    assert "src/sanitize.py" in targets  # 宪法 V 的边界也在里面


# --- 命中判定：这是它唯一真正要算对的事 ---


def test_hits_when_a_mutated_file_changed():
    targets = ["src/ids.py", "src/sync.py"]
    assert mr.find_hits(["README.md", "src/ids.py"], targets) == ["src/ids.py"]


def test_no_hits_when_only_other_files_changed():
    targets = ["src/ids.py", "src/sync.py"]
    assert mr.find_hits(["tests/unit/test_ids.py", "README.md"], targets) == []


def test_hits_keep_the_order_of_the_mutation_list():
    targets = ["src/a.py", "src/b.py", "src/c.py"]
    assert mr.find_hits(["src/c.py", "src/a.py"], targets) == ["src/a.py", "src/c.py"]


def test_no_hits_when_nothing_changed():
    assert mr.find_hits([], ["src/ids.py"]) == []


def test_changed_files_returns_empty_when_git_fails(monkeypatch):
    """git 报错（比如仓库只有一个 commit，HEAD~1 不存在）不该炸。"""

    def boom(*_args, **_kwargs):
        return subprocess.CompletedProcess([], returncode=128, stdout="", stderr="fatal")

    monkeypatch.setattr(mr.subprocess, "run", boom)
    assert mr.changed_files("HEAD~1") == []


# --- 退出码：只提醒，不拦人 ---


def test_main_stays_quiet_without_hits(capsys):
    assert mr.main(["--since", "HEAD"]) == 0
    assert capsys.readouterr().out == ""


def test_main_reminds_but_exits_zero(monkeypatch, capsys):
    monkeypatch.setattr(mr, "changed_files", lambda _since: ["src/ids.py"])
    assert mr.main(["--since", "HEAD"]) == 0
    assert "src/ids.py" in capsys.readouterr().out


def test_json_output(monkeypatch, capsys):
    import json

    monkeypatch.setattr(mr, "changed_files", lambda _since: ["src/ids.py"])
    assert mr.main(["--since", "HEAD", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["changed_and_covered"] == ["src/ids.py"]
