"""Unit tests for the zcode plugin (T055) — `~/.zcode/cli/db/db.sqlite`。

真实形状（2026-09-25 只读勘察，1863 行 `message` + 5894 行 `part`）：

    message(id, session_id, time_created, time_updated, data, sequence)
    part(id, message_id, session_id, time_created, time_updated, data, sequence)

`data` 都是 JSON。实测 `message.data.role`：user **343** / assistant 1520；
`message.data.synthetic` 取值只有 `None`(1748) 与 `True`(**115**) 两种，且
115 条全是 user —— 即 PRD FR-002a 说的「真用户输入仅 343−115=**228** 条」，
这条断言已在真实库上核对。`message.data.path` 是 `{"cwd": …, "root": …}`
字典（1429 条有），故 `meta.cwd` 白拿。

`part.data.type` 实测：text 1003 / tool 1560 / reasoning 515 / step-start 1405 /
step-finish 1398 / timeline 13 —— 只有 text 进正文，tool 参与 FR-008 判据。
`part.data.state.status` 实测 completed 1531 / error **29**，且 `state.error`
是**字符串**（opencode 那边是字典，两族各自实现的意义正在这里）。

凭据边界（NFR-001）：表允许列表只有 `message` + `part`；库内另有
`input_history`(100) / `local_setting`(6) / `permission`(0) 等表一律不读。
凭据文件 `~/.zcode/v2/credentials*.json` 在库外，本插件只开库、不碰文件系统
其余部分。单测植入哨兵值钉住这条。

源目录只读（宪法 V）：一律 `file:…?mode=ro` + `uri=True`。
"""

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone

import pytest

from src.plugins import SourceRef, zcode

DAY = date(2026, 9, 18)
OTHER = date(2026, 9, 17)
TZ = timezone(timedelta(hours=8))

SESSION_A = "claude-import-131628db6f9105a20517a430"
SESSION_B = "claude-import-0c7f0f0d0f0f0f0f0f0f0f0f"

# 故意不带 sk- 形态：那是令牌形状，提交门禁（scripts/secret_scan.py 的 token
# 规则）会把它判成提交物泄漏。这里要的只是一个"必须不出现在产出物里"的哨兵，
# 不带令牌形状反而更灵敏 —— 它不会被 sanitize 抹掉，凭据表一旦被读就必然暴露。
SECRET = "zcode-CREDENTIAL-SENTINEL"
CWD = "/Users/nava/Code/llm/rag/first-rag"

SCHEMA = """
CREATE TABLE message (
    id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER,
    time_updated INTEGER, data TEXT, sequence INTEGER
);
CREATE TABLE part (
    id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT, time_created INTEGER,
    time_updated INTEGER, data TEXT, sequence INTEGER
);
CREATE TABLE input_history (id TEXT PRIMARY KEY, secret TEXT);
CREATE TABLE local_setting (id TEXT PRIMARY KEY, secret TEXT);
CREATE TABLE permission (id TEXT PRIMARY KEY, secret TEXT);
"""


def ms(y, mo, d, h=10, mi=0) -> int:
    return int(datetime(y, mo, d, h, mi, tzinfo=TZ).timestamp() * 1000)


def user_message(synthetic=None, cwd=CWD) -> dict:
    """实测形状：user 消息多带 synthetic 与 source/visibility 等键。"""
    data = {"role": "user", "time": {"created": ms(2026, 9, 18, 10)},
            "agent": "zcode-agent", "model": {"modelID": "GLM-5.3"}}
    if synthetic is not None:
        data["synthetic"] = synthetic
    if cwd is not None:
        data["path"] = {"cwd": cwd, "root": cwd}
    return data


def assistant_message() -> dict:
    return {"role": "assistant", "time": {"created": ms(2026, 9, 18, 10)},
            "agent": "zcode-agent", "modelID": "GLM-5.3",
            "cost": 0.1, "tokens": {"input": 1, "output": 1}}


def text_part(text) -> dict:
    return {"type": "text", "text": text,
            "time": {"start": ms(2026, 9, 18, 10), "end": ms(2026, 9, 18, 10)}}


def reasoning_part(text) -> dict:
    return {"type": "reasoning", "text": text}


def step_start_part() -> dict:
    return {"type": "step-start"}


def step_finish_part() -> dict:
    return {"type": "step-finish", "tokens": {}}


def tool_part(tool="Read", status="completed", error=None) -> dict:
    state = {"status": status, "input": {"file_path": "/tmp/x"}, "metadata": {},
             "time": {"start": ms(2026, 9, 18, 10), "end": ms(2026, 9, 18, 10)}}
    if error is not None:
        state["error"] = error
    return {"type": "tool", "callID": "call_1", "tool": tool, "state": state}


class DB:
    """往临时库里写消息与零件；镜像真实的两表形状。"""

    def __init__(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.unlink(missing_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.executescript(SCHEMA)
        for t in ("input_history", "local_setting", "permission"):
            self.conn.execute(f"INSERT INTO {t} (id, secret) VALUES ('x', ?)", (SECRET,))
        self.path = path
        self._seq = 0

    def message(self, session_id, data, parts, ts=None, seq=None, part_seqs=None):
        """`seq` / `part_seqs` 默认按插入顺序，但要与 rowid 脱钩时才显式指定。"""
        ts = ts if ts is not None else ms(2026, 9, 18, 10)
        seq = self._seq if seq is None else seq
        mid = f"msg_{session_id[:12]}_{self._seq}"
        self.conn.execute(
            "INSERT INTO message (id, session_id, time_created, time_updated, data, "
            "sequence) VALUES (?, ?, ?, ?, ?, ?)",
            (mid, session_id, ts, ts, json.dumps(data, ensure_ascii=False), seq))
        for i, part in enumerate(parts):
            pseq = i if part_seqs is None else part_seqs[i]
            self.conn.execute(
                "INSERT INTO part (id, message_id, session_id, time_created, "
                "time_updated, data, sequence) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (f"{mid}_p{i}", mid, session_id, ts, ts,
                 json.dumps(part, ensure_ascii=False), pseq))
        self._seq += 1
        return self

    def done(self):
        self.conn.commit()
        self.conn.close()
        return self.path


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "zcode.sqlite"
    monkeypatch.setattr(zcode, "DB_PATH", path)
    return path


def ref(session_id=SESSION_A, day=DAY) -> SourceRef:
    return SourceRef(source="zcode", ref=f"{zcode.DB_PATH}#{session_id}", day=day)


# --- discover ---


def test_discover_returns_one_ref_per_session(db):
    DB(db).message(SESSION_A, user_message(), [text_part("改一下 upsert")]) \
         .message(SESSION_B, user_message(), [text_part("另一件事")]).done()

    refs = zcode.discover(DAY)

    # 会话 id 排序是确定的（与文件系统的遍历顺序无关）
    expected = sorted([SESSION_A, SESSION_B])
    assert [r.ref for r in refs] == [f"{db}#{sid}" for sid in expected]
    assert all(r.source == "zcode" and r.day == DAY for r in refs)


def test_discover_filters_by_day(db):
    DB(db).message(SESSION_A, user_message(), [text_part("昨天")],
                   ts=ms(2026, 9, 17, 8)) \
         .message(SESSION_B, user_message(), [text_part("今天")],
                  ts=ms(2026, 9, 18, 8)).done()

    assert [r.ref for r in zcode.discover(DAY)] == [f"{db}#{SESSION_B}"]
    assert [r.ref for r in zcode.discover(OTHER)] == [f"{db}#{SESSION_A}"]


def test_discover_ignores_a_session_without_text(db):
    """只有 reasoning / step-* / tool 的会话不构成素材。

    实测 part 里 reasoning 515 + step-start 1405 + step-finish 1398 + timeline 13
    全是簿记，只有 text(1003) 进正文。
    """
    DB(db).message(SESSION_A, assistant_message(), [
        step_start_part(), reasoning_part("先想想"), tool_part(),
        step_finish_part()]).done()

    assert zcode.discover(DAY) == []


def test_discover_ignores_a_session_whose_only_text_is_synthetic(db):
    DB(db).message(SESSION_A, user_message(synthetic=True),
                   [text_part("这是 fork 提示，不是用户说的")]).done()

    assert zcode.discover(DAY) == []


def test_discover_across_the_midnight_boundary(db):
    """23:59 属当天，次日 00:00 不属于——边界按上海时区切。

    （补自 T054 变异表：把 `<` 改成 `<=` 时这里会红。）
    """
    DB(db).message(SESSION_A, user_message(), [text_part("压线")],
                   ts=ms(2026, 9, 18, 23, 59)) \
         .message(SESSION_B, user_message(), [text_part("过线")],
                  ts=ms(2026, 9, 19, 0, 0)).done()

    assert [r.ref for r in zcode.discover(DAY)] == [f"{db}#{SESSION_A}"]
    assert [r.ref for r in zcode.discover(date(2026, 9, 19))] == [f"{db}#{SESSION_B}"]


def test_discover_missing_db_is_noop(tmp_path, monkeypatch):
    monkeypatch.setattr(zcode, "DB_PATH", tmp_path / "nope.sqlite")

    assert zcode.discover(DAY) == []


def test_discover_survives_a_file_that_is_not_a_database(tmp_path, monkeypatch):
    bad = tmp_path / "zcode.sqlite"
    bad.write_text("这不是数据库", encoding="utf-8")
    monkeypatch.setattr(zcode, "DB_PATH", bad)

    assert zcode.discover(DAY) == []


# --- parse ---


def test_parse_merges_user_and_assistant_text(db):
    DB(db).message(SESSION_A, user_message(), [text_part("为什么导入会报警告")]) \
         .message(SESSION_A, assistant_message(),
                  [text_part("因为解析器不支持那个扩展名")]).done()

    material = zcode.parse(ref())

    assert material.source == "zcode"
    assert material.kind == "message"
    assert "为什么导入会报警告" in material.text
    assert "因为解析器不支持那个扩展名" in material.text
    assert material.meta["session_id"] == SESSION_A
    assert material.meta["struggle_rounds"] == 0
    assert material.meta["error_count"] == 0


def test_parse_drops_reasoning_step_and_tool_input(db):
    DB(db).message(SESSION_A, assistant_message(), [
        step_start_part(),
        reasoning_part("先怀疑是编码问题，再怀疑是权限"),
        tool_part(tool="Read"),
        step_finish_part(),
        text_part("是扩展名的问题"),
    ]).done()

    material = zcode.parse(ref())

    assert "是扩展名的问题" in material.text
    assert "先怀疑是编码问题" not in material.text
    assert "/tmp/x" not in material.text          # tool 的 input 不进正文
    assert "step-start" not in material.text


def test_parse_excludes_synthetic_messages(db):
    """FR-002a 的核心：`synthetic` 为真的记录不得产生素材。

    实测真实库 343 条 user 里 115 条 synthetic（fork 提示与工具结果回填），
    真用户输入只剩 228 条——本用例用 fixture 复现同样的三分支。
    """
    DB(db).message(SESSION_A, user_message(), [text_part("真的问题一")]) \
         .message(SESSION_A, user_message(synthetic=True),
                  [text_part("fork 提示：请继续之前的对话")]) \
         .message(SESSION_A, user_message(synthetic=False),
                  [text_part("真的问题二")]) \
         .message(SESSION_A, assistant_message(), [text_part("回答")]).done()

    material = zcode.parse(ref())

    assert "真的问题一" in material.text
    assert "真的问题二" in material.text
    assert "fork 提示" not in material.text
    assert "请继续之前的对话" not in material.text


def test_parse_marks_tool_errors(db):
    DB(db).message(SESSION_A, assistant_message(), [
        text_part("我读一下这个文件"),
        tool_part(tool="Read", status="error",
                  error="File does not exist. Note: your current working directory is …"),
    ]).done()

    material = zcode.parse(ref())

    assert "[error]" in material.text
    assert "File does not exist" in material.text
    assert material.meta["error_count"] == 1
    assert material.meta["struggle_rounds"] == 1


def test_parse_counts_consecutive_tool_errors_as_one_struggle(db):
    DB(db).message(SESSION_A, assistant_message(), [
        tool_part(status="error", error="失败一"),
        tool_part(status="error", error="失败二"),
        tool_part(status="error", error="失败三"),
    ]).done()

    material = zcode.parse(ref())

    assert material.meta["error_count"] == 3
    assert material.meta["struggle_rounds"] == 3


def test_parse_successful_tool_resets_the_run(db):
    DB(db).message(SESSION_A, assistant_message(), [
        tool_part(status="error", error="失败一"),
        tool_part(status="error", error="失败二"),
        tool_part(status="completed"),
        tool_part(status="error", error="失败三"),
    ]).done()

    material = zcode.parse(ref())

    assert material.meta["error_count"] == 3
    assert material.meta["struggle_rounds"] == 2   # 成功那次打断了连击


def test_parse_only_reads_the_ref_day(db):
    DB(db).message(SESSION_A, user_message(), [text_part("昨天的话")],
                   ts=ms(2026, 9, 17, 8)) \
         .message(SESSION_A, user_message(), [text_part("今天的话")],
                  ts=ms(2026, 9, 18, 8)).done()

    assert zcode.parse(ref()).text == "今天的话"
    assert zcode.parse(ref(day=OTHER)).text == "昨天的话"


def test_parse_follows_the_sequence_columns_not_insertion_order(db):
    """转录顺序由 `sequence` 列决定，不是由插入顺序（rowid）决定。

    （补自 T054 变异表：去掉 `ORDER BY m.sequence, p.sequence` 时这里会红。）
    """
    DB(db).message(SESSION_A, user_message(), [text_part("第二句")],
                   seq=5, part_seqs=[5]) \
         .message(SESSION_A, user_message(), [text_part("第一句")],
                  seq=1, part_seqs=[1]).done()

    assert zcode.parse(ref()).text == "第一句\n---\n第二句"


def test_parse_takes_cwd_from_the_message_payload(db):
    """实测 `message.data.path` 是 `{"cwd": …, "root": …}` 字典（1429 条有）。"""
    DB(db).message(SESSION_A, user_message(), [text_part("在吗")]).done()

    assert zcode.parse(ref()).meta["cwd"] == CWD


def test_parse_cwd_is_none_when_the_payload_has_no_path(db):
    DB(db).message(SESSION_A, user_message(cwd=None), [text_part("在吗")]).done()

    assert zcode.parse(ref()).meta["cwd"] is None


def test_parse_anchors_ts_to_the_first_contributing_record(db):
    DB(db).message(SESSION_A, assistant_message(), [reasoning_part("只有思考")],
                   ts=ms(2026, 9, 18, 9)) \
         .message(SESSION_A, user_message(), [text_part("第一句")],
                  ts=ms(2026, 9, 18, 9, 17)) \
         .message(SESSION_A, user_message(), [text_part("第二句")],
                  ts=ms(2026, 9, 18, 10)).done()

    ts = zcode.parse(ref()).ts

    assert ts.tzinfo is not None
    assert ts.astimezone(TZ) == datetime(2026, 9, 18, 9, 17, tzinfo=TZ)


def test_parse_falls_back_to_day_midnight_when_nothing_contributes(db):
    DB(db).message(SESSION_A, assistant_message(), [reasoning_part("只有思考")]).done()

    material = zcode.parse(ref())

    assert material.text == ""
    assert material.ts == datetime.combine(DAY, datetime.min.time(), tzinfo=TZ)


def test_parse_truncates_a_long_session(db, monkeypatch):
    monkeypatch.setattr(zcode, "MAX_SESSION_CHARS", 50)
    DB(db).message(SESSION_A, user_message(), [text_part("x" * 200)]).done()

    assert len(zcode.parse(ref()).text) == 50


def test_parse_survives_a_db_that_vanished_after_discover(db, tmp_path, monkeypatch):
    DB(db).message(SESSION_A, user_message(), [text_part("在吗")]).done()
    r = ref()
    monkeypatch.setattr(zcode, "DB_PATH", tmp_path / "gone.sqlite")

    material = zcode.parse(r)

    assert material.text == ""
    assert material.ts == datetime.combine(DAY, datetime.min.time(), tzinfo=TZ)


def test_never_reads_tables_outside_the_allowlist(db):
    """NFR-001：允许列表只有 message + part；凭据哨兵不得出现在任何输出里。"""
    DB(db).message(SESSION_A, user_message(), [text_part("普通一句话")]).done()

    material = zcode.parse(ref())
    haystack = material.text + json.dumps(material.meta, ensure_ascii=False)

    assert SECRET not in haystack
    assert all(SECRET not in r.ref for r in zcode.discover(DAY))
    assert zcode.ALLOWED_TABLES == frozenset({"message", "part"})


# --- registry ---


def test_registered_in_registry():
    from src.plugins import iter_plugins

    assert "zcode" in {p.name for p in iter_plugins()}
