"""Unit tests for the qoder_cn plugin (T051).

Fixture 照 2026-09-25 本机 `~/.qoder-cn/projects/` 的实测绘形（12 会话 /
34513 行）。它与 `qoder` **同属一套 schema**（字段名一致），但**不是逐字段
同分布**——下面这张对照表就是本文件为什么不能照抄 test_qoder.py 的原因：

| | qoder | qoder_cn |
|---|---|---|
| 会话 / 行数 | 3 / 38629 | 12 / 34513 |
| `origin.kind="human"` | 146 | 284 |
| `origin.kind="task-notification"` | **5** | **65** |
| `isCompactSummary` | 106 | 10 |
| `isMeta` 元记录 | 88 | 25 |
| `redacted_thinking` 块 | 431 | **0** |
| human 记录里的 image 块 | 0 | 5 |
| `toolUseResult` 为字符串 | 0 | 2 |
| 失败的工具调用 | 138 | 77 |

最要命的一行是 task-notification：qoder 只有 5 条，qoder_cn 有 65 条——**13 倍**。
换句话说，同一条"用户角色不等于用户输入"的过滤规则，在 qoder_cn 上的分量
重得多；照抄一份"只丢 isCompactSummary"的实现，会在 qoder 上看起来没问题，
在 qoder_cn 上多收 65 条机器文本。

时间戳同样双编码：`user`(5968) / `assistant`(12670) 是 ISO 字符串，
`active-leaf`(12505) / `runtime-config`(450) 是 epoch 毫秒整数，
另有 1466 条无时间戳的 bookkeeping。
"""

import json
from datetime import date, datetime, timedelta, timezone

import pytest

from src.plugins import SourceRef, qoder_cn

DAY = date(2026, 9, 18)
TZ = timezone(timedelta(hours=8))
DEMO_CWD = "/Users/nava/Code/demo"
SESSION_ID = "1361865f-ad0b-425b-af9e-3633d7af61a0"

TS_ISO = "2026-09-17T16:30:00.000Z"       # 上海 2026-09-18 00:30（属于 DAY）
TS_MS = 1789662600000
TS_ISO_LATER = "2026-09-17T20:15:00.000Z"  # 上海 2026-09-18 04:15
OLD_ISO = "2026-09-10T02:00:00.000Z"       # 上海 2026-09-10（别的日子）
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


def human(text: str, ts=TS_ISO, image: bool = False) -> dict:
    content: list[dict] = [{"type": "text", "text": text}]
    if image:
        content.append({"type": "image", "source": {"data": "BASE64-IMAGE-BYTES"}})
    return {
        **_base("user", ts),
        "humanInput": True,
        "origin": {"kind": "human"},
        "message": {"role": "user", "content": content},
    }


def tool_result(text: str, ts=TS_ISO, exit_code=None, is_error=None) -> dict:
    tur = {
        "exitCode": exit_code, "interrupted": False, "isImage": False,
        "kind": "bash", "noOutputExpected": False, "signal": None,
        "stderr": "", "stdout": text, "telemetryExecutionId": "t-1",
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
    return tool_result(text, ts, exit_code=1, is_error=True)


def task_notification(text: str, ts=TS_ISO) -> dict:
    """实测 65 条——qoder_cn 上比 qoder（5 条）多 13 倍。"""
    return {
        **_base("user", ts),
        "isMeta": True,
        "origin": {"kind": "task-notification"},
        "message": {"role": "user", "content": [{"type": "text", "text": text}]},
    }


def compaction_summary(text: str, ts=TS_ISO) -> dict:
    return {
        **_base("user", ts),
        "isCompactSummary": True,
        "isVisibleInTranscriptOnly": True,
        "message": {"role": "user", "content": [{"type": "text", "text": text}]},
    }


def assistant(text: str | None = None, ts=TS_ISO, thinking: str | None = None) -> dict:
    content: list[dict] = []
    if thinking:
        content.append({"type": "thinking", "thinking": thinking, "signature": "s"})
    content.append({"type": "tool_use", "id": "call_1", "name": "Bash", "input": {}})
    if text:
        content.append({"type": "text", "text": text})
    return {**_base("assistant", ts), "message": {"role": "assistant", "content": content}}


def active_leaf(ts=TS_MS) -> dict:
    return {"type": "active-leaf", "timestamp": ts, "sessionId": SESSION_ID,
            "leafUuid": "x", "explicit": False}


@pytest.fixture
def projects_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(qoder_cn, "PROJECTS_DIR", tmp_path)
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
    return SourceRef(source="qoder_cn", ref=str(path), day=day)


# --- discover ---


def test_discover_finds_sessions_with_material_on_the_day(projects_dir):
    keep = write_session(projects_dir, f"{SESSION_ID}.jsonl",
                         [active_leaf(), human("今天的提问", TS_ISO)])
    write_session(projects_dir, "other.jsonl", [human("十天前的提问", OLD_ISO)])

    refs = qoder_cn.discover(DAY)

    assert [r.ref for r in refs] == [str(keep)]
    assert refs[0].source == "qoder_cn"
    assert refs[0].day == DAY


def test_discover_ignores_subagent_transcripts(projects_dir):
    """路径允许列表是 `projects/<slug>/<uuid>.jsonl`（两层）。

    实测源目录树里有 113 个 `subagents/*.jsonl`（子代理转写，不是用户可见
    内容——codex 插件丢 `agent_message` 是同一条理由）。它们比会话文件深一层，
    **不在允许列表里**，所以哪怕里面写着 human 标记也不该被读进来。
    """
    write_session(projects_dir, f"{SESSION_ID}.jsonl", [human("主会话的提问", TS_ISO)])
    nested = projects_dir / "-Users-nava-Code-demo" / SESSION_ID / "subagents"
    nested.mkdir(parents=True)
    (nested / "agent-aExplore-0cf6bedb.jsonl").write_text(
        json.dumps(human("子代理转写里也有 human 标记", TS_ISO)) + "\n",
        encoding="utf-8")

    refs = qoder_cn.discover(DAY)

    assert len(refs) == 1
    assert "subagents" not in refs[0].ref


def test_discover_ignores_the_state_json_beside_the_session(projects_dir):
    """实测每个会话旁边就有 `state.json` / `compression-v2/state.json`。

    允许列表限定 `*.jsonl`，所以它们从不进入读取路径（PRD NFR-001 的
    路径允许列表要挡的正是这类"顺手读进来"）。
    """
    write_session(projects_dir, f"{SESSION_ID}.jsonl", [human("提问", TS_ISO)])
    session_dir = projects_dir / "-Users-nava-Code-demo" / SESSION_ID
    session_dir.mkdir(parents=True)
    (session_dir / "state.json").write_text('{"token": "SECRET"}', encoding="utf-8")

    (only,) = qoder_cn.discover(DAY)

    assert only.ref.endswith(f"{SESSION_ID}.jsonl")


def test_discover_ignores_bookkeeping_only_sessions(projects_dir):
    write_session(projects_dir, "bookkeeping.jsonl", [active_leaf(), active_leaf()])

    assert qoder_cn.discover(DAY) == []


def test_discover_missing_dir_is_noop(tmp_path, monkeypatch):
    monkeypatch.setattr(qoder_cn, "PROJECTS_DIR", tmp_path / "nope")

    assert qoder_cn.discover(DAY) == []


def test_discover_ignores_unparsable_lines(projects_dir):
    d = projects_dir / "slug"
    d.mkdir(parents=True)
    path = d / "broken.jsonl"
    path.write_text("{不是 json}\n" + json.dumps(human("还好有这行", TS_ISO)) + "\n",
                    encoding="utf-8")

    assert [r.ref for r in qoder_cn.discover(DAY)] == [str(path)]


# --- parse：取什么 ---


def test_parse_extracts_human_and_assistant_text(projects_dir):
    path = write_session(projects_dir, "a.jsonl", [
        human("为什么这么慢", TS_ISO),
        assistant("因为模型在思考", ts=TS_ISO),
    ])

    material = qoder_cn.parse(ref(path))

    assert material.source == "qoder_cn"
    assert material.kind == "message"
    assert material.text == "为什么这么慢\n---\n因为模型在思考"
    assert material.ts.astimezone(TZ).date() == DAY


def test_parse_caps_a_long_transcript(projects_dir):
    path = write_session(projects_dir, "a.jsonl", [
        assistant("回" * (qoder_cn.MAX_SESSION_CHARS + 5000), ts=TS_ISO),
    ])

    assert len(qoder_cn.parse(ref(path)).text) == qoder_cn.MAX_SESSION_CHARS


# --- parse：丢什么（注入物）---


def test_parse_drops_tool_results(projects_dir):
    path = write_session(projects_dir, "a.jsonl", [
        human("提问", TS_ISO),
        tool_result("工具吐出来的成功输出", TS_ISO, exit_code=0),
    ])

    material = qoder_cn.parse(ref(path))

    assert material.text == "提问"


def test_parse_drops_task_notifications(projects_dir):
    """本文件里分量最重的一条：qoder_cn 实测 65 条，是 qoder 的 13 倍。

    只丢 `isCompactSummary` 的实现会在 qoder 上看着没事（那边只有 5 条
    task-notification），在 qoder_cn 上多收 65 条机器文本。
    """
    path = write_session(projects_dir, "a.jsonl", [
        task_notification("任务已完成，请继续", TS_ISO),
        human("真正的提问", TS_ISO),
    ])

    assert qoder_cn.parse(ref(path)).text == "真正的提问"


def test_parse_drops_compaction_summaries(projects_dir):
    path = write_session(projects_dir, "a.jsonl", [
        compaction_summary(
            "This session is being continued from a previous conversation "
            "that ran out of context. 用户说：帮我加个门禁"),
        human("帮我加个门禁", TS_ISO),
    ])

    material = qoder_cn.parse(ref(path))

    assert "continued from a previous conversation" not in material.text
    assert material.text == "帮我加个门禁"


def test_parse_drops_thinking_blocks(projects_dir):
    """实测 5283 块 thinking：体量大、充满自我纠正，不是用户看得见的内容。"""
    path = write_session(projects_dir, "a.jsonl", [
        assistant("结论", thinking="内部推理：先否定再肯定", ts=TS_ISO),
    ])

    material = qoder_cn.parse(ref(path))

    assert "内部推理" not in material.text
    assert material.text == "结论"


def test_parse_drops_image_blocks_from_human_records(projects_dir):
    """实测 5 条 human 记录里除 text 外还挂着 image 块（base64 图片）。

    图片的 payload 不该进转写：它既不是文本，又是整条记录里最大的字段。
    """
    path = write_session(projects_dir, "a.jsonl", [
        human("看这张图", TS_ISO, image=True),
    ])

    material = qoder_cn.parse(ref(path))

    assert material.text == "看这张图"
    assert "BASE64-IMAGE-BYTES" not in material.text


# --- parse：时间戳 ---


def test_parse_skips_other_days(projects_dir):
    path = write_session(projects_dir, "a.jsonl", [
        human("今天的", TS_ISO),
        human("别的日子的", OLD_ISO),
        active_leaf(OLD_MS),
    ])

    material = qoder_cn.parse(ref(path))

    assert material.text == "今天的"


def test_parse_walks_past_integer_timestamps(projects_dir):
    """active-leaf（毫秒整数）占 34513 行里的 12505 行，且排在用户发言之前。

    只认字符串的实现会在这里 `AttributeError` 崩掉——而 collect 会把异常当成
    "该源今天失败"，整个 qoder_cn 当天素材全丢（NFR-004 的静默降级会掩盖它）。
    """
    path = write_session(projects_dir, "a.jsonl", [
        active_leaf(TS_MS),
        active_leaf(TS_MS),
        human("真正要记的那句", TS_ISO_LATER),
    ])

    material = qoder_cn.parse(ref(path))

    assert material.text == "真正要记的那句"
    assert material.ts.astimezone(TZ) == datetime(2026, 9, 18, 4, 15, tzinfo=TZ)


def test_parse_anchors_ts_to_shanghai(projects_dir):
    """UTC 16:30 属于上海的第二天——归属日按本地算，不按 UTC。"""
    path = write_session(projects_dir, "a.jsonl", [human("跨零点的那句", TS_ISO)])

    ts = qoder_cn.parse(ref(path)).ts

    assert ts.tzinfo is not None
    assert ts.astimezone(TZ) == datetime(2026, 9, 18, 0, 30, tzinfo=TZ)


def test_parse_survives_timestamp_less_records(projects_dir):
    """1466 条 bookkeeping 没有时间戳，混在文件里不能让解析崩掉。"""
    path = write_session(projects_dir, "a.jsonl", [
        {"type": "file-history-snapshot", "sessionId": SESSION_ID},
        human("有时间的提问", TS_ISO),
        {"type": "last-prompt", "sessionId": SESSION_ID},
    ])

    material = qoder_cn.parse(ref(path))

    assert material.text == "有时间的提问"


def test_parse_produces_no_text_when_everything_is_injected(projects_dir):
    path = write_session(projects_dir, "a.jsonl", [
        task_notification("通知", TS_ISO),
        compaction_summary("摘要", TS_ISO),
        active_leaf(TS_MS),
    ])

    material = qoder_cn.parse(ref(path))

    assert material.text == ""
    assert material.ts == datetime.combine(DAY, datetime.min.time(), tzinfo=TZ)


# --- parse：行为证据（FR-008）---


def test_parse_turns_a_failed_tool_call_into_error_text(projects_dir):
    path = write_session(projects_dir, "a.jsonl", [
        human("跑一下测试", TS_ISO),
        failed_tool_result("Exit code 1\nFAILED test_foo.py::test_bar", TS_ISO),
    ])

    material = qoder_cn.parse(ref(path))

    assert "[error]" in material.text
    assert material.meta["error_count"] == 1


def test_parse_counts_a_failure_that_produced_no_output(projects_dir):
    """显式标志位已知失败 → 正文为空也照样记账。

    与 codex 的判据相反，理由和 qoder 那边同一条：codex 是从输出正文里**找**
    失败信号，没有正文就没有证据；这里的 isError / exitCode 是显式给出的，
    丢掉等于把已经知道的事扔了。
    """
    path = write_session(projects_dir, "a.jsonl", [failed_tool_result("", TS_ISO)])

    material = qoder_cn.parse(ref(path))

    assert material.meta["error_count"] == 1
    assert material.meta["struggle_rounds"] == 1
    assert material.text.startswith("[error]")


def test_parse_drops_an_unknown_origin_kind(projects_dir):
    """正标记的价值就在这里，"非 isMeta 即用户输入"式的黑名单会把它收进来。

    实测已知的注入物是 isMeta / isCompactSummary / task-notification 三种，
    但这份名单**会变**——工具升级会加新的机器记录类型。允许列表让新类型默认
    被挡在外面；黑名单只能等它先污染了素材再补一条。
    """
    unknown = {**_base("user", TS_ISO), "origin": {"kind": "future-machinery"},
               "message": {"role": "user", "content": [
                   {"type": "text", "text": "未来的机器文本"}]}}
    path = write_session(projects_dir, "a.jsonl", [unknown, human("人说的", TS_ISO)])

    assert qoder_cn.parse(ref(path)).text == "人说的"


def test_parse_tolerates_a_string_tool_use_result(projects_dir):
    """实测 5577 条 toolUseResult 是 dict，另有 2 条是字符串。

    字符串里没有 exitCode/isError，也就是**没有失败证据**——不该因此抛异常，
    也不该凭空算成一次失败。
    """
    record = tool_result("输出", TS_ISO)
    record["toolUseResult"] = "some legacy string payload"
    path = write_session(projects_dir, "a.jsonl", [record])

    material = qoder_cn.parse(ref(path))

    assert material.meta["error_count"] == 0
    assert material.text == ""


def test_parse_counts_consecutive_failures_as_struggle(projects_dir):
    path = write_session(projects_dir, "a.jsonl", [
        failed_tool_result("Exit code 1", TS_ISO),
        assistant("再试一次", ts=TS_ISO),
        failed_tool_result("Exit code 1", TS_ISO),
    ])

    assert qoder_cn.parse(ref(path)).meta["struggle_rounds"] == 2


def test_parse_clean_tool_result_ends_a_struggle_run(projects_dir):
    path = write_session(projects_dir, "a.jsonl", [
        failed_tool_result("Exit code 1", TS_ISO),
        failed_tool_result("Exit code 1", TS_ISO),
        tool_result("ok", TS_ISO, exit_code=0),
        failed_tool_result("Exit code 1", TS_ISO),
    ])

    material = qoder_cn.parse(ref(path))

    assert material.meta["struggle_rounds"] == 2
    assert material.meta["error_count"] == 3


# --- meta ---


def test_parse_carries_cwd_session_id_and_branch(projects_dir):
    path = write_session(projects_dir, f"{SESSION_ID}.jsonl", [human("提问", TS_ISO)])

    meta = qoder_cn.parse(ref(path)).meta

    assert meta["cwd"] == DEMO_CWD
    assert meta["session_id"] == SESSION_ID
    assert meta["git_branch"] == "main"


# --- registry ---


def test_registered_in_registry():
    from src.plugins import iter_plugins

    assert "qoder_cn" in {p.name for p in iter_plugins()}
