"""Unit tests for the workbuddy_ai plugin (T052).

Fixture 照 2026-09-25 本机 `~/.workbuddy-ai/projects/` 的实测绘形（5 会话 /
5326 行）。它是 **CodeBuddy 系**，与 qoder 系不共用解析器（PRD FR-002a：连
时间戳策略都相反）。

记录类型（实测）：`function_call` 1553 / `function_call_result` 1552 /
`reasoning` 1020 / `message` 678 / `file-history-snapshot` 515 / `ai-title` 7 /
`resend-fork-notice` 1。

**这个插件的全部难点在注入物**。`message` 678 条里 `role=user` 116 条，其中
**只有 47 条**带 `<user_query>` 标签；另外 69 条是机器物——
`<task-notification>` 53、`<conversation_history_summary>` 6、
"Please continue with the conversation…" 6、`<system-reminder>` 2、
`<teammate-message>` 2（后两条的 `content` 是**字符串**而不是块列表）。
所以"role=user 就取正文"会多收 69 条（虚增 147%），而且这还没算上真正的大头：
那 47 条里，`<user_query>` 之外的正文最多有 **19429 字**注入模板（全库最大的一条
user 记录：整块 19486，标签外 19429，标签内 32）。

首条样本实测：整块 12392 字，标签外 **12354** 字，`<user_query>…</user_query>`
整段 38 字（标签内正文 13 + 标签本身 25）。不做提取 = 把 SOUL.md 人设文件、
`<user_info>`、`memory_and_skills_reminder` 全部当素材。故 PRD 的规则是
**正文只取标签内的内容，取不到标签的记录不产生素材**。

块类型实测：user 侧 `input_text` 115 条 + `image_blob_ref` 1 条（该块只有
`blob_id`/`mime`/`size`/`blob_path`/`original_filename`，**没有 `text` 键**，
所以按 text 取正文天然跳过它）；assistant 侧 `output_text` 562 条，没有一条
记录带多于一个块。47 条 `<user_query>` **全部**落在 `input_text` 块里。

**时间戳单一编码**：实测所有记录都是 epoch 毫秒整数（与 qoder 系的双编码相反）。

**失败信号（FR-008）在工具结果的尾部包裹层**：
`…Stdout: …\nStderr: (empty)\nExit Code: 0\nSignal: (none)`。
实测 1552 条结果里 783 条带该尾注（757 条为 0，26 条非零：137×12 是 SIGTERM、
1×14）。这条特别值得记下来的原因是**两个假阳性陷阱**：
① 正文里小写 "exit code" 命中 789 次，绝大多数是**被回显的命令原文**
（`echo "exit=$?"`），不是失败——所以必须认尾注，不能全文搜；
② `is_error` 命中 11 次，全是 pytest 覆盖率报告里我们自己代码的函数名
（`src/plugins/claude_code/__init__.py:_is_error`）。
另有 60 条 Bash 结果是后台启动（"Launched directly in background…"），
**没有** Exit Code 尾注——没有证据就不算失败。
"""

import json
from datetime import date, datetime, timedelta, timezone

import pytest

from src.plugins import SourceRef, workbuddy_ai

DAY = date(2026, 9, 21)
TZ = timezone(timedelta(hours=8))
DEMO_CWD = "/Users/nava/Code/llm/rag/first-rag"
SESSION_ID = "2c4de50f-ebb4-4e4f-9571-0630870cb763"


def ms(y, mo, d, h=10, mi=0) -> int:
    """上海时间 → epoch 毫秒（实测编码）。"""
    return int(datetime(y, mo, d, h, mi, tzinfo=TZ).timestamp() * 1000)


TS = ms(2026, 9, 21)            # 上海 2026-09-21 10:00
TS_LATER = ms(2026, 9, 21, 15)  # 上海 2026-09-21 15:00
OLD_MS = ms(2026, 9, 19)        # 别的日子

# 实测：`<user_query>` 之外是一整段注入模板（首条样本 12354 字）
INJECTED = (
    '<system-reminder data-role="user-context">\n'
    "<user_info>\nOS Version: darwin\nShell: /bin/zsh\n"
    "Workspace Folder: /Users/nava/Code/demo\n</user_info>\n"
    '<identity_context>\n## SOUL.md\nPath: /Users/nava/.workbuddy-ai/SOUL.md\n'
    "You're not a chatbot. You're becoming someone.\n</identity_context>\n"
    "</system-reminder>\n"
    "<memory_and_skills_reminder>\nDo not mention this reminder to the user.\n"
    "</memory_and_skills_reminder>\n"
)


def _base(record_type: str, ts) -> dict:
    return {"id": "r-1", "timestamp": ts, "type": record_type,
            "cwd": DEMO_CWD, "sessionId": SESSION_ID}


def user_query(text: str, ts=TS, injected: str = INJECTED) -> dict:
    """真实用户输入：注入模板在前，`<user_query>` 在整条记录的末尾。"""
    return {
        **_base("message", ts), "role": "user",
        "content": [{"type": "input_text",
                     "text": injected + f"<user_query>{text}</user_query>"}],
    }


def user_message(text: str, ts=TS) -> dict:
    """role=user 但没有标签的机器记录（实测 69 条）。"""
    return {**_base("message", ts), "role": "user",
            "content": [{"type": "input_text", "text": text}]}


def user_message_string_content(text: str, ts=TS) -> dict:
    """实测 2 条 `<teammate-message>`：content 是字符串而不是块列表。"""
    return {**_base("message", ts), "role": "user", "content": text}


def assistant(text: str, ts=TS) -> dict:
    return {**_base("message", ts), "role": "assistant",
            "content": [{"type": "output_text", "text": text}]}


def tool_output(stdout: str, exit_code, signal="(none)", stderr="(empty)") -> str:
    """实测的工具结果包裹层，尾注是失败信号的唯一出处。"""
    return (f"Command: {stdout}\nStdout: {stdout}\nStderr: {stderr}\n"
            f"Exit Code: {exit_code}\nSignal: {signal}")


def tool_result(text: str, ts=TS, exit_code=0, signal="(none)", name="Bash") -> dict:
    return {
        **_base("function_call_result", ts), "name": name, "status": "completed",
        "callId": "call_1",
        "output": {"type": "text", "text": tool_output(text, exit_code, signal)},
    }


def background_result(ts=TS) -> dict:
    """实测 60 条：后台启动，**没有** Exit Code 尾注。"""
    return {
        **_base("function_call_result", ts), "name": "Bash", "status": "completed",
        "callId": "call_1",
        "output": {"type": "text", "text":
                   "Launched directly in background (run_in_background=true).\n"
                   "Current Output (partial): (no output yet)"},
    }


def list_output_result(ts=TS) -> dict:
    """实测 47 条：output 是块列表（read_me / show_widget / present_files），无尾注。"""
    return {
        **_base("function_call_result", ts), "name": "read_me", "status": "completed",
        "callId": "call_1",
        "output": [{"type": "input_text", "text": '{"content":"# Visualizer"}'}],
    }


def function_call(ts=TS) -> dict:
    return {**_base("function_call", ts), "name": "Bash", "callId": "call_1",
            "arguments": '{"command": "ls"}', "message": "ls"}


def reasoning(ts=TS) -> dict:
    return {**_base("reasoning", ts),
            "rawContent": [{"type": "reasoning_text", "text": "内部推理：先看门禁"}]}


def file_history_snapshot(ts=TS) -> dict:
    return {**_base("file-history-snapshot", ts), "isSnapshotUpdate": False,
            "snapshot": {"files": []}}


@pytest.fixture
def sessions_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workbuddy_ai, "SESSIONS_DIR", tmp_path)
    return tmp_path


def write_session(sessions_dir, name: str, records, slug="-Users-nava-Code-demo"):
    d = sessions_dir / slug
    d.mkdir(parents=True, exist_ok=True)
    path = d / name
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
        encoding="utf-8")
    return path


def ref(path, day=DAY) -> SourceRef:
    return SourceRef(source="workbuddy_ai", ref=str(path), day=day)


# --- discover ---


def test_discover_finds_sessions_with_material_on_the_day(sessions_dir):
    keep = write_session(sessions_dir, f"{SESSION_ID}.jsonl",
                         [user_query("今天的提问", TS), assistant("回答", TS_LATER)])
    write_session(sessions_dir, "other.jsonl", [user_query("别的日子的", OLD_MS)])

    refs = workbuddy_ai.discover(DAY)

    assert [r.ref for r in refs] == [str(keep)]
    assert refs[0].source == "workbuddy_ai"
    assert refs[0].day == DAY


def test_discover_ignores_bookkeeping_only_sessions(sessions_dir):
    write_session(sessions_dir, "bookkeeping.jsonl",
                  [reasoning(), function_call(), file_history_snapshot()])

    assert workbuddy_ai.discover(DAY) == []


def test_discover_ignores_a_session_whose_user_messages_have_no_tag(sessions_dir):
    """PRD FR-002a：取不到 `<user_query>` 标签的记录 MUST 不产生素材。

    实测 69 条这样的记录（任务通知 / 摘要 / "Please continue…"）。一个只按
    role=user 取正文的实现会把它当素材——所以这天不该被 discover 认成有素材。
    """
    write_session(sessions_dir, f"{SESSION_ID}.jsonl", [
        user_message("<task-notification>\n<task-id>nOz7vq</task-id>", TS),
        user_message("Please continue with the conversation based on the "
                     "summarized context above.", TS_LATER),
    ])

    assert workbuddy_ai.discover(DAY) == []


def test_discover_finds_a_session_whose_only_material_is_a_failure(sessions_dir):
    """当天只有工具失败、没跟模型说一句话的会话，也是一条要记的素材。

    实测 26 条非零退出码，其中 12 条是 SIGTERM——被中断的后台/长命令很常见，
    那样的会话当天可能一条 user_query 都没有。discover 只认"有标签的 user
    记录"就会把它整条漏掉（快照里连失败证据都不剩）。
    """
    write_session(sessions_dir, f"{SESSION_ID}.jsonl",
                  [tool_result("grep -rln …", TS, exit_code=137, signal="SIGTERM")])

    assert len(workbuddy_ai.discover(DAY)) == 1


def test_discover_ignores_subagent_transcripts(sessions_dir):
    """路径允许列表是 `projects/<slug>/<uuid>.jsonl`（两层）。

    实测源目录树里有 `<uuid>/subagents/agent-*.jsonl`（比会话文件深一层），
    以及 `<uuid>.meta.json` / `<uuid>.file-rollback.ndjson` 两个兄弟文件。
    """
    write_session(sessions_dir, f"{SESSION_ID}.jsonl", [user_query("主会话", TS)])
    session_dir = sessions_dir / "-Users-nava-Code-demo" / SESSION_ID
    (session_dir / "subagents").mkdir(parents=True)
    (session_dir / "subagents" / "agent-0afdd7bc.jsonl").write_text(
        json.dumps(user_query("子代理转写", TS)) + "\n", encoding="utf-8")

    refs = workbuddy_ai.discover(DAY)

    assert len(refs) == 1
    assert "subagents" not in refs[0].ref


def test_discover_ignores_the_meta_and_rollback_siblings(sessions_dir):
    write_session(sessions_dir, f"{SESSION_ID}.jsonl", [user_query("提问", TS)])
    slug = sessions_dir / "-Users-nava-Code-demo"
    (slug / f"{SESSION_ID}.meta.json").write_text('{"title": "x"', encoding="utf-8")
    (slug / f"{SESSION_ID}.file-rollback.ndjson").write_text(
        '{"path": "/x"}\n', encoding="utf-8")

    (only,) = workbuddy_ai.discover(DAY)

    assert only.ref.endswith(f"{SESSION_ID}.jsonl")


def test_discover_missing_dir_is_noop(tmp_path, monkeypatch):
    monkeypatch.setattr(workbuddy_ai, "SESSIONS_DIR", tmp_path / "nope")

    assert workbuddy_ai.discover(DAY) == []


def test_discover_ignores_unparsable_lines(sessions_dir):
    d = sessions_dir / "slug"
    d.mkdir(parents=True)
    path = d / "broken.jsonl"
    path.write_text("{不是 json}\n" + json.dumps(user_query("还好有这行", TS)) + "\n",
                    encoding="utf-8")

    assert [r.ref for r in workbuddy_ai.discover(DAY)] == [str(path)]


# --- parse：取什么 ---


def test_parse_extracts_user_query_and_assistant_text(sessions_dir):
    path = write_session(sessions_dir, "a.jsonl", [
        user_query("为什么这么慢", TS),
        assistant("因为模型在思考", TS_LATER),
    ])

    material = workbuddy_ai.parse(ref(path))

    assert material.source == "workbuddy_ai"
    assert material.kind == "message"
    assert material.text == "为什么这么慢\n---\n因为模型在思考"
    assert material.ts.astimezone(TZ).date() == DAY


def test_parse_caps_a_long_transcript(sessions_dir):
    path = write_session(sessions_dir, "a.jsonl", [
        assistant("回" * (workbuddy_ai.MAX_SESSION_CHARS + 5000), TS),
    ])

    assert len(workbuddy_ai.parse(ref(path)).text) == workbuddy_ai.MAX_SESSION_CHARS


# --- parse：注入物（本插件的核心）---


def test_parse_drops_the_injected_wrapper_around_the_tag(sessions_dir):
    """AC-015 的核心：正文只含标签内的内容。

    实测首条样本整块 12392 字，标签外 12354 字是注入物——含用户的 `SOUL.md`
    全文、`<user_info>`、`memory_and_skills_reminder`。取整块就等于把注入模板
    当素材蒸馏。
    """
    path = write_session(sessions_dir, "a.jsonl",
                         [user_query("这个项目目前是不是缺少门禁", TS)])

    material = workbuddy_ai.parse(ref(path))

    assert material.text == "这个项目目前是不是缺少门禁"
    assert "SOUL.md" not in material.text
    assert "<user_info>" not in material.text
    assert "memory_and_skills_reminder" not in material.text
    assert "system-reminder" not in material.text


def test_parse_drops_untagged_user_messages(sessions_dir):
    """实测 69 条：task-notification / conversation_history_summary /
    "Please continue…" / system-reminder / teammate-message，一条都不该进正文。"""
    path = write_session(sessions_dir, "a.jsonl", [
        user_message("<task-notification>\n<task-id>nOz7vq</task-id>\n"
                     "<tool-use-id>chatcmpl-tool-1</tool-use-id>\n</task-notification>", TS),
        user_message("<conversation_history_summary>\nSummary:\n"
                     "1. **Primary Request and Intent:** 用户想要…\n"
                     "</conversation_history_summary>", TS),
        user_message("Please continue with the conversation based on the "
                     "summarized context above.", TS),
        user_message_string_content(
            '<teammate-message teammate_id="Explore-1" summary="只读核查完成">\n'
            "【只读核查报告】仓库 HEAD 3ea4ea9\n</teammate-message>", TS),
        user_query("真正要记的那句", TS_LATER),
    ])

    material = workbuddy_ai.parse(ref(path))

    assert material.text == "真正要记的那句"
    assert "task-notification" not in material.text
    assert "conversation_history_summary" not in material.text
    assert "Please continue" not in material.text
    assert "teammate-message" not in material.text


def test_parse_drops_reasoning(sessions_dir):
    """实测 1020 条 reasoning：内容是模型自述（"用户问：…我需要先…"），
    既不是用户输入也不是给用户看的回答。"""
    path = write_session(sessions_dir, "a.jsonl", [reasoning(), user_query("提问", TS)])

    material = workbuddy_ai.parse(ref(path))

    assert "内部推理" not in material.text
    assert material.text == "提问"


def test_parse_drops_tool_call_output(sessions_dir):
    path = write_session(sessions_dir, "a.jsonl", [
        function_call(),
        tool_result("工具吐出来的成功输出", TS, exit_code=0),
        user_query("提问", TS_LATER),
    ])

    material = workbuddy_ai.parse(ref(path))

    assert material.text == "提问"
    assert "工具吐出来的成功输出" not in material.text


def test_parse_produces_no_text_when_everything_is_injected(sessions_dir):
    path = write_session(sessions_dir, "a.jsonl", [
        user_message("<task-notification>通知</task-notification>", TS),
        reasoning(), function_call(), file_history_snapshot(),
    ])

    material = workbuddy_ai.parse(ref(path))

    assert material.text == ""
    assert material.ts == datetime.combine(DAY, datetime.min.time(), tzinfo=TZ)


def test_parse_produces_no_text_when_a_user_message_has_no_tag(sessions_dir):
    """标签缺失 = 不产素材，而不是"退而求其次"取整块。"""
    path = write_session(sessions_dir, "a.jsonl", [
        user_message(INJECTED + "没有标签的一段话", TS),
    ])

    assert workbuddy_ai.parse(ref(path)).text == ""


# --- parse：时间戳 ---


def test_parse_skips_other_days(sessions_dir):
    path = write_session(sessions_dir, "a.jsonl", [
        user_query("今天的", TS),
        user_query("别的日子的", OLD_MS),
    ])

    assert workbuddy_ai.parse(ref(path)).text == "今天的"


def test_parse_anchors_ts_to_shanghai(sessions_dir):
    path = write_session(sessions_dir, "a.jsonl",
                         [user_query("跨零点的", ms(2026, 9, 21, 0, 30))])

    ts = workbuddy_ai.parse(ref(path)).ts

    assert ts.tzinfo is not None
    assert ts.astimezone(TZ) == datetime(2026, 9, 21, 0, 30, tzinfo=TZ)


def test_parse_takes_ts_from_the_first_contributing_record(sessions_dir):
    path = write_session(sessions_dir, "a.jsonl", [
        user_query("先说的", TS),
        user_query("后说的", TS_LATER),
    ])

    assert workbuddy_ai.parse(ref(path)).ts.astimezone(TZ) == \
        datetime(2026, 9, 21, 10, 0, tzinfo=TZ)


def test_parse_survives_records_without_a_usable_timestamp(sessions_dir):
    path = write_session(sessions_dir, "a.jsonl", [
        {**_base("message", None), "role": "user",
         "content": [{"type": "input_text", "text": INJECTED + "<user_query>无</user_query>"}]},
        {**_base("message", "2026-09-21T02:00:00.000Z"), "role": "user",
         "content": [{"type": "input_text", "text": INJECTED + "<user_query>错编码</user_query>"}]},
        user_query("有时间的", TS),
    ])

    material = workbuddy_ai.parse(ref(path))

    assert material.text == "有时间的"


# --- parse：行为证据（FR-008）---


def test_parse_turns_a_nonzero_exit_code_into_error_text(sessions_dir):
    path = write_session(sessions_dir, "a.jsonl", [
        user_query("跑一下测试", TS),
        tool_result("FAILED tests/integration/test_retention.py::test_x",
                    TS, exit_code=1),
    ])

    material = workbuddy_ai.parse(ref(path))

    assert "[error]" in material.text
    assert "test_retention" in material.text
    assert material.meta["error_count"] == 1


def test_parse_counts_a_sigterm_as_a_failure(sessions_dir):
    """实测 12 条 Exit Code 137 / Signal: SIGTERM——被杀掉的命令也是失败。"""
    path = write_session(sessions_dir, "a.jsonl", [
        tool_result("grep -rln …", TS, exit_code=137, signal="SIGTERM"),
    ])

    material = workbuddy_ai.parse(ref(path))

    assert material.meta["error_count"] == 1
    assert material.text.startswith("[error]")


def test_parse_ignores_the_echoed_command_text(sessions_dir):
    """**本文件最要紧的一条。** 正文里小写 "exit code" 命中 789 次，绝大多数
    是**被回显的命令原文**（`echo "exit=$?"`），不是失败——所以不能全文搜关键词。

    但"只认 `Exit Code:` 这几个字"还不够。这里刻意让命令的**输出里**出现一行
    字面量 `Exit Code: 1`（`cat docs/format.md`，而那份文档讲的正是包裹层格式，
    它当然会把它作为一个例子印出来），真实尾注却是 0。取*第一个*匹配的实现会在
    这里平白记一次失败；只有认**末尾**那一条才对。
    """
    doc = ("# 工具结果包裹层\n\n命令失败时尾注长这样：\n\n"
           "Exit Code: 1\nSignal: SIGTERM\n\n成功则是 Exit Code: 0。\n")
    output = ("Command: cat docs/format.md\n"
              f"Stdout: {doc}\nStderr: (empty)\nExit Code: 0\nSignal: (none)")
    path = write_session(sessions_dir, "a.jsonl", [
        user_query("跑一下", TS),
        {**_base("function_call_result", TS), "name": "Bash", "status": "completed",
         "callId": "c1", "output": {"type": "text", "text": output}},
    ])

    material = workbuddy_ai.parse(ref(path))

    assert material.meta["error_count"] == 0
    assert "[error]" not in material.text
    assert material.text == "跑一下"


def test_parse_does_not_count_a_result_without_an_exit_code(sessions_dir):
    """实测 60 条后台启动 + 47 条块列表输出没有尾注——没有证据就不算失败。"""
    path = write_session(sessions_dir, "a.jsonl", [
        background_result(TS), list_output_result(TS_LATER),
    ])

    material = workbuddy_ai.parse(ref(path))

    assert material.meta["error_count"] == 0
    assert material.text == ""


def test_parse_counts_consecutive_failures_as_struggle(sessions_dir):
    path = write_session(sessions_dir, "a.jsonl", [
        tool_result("boom", TS, exit_code=1),
        assistant("再试一次", TS),
        tool_result("boom again", TS, exit_code=1),
    ])

    assert workbuddy_ai.parse(ref(path)).meta["struggle_rounds"] == 2


def test_parse_clean_tool_result_ends_a_struggle_run(sessions_dir):
    path = write_session(sessions_dir, "a.jsonl", [
        tool_result("boom", TS, exit_code=1),
        tool_result("boom", TS, exit_code=1),
        tool_result("ok", TS, exit_code=0),
        tool_result("boom", TS, exit_code=1),
    ])

    material = workbuddy_ai.parse(ref(path))

    assert material.meta["struggle_rounds"] == 2
    assert material.meta["error_count"] == 3


def test_parse_counts_a_failure_that_produced_no_output(sessions_dir):
    """尾注说失败就是失败，哪怕 stdout 是空的（与 codex 的判据相反：
    codex 要从正文里*找*信号，没正文就没证据；这里的尾注是显式给出的）。"""
    path = write_session(sessions_dir, "a.jsonl",
                         [tool_result("", TS, exit_code=1)])

    material = workbuddy_ai.parse(ref(path))

    assert material.meta["error_count"] == 1
    assert material.meta["struggle_rounds"] == 1


# --- meta ---


def test_parse_carries_cwd_and_session_id_without_inventing_fields(sessions_dir):
    """实测源里**没有** `gitBranch` 键（qoder 系才有）。

    所以这里不塞 `git_branch`：一个恒为 None 的键等于断言"该源有这个字段但
    是空的"，而事实是这个源根本没有它。
    """
    path = write_session(sessions_dir, f"{SESSION_ID}.jsonl",
                         [user_query("提问", TS)])

    meta = workbuddy_ai.parse(ref(path)).meta

    assert meta["cwd"] == DEMO_CWD
    assert meta["session_id"] == SESSION_ID
    assert "git_branch" not in meta


# --- registry ---


def test_registered_in_registry():
    from src.plugins import iter_plugins

    assert "workbuddy_ai" in {p.name for p in iter_plugins()}
