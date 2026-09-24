"""kimi_code 插件：对着 **真实** wire.jsonl 形态的单测（T022 重写）。

为什么重写：这个文件原来那批夹具（flat ``{timestamp, role, content}``、
``{type: user_message}``）是 research.md 阶段照别家产品**猜**出来的，本机真实
数据一直就在 ``~/.kimi-code/sessions`` 里。对着 39206 个真实事件跑原实现：
``_classify`` 认出 **0 条**，时间戳是 int 毫秒所以 ``_event_ts`` 也全部返回
None —— 采集源静默空转，discover 对每一天都返回 0 refs。测试全绿，因为它们
测的是猜出来的形状。

真实形态（2026-09-24 本机实测，见 test_real_sessions_are_recognised 的冒烟）::

    {"type": "turn.prompt",              "input": [{"type":"text","text":…}],
                                          "origin": {"kind": "user"|"task"}, "time": <int ms>}
    {"type": "agent.message.appended",   "message": {"message": {"role": "assistant",
                                          "content": [{"type":"text"|"think","text":…}]}, "meta": {}}, "time": …}
    {"type": "context.append_loop_event","event": {"type": "tool.result",
                                          "result": {"output": …, "isError": true}}, "time": …}

state.json 的 ``createdAt`` 同样是 int 毫秒。
"""

import json
from datetime import date, datetime, timedelta, timezone

import pytest

from src.plugins import SourceRef, kimi_code

DAY = date(2026, 9, 18)


def _ms(iso: str) -> int:
    """夹具按 ISO 写更好读，这里换成 kimi 真正用的 int 毫秒。

    毫秒这个单位不靠这里自证：test_timestamps_are_int_epoch_milliseconds 拿
    真实数据里的原值钉死了。
    """
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000)


# 2026-09-17T16:30Z = 上海 2026-09-18 00:30 —— UTC 日期是前一天，归属日必须是本地日
TS_MS = _ms("2026-09-17T16:30:00.000Z")
OLD_TS_MS = _ms("2026-09-10T02:00:00.000Z")  # 上海 2026-09-10


@pytest.fixture
def sessions_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(kimi_code, "SESSIONS_DIR", tmp_path)
    return tmp_path


def write_session(sessions_dir, ws: str, sid: str, events, state=None):
    d = sessions_dir / ws / sid
    (d / "agents" / "main").mkdir(parents=True, exist_ok=True)
    state = {"cwd": "/work/proj", "title": "fix bug",
             "createdAt": TS_MS} | (state or {})
    (d / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (d / "agents" / "main" / "wire.jsonl").write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n",
        encoding="utf-8")
    return d


def prompt(ms, text, origin="user"):
    return {"type": "turn.prompt", "agentId": "main", "time": ms,
            "origin": {"kind": origin},
            "input": [{"type": "text", "text": text}]}


def assistant(ms, text, block="text"):
    return {"type": "agent.message.appended", "kind": "event", "time": ms,
            "message": {"message": {"role": "assistant", "content": [
                {"type": block, "text": text}]}, "meta": {}}}


def tool_result(ms, output, is_error=False):
    event = {"type": "tool.result", "step": 1, "toolCallId": "call_1",
             "result": {"output": output}}
    if is_error:
        event["result"]["isError"] = True
    return {"type": "context.append_loop_event", "agentId": "main",
            "time": ms, "event": event}


def test_timestamps_are_int_epoch_milliseconds():
    """kimi 的 `time` 是 int 毫秒，不是 ISO 串 —— 这是单位契约，钉死。

    只测"能解析出个时间"分不开秒/毫秒/微秒，所以用本机真实数据里的一行原值
    对上算好的上海时刻（1789111903551 → 2026-09-11 15:31:43+08:00）。
    """
    ts = kimi_code._event_ts({"time": 1789111903551})

    assert ts == datetime(2026, 9, 11, 15, 31, 43, 551000,
                          tzinfo=timezone(timedelta(hours=8)))


def test_discover_uses_the_real_event_shape(sessions_dir):
    """归属日按上海时区判：UTC 9-17 的事件属于本地 9-18。"""
    write_session(sessions_dir, "wd_a", "session_today",
                  [prompt(TS_MS, "今天的话")])
    write_session(sessions_dir, "wd_a", "session_old",
                  [prompt(OLD_TS_MS, "上周的话")])

    refs = kimi_code.discover(DAY)

    assert [r.ref.split("/")[-1] for r in refs] == ["session_today"]
    assert refs[0].source == "kimi_code"


def test_parse_extracts_user_turns_and_assistant_text(sessions_dir):
    d = write_session(sessions_dir, "wd_a", "session_1", [
        prompt(TS_MS, "用户问的是这个"),
        assistant(TS_MS, "助手答的是这个"),
    ])

    mat = kimi_code.parse(SourceRef(source="kimi_code", ref=str(d), day=DAY))

    assert "用户问的是这个" in mat.text
    assert "助手答的是这个" in mat.text
    assert mat.meta["cwd"] == "/work/proj"
    assert mat.meta["title"] == "fix bug"
    assert mat.meta["session_id"] == "session_1"
    assert mat.ts.date() == DAY and mat.ts.tzinfo is not None


def test_parse_drops_reasoning_blocks(sessions_dir):
    """`{"type":"think"}` 是模型的思维链，不进素材。

    它体量最大（真实数据里 think 块比 text 块还多），跟着进转写既泄露不该外流的
    内容，又会把 MAX_SESSION_CHARS 的额度吃掉。
    """
    d = write_session(sessions_dir, "wd_a", "session_1", [
        assistant(TS_MS, "这段思维链不该入库", block="think"),
        assistant(TS_MS, "这段才是要入库的"),
    ])

    mat = kimi_code.parse(SourceRef(source="kimi_code", ref=str(d), day=DAY))

    assert "这段思维链不该入库" not in mat.text
    assert "这段才是要入库的" in mat.text


def test_subagent_notification_is_not_a_human_turn(sessions_dir):
    """`origin.kind == "task"` 是子代理回报，不是人重新开口。

    真实数据里 task 比 user 还多；把它当人说话，struggle 会被子代理噪声随意打断。
    """
    d = write_session(sessions_dir, "wd_a", "session_1", [
        tool_result(TS_MS, "boom 1", is_error=True),
        prompt(TS_MS, "子代理：搞定了", origin="task"),
        tool_result(TS_MS, "boom 2", is_error=True),
    ])

    mat = kimi_code.parse(SourceRef(source="kimi_code", ref=str(d), day=DAY))

    assert mat.meta["error_count"] == 2
    assert mat.meta["struggle_rounds"] == 2
    assert "子代理" not in mat.text


def test_parse_collects_tool_errors_from_is_error_flag(sessions_dir):
    d = write_session(sessions_dir, "wd_a", "session_1", [
        tool_result(TS_MS, "Command failed with exit code 1", is_error=True),
    ])

    mat = kimi_code.parse(SourceRef(source="kimi_code", ref=str(d), day=DAY))

    assert mat.meta["error_count"] == 1
    assert "[error] Command failed with exit code 1" in mat.text


def test_clean_tool_result_ends_a_struggle_run(sessions_dir):
    """失败连击只在**工具真成功**时归零；助手叙述和人的下一句都不算。

    与 claude_code / codex 同一条语义（FR-008）。kimi 原来用「人重新发一句话」
    当结束信号，理由写的是"wire 里没有 tool 结果事件"——本机实测证伪：
    `context.append_loop_event` 里 3669 条 tool.result，其中 108 条带
    `isError: true`。既然有真成功信号，就不该再用更弱的人为代理。
    """
    d = write_session(sessions_dir, "wd_a", "session_1", [
        tool_result(TS_MS, "第一次失败", is_error=True),
        assistant(TS_MS, "我换个写法"),          # 叙述：不打断
        tool_result(TS_MS, "第二次失败", is_error=True),
        prompt(TS_MS, "再试试"),                 # 人开口：不打断
        tool_result(TS_MS, "第三次失败", is_error=True),
        tool_result(TS_MS, "正常输出"),          # 真成功：归零
        tool_result(TS_MS, "隔开的第四次失败", is_error=True),
    ])

    mat = kimi_code.parse(SourceRef(source="kimi_code", ref=str(d), day=DAY))

    assert mat.meta["error_count"] == 4
    assert mat.meta["struggle_rounds"] == 3


def test_parse_skips_other_days(sessions_dir):
    d = write_session(sessions_dir, "wd_a", "session_1", [
        prompt(OLD_TS_MS, "别的地方的话"),
        prompt(TS_MS, "今天的话"),
    ])

    mat = kimi_code.parse(SourceRef(source="kimi_code", ref=str(d), day=DAY))

    assert "别的地方的话" not in mat.text
    assert "今天的话" in mat.text


def test_parse_falls_back_when_state_json_missing(sessions_dir):
    """没有 state.json 也要出素材：createdAt 只是 ts 的兜底来源之一。"""
    d = write_session(sessions_dir, "wd_a", "session_1",
                      [prompt(TS_MS, "只有 wire 有内容")])
    (d / "state.json").unlink()

    mat = kimi_code.parse(SourceRef(source="kimi_code", ref=str(d), day=DAY))

    assert "只有 wire 有内容" in mat.text
    assert mat.meta["cwd"] is None
    assert mat.ts.date() == DAY


def test_registered_in_registry():
    from src.plugins import iter_plugins
    assert "kimi_code" in [p.name for p in iter_plugins()]


REAL_WIRES = any(kimi_code.SESSIONS_DIR.glob(
    "wd_*/session_*/agents/main/wire.jsonl"))


@pytest.mark.skipif(not REAL_WIRES,
                    reason="本机没有 kimi-code 真实数据（CI 上正常跳过）")
def test_real_sessions_are_recognised():
    """对着**真实** wire.jsonl 的格式漂移冒烟。

    这条就是原来缺的那道闸：夹具猜错形态时它不会响，采集源于是静默空转。
    断言写成"真实事件里至少认得出一些"，不绑具体日期，免得隔天数据变了就红。
    """
    recognised = 0
    seen = 0
    for wire in sorted(kimi_code.SESSIONS_DIR.glob(
            "wd_*/session_*/agents/main/wire.jsonl")):
        for line in wire.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            seen += 1
            if kimi_code._classify(event)[0]:
                recognised += 1

    assert seen > 1000, f"真实数据只有 {seen} 行，冒烟没有意义"
    assert recognised > 0, (
        f"{seen} 个真实事件里一条都没认出 —— wire 形态又变了，采集源正在空转")
