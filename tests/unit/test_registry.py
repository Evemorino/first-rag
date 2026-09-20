"""Registry behavior: silent skip on broken/missing plugins (T009 / AC-006).

Fixture packages are built in system tmp (constitution V).
"""

import sys
import textwrap
from datetime import date

import pytest

from src.plugins import Plugin, RawMaterial, SourceRef, iter_plugins

GOOD_PLUGIN = textwrap.dedent("""
    from datetime import date
    from src.plugins import Plugin, RawMaterial, SourceRef

    def discover(day):
        return [SourceRef(source="good", ref="f.jsonl", day=day)]

    def parse(ref):
        return RawMaterial(source="good", ref=ref.ref,
                           ts=None, kind="message", text="t", meta={})

    PLUGIN = Plugin(name="good", discover=discover, parse=parse)
""")

BROKEN_IMPORT = "raise RuntimeError('product SDK exploded')\n"

NO_PLUGIN_ATTR = "X = 1\n"


@pytest.fixture
def plugin_pkg(tmp_path, monkeypatch):
    """Build a plugin package in tmp with good/broken/no-PLUGIN members."""
    pkg = tmp_path / "fakeplugins"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "good_plugin").mkdir()
    (pkg / "good_plugin" / "__init__.py").write_text(GOOD_PLUGIN, encoding="utf-8")
    (pkg / "broken_plugin").mkdir()
    (pkg / "broken_plugin" / "__init__.py").write_text(BROKEN_IMPORT, encoding="utf-8")
    (pkg / "noattr_plugin").mkdir()
    (pkg / "noattr_plugin" / "__init__.py").write_text(NO_PLUGIN_ATTR, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    sys.modules.pop("fakeplugins", None)
    yield "fakeplugins"
    for name in list(sys.modules):
        if name.startswith("fakeplugins"):
            del sys.modules[name]


def test_iter_plugins_returns_only_valid(plugin_pkg):
    plugins = iter_plugins(plugin_pkg)
    assert [p.name for p in plugins] == ["good"]


def test_broken_plugin_does_not_raise(plugin_pkg, caplog):
    plugins = iter_plugins(plugin_pkg)  # must not raise despite broken member
    assert len(plugins) == 1
    assert any("broken_plugin" in r.message for r in caplog.records)


def test_missing_plugin_attr_logged(plugin_pkg, caplog):
    iter_plugins(plugin_pkg)
    assert any("noattr_plugin" in r.message for r in caplog.records)


def test_template_dir_is_ignored():
    # _template ships NotImplementedError stubs; registry must not pick it up
    names = [p.name for p in iter_plugins()]
    assert "_template" not in names


def test_type_roundtrip():
    ref = SourceRef(source="good", ref="f", day=date(2026, 9, 18))
    mat = RawMaterial(source="good", ref="f", ts=None, kind="note", text="t")
    assert ref.ref == mat.ref
    assert isinstance(Plugin(name="x", discover=lambda d: [], parse=lambda r: mat), Plugin)
