"""T026 unit tests for src/ask.py (filtered search + cited answers)."""

import json
import sys
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src import ask, ask_expand, config, similarity
from src.similarity import Hit

SCHEMA = {
    "types": [{"name": "progress", "desc": "d"}, {"name": "error", "desc": "d"}],
    "distill": {},
    "retrieval": {
        "top_k": 8,
        "answer_max_tokens": 600,
        "disable_thinking": True,
        "expand": {
            "mode": "all",
            "neighbor_limit_per_hit": 2,
            "context_cap": 12,
            "neighbor_min_score": None,
        },
    },
    "trae_type_map": {},
    "raw_retention_days": 90,
}


@pytest.fixture
def schema(monkeypatch):
    monkeypatch.setattr(ask.config, "load_schema", lambda: SCHEMA)


# --- date/filter parsing ---


def test_parse_relative_and_absolute_dates():
    today = date(2026, 9, 20)
    assert ask._parse_date("7d", today=today) == date(2026, 9, 13)
    assert ask._parse_date("2026-09-01", today=today) == date(2026, 9, 1)
    assert ask._parse_date(None, today=today) is None


def test_relative_dates_follow_the_configured_timezone(monkeypatch):
    """相对日期按 config.TZ 算，不是按跑批机器的本地时区。

    换成 `datetime.now(tz=None)` 会用系统本地时区：机器在容器里（UTC）或
    cron 环境里时区不一致时，"最近 7 天"的边界会整体错开一天 —— 不报错，
    只是安静地少给/多给一天。
    """
    far_east = timezone(timedelta(hours=14))
    far_west = timezone(timedelta(hours=-12))
    monkeypatch.setattr(ask.config, "TZ", far_east)
    at_east = ask._parse_date("0d")
    monkeypatch.setattr(ask.config, "TZ", far_west)
    at_west = ask._parse_date("0d")

    # 两个时区相隔 26 小时，任何时刻它们的"今天"都不可能是同一天。
    assert at_east != at_west
    assert at_east == datetime.now(tz=far_east).date()


def test_build_filters_threads_today_into_relative_dates():
    """build_filters 必须把 today 传给 _parse_date，否则相对日期按"现在"算。

    `--since 7d` 的语义是"相对今天"，不是"相对跑测试的那天"。
    """
    filters = ask.build_filters(
        since="7d", until="1d", today=date(2026, 9, 20))
    date_range = {c.key: c for c in filters.must}["date"].range
    assert date_range.gte.date() == date(2026, 9, 13)
    assert date_range.lte.date() == date(2026, 9, 19)


def test_build_filters_type_project_and_range():
    filters = ask.build_filters(
        type="error", project="first-rag",
        since="2026-09-01", until="7d", today=date(2026, 9, 20))
    conditions = {c.key: c for c in filters.must}
    assert conditions["type"].match.value == "error"
    assert conditions["project"].match.value == "first-rag"
    assert conditions["date"].range.gte.date() == date(2026, 9, 1)
    assert conditions["date"].range.lte.date() == date(2026, 9, 13)


def test_build_filters_none_when_no_filters():
    assert ask.build_filters() is None


# --- _cosine：邻居打分的核心数学 ---
# 这一组是变异测试逼出来的：ask.py 纳入 only_mutate 后，_cosine 的三个变异体
# （or→and、0.0→1.0、dot/(na*nb)→dot*(na*nb)）全部存活 —— 说明整个关联扩展
# 的打分逻辑此前一行断言都没有，只有"跑通了"这种程度覆盖。


def test_cosine_identical_orthogonal_and_diagonal():
    assert ask_expand._cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert ask_expand._cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert ask_expand._cosine([1.0, 1.0], [1.0, 0.0]) == pytest.approx(0.7071, abs=1e-4)
    # 两个向量的模都不为 1：只有这样才能区分 dot/(na*nb) 与 dot/(na/nb)
    # —— 取单位向量时两者结果相同，变异体就溜过去了。
    assert ask_expand._cosine([3.0, 4.0], [4.0, 3.0]) == pytest.approx(24 / 25)


def test_cosine_zero_vector_scores_zero_not_one():
    """零向量与任何向量都是 0.0，不能是 1.0。

    返回 1.0 会让空向量被当成"完全相关"，把一堆无关邻居塞进上下文；而判空
    用的是 `or`（任一方为零向量即退化），不是 `and`。
    """
    assert ask_expand._cosine([0.0, 0.0], [1.0, 2.0]) == 0.0
    assert ask_expand._cosine([1.0, 2.0], [0.0, 0.0]) == 0.0


# --- payload 缺字段时引用的默认值 ---
# 缺了就渲染成 "None" 一样的东西，比空串难看得多，也说明上游 payload 有问题。


def test_citations_default_missing_payload_keys_to_empty():
    citation = ask_expand.citations_from_hits([hit({})])[0]
    assert citation.date == ""
    assert citation.type == ""
    assert citation.text == ""
    assert citation.source == ""
    assert citation.source_refs == []


def test_citation_from_record_defaults_missing_payload_keys():
    record = SimpleNamespace(id="n1", vector=[1.0, 0.0], payload={})
    citation = ask_expand._citation_from_record(record, 0.5)
    assert (citation.date, citation.type, citation.text) == ("", "", "")
    assert (citation.source, citation.source_refs) == ("", [])


def test_citations_passes_through_score_and_source_refs():
    """payload 齐全时字段要原样透传 —— 上面那条只测了"缺字段"。

    `.get("source_refs", ...)` 被改成 `.get(None, ...)` 时，缺字段的表现一样
    （都返回默认值），只有字段真的存在才看得出区别：引用会安静地丢掉来源，
    而引用正是这个功能唯一的可追溯性保证（FR-019）。
    """
    citation = ask_expand.citations_from_hits([hit(
        {"date": "2026-09-18", "type": "error", "text": "boom",
         "source": "claude_code", "source_refs": ["s1", "s2"]},
        score=0.42)])[0]
    assert citation.score == pytest.approx(0.42)
    assert citation.source == "claude_code"
    assert citation.source_refs == ["s1", "s2"]


def test_citation_from_record_passes_through_score_and_source_refs():
    record = SimpleNamespace(
        id="n1", vector=[1.0, 0.0],
        payload={"date": "2026-09-18", "type": "error", "text": "boom",
                 "source": "codex", "source_refs": ["r1"]})
    citation = ask_expand._citation_from_record(record, 0.75)
    assert citation.score == pytest.approx(0.75)
    assert citation.source == "codex"
    assert citation.source_refs == ["r1"]


# --- query() ---


def hit(payload: dict, score: float = 0.9, id: str = "id-1") -> Hit:
    return Hit(id=id, score=score, payload=payload)


@pytest.fixture
def pipeline_fakes(monkeypatch):
    captured = {}

    def fake_embed(texts):
        captured["question_embedded"] = texts[0]
        return [[0.1, 0.2, 0.3]]

    def fake_search(vector, k=5, filters=None, **kwargs):
        captured["k"] = k
        captured["filters"] = filters
        return [
            hit({"text": "Qdrant upsert 409 root cause", "date": "2026-09-18",
                 "type": "error", "source": "claude_code",
                 "source_refs": ["s1"], "related": []}),
            hit({"text": "Chose uuid5 for idempotency", "date": "2026-09-19",
                 "type": "progress", "source": "codex",
                 "source_refs": ["r1"], "related": []}, id="id-2"),
        ]

    def fake_chat(messages, json_mode=False, max_tokens=None, thinking=True):
        captured["messages"] = messages
        captured["max_tokens"] = max_tokens
        captured["thinking"] = thinking
        return "409 的根因是维度不匹配 [2026-09-18]。"

    monkeypatch.setattr(ask, "embed", fake_embed)
    monkeypatch.setattr(ask.similarity, "search", fake_search)
    monkeypatch.setattr(ask, "chat", fake_chat)
    return captured


def test_query_answers_with_citations(pipeline_fakes, schema):
    answer = ask.query("我在 qdrant 上踩过什么坑？", type="error")

    assert pipeline_fakes["question_embedded"] == "我在 qdrant 上踩过什么坑？"
    assert pipeline_fakes["k"] == 8  # schema retrieval.top_k
    assert pipeline_fakes["filters"] is not None
    user_content = pipeline_fakes["messages"][-1]["content"]
    assert "[2026-09-18] error:" in user_content  # 引用式上下文（FR-019）
    assert "我在 qdrant 上踩过什么坑？" in user_content
    assert answer.text.startswith("409 的根因")
    assert answer.question == "我在 qdrant 上踩过什么坑？"
    assert [c.type for c in answer.citations] == ["error", "progress"]
    assert answer.citations[0].date == "2026-09-18"
    assert answer.citations[0].source == "claude_code"


def test_query_forwards_every_filter_into_the_search(pipeline_fakes, schema):
    """四个过滤条件都要真的进 Qdrant 的 filter，少传一个就是静默放宽。"""
    ask.query("问题", type="error", project="first-rag",
              since="2026-09-01", until="2026-09-30")

    conditions = {c.key: c for c in pipeline_fakes["filters"].must}
    assert conditions["type"].match.value == "error"
    assert conditions["project"].match.value == "first-rag"
    assert conditions["date"].range.gte.date() == date(2026, 9, 1)
    assert conditions["date"].range.lte.date() == date(2026, 9, 30)


def test_query_sends_well_formed_chat_messages(pipeline_fakes, schema):
    """发给 LLM 的是 [{role, content}, …]：键名写错，整次调用会静默失效。"""
    ask.query("问题")

    messages = pipeline_fakes["messages"]
    assert [m["role"] for m in messages] == ["system", "user"]
    assert all("content" in m for m in messages)
    # 引用块是换行拼的："\n".join 换成别的分隔符，上下文就粘成一坨
    assert ("[2026-09-18] error: Qdrant upsert 409 root cause\n"
            "[2026-09-19] progress: Chose uuid5 for idempotency"
            in messages[-1]["content"])


def test_query_limits_answer_length(pipeline_fakes, schema):
    """回答的 chat 必须带上 answer_max_tokens，给答案长度一个确定上限。

    注意别把这条测成"限长就能压耗时"：2026-09-25 实测 max_tokens 拉到 30
    仍要 38s，瓶颈是模型首 token 延迟（TTFT ~20–40s），不是答案长度。
    限长的真实价值是**上界可预期**（不会突然吐一篇长文）。
    """
    ask.query("问题")

    assert pipeline_fakes["max_tokens"] == 600


def test_query_disables_thinking(pipeline_fakes, schema):
    """retrieval.disable_thinking=true 时，ask 必须把 thinking=False 传下去。

    这才是 ask 延迟的杠杆：同模型同日实测思考开着 33.4s、关掉 1.6–4.1s。
    默认值这里回读的是 False —— 若哪天有人把默认翻成 True，这条会红。
    """
    ask.query("问题")

    assert pipeline_fakes["thinking"] is False


def test_query_keeps_thinking_when_config_disables_the_switch(
    pipeline_fakes, schema, monkeypatch
):
    """配置关掉开关时必须传 thinking=True，而不是省略参数。

    省略会让 fake 的默认值兜底成 True，看起来也是绿的 —— 但真实调用里
    省略等于"不传 extra_body"，语义恰好也对，所以这条测的是**我们发出的
    意图**：配置怎么说，就怎么传。
    """
    monkeypatch.setitem(SCHEMA["retrieval"], "disable_thinking", False)

    ask.query("问题")

    assert pipeline_fakes["thinking"] is True


# --- query_stream() ---


@pytest.fixture
def stream_fakes(monkeypatch):
    """把 chat/chat_stream 都换成假的，分别记录各自收到的 messages 与参数。"""
    captured = {}

    monkeypatch.setattr(ask, "embed", lambda texts: [[0.1, 0.2, 0.3]])
    monkeypatch.setattr(ask.similarity, "search", lambda *a, **k: [
        hit({"text": "Qdrant upsert 409 root cause", "date": "2026-09-18",
             "type": "error", "source": "claude_code",
             "source_refs": ["s1"], "related": []})])

    def fake_chat_stream(messages, max_tokens=None, thinking=True):
        captured["stream_messages"] = messages
        captured["messages"] = messages
        captured["max_tokens"] = max_tokens
        captured["thinking"] = thinking
        return iter(["409 的根因", "是维度不匹配", " [2026-09-18]。"])

    def fake_chat(messages, json_mode=False, max_tokens=None, thinking=True):
        captured["sync_messages"] = messages
        return "409 的根因是维度不匹配 [2026-09-18]。"

    monkeypatch.setattr(ask, "chat_stream", fake_chat_stream)
    monkeypatch.setattr(ask, "chat", fake_chat)
    return captured


def test_query_stream_yields_chunks_in_order_and_exposes_citations(stream_fakes, schema):
    """流式查询：引用立刻可读，文本逐块产出，顺序即生成顺序。"""
    streamed = ask.query_stream("我在 qdrant 上踩过什么坑？")

    assert [c.type for c in streamed.citations] == ["error"]
    assert list(streamed.chunks) == ["409 的根因", "是维度不匹配", " [2026-09-18]。"]
    assert stream_fakes["stream_messages"][-1]["content"].startswith("问题：")
    assert stream_fakes["thinking"] is False  # 走配置，与 query() 同源
    assert stream_fakes["max_tokens"] == 600


def test_query_stream_reports_no_hits_without_calling_the_llm(monkeypatch, schema):
    """无命中时不能去问 LLM：直接给引导语，且不产生任何 API 调用。

    这条是"花了钱却没结果"的守卫 —— 检索为空还发一次 chat，用户等 3 秒
    拿到一句模型编的废话，比直接说"库里没有"差得多。
    """
    monkeypatch.setattr(ask, "embed", lambda texts: [[0.1, 0.2, 0.3]])
    monkeypatch.setattr(ask.similarity, "search", lambda *a, **k: [])

    def boom(*a, **k):
        raise AssertionError("no hits must not call the LLM")

    monkeypatch.setattr(ask, "chat_stream", boom)

    streamed = ask.query_stream("库里没有的问题")

    assert streamed.citations == []
    assert "".join(streamed.chunks) == ask._NO_HITS_GUIDANCE


def test_query_and_query_stream_build_the_same_messages(stream_fakes, schema):
    """两条路径（一次性 / 流式）必须发出同一个 prompt，否则会悄悄漂移。

    这不是形式主义：上下文构建包含过滤、关联扩展、引用行格式三处逻辑，
    复制一份到 query_stream 里，任何一处改动都只会在一条路径上生效 ——
    表现为"流式答得不一样"，而且没有任何测试会红。
    """
    ask.query("同一个问题")
    ask.query_stream("同一个问题")

    assert stream_fakes["stream_messages"] == stream_fakes["sync_messages"]


def test_query_includes_expanded_neighbors(pipeline_fakes, monkeypatch):
    """关联补充要真的进到回答里 —— 这条链路此前只测过 _expand_neighbors 本身。

    `query` 里少了这一环，扩展是**静默**失效的：异常被 `except Exception`
    吞掉，主回答照常返回，只是"关联补充"永远为空。
    """
    scored_schema = deepcopy(SCHEMA)
    scored_schema["retrieval"]["expand"]["neighbor_min_score"] = 0.1
    monkeypatch.setattr(ask.config, "load_schema", lambda: scored_schema)
    monkeypatch.setattr(
        ask.similarity, "search",
        lambda *a, **k: [hit(
            {"text": "主命中", "date": "2026-09-18", "type": "error",
             "source": "claude_code", "source_refs": ["s1"],
             "related": ["n1"]})])
    monkeypatch.setattr(
        ask_expand, "_client",
        lambda: FakeRetrieveClient([
            record("n1", [1.0, 0.0], text="邻居条目", date="2026-09-19",
                   type="progress", source="codex", source_refs=["r1"])]))

    answer = ask.query("问题", expand=True)

    assert [c.id for c in answer.expanded] == ["n1"]


def test_query_survives_expansion_failure(pipeline_fakes, schema, monkeypatch):
    """关联扩展炸了不能拖垮主回答（FR-021 的降级路径）。"""
    calls = []

    def boom(*args, **kwargs):
        calls.append(args)
        raise RuntimeError("qdrant retrieve down")

    # 打在 ask 上而非 ask_expand 上：ask.py 是 `from src.ask_expand import
    # expand_neighbors`，名字在 import 时就绑进了 ask 的命名空间，打在
    # ask_expand 上不会生效。下面那句 calls 断言就是为这个设的 ——
    # 没有它，替换失效时 expanded 照样是 []，用例会静默变绿。
    monkeypatch.setattr(ask, "expand_neighbors", boom)

    answer = ask.query("问题", expand=True)

    assert len(calls) == 1             # 确实走到了扩展，替换没被换空
    assert answer.expanded == []       # 降级成空列表，不是 None
    assert answer.text.startswith("409")
    assert len(answer.citations) == 2  # 主命中不受影响


def test_query_no_hits_returns_guidance_without_llm(
        pipeline_fakes, schema, monkeypatch):
    monkeypatch.setattr(
        ask.similarity, "search", lambda *a, **k: [])
    calls = []
    monkeypatch.setattr(ask, "chat",
                        lambda *a, **k: calls.append(1) or "x")

    answer = ask.query("什么都没有")

    assert calls == []
    assert answer.citations == []
    assert answer.question == "什么都没有"
    assert "make sync" in answer.text  # 明确引导提示（spec Edge Cases）


def test_query_no_expand_flag(pipeline_fakes, schema):
    captured_filters = pipeline_fakes
    answer = ask.query("问题", expand=False)
    assert answer.expanded == []


def test_query_maps_expand_flag_to_mode(pipeline_fakes, schema, monkeypatch):
    """expand 三态 → 扩展模式：False=off、True=all、None=交给配置。

    这个映射此前没人断言：变异测试把 `expand is True` 换成 `is not True` /
    `and False` 都没被发现。
    """
    modes = []
    monkeypatch.setattr(
        ask, "expand_neighbors",
        lambda hits, vector, mode=None, **kwargs: modes.append(mode) or [])

    ask.query("问题", expand=False)
    ask.query("问题", expand=True)
    ask.query("问题")

    assert modes == ["off", "all", None]


# --- 关联扩展的阈值边界 ---


class FakeRetrieveClient:
    """只实现 _expand_neighbors 用到的 retrieve()。

    刻意**照着 with_vector 办事**：没要向量就真的不给向量。真实 Qdrant 也是
    这样（with_vector=False 时 record.vector 是 None），如果 fake 无条件把
    向量塞回去，"忘了传 with_vector"这个错误就会被 fake 掩盖掉。
    """

    def __init__(self, records):
        self.records = records
        self.calls = []

    def retrieve(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("with_vector"):
            return self.records
        return [
            SimpleNamespace(id=r.id, vector=None, payload=r.payload)
            for r in self.records
        ]


def record(point_id: str, vector: list[float], **payload):
    return SimpleNamespace(id=point_id, vector=vector, payload=payload)


def test_expand_neighbors_skips_below_threshold_without_stopping(schema):
    """低于阈值的是"跳过这一个"，不是"到此为止"。

    continue 写成 break 的话，第一个不合格的邻居会把后面所有合格的都挡掉。
    """
    client = FakeRetrieveClient([
        record("n-below", [0.0, 1.0], text="无关", date="2026-09-01",
               type="error", source="codex", source_refs=[]),
        record("n-above", [1.0, 0.0], text="相关", date="2026-09-02",
               type="error", source="codex", source_refs=[]),
    ])
    hits = [hit({"related": ["n-below", "n-above"]})]

    expanded = ask_expand.expand_neighbors(hits, [1.0, 0.0], mode=0.5, client=client)

    assert [c.id for c in expanded] == ["n-above"]


def test_expand_neighbors_keeps_neighbor_exactly_at_threshold(schema):
    """阈值是开区间：score < threshold 才排除，正好等于要留下。"""
    client = FakeRetrieveClient([
        record("n1", [2.0, 0.0], text="边界", date="2026-09-02",
               type="error", source="codex", source_refs=[]),
    ])
    hits = [hit({"related": ["n1"]})]

    # cosine([1,0],[2,0]) 正好 1.0，阈值也设 1.0
    expanded = ask_expand.expand_neighbors(hits, [1.0, 0.0], mode=1.0, client=client)

    assert [c.id for c in expanded] == ["n1"]


def test_expand_neighbors_tolerates_hits_without_related_key(schema):
    """payload 里没有 related 是正常情况（多数条目还没做关联），不能炸。"""
    client = FakeRetrieveClient([])
    assert ask_expand.expand_neighbors(
        [hit({})], [1.0, 0.0], mode=0.5, client=client) == []


def test_expand_neighbors_must_fetch_vectors_when_scoring(schema):
    """阈值模式必须把邻居向量取回来，否则全部邻居被当成零向量静默丢光。

    这是一条**静默**失败：不要向量 → cosine 全是 0.0 → 全部低于阈值 →
    "关联补充"整段消失，而主回答照常返回，没人会发现。
    """
    client = FakeRetrieveClient([
        record("n1", [1.0, 0.0], text="相关", date="2026-09-02",
               type="error", source="codex", source_refs=[]),
    ])
    hits = [hit({"related": ["n1"]})]

    expanded = ask_expand.expand_neighbors(hits, [1.0, 0.0], mode=0.5, client=client)

    assert [c.id for c in expanded] == ["n1"]


def test_expand_neighbors_scores_with_the_question_vector(schema):
    """打分用的是问题向量，且结果要真的写进引用（不是算完就扔）。"""
    client = FakeRetrieveClient([
        record("n1", [3.0, 4.0], text="相关", date="2026-09-02",
               type="error", source="codex", source_refs=[]),
    ])
    hits = [hit({"related": ["n1"]})]

    expanded = ask_expand.expand_neighbors(hits, [4.0, 3.0], mode=0.1, client=client)

    assert [c.id for c in expanded] == ["n1"]
    assert expanded[0].score == pytest.approx(24 / 25)


def test_expand_neighbors_marks_unscored_neighbors_as_full_score(schema):
    """不打分（无阈值）时邻居以 1.0 进入：关联补充不参与主命中的排序。"""
    client = FakeRetrieveClient([
        record("n1", [1.0, 0.0], text="补充", date="2026-09-02",
               type="error", source="codex", source_refs=[]),
    ])
    hits = [hit({"related": ["n1"]})]

    expanded = ask_expand.expand_neighbors(hits, [1.0, 0.0], mode="all", client=client)

    assert expanded[0].score == 1.0


def test_expand_neighbors_off_mode_comes_from_the_schema(monkeypatch):
    """配置里 expand.mode=off 时不传 mode 也不扩展 —— 默认值来自 schema。

    `expand["mode"] if mode is None else mode` 写成 `and False`，默认值就永远
    是 None，"配置里关掉扩展"这个开关直接失效（而 off 恰好是它唯一能挡住
    的行为）。
    """
    off_schema = deepcopy(SCHEMA)
    off_schema["retrieval"]["expand"]["mode"] = "off"
    monkeypatch.setattr(ask.config, "load_schema", lambda: off_schema)
    client = FakeRetrieveClient([
        record("n1", [1.0, 0.0], text="补充", date="2026-09-02",
               type="error", source="codex", source_refs=[]),
    ])

    expanded = ask_expand.expand_neighbors(
        [hit({"related": ["n1"]})], [1.0, 0.0], client=client)

    assert expanded == []
    assert client.calls == []


def test_expand_neighbors_reads_neighbor_min_score_from_schema(monkeypatch):
    """阈值还能来自 schema 的 neighbor_min_score，不只是"mode 是数字"。

    这一整条分支（`elif expand.get("neighbor_min_score") is not None`）
    此前一行都没跑到：SCHEMA 里它是 None。
    """
    scored_schema = deepcopy(SCHEMA)
    scored_schema["retrieval"]["expand"]["neighbor_min_score"] = 0.5
    monkeypatch.setattr(ask.config, "load_schema", lambda: scored_schema)
    client = FakeRetrieveClient([
        record("n-low", [0.0, 1.0], text="无关", date="2026-09-01",
               type="error", source="codex", source_refs=[]),
        record("n-high", [1.0, 0.0], text="相关", date="2026-09-02",
               type="error", source="codex", source_refs=[]),
    ])
    hits = [hit({"related": ["n-low", "n-high"]})]

    expanded = ask_expand.expand_neighbors(hits, [1.0, 0.0], mode="all", client=client)

    assert [c.id for c in expanded] == ["n-high"]


# --- _parse_args：默认值与解析步进 ---
# 默认值是对外契约的一部分；步进写错会让 --type 吃到下一个 flag。


def test_parse_args_defaults():
    assert ask._parse_args(["ask"]) == {
        "Q": None, "type": None, "project": None,
        "since": None, "until": None, "no-expand": False, "stream": False,
    }


def test_parse_args_recognises_stream_flag():
    """--stream 是个布尔开关（不带值），别被当成需要配对的 flag。"""
    assert ask._parse_args(["ask", "Q=x", "--stream"])["stream"] is True
    assert ask._parse_args(["ask", "Q=x"])["stream"] is False


def test_parse_args_reads_q_and_pairs_each_flag_with_its_value():
    opts = ask._parse_args(["ask", "Q=最近学了什么", "--type", "error",
                            "--project", "first-rag", "--no-expand"])
    assert opts["Q"] == "最近学了什么"
    assert opts["type"] == "error"
    assert opts["project"] == "first-rag"
    assert opts["no-expand"] is True


def test_parse_args_flag_at_the_end_yields_none_not_a_crash():
    """flag 落在末尾（没带值）要得到 None，不能 IndexError。

    `i < len(argv)` 换成 `i <= len(argv)`，越界读 argv 直接炸 —— 用户手滑
    打错命令时看到的是 traceback 而不是 usage。
    """
    assert ask._parse_args(["ask", "Q=x", "--type"])["type"] is None


def test_parse_args_recognises_since_and_until():
    """四个 flag 缺一个，`--since 7d` 就会被当成未知参数静默丢掉。"""
    opts = ask._parse_args(["ask", "Q=x", "--since", "7d",
                            "--until", "2026-09-30"])
    assert opts["since"] == "7d"
    assert opts["until"] == "2026-09-30"


# --- CLI (FR-025 surface) ---


def test_main_parses_args_and_prints_answer(
        pipeline_fakes, schema, monkeypatch, capsys):
    code = ask.main(["ask", "Q=我学了啥", "--type", "error",
                     "--since", "7d", "--no-expand"])

    out = capsys.readouterr().out
    assert code == 0
    assert "409 的根因" in out
    assert "[2026-09-18] error" in out  # 引用清单输出
    assert pipeline_fakes["filters"] is not None


def test_main_requires_question(schema, capsys):
    # 退出码是对外契约（Makefile / shell 会看），不只是"非 0"
    assert ask.main(["ask"]) == 2
    # usage 必须走 stderr：stdout 是要给管道用的，不能被帮助文本污染
    captured = capsys.readouterr()
    assert "usage: make ask" in captured.err
    assert captured.out == ""


def test_main_failure_exits_nonzero(pipeline_fakes, schema, monkeypatch, capsys):
    def boom(texts):
        raise RuntimeError("ark down")
    monkeypatch.setattr(ask, "embed", boom)
    assert ask.main(["ask", "Q=x"]) == 1
    captured = capsys.readouterr()
    assert "检索失败" in captured.err
    assert captured.out == ""


def test_main_forwards_every_flag_to_query(schema, monkeypatch):
    """CLI 的活儿就是把参数交给 query；少传一个就是用户明明加了过滤却没生效。"""
    calls = []
    monkeypatch.setattr(
        ask, "query",
        lambda question, **kwargs: calls.append((question, kwargs))
        or SimpleNamespace(text="ok", citations=[], expanded=[]))

    code = ask.main(["ask", "Q=问题", "--type", "error",
                     "--project", "first-rag", "--since", "7d",
                     "--until", "2026-09-30", "--no-expand"])

    assert code == 0
    assert calls == [("问题", {
        "type": "error", "project": "first-rag", "since": "7d",
        "until": "2026-09-30", "expand": False,
    })]

    # 不给 --no-expand 时必须是 None（"交给配置决定"），不能一律 False
    # —— 一律 False 等于把配置里的 expand.mode 永久关掉。
    ask.main(["ask", "Q=问题"])
    assert calls[1][1]["expand"] is None


def test_main_reads_sys_argv_when_argv_is_omitted(schema, monkeypatch):
    """`python -m src.ask` 走的就是这条：不传 argv 时读 sys.argv。"""
    monkeypatch.setattr(sys, "argv", ["ask"])
    assert ask.main() == 2


def test_main_prints_expanded_citations_in_their_own_section(
        pipeline_fakes, schema, monkeypatch, capsys):
    """关联补充要单独一节并带标记 —— 不然读者分不清它是不是直接命中。"""
    monkeypatch.setattr(
        ask, "expand_neighbors",
        lambda hits, vector, mode=None, **kwargs: [
            ask_expand._citation_from_record(
                SimpleNamespace(
                    id="n1", vector=None,
                    payload={"date": "2026-09-20", "type": "progress",
                             "text": "邻居条目", "source": "codex",
                             "source_refs": []}),
                1.0)])

    ask.main(["ask", "Q=问题"])
    out = capsys.readouterr().out

    assert "引用：" in out
    assert "- [2026-09-18] error: Qdrant upsert 409 root cause" in out
    assert "- [2026-09-20] progress（关联补充）: 邻居条目" in out


# --- CLI --stream ---


def _stream_answer(chunks, citations=None, expanded=None):
    """构造一个 StreamedAnswer；chunks 传可迭代对象即可。"""
    return ask.StreamedAnswer(
        question="问题", citations=citations or [], expanded=expanded or [],
        chunks=iter(chunks))


def test_main_stream_prints_incrementally_not_after_the_fact(
        schema, monkeypatch, capsys):
    """流式的全部意义是**边生成边打印**：第二块还没生出来时，第一块就得在屏幕上。

    这条用"生成器恢复执行的那一刻去读已捕获的 stdout"来证明顺序，而不是
    只断言最终输出拼接正确 —— 后者对"先攒完再一次性打印"同样成立，也就
    等于没测出流式。真实调用里那意味着用户要盯着空屏等完全部生成。
    """
    printed_before_second_chunk = []

    def chunks():
        yield "第一块"
        printed_before_second_chunk.append(capsys.readouterr().out)
        yield "第二块"

    monkeypatch.setattr(
        ask, "query_stream",
        lambda question, **kwargs: _stream_answer(chunks()))

    code = ask.main(["ask", "Q=问题", "--stream"])

    assert code == 0
    assert "第一块" in printed_before_second_chunk[0]
    assert "第二块" in capsys.readouterr().out  # 只可能出现在之后的输出里


def test_main_stream_prints_citations_after_the_text(
        schema, monkeypatch, capsys):
    """流式也要有引用清单，且排在正文之后（引用是给读完之后对照用的）。"""
    citation = ask.Citation(
        id="id-1", date="2026-09-18", type="error",
        text="Qdrant upsert 409 root cause", source="claude_code",
        source_refs=["s1"], score=0.9)
    monkeypatch.setattr(
        ask, "query_stream",
        lambda question, **kwargs: _stream_answer(["正文。"], [citation]))

    ask.main(["ask", "Q=问题", "--stream"])

    out = capsys.readouterr().out
    assert out.index("正文。") < out.index("引用：")
    assert "- [2026-09-18] error: Qdrant upsert 409 root cause" in out


def test_main_stream_forwards_filters_to_query_stream(schema, monkeypatch):
    """流式路径也要吃过滤参数 —— 丢了过滤等于回答里混进不符合条件的条目。"""
    calls = []
    monkeypatch.setattr(
        ask, "query_stream",
        lambda question, **kwargs: calls.append((question, kwargs))
        or _stream_answer(["ok"]))

    ask.main(["ask", "Q=问题", "--type", "error", "--since", "7d", "--stream"])

    assert calls == [("问题", {
        "type": "error", "project": None, "since": "7d",
        "until": None, "expand": None,
    })]


def test_main_without_stream_does_not_touch_the_streaming_path(
        pipeline_fakes, schema, monkeypatch, capsys):
    """不给 --stream 时必须走老的一次性路径，流式代码不能悄悄接管默认行为。"""
    def boom(*a, **k):
        raise AssertionError("非流式调用不该走 query_stream")

    monkeypatch.setattr(ask, "query_stream", boom)

    assert ask.main(["ask", "Q=问题"]) == 0
