"""Unit tests for the qoder plugin (T051).

Fixture 全部照 2026-09-25 本机 `~/.qoder/projects/` 的实测绘形（3 会话 /
38629 行），不是凭空想象的 schema。三条实测结论决定了这个插件的形状：

1. **时间戳按记录类型分两种编码，同文件混排**（PRD FR-002a / NFR 硬约束）：
   `user`（7397 条）与 `assistant`（13524 条）**一律**是 ISO 8601 UTC 字符串；
   `active-leaf`（14412 条）与 `runtime-config`（240 条）**一律**是 epoch
   毫秒整数；另有 1016 条 bookkeeping 记录 `timestamp` 为 `None`。
   由于要按时间戳判定归属日，`active-leaf` 这类记录在真实文件里占了一多半，
   解析 MUST 先判类型——假定单一编码会在真实文件上直接崩。

2. **user 角色不等于用户输入**。7397 条 user 记录里只有 **146** 条是真用户
   发言（`origin.kind == "human"`，与 `humanInput == true` 完全重合）。其余：
   7051 条工具结果回填（`content[].type == "tool_result"`）、106 条**压缩摘要**
   （`isCompactSummary`，"This session is being continued from a previous
   conversation that ran out of context…"，正文里引着用户原话）、5 条
   `origin.kind == "task-notification"`、88 条 `isMeta` 的元记录。user 记录里
   一共只有 258 个 text 块，其中 146 个是人的——**按"user 角色就取 text"实现，
   会多收 112 条机器文本，素材量虚增 77%**。

3. **助手侧同理**：13524 条 assistant 记录的 content 块里，`tool_use` 7051、
   `thinking` 5071、`redacted_thinking` 431，`text` 只有 971。只取 text 块。

工具报错（FR-008 行为证据）：失败信号在 `toolUseResult.isError` / `exitCode != 0`
（实测 138 条），报错正文在 `message.content[].content`（失败的 138 条全部非空）。
"""

import json
from datetime import date, datetime, timedelta, timezone

import pytest

from src.plugins import SourceRef, qoder

DAY = date(2026, 9, 18)
TZ = timezone(timedelta(hours=8))
DEMO_CWD = "/Users/nava/Code/demo"
SESSION_ID = "fc625b6b-2514-4f07-b70b-4d15d5fa743e"

# 同一瞬间的两种真实写法：ISO 字符串（消息类）与 epoch 毫秒（bookkeeping）。
# 2026-09-17T16:30:00Z = 上海 2026-09-18 00:30 → 属于 DAY
TS_ISO = "2026-09-17T16:30:00.000Z"
TS_MS = 1789662600000
# 2026-09-17T20:15:00Z = 上海 2026-09-18 04:15 → 也在 DAY，但是另一个时刻
TS_ISO_LATER = "2026-09-17T20:15:00.000Z"
TS_MS_LATER = 1789676100000
# 2026-09-10T02:00:00Z = 上海 2026-09-10 10:00 → 别的日子
OLD_ISO = "2026-09-10T02:00:00.000Z"
OLD_MS = 1789005600000


def _base(record_type: str, ts) -> dict:
    return {
        "type": record_type,
        "timestamp": ts,
        "cwd": DEMO_CWD,
        "sessionId": SESSION_ID,
        "gitBranch": "main",
        "isSidechain": False,
        "userType": "external",
        "uuid": "u-1",
    }


def human(text: str, ts=TS_ISO) -> dict:
    """真实用户输入。`origin.kind == "human"` 是唯一的正标记（允许列表）。"""
    return {
        **_base("user", ts),
        "humanInput": True,
        "origin": {"kind": "human"},
        "message": {"role": "user", "content": [{"type": "text", "text": text}]},
    }


def tool_result(text: str, ts=TS_ISO, exit_code=None, is_error=None) -> dict:
    """工具结果回填。角色是 user，但不是人说的话——真实文件里占绝大多数。"""
    tur = {
        "exitCode": exit_code,
        "interrupted": False,
        "isImage": False,
        "kind": "bash",
        "noOutputExpected": False,
        "signal": None,
        "stderr": "",
        "stdout": text,
        "telemetryExecutionId": "t-1",
    }
    if is_error is not None:
        tur["isError"] = is_error
    return {
        **_base("user", ts),
        "toolUseResult": tur,
        "sourceToolAssistantUUID": "a-1",
        "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "call_1", "content": text}]},
    }


def failed_tool_result(text: str, ts=TS_ISO) -> dict:
    """实测的失败形状：exitCode=1 且 isError=True（138 条里的绝大多数）。"""
    return tool_result(text, ts, exit_code=1, is_error=True)


def failed_block_list_result(text: str, ts=TS_ISO) -> dict:
    """content 为块列表的失败工具调用。

    实测 7051 条 tool_result 里只有 1 条的 content 是块列表（还是个 image 块），
    但块列表是这个 schema 的合法形态，所以报错正文的提取要走同一条路。
    """
    record = failed_tool_result(text, ts)
    record["message"]["content"][0]["content"] = [{"type": "text", "text": text}]
    return record


def compaction_summary(text: str, ts=TS_ISO) -> dict:
    """压缩摘要：没有 origin 字段的 text 记录。

    最危险的一类——它的正文**引着用户原话**（"用户说：…"），所以比工具输出
    更像"用户说过的话"。按"user 角色就取 text"实现会把它整段收进来，
    于是用户的每句话都被机器转述一遍入库。
    """
    return {
        **_base("user", ts),
        "isCompactSummary": True,
        "isVisibleInTranscriptOnly": True,
        "message": {"role": "user", "content": [{"type": "text", "text": text}]},
    }


def task_notification(text: str, ts=TS_ISO) -> dict:
    """任务通知：有 origin 字段，但 kind 不是 human（实测 5 条）。"""
    return {
        **_base("user", ts),
        "isMeta": True,
        "origin": {"kind": "task-notification"},
        "message": {"role": "user", "content": [{"type": "text", "text": text}]},
    }


def meta_record(ts=TS_ISO) -> dict:
    """isMeta 的元记录（实测 88 条）：连 content 都没有。"""
    return {**_base("user", ts), "isMeta": True, "message": {"role": "user"}}


def assistant(text: str | None = None, ts=TS_ISO, thinking: str | None = None,
              redacted: bool = False) -> dict:
    """助手回复：content 里混着 tool_use / thinking / text，只取 text。"""
    content: list[dict] = []
    if thinking:
        content.append({"type": "thinking", "thinking": thinking, "signature": "s"})
    if redacted:
        content.append({"type": "redacted_thinking", "data": "ENCRYPTED-BLOB"})
    content.append({"type": "tool_use", "id": "call_1", "name": "Bash", "input": {}})
    if text:
        content.append({"type": "text", "text": text})
    return {**_base("assistant", ts), "message": {"role": "assistant", "content": content}}


def active_leaf(ts=TS_MS) -> dict:
    """bookkeeping：epoch 毫秒整数时间戳，占真实文件一多半，从不产素材。"""
    return {"type": "active-leaf", "timestamp": ts, "sessionId": SESSION_ID,
            "leafUuid": "x", "explicit": False}


def timestamp_less(record_type: str = "custom-title") -> dict:
    """无时间戳的 bookkeeping（实测 1016 条，全是这类，不产素材）。"""
    return {"type": record_type, "sessionId": SESSION_ID, "title": "某标题"}


@pytest.fixture
def projects_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(qoder, "PROJECTS_DIR", tmp_path)
    return tmp_path


def write_session(projects_dir, name: str, records, slug="-Users-nava-Code-demo"):
    d = projects_dir / slug
    d.mkdir(parents=True, exist_ok=True)
    path = d / name
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
        encoding="utf-8")
    return path


def ref(path, day=DAY) -> SourceRef:
    return SourceRef(source="qoder", ref=str(path), day=day)


# --- discover ---


def test_discover_finds_sessions_with_material_on_the_day(projects_dir):
    keep = write_session(projects_dir, f"{SESSION_ID}.jsonl",
                         [active_leaf(), human("今天的提问", TS_ISO)])
    write_session(projects_dir, "other.jsonl", [human("十天前的提问", OLD_ISO)])

    refs = qoder.discover(DAY)

    assert [r.ref for r in refs] == [str(keep)]
    assert refs[0].source == "qoder"
    assert refs[0].day == DAY


def test_discover_walks_past_integer_timestamps_to_reach_the_human_record(projects_dir):
    """真实文件里 active-leaf（毫秒整数）占一多半，且常排在用户发言之前。

    假定时间戳是字符串的实现，走不到那条 human 记录就抛 AttributeError 了。
    """
    path = write_session(projects_dir, "a.jsonl", [
        active_leaf(TS_MS),
        active_leaf(TS_MS_LATER),
        human("后面的提问", TS_ISO_LATER),
    ])

    assert [r.ref for r in qoder.discover(DAY)] == [str(path)]


def test_discover_ignores_sessions_with_no_material_on_the_day(projects_dir):
    """只有 bookkeeping 的会话不是素材——它的 parse 只会产出空字符串。

    一天里每个跨夜会话都会写 active-leaf；照单全收等于往快照里塞一堆空素材。
    """
    write_session(projects_dir, "bookkeeping.jsonl", [active_leaf(), active_leaf()])

    assert qoder.discover(DAY) == []


def test_discover_ignores_sessions_whose_only_text_is_injected(projects_dir):
    write_session(projects_dir, "injected.jsonl", [
        tool_result("工具输出", TS_ISO),
        compaction_summary("摘要正文", TS_ISO),
    ])

    assert qoder.discover(DAY) == []


def test_discover_skips_files_last_written_before_the_day(tmp_path, monkeypatch):
    """mtime 预筛：最后写入早于当日零点的文件不可能含当日记录，不必读。"""
    monkeypatch.setattr(qoder, "PROJECTS_DIR", tmp_path)
    path = write_session(tmp_path, "old.jsonl", [human("老记录", OLD_ISO)])
    stamp = datetime(2026, 9, 1, 12, 0, tzinfo=TZ).timestamp()
    import os
    os.utime(path, (stamp, stamp))

    assert qoder.discover(DAY) == []


def test_discover_missing_dir_is_noop(tmp_path, monkeypatch):
    monkeypatch.setattr(qoder, "PROJECTS_DIR", tmp_path / "nope")

    assert qoder.discover(DAY) == []


def test_discover_ignores_unparsable_lines(projects_dir):
    """半截 JSON 不该让整个源消失（NFR-004 的本地版）。"""
    d = projects_dir / "slug"
    d.mkdir(parents=True)
    path = d / "broken.jsonl"
    path.write_text("{不是 json}\n" + json.dumps(human("还好有这行", TS_ISO)) + "\n",
                    encoding="utf-8")

    assert [r.ref for r in qoder.discover(DAY)] == [str(path)]


# --- parse：取什么 ---


def test_parse_extracts_human_and_assistant_text(projects_dir):
    path = write_session(projects_dir, "a.jsonl", [
        human("为什么这么慢", TS_ISO),
        assistant("因为模型在思考", ts=TS_ISO),
    ])

    material = qoder.parse(ref(path))

    assert material.source == "qoder"
    assert "为什么这么慢" in material.text
    assert "因为模型在思考" in material.text
    assert material.kind == "message"
    assert material.ts.astimezone(TZ).date() == DAY


def test_parse_caps_a_long_transcript(projects_dir):
    path = write_session(projects_dir, "a.jsonl", [
        human("长" * 50, TS_ISO),
        assistant("回" * (qoder.MAX_SESSION_CHARS + 5000), ts=TS_ISO),
    ])

    assert len(qoder.parse(ref(path)).text) == qoder.MAX_SESSION_CHARS


# --- parse：丢什么（注入物）---


def test_parse_drops_tool_results(projects_dir):
    path = write_session(projects_dir, "a.jsonl", [
        human("提问", TS_ISO),
        tool_result("工具吐出来的成功输出", TS_ISO, exit_code=0),
    ])

    material = qoder.parse(ref(path))

    assert "工具吐出来的成功输出" not in material.text
    assert "提问" in material.text


def test_parse_drops_thinking_blocks(projects_dir):
    """thinking 是内部推理，不是用户看得见的内容（实测 5071 块）。

    体量大、充满自我纠正，蒸馏器会把它当成长篇高价值反思。
    codex 丢 developer 角色、这里丢 thinking，是同一条理由。
    """
    path = write_session(projects_dir, "a.jsonl", [
        human("提问", TS_ISO),
        assistant("结论", thinking="内部推理：先否定再肯定", ts=TS_ISO),
    ])

    material = qoder.parse(ref(path))

    assert "内部推理" not in material.text
    # 也不是"少收了别的"：转写恰好是两条记录各自的 text 块，用 "\n---\n" 连接
    assert material.text == "提问\n---\n结论"


def test_parse_drops_redacted_thinking_blocks(projects_dir):
    """加密的推理块（实测 431 块）：只有 data，没有 text，不能漏进来。"""
    path = write_session(projects_dir, "a.jsonl", [
        assistant("结论", redacted=True, ts=TS_ISO),
    ])

    material = qoder.parse(ref(path))

    assert "ENCRYPTED-BLOB" not in material.text
    assert material.text == "结论"


def test_parse_drops_compaction_summaries(projects_dir):
    """压缩摘要引着用户原话，所以它比工具输出更像"用户说过的话"。"""
    path = write_session(projects_dir, "a.jsonl", [
        compaction_summary(
            "This session is being continued from a previous conversation "
            "that ran out of context. 用户说：帮我加个门禁"),
        human("帮我加个门禁", TS_ISO),
    ])

    material = qoder.parse(ref(path))

    assert "continued from a previous conversation" not in material.text
    assert material.text.count("帮我加个门禁") == 1


def test_parse_drops_task_notifications(projects_dir):
    path = write_session(projects_dir, "a.jsonl", [
        task_notification("任务已完成，请继续", TS_ISO),
        human("真正的提问", TS_ISO),
    ])

    material = qoder.parse(ref(path))

    assert "任务已完成" not in material.text
    assert "真正的提问" in material.text


def test_parse_drops_metadata_only_records(projects_dir):
    path = write_session(projects_dir, "a.jsonl", [meta_record(), human("提问", TS_ISO)])

    material = qoder.parse(ref(path))

    assert "提问" in material.text
    assert len(material.text) == len("提问")


def test_parse_produces_no_text_when_everything_is_injected(projects_dir):
    """一整天的记录全是注入物时，不能凭空造出素材来，也不能拿 bookkeeping
    的时间当素材时间——回落到当日零点。"""
    path = write_session(projects_dir, "a.jsonl", [
        active_leaf(TS_MS),
        tool_result("输出一", TS_ISO),
        compaction_summary("摘要", TS_ISO),
    ])

    material = qoder.parse(ref(path))

    assert material.text == ""
    assert material.ts == datetime.combine(DAY, datetime.min.time(), tzinfo=TZ)


# --- parse：时间戳 ---


def test_parse_skips_other_days(projects_dir):
    path = write_session(projects_dir, "a.jsonl", [
        human("今天的", TS_ISO),
        human("别的日子的", OLD_ISO),
        active_leaf(OLD_MS),
    ])

    material = qoder.parse(ref(path))

    assert "今天的" in material.text
    assert "别的日子的" not in material.text


def test_parse_anchors_ts_to_shanghai(projects_dir):
    """UTC 16:30 属于上海的第二天——归属日按本地算，不按 UTC。"""
    path = write_session(projects_dir, "a.jsonl", [human("跨零点的那句", TS_ISO)])

    ts = qoder.parse(ref(path)).ts

    assert ts.tzinfo is not None
    assert ts.astimezone(TZ).date() == DAY
    assert ts.astimezone(TZ).hour == 0 and ts.astimezone(TZ).minute == 30


def test_parse_ts_is_the_first_contributing_record(projects_dir):
    """ts 描述素材本身，不取 bookkeeping（毫秒整数）的时间。"""
    path = write_session(projects_dir, "a.jsonl", [
        active_leaf(TS_MS),                    # 上海 00:30，不产素材
        human("真正要记的那句", TS_ISO_LATER),  # 上海 04:15
    ])

    ts = qoder.parse(ref(path)).ts

    assert ts.astimezone(TZ) == datetime(2026, 9, 18, 4, 15, tzinfo=TZ)


def test_parse_survives_timestamp_less_records(projects_dir):
    """1016 条 bookkeeping 没有时间戳，混在文件里不能让解析崩掉。"""
    path = write_session(projects_dir, "a.jsonl", [
        timestamp_less("file-history-snapshot"),
        human("有时间的提问", TS_ISO),
        timestamp_less("workspace-directories"),
    ])

    material = qoder.parse(ref(path))

    assert material.text == "有时间的提问"
    assert material.ts.astimezone(TZ).date() == DAY


# --- parse：行为证据（FR-008）---


def test_parse_turns_a_failed_tool_call_into_error_text(projects_dir):
    path = write_session(projects_dir, "a.jsonl", [
        human("跑一下测试", TS_ISO),
        failed_tool_result("Exit code 1\nFAILED test_foo.py::test_bar", TS_ISO),
    ])

    material = qoder.parse(ref(path))

    assert "[error]" in material.text
    assert "FAILED test_foo.py::test_bar" in material.text
    assert material.meta["error_count"] == 1


def test_parse_reads_failure_text_from_a_content_block_list(projects_dir):
    path = write_session(projects_dir, "a.jsonl", [
        failed_block_list_result("Exit code 1\n找不到这个文件", TS_ISO),
    ])

    material = qoder.parse(ref(path))

    assert "找不到这个文件" in material.text


def test_parse_counts_a_failure_that_produced_no_output(projects_dir):
    """标志位说失败了，就不该因为没抓到输出正文而丢掉这个信号。

    与 codex 不同：codex 要从输出正文里**找**失败信号，没正文就没证据；
    qoder 的失败是 `isError`/`exitCode` 明说的，正文空着不影响判据
    （kimi_code 同此）。
    """
    path = write_session(projects_dir, "a.jsonl", [
        failed_tool_result("", TS_ISO),
    ])

    material = qoder.parse(ref(path))

    assert material.meta["error_count"] == 1
    assert "[error]" in material.text


def test_parse_counts_consecutive_failures_as_struggle(projects_dir):
    """三轮连续失败未打断 → struggle_rounds=3（FR-008 的高价值信号）。"""
    path = write_session(projects_dir, "a.jsonl", [
        human("修一下这个 bug", TS_ISO),
        failed_tool_result("Exit code 1", TS_ISO),
        assistant("再试一次", ts=TS_ISO),
        failed_tool_result("Exit code 1", TS_ISO),
        failed_tool_result("Exit code 1", TS_ISO),
    ])

    material = qoder.parse(ref(path))

    assert material.meta["struggle_rounds"] == 3
    assert material.meta["error_count"] == 3


def test_parse_clean_tool_result_ends_a_struggle_run(projects_dir):
    """工具成功才归零——助手插话不算解决（codex/kimi_code 同一条语义）。"""
    path = write_session(projects_dir, "a.jsonl", [
        failed_tool_result("Exit code 1", TS_ISO),
        failed_tool_result("Exit code 1", TS_ISO),
        tool_result("ok", TS_ISO, exit_code=0),
        failed_tool_result("Exit code 1", TS_ISO),
    ])

    material = qoder.parse(ref(path))

    assert material.meta["struggle_rounds"] == 2
    assert material.meta["error_count"] == 3


# --- meta ---


def test_parse_carries_cwd_session_id_and_branch(projects_dir):
    """cwd 直接来自记录，不像 trae 那样要从编码过的目录名猜。"""
    path = write_session(projects_dir, f"{SESSION_ID}.jsonl", [human("提问", TS_ISO)])

    meta = qoder.parse(ref(path)).meta

    assert meta["cwd"] == DEMO_CWD
    assert meta["session_id"] == SESSION_ID
    assert meta["git_branch"] == "main"


# --- registry ---


def test_registered_in_registry():
    from src.plugins import iter_plugins

    assert "qoder" in {p.name for p in iter_plugins()}
