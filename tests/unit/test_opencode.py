"""Unit tests for the opencode plugin (T054) — `~/.local/share/opencode/opencode.db`。

真实形状（2026-09-25 只读勘察，85 行 `session_message`，全部落在 2026-09-23）：

    session_message(id, session_id, type, seq, time_created, time_updated, data)

`type` 列取值与 `data` 的键集合一一对应，实测六种组合：

| 条数 | type          | data 键 |
|---|---|---|
| 14 | `user`          | `{agents, files, text, time}` —— 真用户输入在 `text` |
| 48 | `assistant`     | `{agent, content, cost, finish, model, rawFinish, snapshot, time, tokens}` |
|  4 | `assistant`     | 同上但多一个 `error`（provider 报错） |
|  3 | `assistant`     | 同上但少 `rawFinish` |
| 14 | `idle`          | `{outcome, time}` —— `outcome` 是 `succeeded`/`failed`，**不是素材** |
|  2 | `model-switched`| `{model, previous, time}` —— **不是素材** |

assistant 的 `content` 块实测三种：`reasoning` 51 / `tool` 68 / `text` 30。只有
`text` 进正文；`tool` 块的 `state.status` 实测 `completed` 63 / `error` 5，取
`error` 作 FR-008 的失败判据（`state.error` 是 `{type, message}` 字典）。

凭据边界（NFR-001）：库内有 `credential`（1 行）/`account`/`control_account`
三张表，本插件**表允许列表只有 `session_message`**，一张都不读——单测里植入
哨兵值来证明。`cwd` 只能从 `session_v2.directory` 取，为守住最小允许列表
**故意不取**，`meta.cwd` 恒为 None（实测 `session_message.data` 里没有
`path`/`directory`/`cwd` 任何一键）。

源目录只读（宪法 V）：连接一律 `file:…?mode=ro` + `uri=True`。
"""

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone

import pytest

from src.plugins import SourceRef, opencode

DAY = date(2026, 9, 23)          # 实测 opencode 全部数据所在日
OTHER = date(2026, 9, 22)
TZ = timezone(timedelta(hours=8))

SESSION_A = "ses_f33f04934ffed3j9tc06doMoWg"
SESSION_B = "ses_f3421ab85ffeHPniJdlHo17YDJ"

# 故意不带 sk- 形态：那是令牌形状，提交门禁（scripts/secret_scan.py 的 token
# 规则）会把它判成提交物泄漏。这里要的只是一个"必须不出现在产出物里"的哨兵，
# 不带令牌形状反而更灵敏 —— 它不会被 sanitize 抹掉，凭据表一旦被读就必然暴露。
SECRET = "opencode-CREDENTIAL-SENTINEL"

SCHEMA = """
CREATE TABLE session_message (
    id TEXT PRIMARY KEY, session_id TEXT, type TEXT, seq INTEGER,
    time_created INTEGER, time_updated INTEGER, data TEXT
);
CREATE TABLE credential (id TEXT PRIMARY KEY, secret TEXT);
CREATE TABLE account (id TEXT PRIMARY KEY, secret TEXT);
CREATE TABLE control_account (id TEXT PRIMARY KEY, secret TEXT);
"""


def ms(y, mo, d, h=10, mi=0) -> int:
    """上海时间 → epoch 毫秒。"""
    return int(datetime(y, mo, d, h, mi, tzinfo=TZ).timestamp() * 1000)


def user_message(text, ts=ms(2026, 9, 23)) -> dict:
    return {"type": "user", "seq": 0,
            "data": {"time": {"created": ts}, "text": text,
                     "files": [], "agents": []}}


def assistant(blocks, ts=ms(2026, 9, 23)) -> dict:
    return {"type": "assistant", "seq": 1,
            "data": {"time": {"created": ts}, "agent": "build",
                     "model": {"id": "deepseek-v4.1-flash"},
                     "content": blocks, "snapshot": {}}}


def text_block(text) -> dict:
    return {"type": "text", "text": text}


def reasoning_block(text) -> dict:
    return {"type": "reasoning", "text": text}


def tool_block(name="Bash", status="completed", error=None, inp=None) -> dict:
    state = {"status": status, "input": inp or {"command": "ls"}}
    if error is not None:
        state["error"] = error
    return {"type": "tool", "id": "call_1", "name": name, "executed": False,
            "state": state, "time": {}}


def idle(outcome="succeeded", ts=ms(2026, 9, 23)) -> dict:
    return {"type": "idle", "seq": 9,
            "data": {"time": {"created": ts}, "outcome": outcome}}


def model_switched(ts=ms(2026, 9, 23)) -> dict:
    return {"type": "model-switched", "seq": 2,
            "data": {"time": {"created": ts}, "model": {"id": "x"},
                     "previous": {"id": "y"}}}


def make_db(path, rows):
    """建一个只有 opencode 真实 schema 子集的库，植入凭据哨兵。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.executescript(SCHEMA)
        for t in ("credential", "account", "control_account"):
            conn.execute(f"INSERT INTO {t} (id, secret) VALUES ('x', ?)", (SECRET,))
        for i, row in enumerate(rows):
            conn.execute(
                "INSERT INTO session_message (id, session_id, type, seq, "
                "time_created, time_updated, data) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (f"msg_{i:03d}", row["session_id"], row["type"], row.get("seq", i),
                 row["ts"], row["ts"], json.dumps(row["data"], ensure_ascii=False)))
        conn.commit()
    finally:
        conn.close()
    return path


def row(session_id, record, ts) -> dict:
    return {"session_id": session_id, "type": record["type"],
            "seq": record["seq"], "ts": ts, "data": record["data"]}


@pytest.fixture
def db(tmp_path, monkeypatch):
    """只把 `DB_PATH` 指到 tmp；库由 `fill()` 建，文件不存在时 discover 该是 no-op。"""
    path = tmp_path / "opencode.db"
    monkeypatch.setattr(opencode, "DB_PATH", path)
    return path


def fill(path, rows):
    return make_db(path, rows)


def ref(session_id=SESSION_A, day=DAY) -> SourceRef:
    return SourceRef(source="opencode", ref=f"{opencode.DB_PATH}#{session_id}", day=day)


# --- discover ---


def test_discover_returns_one_ref_per_session(db):
    fill(db, [row(SESSION_A, user_message("改一下 upsert"), ms(2026, 9, 23, 9)),
              row(SESSION_A, assistant([text_block("好")]), ms(2026, 9, 23, 9, 1)),
              row(SESSION_B, user_message("另一个会话"), ms(2026, 9, 23, 11)),
              row(SESSION_B, assistant([text_block("嗯")]), ms(2026, 9, 23, 11, 1))])

    refs = opencode.discover(DAY)

    assert [r.ref for r in refs] == [f"{db}#{SESSION_A}", f"{db}#{SESSION_B}"]
    assert all(r.source == "opencode" and r.day == DAY for r in refs)


def test_discover_filters_by_day(db):
    fill(db, [row(SESSION_A, user_message("昨天的话", ms(2026, 9, 22, 8)),
                  ms(2026, 9, 22, 8)),
              row(SESSION_A, user_message("今天的话"), ms(2026, 9, 23, 8))])

    assert [r.ref for r in opencode.discover(DAY)] == [f"{db}#{SESSION_A}"]
    assert [r.ref for r in opencode.discover(OTHER)] == [f"{db}#{SESSION_A}"]


def test_discover_ignores_a_session_without_material(db):
    """只有 `idle` / `model-switched` 的会话不构成素材（实测这两类共 16 条）。"""
    fill(db, [row(SESSION_A, idle("failed"), ms(2026, 9, 23, 9)),
              row(SESSION_A, model_switched(), ms(2026, 9, 23, 9, 1))])

    assert opencode.discover(DAY) == []


def test_discover_across_the_midnight_boundary(db):
    """23:59:59.999 属当天，次日 00:00:00.000 不属于——边界按上海时区切。"""
    fill(db, [row(SESSION_A, user_message("压线", ms(2026, 9, 23, 23, 59)),
                  ms(2026, 9, 23, 23, 59)),
              row(SESSION_B, user_message("过线", ms(2026, 9, 24, 0, 0)),
                  ms(2026, 9, 24, 0, 0))])

    assert [r.ref for r in opencode.discover(DAY)] == [f"{db}#{SESSION_A}"]
    assert [r.ref for r in opencode.discover(date(2026, 9, 24))] == [f"{db}#{SESSION_B}"]


def test_discover_missing_db_is_noop(tmp_path, monkeypatch):
    monkeypatch.setattr(opencode, "DB_PATH", tmp_path / "nope.db")

    assert opencode.discover(DAY) == []


def test_discover_survives_a_file_that_is_not_a_database(tmp_path, monkeypatch):
    """文件在但不是 SQLite：记日志返回 []，不把异常抛进 sync（NFR-004）。"""
    bad = tmp_path / "opencode.db"
    bad.write_text("这不是数据库", encoding="utf-8")
    monkeypatch.setattr(opencode, "DB_PATH", bad)

    assert opencode.discover(DAY) == []


# --- parse ---


def test_parse_merges_user_and_assistant_text(db):
    fill(db, [row(SESSION_A, user_message("为什么 upsert 会 409"), ms(2026, 9, 23, 9)),
              row(SESSION_A, assistant([text_block("因为点 id 撞了")]),
                  ms(2026, 9, 23, 9, 1))])

    material = opencode.parse(ref())

    assert material.source == "opencode"
    assert material.kind == "message"
    assert "为什么 upsert 会 409" in material.text
    assert "因为点 id 撞了" in material.text
    assert material.meta["session_id"] == SESSION_A
    assert material.meta["cwd"] is None      # 最小允许列表：不读 session_v2
    assert material.meta["struggle_rounds"] == 0
    assert material.meta["error_count"] == 0


def test_parse_drops_reasoning_and_tool_input(db):
    """实测 content 块 reasoning 51 / tool 68 / text 30——只有 text 进正文。"""
    fill(db, [row(SESSION_A, assistant([
        reasoning_block("先怀疑是网络，再怀疑是 id"),
        tool_block(inp={"command": "rg upsert"}),
        text_block("是点 id 撞了"),
    ]), ms(2026, 9, 23, 9))])

    material = opencode.parse(ref())

    assert "是点 id 撞了" in material.text
    assert "先怀疑是网络" not in material.text
    assert "rg upsert" not in material.text


def test_parse_marks_tool_errors(db):
    fill(db, [row(SESSION_A, user_message("搜一下"), ms(2026, 9, 23, 9)),
              row(SESSION_A, assistant([
                  tool_block(name="websearch", status="error",
                             error={"type": "unknown", "message": "Web search cancelled"}),
              ]), ms(2026, 9, 23, 9, 1))])

    material = opencode.parse(ref())

    assert "[error]" in material.text
    assert "Web search cancelled" in material.text
    assert material.meta["error_count"] == 1
    assert material.meta["struggle_rounds"] == 1


def test_parse_counts_consecutive_tool_errors_as_one_struggle(db):
    fill(db, [row(SESSION_A, assistant([
        tool_block(status="error", error={"type": "x", "message": "第一次失败"}),
        tool_block(status="error", error={"type": "x", "message": "第二次失败"}),
        tool_block(status="error", error={"type": "x", "message": "第三次失败"}),
    ]), ms(2026, 9, 23, 9))])

    material = opencode.parse(ref())

    assert material.meta["error_count"] == 3
    assert material.meta["struggle_rounds"] == 3


def test_parse_successful_tool_resets_the_run(db):
    fill(db, [row(SESSION_A, assistant([
        tool_block(status="error", error={"type": "x", "message": "失败一"}),
        tool_block(status="error", error={"type": "x", "message": "失败二"}),
        tool_block(status="completed"),
        tool_block(status="error", error={"type": "x", "message": "失败三"}),
    ]), ms(2026, 9, 23, 9))])

    material = opencode.parse(ref())

    assert material.meta["error_count"] == 3
    assert material.meta["struggle_rounds"] == 2   # 成功那次把连击打断了


def test_parse_ignores_idle_outcome(db):
    """`idle` 的 outcome=failed 是会话空闲结果，不是工具失败（idle 共 14 条）。"""
    fill(db, [row(SESSION_A, user_message("在吗"), ms(2026, 9, 23, 9)),
              row(SESSION_A, idle("failed"), ms(2026, 9, 23, 9, 1)),
              row(SESSION_A, model_switched(), ms(2026, 9, 23, 9, 2))])

    material = opencode.parse(ref())

    assert material.text == "在吗"
    assert material.meta["error_count"] == 0
    assert material.meta["struggle_rounds"] == 0


def test_parse_only_reads_the_ref_day(db):
    fill(db, [row(SESSION_A, user_message("昨天的话", ms(2026, 9, 22, 8)),
                  ms(2026, 9, 22, 8)),
              row(SESSION_A, user_message("今天的话"), ms(2026, 9, 23, 8))])

    assert opencode.parse(ref()).text == "今天的话"
    assert opencode.parse(ref(day=OTHER)).text == "昨天的话"


def test_parse_anchors_ts_to_the_first_contributing_record(db):
    fill(db, [row(SESSION_A, idle("succeeded"), ms(2026, 9, 23, 8)),
              row(SESSION_A, user_message("第一句"), ms(2026, 9, 23, 9, 17, )),
              row(SESSION_A, user_message("第二句"), ms(2026, 9, 23, 10))])

    ts = opencode.parse(ref()).ts

    assert ts.tzinfo is not None
    assert ts.astimezone(TZ) == datetime(2026, 9, 23, 9, 17, tzinfo=TZ)


def test_parse_falls_back_to_day_midnight_when_nothing_contributes(db):
    fill(db, [row(SESSION_A, idle("succeeded"), ms(2026, 9, 23, 9))])

    material = opencode.parse(ref())

    assert material.text == ""
    assert material.ts == datetime.combine(DAY, datetime.min.time(), tzinfo=TZ)


def test_parse_truncates_a_long_session(db, monkeypatch):
    monkeypatch.setattr(opencode, "MAX_SESSION_CHARS", 50)
    fill(db, [row(SESSION_A, user_message("x" * 200), ms(2026, 9, 23, 9))])

    assert len(opencode.parse(ref()).text) == 50


def test_parse_survives_a_db_that_vanished_after_discover(db, tmp_path, monkeypatch):
    fill(db, [row(SESSION_A, user_message("在吗"), ms(2026, 9, 23, 9))])
    r = ref()
    monkeypatch.setattr(opencode, "DB_PATH", tmp_path / "gone.db")

    material = opencode.parse(r)

    assert material.text == ""
    assert material.ts == datetime.combine(DAY, datetime.min.time(), tzinfo=TZ)


def test_never_reads_the_credential_tables(db):
    """NFR-001：表允许列表只有 `session_message`，凭据哨兵不得出现在任何输出里。"""
    fill(db, [row(SESSION_A, user_message("普通一句话"), ms(2026, 9, 23, 9))])

    material = opencode.parse(ref())
    haystack = material.text + json.dumps(material.meta, ensure_ascii=False)
    discovered = opencode.discover(DAY)

    assert SECRET not in haystack
    assert all(SECRET not in r.ref for r in discovered)
    assert opencode.ALLOWED_TABLES == frozenset({"session_message"})


# --- registry ---


def test_registered_in_registry():
    from src.plugins import iter_plugins

    assert "opencode" in {p.name for p in iter_plugins()}
