"""scripts/mutation_selfcheck.py 的单元测试。

它守的是"变异分数可不可信"，而它自己会往源码里写东西。一旦被中断，就可能
把源码留在改坏的状态 —— 真踩过一次：SIGTERM 打断了 `finally`，`src/ids.py`
留在 `NAMESPACE_DNS`，紧接着的全量 pytest 因此红了 1 个，而那个红跟测试
质量毫无关系。所以"还原"和"预检"这两条必须有测试盯着。
"""

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import mutation_selfcheck as ms  # noqa: E402  (先补 sys.path 才能导入)

IDS_FILE = "src/ids.py"
GOOD_SOURCE = "return uuid.uuid5(uuid.NAMESPACE_URL, identity)\n"
BAD_SOURCE = "return uuid.uuid5(uuid.NAMESPACE_DNS, identity)\n"

NAMESPACE_CANARY = ms.Canary(
    name="ids.point_id 命名空间 URL → DNS",
    file=IDS_FILE,
    find="uuid.NAMESPACE_URL",
    replace="uuid.NAMESPACE_DNS",
    tests=("tests/unit/test_ids.py",),
)


@pytest.fixture(autouse=True)
def clean_pending():
    """别让一个测试残留的"待还原"状态影响下一个。"""
    ms._PENDING_RESTORE.clear()
    yield
    ms._PENDING_RESTORE.clear()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    (tmp_path / "src").mkdir()
    (tmp_path / IDS_FILE).write_text(GOOD_SOURCE, encoding="utf-8")
    monkeypatch.setattr(ms, "REPO_ROOT", tmp_path)
    return tmp_path


# --- 预检：把"上次中断留下的污染"挡在开跑之前 ---


def test_preflight_passes_when_source_is_intact(repo):
    assert ms.preflight((NAMESPACE_CANARY,)) == []


def test_preflight_catches_leftover_mutation(repo):
    (repo / IDS_FILE).write_text(BAD_SOURCE, encoding="utf-8")
    problems = ms.preflight((NAMESPACE_CANARY,))
    assert len(problems) == 1
    assert "uuid.NAMESPACE_URL" in problems[0]
    # 提示里得说清"可能是上次没还原"，否则下一个人只会以为 canary 写错了
    assert "没还原" in problems[0]
    assert "git diff" in problems[0]


def test_preflight_catches_missing_file(repo):
    missing = ms.Canary("m", "src/nope.py", "a", "b", ("tests/unit/test_ids.py",))
    assert ms.preflight((missing,)) == ["找不到源码文件：src/nope.py"]


def test_preflight_reports_every_broken_canary(repo):
    (repo / IDS_FILE).write_text(BAD_SOURCE, encoding="utf-8")
    second = ms.Canary("second", IDS_FILE, "hashlib.sha256", "hashlib.md5", ("t",))
    assert len(ms.preflight((NAMESPACE_CANARY, second))) == 2


# --- 还原：无论怎么被打断，都得把源码放回去 ---


def test_restore_all_puts_source_back(tmp_path):
    src = tmp_path / "ids.py"
    src.write_text(GOOD_SOURCE, encoding="utf-8")
    backup_dir = tmp_path / "bak"
    backup_dir.mkdir()
    backup = backup_dir / "ids.py"
    backup.write_text(GOOD_SOURCE, encoding="utf-8")
    src.write_text(BAD_SOURCE, encoding="utf-8")  # 变异已写入

    ms._PENDING_RESTORE[src] = backup
    ms.restore_all()

    assert src.read_text(encoding="utf-8") == GOOD_SOURCE
    assert ms._PENDING_RESTORE == {}


def test_restore_all_is_idempotent(tmp_path):
    src = tmp_path / "ids.py"
    backup_dir = tmp_path / "bak"
    backup_dir.mkdir()
    backup = backup_dir / "ids.py"
    src.write_text(BAD_SOURCE, encoding="utf-8")
    backup.write_text(GOOD_SOURCE, encoding="utf-8")

    ms._PENDING_RESTORE[src] = backup
    ms.restore_all()
    ms.restore_all()  # atexit 与 signal handler 可能各来一次

    assert src.read_text(encoding="utf-8") == GOOD_SOURCE


def test_restore_all_tolerates_vanished_backup(tmp_path):
    """备份被清掉了也不能抛异常 —— 那是 finally 之后的第二次调用。"""
    src = tmp_path / "ids.py"
    src.write_text(BAD_SOURCE, encoding="utf-8")
    ms._PENDING_RESTORE[src] = tmp_path / "gone.py"
    ms.restore_all()
    assert ms._PENDING_RESTORE == {}
