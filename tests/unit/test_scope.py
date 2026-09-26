"""T034/T035 unit tests: 范围选择器（scope matrix + scope.json 过滤生效）。"""

import json
from datetime import date, datetime

import pytest

from src import collect, config, scope, sync
from src.plugins import Plugin, RawMaterial, SourceRef


class FakePlugin:
    """Minimal plugin double: discover returns canned refs per day."""

    def __init__(self, name, by_day):
        self.name = name
        self.by_day = by_day
        self.discover_calls = []

    def discover(self, day):
        self.discover_calls.append(day)
        return self.by_day.get(day, [])

    def parse(self, ref):
        return RawMaterial(
            source=self.name, ref=ref.ref,
            ts=datetime(2026, 9, 20, 10, 0, 0, tzinfo=config.TZ),
            kind="message", text=f"material from {self.name}")


@pytest.fixture
def cfg_dir(tmp_data_dir, monkeypatch):
    cfg = tmp_data_dir / "config"
    cfg.mkdir()
    monkeypatch.setattr(config, "CONFIG_DIR", cfg)
    return cfg


TODAY = date(2026, 9, 20)


def ref():
    return SourceRef(source="claude_code", ref="x", day=TODAY)


# --- estimation matrix (FR-005 元信息) ---


def test_matrix_estimates_latest_day_and_counts(cfg_dir, monkeypatch):
    plugin = FakePlugin("claude_code", {
        TODAY: [ref(), ref()],                    # 今天 2 条
        date(2026, 9, 19): [ref()],               # 昨天 1 条
    })
    monkeypatch.setattr(scope, "iter_plugins", lambda: [Plugin(
        name="claude_code", discover=plugin.discover, parse=plugin.parse)])
    monkeypatch.setattr(scope, "_today", lambda: TODAY)

    matrix = scope.build_matrix(lookback_days=7)

    row = matrix["tools"]["claude_code"]
    assert row["latest_day"] == "2026-09-20"
    assert row["estimated_items"] == 3  # 7 天窗口内估算条数


def test_matrix_includes_repos_from_repos_txt(cfg_dir, monkeypatch):
    (cfg_dir / "repos.txt").write_text(
        "C:/Code/first-rag\nC:/Code/other\n", encoding="utf-8")
    monkeypatch.setattr(scope, "iter_plugins", lambda: [])  # 只测 repos 解析，不碰真实源

    matrix = scope.build_matrix()

    assert set(matrix["projects"]) == {"C:/Code/first-rag", "C:/Code/other"}


# --- selection model ---


def test_selection_toggle_and_save(cfg_dir):
    matrix = {"tools": {"claude_code": {"selected": True},
                        "codex": {"selected": True}},
              "projects": {"C:/Code/first-rag": {"selected": True}}}

    scope.toggle(matrix, "tools", "codex")
    scope.toggle(matrix, "projects", "C:/Code/first-rag")

    path = scope.save(matrix)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved == {"tools": {"claude_code": True, "codex": False},
                     "projects": {"C:/Code/first-rag": False}}


# --- AC-007: 取消勾选后 sync 跳过该工具 ---


def test_disabled_tool_is_skipped_in_gather(cfg_dir, monkeypatch):
    plugin = FakePlugin("claude_code", {TODAY: [ref()]})
    other = FakePlugin("codex", {TODAY: [
        SourceRef(source="codex", ref="y", day=TODAY)]})
    monkeypatch.setattr(collect, "iter_plugins", lambda: [
        Plugin(name=p.name, discover=p.discover, parse=p.parse)
        for p in (plugin, other)])
    (cfg_dir / "repos.txt").write_text("", encoding="utf-8")
    (cfg_dir / "scope.json").write_text(json.dumps(
        {"tools": {"claude_code": False}, "projects": {}}),
        encoding="utf-8")

    day_raw = collect.gather(TODAY, sync._load_scope())

    assert plugin.discover_calls == []          # 取消勾选：discover 都不调
    assert len(other.discover_calls) == 1       # 其余工具正常
    assert {m.source for m in day_raw.materials} == {"codex"}


def test_disabled_project_skips_repo_commits(cfg_dir, monkeypatch):
    (cfg_dir / "repos.txt").write_text(
        "C:/Code/first-rag\nC:/Code/other\n", encoding="utf-8")
    (cfg_dir / "scope.json").write_text(json.dumps(
        {"tools": {}, "projects": {"C:/Code/first-rag": False}}),
        encoding="utf-8")
    ran = []
    monkeypatch.setattr(collect, "iter_plugins", lambda: [])  # 只测 repos 勾选，不碰真实源
    monkeypatch.setattr(collect, "_repo_commits",
                        lambda repo, day: ran.append(repo) or [])

    collect.gather(TODAY, sync._load_scope())

    assert ran == ["C:/Code/other"]


# --- CLI ---


def test_main_non_interactive_save_all(cfg_dir, monkeypatch, capsys):
    monkeypatch.setattr(scope, "iter_plugins", lambda: [])
    monkeypatch.setattr(scope, "_today", lambda: TODAY)

    code = scope.main(["scope"], stdin_lines=["s"])  # s = save & exit

    assert code == 0
    saved = json.loads((cfg_dir / "scope.json").read_text(encoding="utf-8"))
    assert saved == {"tools": {}, "projects": {}}


def test_main_toggle_then_save(cfg_dir, monkeypatch):
    plugin = FakePlugin("claude_code", {})
    monkeypatch.setattr(scope, "iter_plugins", lambda: [Plugin(
        name=plugin.name, discover=plugin.discover, parse=plugin.parse)])
    monkeypatch.setattr(scope, "_today", lambda: TODAY)

    code = scope.main(["scope"], stdin_lines=["t claude_code", "s"])

    assert code == 0
    saved = json.loads((cfg_dir / "scope.json").read_text(encoding="utf-8"))
    assert saved["tools"] == {"claude_code": False}
