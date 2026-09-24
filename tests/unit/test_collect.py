"""Unit tests for collect.gather (T011): plugin no-op, notes parsing,
git no-op, char cap, snapshot persistence. All in system tmp (constitution V).
"""

import json
import subprocess
from datetime import date, datetime, timedelta, timezone

import pytest

from src import collect, config
from src.plugins import Plugin, RawMaterial, SourceRef

TZ = timezone(timedelta(hours=8))
DAY = date(2026, 9, 18)


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "NOTES_DIR", tmp_path / "notes")
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path / "config")
    monkeypatch.setattr(config, "RAW_DIR", tmp_path / "raw")
    (tmp_path / "notes").mkdir()
    (tmp_path / "config").mkdir()
    return tmp_path


def _fake_plugin(materials_factory, name="fake"):
    def discover(day):
        return [SourceRef(source=name, ref="x", day=day)]

    def parse(ref):
        return materials_factory(ref)

    return Plugin(name=name, discover=discover, parse=parse)


def test_gather_writes_snapshot_with_distill_meta(dirs, monkeypatch):
    monkeypatch.setattr(collect, "iter_plugins",
                        lambda: [_fake_plugin(lambda r: RawMaterial(
                            source="fake", ref="x", ts=datetime(2026, 9, 18, 10, tzinfo=TZ),
                            kind="message", text="hello", meta={}))])
    day_raw = collect.gather(DAY)
    path = collect.snapshot_path(DAY)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["date"] == "2026-09-18"
    assert saved["distill_run"] == {"status": "noop"}
    assert saved["materials"][0]["text"] == "hello"
    assert saved["materials"][0]["ts"].endswith("+08:00")
    assert len(day_raw.materials) == 1


def test_broken_plugin_is_noop_not_fatal(dirs, monkeypatch, caplog):
    def bad_discover(day):
        raise RuntimeError("boom")

    monkeypatch.setattr(collect, "iter_plugins",
                        lambda: [Plugin(name="bad", discover=bad_discover,
                                        parse=lambda r: None)])
    day_raw = collect.gather(DAY)  # must not raise
    assert day_raw.materials == []
    assert any("bad" in r.message for r in caplog.records)


def test_git_missing_repos_file_is_noop(dirs):
    # no repos.txt at all — gather still succeeds
    day_raw = collect.gather(DAY)
    assert all(m.source != "git" for m in day_raw.materials)


def test_note_lines_extracted_for_day(dirs):
    inbox = config.NOTES_DIR / "inbox.md"
    inbox.write_text(
        "- [2026-09-18T22:31:00+08:00 #idea] a tagged idea\n"
        "- [2026-09-18T23:00:00+08:00] plain note\n"
        "- [2026-09-17T09:00:00+08:00] yesterday, must not appear\n"
        "not a note line\n",
        encoding="utf-8",
    )
    mats = collect._note_materials(DAY)
    texts = [(m.text, m.meta["note_type"]) for m in mats]
    assert ("a tagged idea", "idea") in texts
    assert ("plain note", "reflection") in texts
    assert len(mats) == 2


def test_dated_note_file_picked_up(dirs):
    (config.NOTES_DIR / "2026-09-18-field-notes.md").write_text(
        "standalone note", encoding="utf-8")
    mats = collect._note_materials(DAY)
    assert any(m.text == "standalone note" for m in mats)


def test_char_cap_truncates(dirs, monkeypatch):
    big = "x" * 150
    monkeypatch.setattr(collect, "iter_plugins", lambda: [
        _fake_plugin(lambda r: RawMaterial(
            source="fake", ref="x", ts=datetime(2026, 9, 18, 10, tzinfo=TZ),
            kind="message", text=big, meta={}))])
    schema = json.loads(config.SCHEMA_PATH.read_text(encoding="utf-8"))
    schema["distill"]["max_raw_chars"] = 200
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(schema, f)
        monkeypatch.setattr(config, "SCHEMA_PATH", type(config.SCHEMA_PATH)(f.name))
    day_raw = collect.gather(DAY)
    total = sum(len(m.text) for m in day_raw.materials)
    assert total <= 200


# --- _cap：超长日截断（FR-006）---


def _day_raw(*texts):
    return collect.DayRaw(
        day=DAY,
        collected_at=datetime(2026, 9, 18, 22, tzinfo=TZ),
        materials=[
            RawMaterial(source="fake", ref=f"r{i}",
                        ts=datetime(2026, 9, 18, 10, tzinfo=TZ),
                        kind="message", text=text, meta={"k": "v"})
            for i, text in enumerate(texts)
        ],
    )


def test_cap_leaves_days_under_the_limit_untouched():
    day_raw = _day_raw("a" * 10, "b" * 10)

    collect._cap(day_raw, 1000)

    assert [m.text for m in day_raw.materials] == ["a" * 10, "b" * 10]


def test_cap_truncates_the_overflowing_material_when_room_is_usable():
    """剩余空间够（>100 字符）就截断保留，而不是整条丢掉。"""
    day_raw = _day_raw("a" * 100, "b" * 500)

    collect._cap(day_raw, 300)

    assert [m.text for m in day_raw.materials] == ["a" * 100, "b" * 200]
    # 截断后仍是一条完整素材：其余字段与 meta 都得留着
    cut = day_raw.materials[1]
    assert cut.source == "fake"
    assert cut.kind == "message"
    assert cut.meta == {"k": "v"}


def test_cap_drops_the_material_when_under_100_chars_would_remain():
    """剩下不到 100 字符就没意义了，整条丢弃而不是留个残片。"""
    day_raw = _day_raw("a" * 195, "b" * 500)

    collect._cap(day_raw, 200)

    assert [m.text for m in day_raw.materials] == ["a" * 195]


def test_cap_warns_about_the_cut(caplog):
    day_raw = _day_raw("a" * 100, "b" * 500)

    with caplog.at_level("WARNING", logger="src.collect"):
        collect._cap(day_raw, 300)

    assert any("day truncated at 100 chars" in record.getMessage()
               for record in caplog.records)


def test_scope_disables_tool(dirs, monkeypatch):
    calls = {"discover": 0}

    def discover(day):
        calls["discover"] += 1
        return []

    monkeypatch.setattr(collect, "iter_plugins",
                        lambda: [Plugin(name="fake", discover=discover,
                                        parse=lambda r: None)])
    collect.gather(DAY, scope={"tools": {"fake": False}})
    assert calls["discover"] == 0


# --- 变异测试分诊后补的测试：坏数据必须放在前面 ---
#
# 下面几条对应 5 个存活的 `continue` → `break` 变异体。它们原先杀不掉的原因
# 都一样：夹具里的坏数据排在**最后**，走到 break 时后面已经没有东西了，
# 于是 break 与 continue 的结果完全相同 —— 测试看着在测循环，其实没测。


def test_note_materials_keeps_reading_after_bad_lines_come_first(dirs):
    """坏行在前、好行在后：`continue` 改成 `break` 就会丢掉后面的好行。"""
    inbox = config.NOTES_DIR / "inbox.md"
    inbox.write_text(
        "not a note line\n"
        "- [definitely-not-an-iso-timestamp #idea] 时间戳坏了\n"
        "- [2026-09-18T22:31:00+08:00 #idea] 坏行之后仍要读到\n",
        encoding="utf-8",
    )

    mats = collect._note_materials(DAY)

    assert [m.text for m in mats] == ["坏行之后仍要读到"]


def test_git_materials_skips_comment_lines_but_keeps_reading(dirs, monkeypatch):
    """注释行在前、真仓库在后：break 会让真仓库整个不被采集。"""
    (config.CONFIG_DIR / "repos.txt").write_text(
        "# 这是注释\n~/Code/llm/rag/first-rag\n", encoding="utf-8")
    asked: list[str] = []
    monkeypatch.setattr(
        collect, "_repo_commits",
        lambda repo, day: asked.append(repo) or [])

    collect._git_materials(DAY, None)

    assert asked == ["~/Code/llm/rag/first-rag"]


def test_gather_keeps_later_refs_when_one_fails_to_parse(dirs, monkeypatch):
    """同一个插件的两个 ref：第一个 parse 抛异常，第二个必须照样入库。

    NFR-004 说的是"单个插件失败只跳过该插件"，而这里更细一层：一个 ref 坏了
    不能把同插件后面的 ref 一起带走。
    """
    def discover(day):
        return [SourceRef(source="fake", ref="broken", day=day),
                SourceRef(source="fake", ref="good", day=day)]

    def parse(ref):
        if ref.ref == "broken":
            raise RuntimeError("boom")
        return RawMaterial(source="fake", ref=ref.ref,
                           ts=datetime(2026, 9, 18, 10, tzinfo=TZ),
                           kind="message", text="kept")

    monkeypatch.setattr(collect, "iter_plugins",
                        lambda: [Plugin(name="fake", discover=discover, parse=parse)])

    day_raw = collect.gather(DAY)

    assert [m.text for m in day_raw.materials] == ["kept"]


def test_gather_collected_at_is_shanghai_aware(dirs, monkeypatch):
    """`collected_at` 必须带 Asia/Shanghai 时区，不能退化成进程本地时间。

    对应存活的 `datetime.now(tz=config.TZ)` → `tz=None` 变异体：本机时区恰好
    也是 +08，所以两种写法在测试里给出同一个值 —— 契约（PRD 时区约束）实际上
    一条都没被测过。断言偏移量而不是"相等"才测得到。
    """
    monkeypatch.setattr(collect, "iter_plugins", lambda: [])

    day_raw = collect.gather(DAY)

    assert day_raw.collected_at.utcoffset() == timedelta(hours=8)


def test_dated_note_file_timestamp_is_shanghai_aware(dirs):
    """独立笔记的 ts 取自文件 mtime，也必须折算成上海时区。"""
    note = config.NOTES_DIR / "2026-09-18-field-notes.md"
    note.write_text("standalone note", encoding="utf-8")

    mats = collect._dated_note_files(DAY)

    assert [m.ts.utcoffset() for m in mats] == [timedelta(hours=8)]


def test_gather_keeps_later_plugins_when_an_earlier_discover_raises(dirs, monkeypatch):
    """第一个插件 discover 抛异常，第二个插件必须仍被采集。

    对应存活的 `continue` → `break`（collect.x_gather__mutmut_25）。已有的
    test_broken_plugin_is_noop_not_fatal 只放**一个**坏插件，break 与 continue
    在那个形状下等价 —— NFR-004 要的是"不中断其他来源"，那就必须有"坏在前、
    好在后"的两个插件才测得到。
    """
    def bad_discover(day):
        raise RuntimeError("discover exploded")

    bad = Plugin(name="bad", discover=bad_discover, parse=lambda r: None)
    good = _fake_plugin(lambda r: RawMaterial(
        source="fake", ref="x", ts=datetime(2026, 9, 18, 10, tzinfo=TZ),
        kind="message", text="still collected"), name="good")
    monkeypatch.setattr(collect, "iter_plugins", lambda: [bad, good])

    day_raw = collect.gather(DAY)

    assert [m.text for m in day_raw.materials] == ["still collected"]


# --- 变异分诊后剩下的 collect 缺口：git 源的超时与时间戳 ---


def test_repo_commits_passes_a_subprocess_timeout(dirs, monkeypatch):
    """git log 必须带超时；否则一个挂住的仓库会让 sync 永久卡死（NFR-006）。

    对应存活的 `timeout=30` → `timeout=None`：这条在两轮重建里都活着，因为测试
    只覆盖了"git 失败时降级为 no-op"，从没检查过**有没有**超时。挂住不是异常，
    是永远不返回，所以 except 分支那条测试根本挡不住它。
    """
    seen = {}

    def fake_run(argv, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    (config.CONFIG_DIR / "repos.txt").write_text("~/x\n", encoding="utf-8")
    monkeypatch.setattr(collect.subprocess, "run", fake_run)

    collect._repo_commits("~/x", DAY)

    assert seen["timeout"] == 30


def test_repo_commit_material_carries_the_author_timestamp(dirs, monkeypatch):
    """提交条目的 ts 必须来自 git 的作者日期，不能是 None。

    对应存活的 `ts=datetime.fromisoformat(ad)` → `ts=None`。ts 是 payload 的
    date 来源之一，丢了它条目就没有时间归属。
    """
    ad = "2026-09-18T10:00:00+08:00"
    out = f"abc123\x00{ad}\x00subject\x00body\x1e"
    monkeypatch.setattr(
        collect.subprocess, "run",
        lambda argv, **kw: subprocess.CompletedProcess(argv, 0, stdout=out, stderr=""))

    mats = collect._repo_commits("~/x", DAY)

    assert [m.ts for m in mats] == [datetime.fromisoformat(ad)]


def test_iso_returns_none_for_a_missing_timestamp():
    """`_iso(None)` 必须是 None，不是抛异常。

    对应存活的 `if ts` → `if (ts) or True`：真值兜底被写成恒真，None 就会
    AttributeError。这种变异只有"喂一个 None 进去"的测试能杀。
    """
    assert collect._iso(None) is None
    assert collect._iso(datetime(2026, 9, 18, 10, tzinfo=TZ)) == \
        "2026-09-18T10:00:00+08:00"
