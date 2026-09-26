"""`scripts/collect_probe.py` 的单元测试（T083）。

这个脚本要替 AC-015 的「这个源今天有素材吗」卸掉写盘这一步，所以三件事必须测准：
**它真的不写盘**（而且不写盘是靠自校证明的，不是靠注释承诺）、**`--source` 的合计
口径只算那一个源**（scope 关不掉 git 与手动快记，所以口径在 render 这一层兑现）、
**用法错的退出码**（2 而不是 1 —— 1 是"探针发现自己动了盘"，两者混了就没法自动化）。
"""

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import collect_probe as probe  # noqa: E402  (先补 sys.path 才能导入)

from src import collect, config  # noqa: E402
from src.plugins import Plugin, RawMaterial, SourceRef  # noqa: E402

TZ = timezone(timedelta(hours=8))
DAY = date(2026, 9, 18)


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    """与 test_collect.py 同一个隔离方式：一切落在 system tmp（宪法 V）。"""
    monkeypatch.setattr(config, "NOTES_DIR", tmp_path / "notes")
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path / "config")
    monkeypatch.setattr(config, "RAW_DIR", tmp_path / "raw")
    (tmp_path / "notes").mkdir()
    (tmp_path / "config").mkdir()
    return tmp_path


def _plugins(*names) -> list[Plugin]:
    return [Plugin(name=n, discover=lambda d: [], parse=lambda r: None)
            for n in names]


def _ref(day: date) -> SourceRef:
    return SourceRef(source="fake", ref="x", day=day)


def _day_raw(*sources: str) -> collect.DayRaw:
    return collect.DayRaw(
        day=DAY, collected_at=datetime(2026, 9, 18, 10, tzinfo=TZ),
        materials=[RawMaterial(source=s, ref="x", kind="message", text="t", meta={},
                               ts=datetime(2026, 9, 18, 10, tzinfo=TZ))
                   for s in sources])


# --- scope：只看一个源 ---


def test_scope_for_source_none_means_every_source(monkeypatch):
    monkeypatch.setattr(probe, "iter_plugins", lambda: _plugins("qoder"))

    assert probe.scope_for_source(None) is None


def test_scope_for_source_writes_every_other_tool_as_false(monkeypatch):
    """缺键 = 启用，所以"只看一个源"必须把其余源**显式**写 false。

    写成 `{"tools": {"qoder": True}}` 会静默退化成全源 —— 那正是本探针最不该犯的
    错：读起来是"qoder 有素材"，数的是所有源。
    """
    monkeypatch.setattr(probe, "iter_plugins",
                        lambda: _plugins("claude_code", "codex", "qoder"))

    assert probe.scope_for_source("qoder") == {
        "tools": {"claude_code": False, "codex": False, "qoder": True}}


def test_scope_for_source_rejects_unknown_names(monkeypatch):
    monkeypatch.setattr(probe, "iter_plugins", lambda: _plugins("qoder"))

    with pytest.raises(KeyError):
        probe.scope_for_source("qoder_cn")


# --- render：合计口径 ---


def test_render_without_source_counts_every_material(dirs):
    lines = probe.render(DAY, _day_raw("qoder", "git", "git"), None, None)

    text = "\n".join(lines)
    assert "素材合计 3 条" in text
    assert "git    2" in text


def test_render_counts_only_the_requested_source(dirs):
    """scope 关不掉 git 与手动快记，所以"只看一个源"必须在合计口径上兑现。

    顺带采到的来源仍然列出来（不静默丢掉 —— 它们当天真的被采到了），但要标明
    没算进合计，否则 `--source qoder` 报的"素材合计"读起来像 qoder 的条数。
    """
    lines = probe.render(DAY, _day_raw("qoder", "git", "git"), None, None,
                         source="qoder")

    text = "\n".join(lines)
    assert "素材合计 1 条（--source qoder；全源 3 条）" in text
    assert "未计入合计" in text
    assert "git    2" in text


def test_render_zero_for_the_requested_source_is_a_conclusion(dirs):
    lines = probe.render(DAY, _day_raw("git"), None, None, source="qoder")

    text = "\n".join(lines)
    assert "素材合计 0 条" in text
    assert "qoder 今天没有素材" in text


def test_render_reports_a_snapshot_that_was_touched(dirs):
    lines = probe.render(DAY, _day_raw("qoder"), None, (12, 34))

    assert "被改动了" in "\n".join(lines)


# --- 自校：探针不写盘 ---


def test_snapshot_fingerprint_is_none_until_the_file_exists(dirs):
    assert probe.snapshot_fingerprint(DAY) is None

    collect.snapshot_path(DAY).parent.mkdir(parents=True, exist_ok=True)
    collect.snapshot_path(DAY).write_text("{}", encoding="utf-8")

    fingerprint = probe.snapshot_fingerprint(DAY)
    assert fingerprint is not None and fingerprint[0] == 2


def test_main_leaves_no_snapshot_behind(dirs, monkeypatch, capsys):
    """端到端：采集照跑（素材数照给），盘上什么都没多。"""
    monkeypatch.setattr(probe, "iter_plugins", lambda: _plugins("fake"))
    monkeypatch.setattr(collect, "iter_plugins", lambda: [
        Plugin(name="fake",
               discover=lambda d: [_ref(d)], parse=lambda r: RawMaterial(
                   source="fake", ref="x", kind="message", text="hello", meta={},
                   ts=datetime(2026, 9, 18, 10, tzinfo=TZ)))])

    assert probe.main(["--day", "2026-09-18"]) == 0

    assert "素材合计 1 条" in capsys.readouterr().out
    assert not collect.snapshot_path(DAY).exists()



def test_main_exits_1_when_the_gather_would_write(dirs, monkeypatch, capsys):
    """自校那一半：补一个**真会写盘**的 gather，探针必须自己发现并红。

    没有这条，"探针不写盘"就只是注释里的承诺 —— 而这里的假实现证明探针看的是盘，
    不是返回值。
    """
    monkeypatch.setattr(probe, "iter_plugins", lambda: [])
    monkeypatch.setattr(collect, "iter_plugins", lambda: [])

    def leaky_gather(day, scope=None, **kwargs):
        day_raw = collect.DayRaw(day=day,
                                 collected_at=datetime(2026, 9, 18, tzinfo=TZ))
        collect.save_snapshot(day_raw)
        return day_raw

    monkeypatch.setattr(collect, "gather", leaky_gather)

    assert probe.main(["--day", "2026-09-18"]) == 1
    assert "写了盘" in capsys.readouterr().err


# --- 退出码：用法错是 2，不是 1 ---


def test_main_exits_2_on_an_unknown_source(dirs, monkeypatch, capsys):
    monkeypatch.setattr(probe, "iter_plugins", lambda: _plugins("qoder"))

    assert probe.main(["--day", "2026-09-18", "--source", "nope"]) == 2
    assert "没有名为" in capsys.readouterr().err


def test_main_exits_2_on_a_bad_day(dirs, capsys):
    assert probe.main(["--day", "2026-09-2X"]) == 2
    assert "YYYY-MM-DD" in capsys.readouterr().err
