"""scripts/schema_check.py 的单测。

被测的是纯逻辑（格式判定、结构归并、渲染），文件遍历那半边只是 IO，
把判断拆出来才测得到规则本身。

与 test_secret_scan.py 同一条约定：先补 sys.path 再直接 import 脚本，
不牵扯 src/ 的依赖链（不需要 .env，也不需要 Qdrant）。
"""

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import schema_check  # noqa: E402  (先补 sys.path 才能导入)


# --- 文本 → 记录 ---


def test_parse_records_handles_jsonl():
    assert schema_check.parse_records('{"a": 1}\n{"a": 2}\n') == [{"a": 1}, {"a": 2}]


def test_parse_records_handles_single_json_document():
    assert schema_check.parse_records('{"a": [1, 2]}') == [{"a": [1, 2]}]


def test_parse_records_skips_blank_lines():
    assert schema_check.parse_records('{"a": 1}\n\n  \n{"a": 2}\n') == [
        {"a": 1}, {"a": 2}]


# --- 记录 → 结构 ---


def test_merge_shapes_records_dotted_paths_and_types():
    shapes = schema_check.merge_shapes([{"m": {"text": "x"}}, {"m": {"text": 3}}])

    assert "m.text" in shapes
    assert shapes["m.text"]["types"] == {"str", "int"}


def test_merge_shapes_survives_missing_keys_across_records():
    """真实会话里字段常常时有时无，缺键不能把已见到的路径抹掉。"""
    shapes = schema_check.merge_shapes([{"a": 1, "b": 2}, {"a": 1}])

    assert "b" in shapes
    assert shapes["a"]["types"] == {"int"}


def test_merge_shapes_collapses_list_items_to_bracket_suffix():
    shapes = schema_check.merge_shapes(
        [{"parts": [{"type": "text"}, {"type": "tool"}]}])

    assert "parts[].type" in shapes
    assert shapes["parts[].type"]["types"] == {"str"}


def test_merge_shapes_keeps_list_of_scalars_as_the_list_path():
    shapes = schema_check.merge_shapes([{"learned": ["a", "b"]}])

    assert shapes["learned"]["types"] == {"list"}
    assert "learned[]" in shapes


def test_merge_shapes_truncates_long_examples():
    """素材正文动辄几千字，不截断的话这份报告没法看。"""
    shapes = schema_check.merge_shapes([{"t": "x" * 200}])

    assert len(shapes["t"]["example"]) <= schema_check.MAX_EXAMPLE + 1


def test_merge_shapes_respects_max_depth():
    deep = {"a": {"b": {"c": {"d": {"e": 1}}}}}

    shapes = schema_check.merge_shapes([deep], max_depth=2)

    assert "a.b.c.d.e" not in shapes
    assert "a.b.c" in shapes


def test_render_lists_every_path_sorted():
    shapes = schema_check.merge_shapes([{"b": 1, "a": {"z": "v"}}])

    text = schema_check.render(shapes)

    assert text.index("a.z") < text.index("b")


# --- SQLite ---


def test_is_sqlite_detects_by_header_not_extension(tmp_path):
    """扩展名会骗人：源目录里 .db 未必是库，库也未必叫 .db。

    注意 connect() 建出来的新库是零字节的 —— SQLite 到第一次写入才落文件头，
    所以必须先建一张表，否则这夹具测的是"空文件"而不是"库文件"。
    """
    real = tmp_path / "real.db"
    con = sqlite3.connect(real)
    con.execute("create table t(a integer)")
    con.commit()
    con.close()
    liar = tmp_path / "liar.db"
    liar.write_text("{}")

    assert schema_check.is_sqlite(real)
    assert not schema_check.is_sqlite(liar)


def test_describe_sqlite_lists_tables_and_columns(tmp_path):
    db = tmp_path / "x.db"
    con = sqlite3.connect(db)
    con.execute("create table sessions(id integer, name text)")
    con.execute("insert into sessions values (1, 'a')")
    con.commit()
    con.close()

    text = schema_check.describe_sqlite(db)

    assert "sessions" in text
    assert "name" in text


def test_describe_sqlite_does_not_write_to_the_source_dir(tmp_path):
    """宪法 V：产品源目录只读。

    普通 connect() 会在源目录建 -wal/-journal 文件 —— 那是实打实的写入。
    这条断言盯的就是"必须走 mode=ro URI"。
    """
    db = tmp_path / "x.db"
    con = sqlite3.connect(db)
    con.execute("create table t(a integer)")
    con.commit()
    con.close()
    before = sorted(p.name for p in tmp_path.iterdir())

    schema_check.describe_sqlite(db)

    assert sorted(p.name for p in tmp_path.iterdir()) == before


def test_describe_sqlite_handles_empty_database(tmp_path):
    """hermes 本机就是表结构齐全、零行数据。"""
    db = tmp_path / "empty.db"
    sqlite3.connect(db).close()

    assert "no tables" in schema_check.describe_sqlite(db).lower()
