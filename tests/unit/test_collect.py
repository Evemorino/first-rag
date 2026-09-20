"""Unit tests for collect.gather (T011): plugin no-op, notes parsing,
git no-op, char cap, snapshot persistence. All in system tmp (constitution V).
"""

import json
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
