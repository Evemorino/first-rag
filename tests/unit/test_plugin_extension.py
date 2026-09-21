"""T043/T044: 配置驱动扩展的两个验收面（US-6 / AC-011）。

T043：按 _template 契约在临时目录写一个测试插件 → registry 自动发现、
素材进入当日采集——核心代码零改动（FR-001/宪法 III）。
T044：schema.json 新增类型 → 蒸馏直接识别，无代码迁移（FR-016/AC-011）。
"""

import json
import sys
from datetime import date, datetime

import pytest

from src import collect, config, distill
from src.plugins import RawMaterial, SourceRef, iter_plugins

DAY = date(2026, 9, 20)

PLUGIN_CODE = '''
"""测试插件：_template 契约的最小填充实例（US-6）。"""
from datetime import date, datetime, timedelta, timezone
from src.plugins import Plugin, RawMaterial, SourceRef

TZ = timezone(timedelta(hours=8))

def discover(day: date) -> list[SourceRef]:
    return [SourceRef(source="plugdemo", ref=DATA_FILE, day=day)]

def parse(ref: SourceRef) -> RawMaterial:
    text = open(ref.ref, encoding="utf-8").read()
    return RawMaterial(source="plugdemo", ref=ref.ref,
                       ts=datetime.now(tz=TZ), kind="message",
                       text=text, meta={"cwd": "/tmp/plugdemo"})

PLUGIN = Plugin(name="plugdemo", discover=discover, parse=parse)
'''

# discover 需要拿到数据文件路径：通过模块级注入而不是硬编码
PLUGIN_CODE = "DATA_FILE = __import__('os').environ['PLUGDEMO_DATA']\n" + PLUGIN_CODE


@pytest.fixture
def demo_plugin(tmp_path, monkeypatch):
    # registry 扫描包的子模块（src/plugins/<product>/），所以这里造一个
    # 父包 plugdemo_root/plugdemo/，对应「把插件目录放进 src/plugins/」
    root = tmp_path / "plugdemo_root"
    root.mkdir()
    (root / "__init__.py").write_text("", encoding="utf-8")
    pkg_dir = root / "plugdemo"
    pkg_dir.mkdir()
    data_file = tmp_path / "demo.txt"
    data_file.write_text("plugdemo: learned template contract", encoding="utf-8")
    (pkg_dir / "__init__.py").write_text(PLUGIN_CODE, encoding="utf-8")
    monkeypatch.setenv("PLUGDEMO_DATA", str(data_file))
    monkeypatch.syspath_prepend(str(tmp_path))
    sys.modules.pop("plugdemo_root", None)
    sys.modules.pop("plugdemo_root.plugdemo", None)
    yield tmp_path
    sys.modules.pop("plugdemo_root", None)
    sys.modules.pop("plugdemo_root.plugdemo", None)


def test_template_plugin_auto_registers(demo_plugin):
    plugins = iter_plugins("plugdemo_root")

    assert [p.name for p in plugins] == ["plugdemo"]


def test_new_plugin_collected_without_core_changes(
        demo_plugin, tmp_data_dir, monkeypatch):
    """AC-011 插件面：放入即被采集，核心零改动。"""
    monkeypatch.setattr(collect, "iter_plugins",
                        lambda: iter_plugins("plugdemo_root"))
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_data_dir / "cfg")

    day_raw = collect.gather(DAY)

    sources = {m.source for m in day_raw.materials}
    assert "plugdemo" in sources
    assert any("template contract" in m.text for m in day_raw.materials)


SCHEMA_WITH_NEW_TYPE = {
    "types": [
        {"name": "progress", "desc": "d"},
        {"name": "reflection", "desc": "d"},
        {"name": "insight", "desc": "newly added via config only"},
    ],
    "distill": {
        "include_signals": [], "exclude_signals": [],
        "examples": {"keep": [], "drop": []},
        "novelty_threshold": 0.82, "max_entries_per_day": 30,
        "struggle_rounds": 3, "max_raw_chars": 100000,
    },
    "retrieval": {"top_k": 8, "expand": {
        "mode": "all", "neighbor_limit_per_hit": 2,
        "context_cap": 12, "neighbor_min_score": None}},
    "trae_type_map": {}, "raw_retention_days": 90,
}


def test_new_config_type_accepted_by_llm_path(tmp_data_dir, monkeypatch):
    """T044/AC-011：schema 加类型 → 蒸馏产物直接被接受，零代码迁移。"""
    monkeypatch.setattr(distill.config, "load_schema",
                        lambda: SCHEMA_WITH_NEW_TYPE)
    monkeypatch.setattr(distill.config, "env",
                        lambda name: {"CHAT_MODEL": "m"}[name])
    monkeypatch.setattr(
        distill, "chat",
        lambda messages, json_mode=False: json.dumps({"entries": [{
            "text": "A new kind of learning worth its own type",
            "type": "insight", "tags": ["t"], "source_refs": ["s1"],
        }]}))

    day_raw = collect.DayRaw(
        day=DAY, collected_at=datetime(2026, 9, 20, 12, 0, 0),
        materials=[RawMaterial(
            source="claude_code", ref="s1",
            ts=datetime(2026, 9, 20, 10, 0, 0),
            kind="message", text="raw material")])

    entries = distill.distill(day_raw)

    assert [e.type for e in entries] == ["insight"]


def test_new_config_type_accepted_by_direct_path(tmp_data_dir, monkeypatch):
    monkeypatch.setattr(distill.config, "load_schema",
                        lambda: SCHEMA_WITH_NEW_TYPE)
    monkeypatch.setattr(distill, "chat",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("direct path must not call LLM")))

    day_raw = collect.DayRaw(
        day=DAY, collected_at=datetime(2026, 9, 20, 12, 0, 0),
        materials=[RawMaterial(
            source="manual", ref="inbox.md",
            ts=datetime(2026, 9, 20, 10, 0, 0),
            kind="note", text="typed note",
            meta={"note_type": "insight"})])

    entries = distill.distill(day_raw)

    assert [e.type for e in entries] == ["insight"]
