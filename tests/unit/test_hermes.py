"""Unit tests for the hermes plugin (T056) — `~/.hermes/state.db`。

**⚠️ 未经真实数据验证**（AC-017③ / FR-002a）。

PRD 原文：hermes「该源当前零真实数据（库建于 2026-09-24，`messages` / `sessions`
均 0 行，`freelist=0` 确认为真空库），按 schema 实现。其验收依据**仅为构造的
fixture，未经真实数据验证**；待该源产生数据后补验，在此之前不得据其声称任何 AC
通过。」本文件的所有用例都只是把 schema 与产品自身写库代码的形状钉住，**不构成
任何 AC 通过的证据**（2026-09-25 复核：`messages` 0 行 / `sessions` 0 行，
schema_version 30）。

以下事实取自 hermes-agent 0.21.5 的源码（`~/.hermes/hermes-agent/`），不是猜的：

- 表：`sessions(id, source, cwd, started_at, …)` 与
  `messages(id, session_id, role, content, tool_name, tool_call_id, timestamp, …)`；
  写入统一走 `hermes_state_messages._INSERT_MESSAGE_SQL`。
- **时间戳是 epoch 秒**（REAL）：`_coerce_timestamp` 的兜底是 `time.time()`，
  不是毫秒。按毫秒解会得到 year **58684**——本文件有一条用例专门钉这个。
- `content` 是 TEXT；结构化（多模态）内容以哨兵前缀 `"\\x00json:"` 开头
  （`hermes_state._CONTENT_JSON_PREFIX`）。
- **失败标记**：`tools/registry.tool_error()` 写出 `{"error": …}` 的 JSON，而
  工具异常经 `model_tools._sanitize_tool_error` 包成 `"[TOOL_ERROR] …"` 再塞进
  那个 `error` 字段（`model_tools.py:644` 构造、`:964` 落库）。因此本插件的
  FR-008 判据取「`role='tool'` 且 content 解析出真值 `error` 键」——
  **这是产品自己写的形状，但零数据下无法实测，故未经真实数据验证**。
  注意 `agent/display._detect_tool_failure` 还有一套更细的判据（terminal 的
  `exit_code`、`success is False`、degraded、guardrail refusal）；本插件
  **不**复制那一整张表，缺口记在插件 docstring 里。
- **注入物过滤：FR-002a 对 hermes 没有给规则**（zcode 有 `synthetic`、
  workbuddy 有 `<user_query>`，hermes 只有「零数据、按 schema 实现」）。
  因此本插件不施加任何注入物判据，只按 role 与空值过滤——**不发明规则**。
  库里确有候选列（`_compressed_summary`、`active`/`compacted`、`observed`、
  `display_kind`），但挑哪条属于需求决定，须回 PRD 变更控制，见插件 docstring。

凭据边界（NFR-001）：表允许列表只有 `messages` + `sessions`。库内另有
`system_prompts`（系统提示词全文）`state_meta`、`gateway_routing`、
`session_model_usage`（计费）等表一律不读；凭据文件 `~/.hermes/.env` 与
`auth.json` 在库外（PRD §NFR-001 点名），本插件只开库、不碰文件系统其余部分。
单测植入哨兵值钉住这条。

源目录只读（宪法 V）：一律 `file:…?mode=ro` + `uri=True`。
"""

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone

import pytest

from src.plugins import SourceRef, hermes

DAY = date(2026, 9, 18)
OTHER = date(2026, 9, 17)
TZ = timezone(timedelta(hours=8))

SESSION_A = "sess_hermes_0001"
SESSION_B = "sess_hermes_0002"

# 故意不带 sk- 形态：那是令牌形状，提交门禁（scripts/secret_scan.py 的 token
# 规则）会把它判成提交物泄漏。这里要的只是一个"必须不出现在产出物里"的哨兵，
# 不带令牌形状反而更灵敏 —— 它不会被 sanitize 抹掉，凭据表一旦被读就必然暴露。
SECRET = "hermes-CREDENTIAL-SENTINEL"
CWD = "/Users/nava/Code/llm/rag/first-rag"

# 镜像真实库的列子集（schema_version 30）。多出来的几张表全是凭据/计费/系统
# 提示词，用来证明允许列表把它们挡在外面。
SCHEMA = """
CREATE TABLE sessions (
    id TEXT PRIMARY KEY, source TEXT NOT NULL, cwd TEXT, session_key TEXT,
    started_at REAL NOT NULL, ended_at REAL, title TEXT,
    message_count INTEGER DEFAULT 0, tool_call_count INTEGER DEFAULT 0
);
CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
    role TEXT NOT NULL, content TEXT, tool_call_id TEXT, tool_calls TEXT,
    tool_name TEXT, effect_disposition TEXT, timestamp REAL NOT NULL,
    token_count INTEGER, finish_reason TEXT, reasoning TEXT,
    _compressed_summary INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1,
    compacted INTEGER NOT NULL DEFAULT 0, display_kind TEXT
);
CREATE TABLE state_meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE system_prompts (hash TEXT PRIMARY KEY, prompt TEXT NOT NULL);
CREATE TABLE gateway_routing (scope TEXT NOT NULL DEFAULT '',
    session_key TEXT NOT NULL, entry_json TEXT NOT NULL, updated_at REAL NOT NULL,
    PRIMARY KEY (scope, session_key));
CREATE TABLE session_model_usage (session_id TEXT NOT NULL, model TEXT NOT NULL,
    estimated_cost_usd REAL NOT NULL DEFAULT 0, PRIMARY KEY (session_id, model));
"""


def s(y, mo, d, h=10, mi=0) -> float:
    """上海时间 → epoch **秒**（不是毫秒）。"""
    return datetime(y, mo, d, h, mi, tzinfo=TZ).timestamp()


class DB:
    """往临时库里写会话与消息；镜像真实的两表形状。"""

    def __init__(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.unlink(missing_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.executescript(SCHEMA)
        self.conn.execute("INSERT INTO state_meta (key, value) VALUES ('k', ?)", (SECRET,))
        self.conn.execute("INSERT INTO system_prompts (hash, prompt) VALUES ('h', ?)", (SECRET,))
        self.conn.execute(
            "INSERT INTO gateway_routing (session_key, entry_json, updated_at) "
            "VALUES ('k', ?, 0)", ('{"token": "%s"}' % SECRET,))
        self.conn.execute(
            "INSERT INTO session_model_usage (session_id, model, estimated_cost_usd) "
            "VALUES ('s', ?, 0)", (SECRET,))
        self.path = path
        self._seq = 0

    def session(self, session_id, cwd=CWD, ts=None):
        ts = ts if ts is not None else s(2026, 9, 18, 9)
        self.conn.execute(
            "INSERT OR REPLACE INTO sessions (id, source, cwd, started_at) "
            "VALUES (?, 'hermes', ?, ?)", (session_id, cwd, ts))
        return self

    def message(self, session_id, role, content, ts=None, tool_name=None, reasoning=None):
        ts = ts if ts is not None else s(2026, 9, 18, 10)
        self.conn.execute(
            "INSERT INTO messages (session_id, role, content, tool_name, timestamp, "
            "reasoning) VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, role, content, tool_name, ts, reasoning))
        self._seq += 1
        return self

    def done(self):
        self.conn.commit()
        self.conn.close()
        return self.path


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "state.db"
    monkeypatch.setattr(hermes, "DB_PATH", path)
    return path


def ref(session_id=SESSION_A, day=DAY) -> SourceRef:
    return SourceRef(source="hermes", ref=f"{hermes.DB_PATH}#{session_id}", day=day)


def tool_error_content(message="File does not exist") -> str:
    """产品写失败时的真实形状：`tool_error(json.dumps({"error": …}))`。"""
    return json.dumps({"error": f"[TOOL_ERROR] {message}"}, ensure_ascii=False)


# --- discover ---


def test_discover_returns_one_ref_per_session(db):
    DB(db).session(SESSION_A).message(SESSION_A, "user", "改一下 upsert") \
         .session(SESSION_B).message(SESSION_B, "user", "另一件事").done()

    refs = hermes.discover(DAY)

    assert [r.ref for r in refs] == [f"{db}#{SESSION_A}", f"{db}#{SESSION_B}"]
    assert all(r.source == "hermes" and r.day == DAY for r in refs)


def test_discover_filters_by_day(db):
    DB(db).session(SESSION_A).message(SESSION_A, "user", "昨天", ts=s(2026, 9, 17, 8)) \
         .session(SESSION_B).message(SESSION_B, "user", "今天", ts=s(2026, 9, 18, 8)).done()

    assert [r.ref for r in hermes.discover(DAY)] == [f"{db}#{SESSION_B}"]
    assert [r.ref for r in hermes.discover(OTHER)] == [f"{db}#{SESSION_A}"]


def test_discover_ignores_a_session_without_material(db):
    """只有成功的 tool 行（无文本、无失败）的会话不构成素材。"""
    DB(db).session(SESSION_A).message(SESSION_A, "tool", '{"ok": true}', tool_name="read").done()

    assert hermes.discover(DAY) == []


def test_discover_ignores_a_session_with_only_blank_text(db):
    DB(db).session(SESSION_A).message(SESSION_A, "user", "   ").done()

    assert hermes.discover(DAY) == []


def test_discover_across_the_midnight_boundary(db):
    """23:59 属当天，次日 00:00 不属于——边界按上海时区切。

    （补自 T056 变异表：把 `<` 改成 `<=` 时这里会红。）
    """
    DB(db).session(SESSION_A) \
         .message(SESSION_A, "user", "压线", ts=s(2026, 9, 18, 23, 59)) \
         .session(SESSION_B) \
         .message(SESSION_B, "user", "过线", ts=s(2026, 9, 19, 0, 0)).done()

    assert [r.ref for r in hermes.discover(DAY)] == [f"{db}#{SESSION_A}"]
    assert [r.ref for r in hermes.discover(date(2026, 9, 19))] == [f"{db}#{SESSION_B}"]


def test_discover_missing_db_is_noop(tmp_path, monkeypatch):
    monkeypatch.setattr(hermes, "DB_PATH", tmp_path / "nope.db")

    assert hermes.discover(DAY) == []


def test_discover_survives_a_file_that_is_not_a_database(tmp_path, monkeypatch):
    bad = tmp_path / "state.db"
    bad.write_text("这不是数据库", encoding="utf-8")
    monkeypatch.setattr(hermes, "DB_PATH", bad)

    assert hermes.discover(DAY) == []


# --- parse ---


def test_parse_merges_user_and_assistant_text(db):
    DB(db).session(SESSION_A) \
         .message(SESSION_A, "user", "为什么导入会报警告") \
         .message(SESSION_A, "assistant", "因为解析器不支持那个扩展名").done()

    material = hermes.parse(ref())

    assert material.source == "hermes"
    assert material.kind == "message"
    assert "为什么导入会报警告" in material.text
    assert "因为解析器不支持那个扩展名" in material.text
    assert material.meta["session_id"] == SESSION_A
    assert material.meta["struggle_rounds"] == 0
    assert material.meta["error_count"] == 0


def test_parse_drops_reasoning(db):
    """`reasoning` 是思考过程，不进正文（与其他源一致）。"""
    DB(db).session(SESSION_A) \
         .message(SESSION_A, "assistant", "是扩展名的问题",
                  reasoning="先怀疑是编码问题，再怀疑是权限").done()

    material = hermes.parse(ref())

    assert "是扩展名的问题" in material.text
    assert "先怀疑是编码问题" not in material.text


def test_parse_marks_tool_errors(db):
    DB(db).session(SESSION_A) \
         .message(SESSION_A, "user", "读一下这个文件") \
         .message(SESSION_A, "tool", tool_error_content("File does not exist. Note: …"),
                  tool_name="read").done()

    material = hermes.parse(ref())

    assert "[error]" in material.text
    assert "File does not exist" in material.text
    assert material.meta["error_count"] == 1
    assert material.meta["struggle_rounds"] == 1


def test_parse_counts_consecutive_tool_errors_as_one_struggle(db):
    DB(db).session(SESSION_A) \
         .message(SESSION_A, "tool", tool_error_content("失败一"), tool_name="shell") \
         .message(SESSION_A, "tool", tool_error_content("失败二"), tool_name="shell") \
         .message(SESSION_A, "tool", tool_error_content("失败三"), tool_name="shell").done()

    material = hermes.parse(ref())

    assert material.meta["error_count"] == 3
    assert material.meta["struggle_rounds"] == 3


def test_parse_successful_tool_resets_the_run(db):
    DB(db).session(SESSION_A) \
         .message(SESSION_A, "tool", tool_error_content("失败一"), tool_name="shell") \
         .message(SESSION_A, "tool", tool_error_content("失败二"), tool_name="shell") \
         .message(SESSION_A, "tool", '{"ok": true}', tool_name="shell") \
         .message(SESSION_A, "tool", tool_error_content("失败三"), tool_name="shell").done()

    material = hermes.parse(ref())

    assert material.meta["error_count"] == 3
    assert material.meta["struggle_rounds"] == 2   # 成功那次打断了连击


def test_parse_drops_multimodal_sentinel_content(db):
    """结构化（多模态）内容以哨兵前缀开头，不是纯文本，不产素材。

    （补自 T056 变异表：去掉前缀判断时这里会红。）
    """
    DB(db).session(SESSION_A) \
         .message(SESSION_A, "user",
                  '\x00json:[{"type": "text", "text": "hi"}]').done()

    assert hermes.parse(ref()).text == ""
    assert hermes.discover(DAY) == []


def test_parse_only_reads_the_ref_day(db):
    DB(db).session(SESSION_A) \
         .message(SESSION_A, "user", "昨天的话", ts=s(2026, 9, 17, 8)) \
         .message(SESSION_A, "user", "今天的话", ts=s(2026, 9, 18, 8)).done()

    assert hermes.parse(ref()).text == "今天的话"
    assert hermes.parse(ref(day=OTHER)).text == "昨天的话"


def test_parse_reads_timestamps_as_epoch_seconds(db):
    """T056 钉的坑：按毫秒解这条记录会得到 year **58684**，而不是 2026。

    `_coerce_timestamp` 的兜底是 `time.time()`（秒），故库里的 REAL 是秒。
    """
    DB(db).session(SESSION_A) \
         .message(SESSION_A, "user", "在吗", ts=s(2026, 9, 18, 10)).done()

    ts = hermes.parse(ref()).ts

    assert ts.tzinfo is not None
    assert ts.astimezone(TZ).year == 2026
    assert ts.astimezone(TZ) == datetime(2026, 9, 18, 10, tzinfo=TZ)


def test_parse_anchors_ts_to_the_first_contributing_record(db):
    DB(db).session(SESSION_A) \
         .message(SESSION_A, "tool", '{"ok": true}', ts=s(2026, 9, 18, 9), tool_name="read") \
         .message(SESSION_A, "user", "第一句", ts=s(2026, 9, 18, 9, 17)) \
         .message(SESSION_A, "user", "第二句", ts=s(2026, 9, 18, 10)).done()

    ts = hermes.parse(ref()).ts

    assert ts.astimezone(TZ) == datetime(2026, 9, 18, 9, 17, tzinfo=TZ)


def test_parse_takes_cwd_from_the_sessions_table(db):
    DB(db).session(SESSION_A).message(SESSION_A, "user", "在吗").done()

    assert hermes.parse(ref()).meta["cwd"] == CWD


def test_parse_cwd_is_none_when_the_session_has_no_cwd(db):
    DB(db).session(SESSION_A, cwd=None).message(SESSION_A, "user", "在吗").done()

    assert hermes.parse(ref()).meta["cwd"] is None


def test_parse_falls_back_to_day_midnight_when_nothing_contributes(db):
    DB(db).session(SESSION_A).message(SESSION_A, "tool", '{"ok": true}', tool_name="read").done()

    material = hermes.parse(ref())

    assert material.text == ""
    assert material.ts == datetime.combine(DAY, datetime.min.time(), tzinfo=TZ)


def test_parse_truncates_a_long_session(db, monkeypatch):
    monkeypatch.setattr(hermes, "MAX_SESSION_CHARS", 50)
    DB(db).session(SESSION_A).message(SESSION_A, "user", "x" * 200).done()

    assert len(hermes.parse(ref()).text) == 50


def test_parse_survives_a_db_that_vanished_after_discover(db, tmp_path, monkeypatch):
    DB(db).session(SESSION_A).message(SESSION_A, "user", "在吗").done()
    r = ref()
    monkeypatch.setattr(hermes, "DB_PATH", tmp_path / "gone.db")

    material = hermes.parse(r)

    assert material.text == ""
    assert material.ts == datetime.combine(DAY, datetime.min.time(), tzinfo=TZ)


def test_never_reads_tables_outside_the_allowlist(db):
    """NFR-001：允许列表只有 messages + sessions；凭据哨兵不得出现在任何输出里。"""
    DB(db).session(SESSION_A).message(SESSION_A, "user", "普通一句话").done()

    material = hermes.parse(ref())
    haystack = material.text + json.dumps(material.meta, ensure_ascii=False)

    assert SECRET not in haystack
    assert all(SECRET not in r.ref for r in hermes.discover(DAY))
    assert hermes.ALLOWED_TABLES == frozenset({"messages", "sessions"})


# --- AC-017③：零数据的免责声明必须留在测试与实现里 ---


def test_plugin_states_it_is_unverified_against_real_data():
    """AC-017③ 要求「须在测试中显式标注『未经真实数据验证』」——机械钉住它。"""
    from pathlib import Path

    marker = "未经真实数据验证"
    here = Path(__file__).read_text(encoding="utf-8")
    impl = Path(hermes.__file__).read_text(encoding="utf-8")

    assert marker in here
    assert marker in impl


# --- registry ---


def test_registered_in_registry():
    from src.plugins import iter_plugins

    assert "hermes" in {p.name for p in iter_plugins()}
