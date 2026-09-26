"""T053a: 同源插件对的等价性测试 —— "各自实现"这个选择的唯一代价补偿。

`qoder`/`qoder_cn` 与 `trae`/`trae_work_cn` 两组 schema 相同，但按契约规则 6
不得互相 import，于是各写一份解析器。没有这个文件，两份实现漂移时不会有任何
东西响：改了一边的过滤条件、另一边照旧，单测各自全绿，而两条源的素材在快照里
悄悄长得不一样。

**喂同样的字节**：同一份 fixture 写进两个插件的目录（parse 直接比同一个文件
路径的引用，连"内容是否真相同"都不必赌），除 `source` 外逐字段断言相同。

**不是空对空**：如果两份实现都返回空素材，逐字段比较会"通过"而什么都没证明。
所以先钉住 fixture 确实产出了可观的文本与 struggle 计数，再比等价——这条是
变异测试教出来的：一个恒真的断言比没有断言更坏。

trae 那一组的 fixture 刻意走**真实数据里没走过的分支**（ISO 时间戳兜底、
时间戳缺失落零点、条目级空白过滤）：实测 `~/.trae` 与 `~/.trae-cn` 的记录
全部是 `%Y-%m-%d %H:%M:%S`，这些分支在真实数据上永不触发——正因为不触发，
漂移了也不会有人发现，所以由这条测试来盯。
"""

import json
from datetime import date, datetime, timedelta, timezone

import pytest

from src.plugins import SourceRef, qoder, qoder_cn, trae, trae_work_cn

DAY = date(2026, 9, 18)
TZ = timezone(timedelta(hours=8))
DEMO_CWD = "/Users/nava/Code/demo"
SESSION_ID = "fc625b6b-2514-4f07-b70b-4d15d5fa743e"
ISO = "2026-09-17T16:30:00.000Z"       # 上海 2026-09-18 00:30
ISO_LATER = "2026-09-17T20:15:00.000Z"  # 上海 2026-09-18 04:15
OLD_ISO = "2026-09-10T02:00:00.000Z"   # 别的日子
MS = 1789662600000
OLD_MS = 1789005600000


def _base(record_type: str, ts) -> dict:
    return {
        "type": record_type, "timestamp": ts, "cwd": DEMO_CWD,
        "sessionId": SESSION_ID, "gitBranch": "main", "isSidechain": False,
    }


def human(text: str, ts=ISO) -> dict:
    return {**_base("user", ts), "humanInput": True, "origin": {"kind": "human"},
            "message": {"role": "user", "content": [{"type": "text", "text": text}]}}


def assistant(text: str, ts=ISO, thinking: str | None = None) -> dict:
    content: list[dict] = []
    if thinking:
        content.append({"type": "thinking", "thinking": thinking, "signature": "s"})
    content.append({"type": "tool_use", "id": "call_1", "name": "Bash", "input": {}})
    content.append({"type": "text", "text": text})
    return {**_base("assistant", ts), "message": {"role": "assistant", "content": content}}


def tool_result(text: str, ts=ISO, exit_code=None, is_error=None) -> dict:
    tur: dict = {"exitCode": exit_code, "kind": "bash", "stdout": text, "stderr": ""}
    if is_error is not None:
        tur["isError"] = is_error
    return {**_base("user", ts), "toolUseResult": tur,
            "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "call_1", "content": text}]}}


def _injected(kind: str, text: str, ts=ISO) -> dict:
    """user 角色但**不是**用户输入：这一族是两份实现最容易漂移的地方。"""
    return {**_base("user", ts), "isMeta": True, "origin": {"kind": kind},
            "message": {"role": "user", "content": [{"type": "text", "text": text}]}}


def _fixture_records() -> list[dict]:
    """把所有分支走一遍：取什么、丢什么、双编码时间戳、失败连击。"""
    return [
        {"type": "active-leaf", "timestamp": MS, "sessionId": SESSION_ID},
        human("为什么 upsert 会 409"),
        assistant("因为点 id 撞了", thinking="先怀疑是网络，再怀疑是 id"),
        tool_result("ok", exit_code=0),
        human("那怎么改"),
        assistant("用 deterministic uuid5"),
        tool_result("Exit code 1\nFAILED test_ingest.py::test_upsert",
                    exit_code=1, is_error=True),
        assistant("再试一次"),
        tool_result("Exit code 1\nFAILED again", exit_code=1),
        tool_result("ok", exit_code=0),
        _injected("task-notification", "任务已完成"),
        _injected("task-notification", "还有一件事"),
        {**_base("user", ISO), "isCompactSummary": True,
         "message": {"role": "user", "content": [
             {"type": "text", "text": "This session is being continued…"}]}},
        {"type": "file-history-snapshot", "sessionId": SESSION_ID},
        {"type": "last-prompt", "sessionId": SESSION_ID},
        human("这是别的日子的", OLD_ISO),
        {"type": "active-leaf", "timestamp": OLD_MS, "sessionId": SESSION_ID},
        human("最后那句", ISO_LATER),
        {**_base("user", ISO), "toolUseResult": "legacy string payload",
         "message": {"role": "user", "content": [{"type": "text", "text": "ignored"}]}},
    ]


def _write(directory, records) -> "object":
    d = directory / "-Users-nava-Code-demo"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{SESSION_ID}.jsonl"
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
        encoding="utf-8")
    return path


@pytest.fixture
def both_projects_dirs(tmp_path, monkeypatch):
    """两个插件各自指向自己的 tmp 目录，写着同样的字节。"""
    qoder_dir = tmp_path / "qoder"
    qoder_cn_dir = tmp_path / "qoder_cn"
    records = _fixture_records()
    qoder_path = _write(qoder_dir, records)
    qoder_cn_path = _write(qoder_cn_dir, records)
    monkeypatch.setattr(qoder, "PROJECTS_DIR", qoder_dir)
    monkeypatch.setattr(qoder_cn, "PROJECTS_DIR", qoder_cn_dir)
    assert qoder_path.read_bytes() == qoder_cn_path.read_bytes()  # 同一个输入
    return qoder_path, qoder_cn_path


def _shape(ref: SourceRef) -> tuple:
    from pathlib import Path
    return (Path(ref.ref).name, ref.day)


def test_qoder_pair_discovers_the_same_sessions(both_projects_dirs):
    """发现口径漂移最要紧：它决定某天到底有没有这条源。"""
    (qoder_path, _qoder_cn_path) = both_projects_dirs

    qoder_refs = qoder.discover(DAY)
    qoder_cn_refs = qoder_cn.discover(DAY)

    assert [r.ref for r in qoder_refs] == [str(qoder_path)]
    assert len(qoder_cn_refs) == len(qoder_refs)
    assert ([_shape(r) for r in qoder_refs]
            == [_shape(r) for r in qoder_cn_refs])


def test_qoder_pair_agrees_on_an_injected_only_session(tmp_path, monkeypatch):
    """上面那条**看不见**过滤口径的漂移：只要文件里有人说的话，两边都会说
    "这天有素材"，哪怕一边把注入物也算成了正文（实测过：把 qoder_cn 的
    human 正标记去掉，"取什么"那条会红，"发现"那条依旧绿）。

    所以补这条：整条会话只有注入物 —— 两边都该认为它不构成素材。这一条才是
    真正盯着 `_file_hits_day` 里那个 `_material_text(record)` 判据的。
    """
    records = [_injected("task-notification", "任务已完成"), _injected("task-notification", "还有一件事"),
               {**_base("user", ISO), "isCompactSummary": True,
                "message": {"role": "user", "content": [
                    {"type": "text", "text": "This session is being continued…"}]}},
               {"type": "active-leaf", "timestamp": MS, "sessionId": SESSION_ID}]
    qoder_dir, qoder_cn_dir = tmp_path / "qoder", tmp_path / "qoder_cn"
    _write(qoder_dir, records)
    _write(qoder_cn_dir, records)
    monkeypatch.setattr(qoder, "PROJECTS_DIR", qoder_dir)
    monkeypatch.setattr(qoder_cn, "PROJECTS_DIR", qoder_cn_dir)

    assert qoder.discover(DAY) == []
    assert qoder_cn.discover(DAY) == []


def test_qoder_pair_parses_to_the_same_material(both_projects_dirs):
    (qoder_path, qoder_cn_path) = both_projects_dirs

    a = qoder.parse(SourceRef(source="qoder", ref=str(qoder_path), day=DAY))
    b = qoder_cn.parse(SourceRef(source="qoder_cn", ref=str(qoder_cn_path), day=DAY))

    # 先证明这条 fixture 不是空对空：断言的是**共享**的那部分有实在内容
    assert "[error]" in a.text
    assert "为什么 upsert 会 409" in a.text
    assert "最后那句" in a.text
    assert "任务已完成" not in a.text          # 注入物没进来
    assert "This session is being continued" not in a.text
    assert "这是别的日子的" not in a.text
    assert "先怀疑是网络" not in a.text        # thinking 没进来
    assert a.meta["struggle_rounds"] == 2
    assert a.meta["error_count"] == 2

    assert a.source == "qoder"
    assert b.source == "qoder_cn"
    assert (a.ts, a.kind, a.text, a.meta) == (b.ts, b.kind, b.text, b.meta)


# ---------------------------------------------------------------------------
# trae / trae_work_cn —— pre-summarized 轻转换路径（一条记录一条素材）
# ---------------------------------------------------------------------------

LEARNED_TEXT = "项目是一个基于 RAG 的个人学习记忆系统"
OUTCOME_TEXT = "清晰呈现了已完成和待办项清单"
INTENT_TEXT = "了解项目现状并确定下一步开发方向"
DIGEST = "940b8822deea819bcb08ff6cc784d404"
PROJECT = "-Users-nava-Code-llm-rag-first-rag--p2-a36559cd5ee996ba2f18"
TRAE_DAY = date(2026, 9, 18)
TRAE_OTHER_DAY = date(2026, 9, 17)


def trae_record(ts="2026-09-18 09:17:31", learned=None, outcome=None,
                with_meta=True) -> dict:
    r = {
        "intent": INTENT_TEXT,
        "actions": ["分析 tasks.md 梳理开发进度"],
        "outcome": OUTCOME_TEXT if outcome is None else outcome,
        "learned": [LEARNED_TEXT] if learned is None else learned,
        "message_summary_time": ts,
        "message_id": "6aaf33f0542a496b86a19d81",
    }
    if with_meta:
        r["compact_summary_meta"] = {
            "trigger": "auto", "mode": "async",
            "server_history_id": "6aaf33f0542a496b86a19d81",
            "summary_digest": DIGEST, "created_at_ms": 1789867051573,
        }
    return r


def _trae_lines() -> list[str]:
    """按真实形状造文件，**原始行**返回——好插入空行与坏行，验证行号忠实。"""
    return [
        json.dumps(trae_record(), ensure_ascii=False),                     # L0
        "",                                                                # L1 空行
        "{这一行不是 json",                                                 # L2 坏行
        json.dumps(trae_record(learned=[], outcome="Shipped the fix."),
                   ensure_ascii=False),                                    # L3 learned 空
        json.dumps(trae_record(learned=["只有 learned"], outcome=""),
                   ensure_ascii=False),                                    # L4 outcome 空
        json.dumps(trae_record(ts="2026-09-18T09:17:31"), ensure_ascii=False),  # L5 ISO
        json.dumps(trae_record(ts="not-a-timestamp"), ensure_ascii=False),  # L6 读不出
        json.dumps(trae_record(learned=["  ", "留白的条目被丢"]),
                   ensure_ascii=False),                                    # L7 条目级空白
    ]


def _write_trae(memory_dir, day: date, lines: list[str]):
    """写到 `projects/<slug>/<YYYYMMDD>/session_memory_*.jsonl`（真实目录形状）。"""
    d = memory_dir / "projects" / PROJECT / day.strftime("%Y%m%d")
    d.mkdir(parents=True, exist_ok=True)
    # 同级与上级兄弟文件：真实存在，两边都不许碰它们
    (d / "topics.md").write_text("## 话题\n- 项目现状\n", encoding="utf-8")
    (memory_dir / "projects" / PROJECT / "project_memory.md").write_text(
        "## 项目记忆\n", encoding="utf-8")
    path = d / "session_memory_6aaf33f0542a496b86a19d80.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def both_memory_dirs(tmp_path, monkeypatch):
    """两个插件各自指向自己的 tmp 目录，写着同样的字节。"""
    work_cn_dir, trae_dir = tmp_path / "trae_work_cn", tmp_path / "trae"
    lines = _trae_lines()
    work_cn_path = _write_trae(work_cn_dir, TRAE_DAY, lines)
    trae_path = _write_trae(trae_dir, TRAE_DAY, lines)
    _write_trae(work_cn_dir, TRAE_OTHER_DAY, [json.dumps(trae_record(
        ts="2026-09-17 08:00:00"), ensure_ascii=False)])
    _write_trae(trae_dir, TRAE_OTHER_DAY, [json.dumps(trae_record(
        ts="2026-09-17 08:00:00"), ensure_ascii=False)])
    monkeypatch.setattr(trae_work_cn, "MEMORY_DIR", work_cn_dir)
    monkeypatch.setattr(trae, "MEMORY_DIR", trae_dir)
    assert work_cn_path.read_bytes() == trae_path.read_bytes()  # 同一个输入
    return work_cn_path, trae_path


def _record_shape(ref: SourceRef) -> tuple:
    from pathlib import Path
    path, _, marker = ref.ref.partition("#L")
    return (Path(path).name, marker, ref.day)


def test_trae_pair_discovers_the_same_records(both_memory_dirs):
    """发现口径漂移最要紧：它决定某天到底有没有这条源。

    一处**行号**漂移就够红：空行与坏行混在里面，两边的 `#L<n>` 必须一致
    （一边按"有效记录序号"编号、另一边按"文件行号"编号，就会错位）。
    """
    (work_cn_path, _trae_path) = both_memory_dirs

    a = trae_work_cn.discover(TRAE_DAY)
    b = trae.discover(TRAE_DAY)

    assert [r.ref for r in a] == [f"{work_cn_path}#L{i}" for i in (0, 3, 4, 5, 6, 7)]
    assert len(b) == len(a)
    assert a[0].source == "trae_work_cn"
    assert b[0].source == "trae"
    assert [_record_shape(r) for r in a] == [_record_shape(r) for r in b]
    assert all(r.day == TRAE_DAY for r in a + b)


def test_trae_pair_agrees_on_a_day_without_record_files(tmp_path, monkeypatch):
    """那天只有兄弟 markdown、没有 session_memory_*.jsonl —— 两边都该说"没有"。"""
    work_cn_dir, trae_dir = tmp_path / "trae_work_cn", tmp_path / "trae"
    for root in (work_cn_dir, trae_dir):
        d = root / "projects" / PROJECT / TRAE_DAY.strftime("%Y%m%d")
        d.mkdir(parents=True)
        (d / "topics.md").write_text("## 话题\n", encoding="utf-8")
    monkeypatch.setattr(trae_work_cn, "MEMORY_DIR", work_cn_dir)
    monkeypatch.setattr(trae, "MEMORY_DIR", trae_dir)

    assert trae_work_cn.discover(TRAE_DAY) == []
    assert trae.discover(TRAE_DAY) == []


def test_trae_pair_parses_to_the_same_material(both_memory_dirs):
    (work_cn_path, trae_path) = both_memory_dirs
    refs_a = trae_work_cn.discover(TRAE_DAY)
    refs_b = trae.discover(TRAE_DAY)
    assert len(refs_a) == len(refs_b) == 6  # 空行与坏行都不产生素材

    a = trae_work_cn.parse(refs_a[0])
    b = trae.parse(refs_b[0])

    # 先证明这条 fixture 不是空对空
    assert LEARNED_TEXT in a.text
    assert OUTCOME_TEXT in a.text
    assert INTENT_TEXT not in a.text              # intent 不进正文
    assert "分析 tasks.md 梳理开发进度" not in a.text  # actions 也不进
    assert DIGEST not in a.text                   # 机器字段不进来
    assert "1789867051573" not in a.text
    assert a.kind == "trae_record"
    assert a.meta["note_type"] == "reflection"    # learned 是 trae_type_map 的首键
    assert a.meta["project"] is None
    assert a.ts.astimezone(TZ) == datetime(2026, 9, 18, 9, 17, 31, tzinfo=TZ)

    # 真实数据里走不到的分支，逐条比：漂移了也不会有别的测试响
    assert trae.parse(refs_b[1]).meta["note_type"] == "progress"  # learned 空
    assert trae.parse(refs_b[2]).meta["note_type"] == "reflection"  # outcome 空
    assert trae.parse(refs_b[3]).ts.astimezone(TZ) == \
        datetime(2026, 9, 18, 9, 17, 31, tzinfo=TZ)  # ISO 兜底
    assert trae.parse(refs_b[4]).ts == \
        datetime.combine(TRAE_DAY, datetime.min.time(), tzinfo=TZ)  # 读不出 → 零点
    assert "留白的条目被丢" in trae.parse(refs_b[5]).text
    assert "- \n" not in trae.parse(refs_b[5]).text  # 空白条目没有变成空 bullet

    assert a.source == "trae_work_cn"
    assert b.source == "trae"
    assert (a.ts, a.kind, a.text, a.meta) == (b.ts, b.kind, b.text, b.meta)
    for ref_a, ref_b in list(zip(refs_a, refs_b))[1:]:
        x, y = trae_work_cn.parse(ref_a), trae.parse(ref_b)
        assert (x.ts, x.kind, x.text, x.meta) == (y.ts, y.kind, y.text, y.meta)
