"""T017 contract tests for novelty search and deduplication."""

from types import SimpleNamespace

import pytest

from src import similarity


def scored_point(point_id: str, score: float, payload: dict | None = None):
    return SimpleNamespace(id=point_id, score=score, payload=payload or {})


class FakeQdrantClient:
    def __init__(self, *, exists: bool = True, responses: dict | None = None):
        self.exists = exists
        self.responses = responses or {}
        self.queries = []

    def collection_exists(self, collection_name: str) -> bool:
        return self.exists

    def query_points(self, **kwargs):
        self.queries.append(kwargs)
        key = tuple(kwargs["query"])
        points = self.responses.get(key, [])
        return SimpleNamespace(points=points[: kwargs["limit"]])


def test_search_returns_hits_with_payload():
    client = FakeQdrantClient(
        responses={(0.1, 0.2): [scored_point("existing", 0.91, {"text": "old"})]}
    )

    hits = similarity.search(
        [0.1, 0.2],
        k=1,
        client=client,
        collection_name="learning_memory",
    )

    assert hits == [
        similarity.Hit(id="existing", score=0.91, payload={"text": "old"})
    ]
    assert client.queries[0]["limit"] == 1
    assert client.queries[0]["with_payload"] is True


def test_search_passes_filter_to_qdrant():
    marker = object()
    client = FakeQdrantClient()

    similarity.search(
        [0.1, 0.2],
        k=3,
        filters=marker,
        client=client,
        collection_name="learning_memory",
    )

    assert client.queries[0]["query_filter"] is marker


def test_search_returns_empty_when_collection_is_missing():
    client = FakeQdrantClient(exists=False)

    assert similarity.search(
        [0.1, 0.2],
        client=client,
        collection_name="learning_memory",
    ) == []
    assert client.queries == []


def test_filter_novel_keeps_all_when_collection_is_missing():
    client = FakeQdrantClient(exists=False)

    kept = similarity.filter_novel(
        ["candidate-a", "candidate-b"],
        [[1.0, 0.0], [0.0, 1.0]],
        client=client,
        collection_name="learning_memory",
        threshold=0.82,
    )

    assert kept == [0, 1]
    assert client.queries == []


def test_filter_novel_skips_similar_existing_point():
    client = FakeQdrantClient(
        responses={(1.0, 0.0): [scored_point("existing", 0.91)]}
    )

    kept = similarity.filter_novel(
        ["candidate"],
        [[1.0, 0.0]],
        client=client,
        collection_name="learning_memory",
        threshold=0.82,
    )

    assert kept == []


def test_filter_novel_keeps_candidate_at_threshold():
    client = FakeQdrantClient(
        responses={(1.0, 0.0): [scored_point("existing", 0.82)]}
    )

    kept = similarity.filter_novel(
        ["candidate"],
        [[1.0, 0.0]],
        client=client,
        collection_name="learning_memory",
        threshold=0.82,
    )

    assert kept == [0]


def test_filter_novel_ignores_exact_same_point_id():
    client = FakeQdrantClient(
        responses={(1.0, 0.0): [scored_point("candidate", 1.0)]}
    )

    kept = similarity.filter_novel(
        ["candidate"],
        [[1.0, 0.0]],
        client=client,
        collection_name="learning_memory",
        threshold=0.82,
    )

    assert kept == [0]


def test_filter_novel_uses_config_threshold_when_not_supplied(
    monkeypatch,
):
    monkeypatch.setattr(
        similarity.config,
        "load_schema",
        lambda: {"distill": {"novelty_threshold": 0.5}},
    )
    client = FakeQdrantClient(
        responses={(1.0, 0.0): [scored_point("existing", 0.6)]}
    )

    kept = similarity.filter_novel(
        ["candidate"],
        [[1.0, 0.0]],
        client=client,
        collection_name="learning_memory",
    )

    assert kept == []


def test_filter_novel_returns_indices_for_mixed_candidates():
    client = FakeQdrantClient(
        responses={
            (1.0, 0.0): [scored_point("existing", 0.9)],
            (0.0, 1.0): [scored_point("unrelated", 0.1)],
        }
    )

    kept = similarity.filter_novel(
        ["duplicate", "novel"],
        [[1.0, 0.0], [0.0, 1.0]],
        client=client,
        collection_name="learning_memory",
        threshold=0.82,
    )

    assert kept == [1]


def test_filter_novel_sees_past_the_whole_current_batch():
    """本批次占满前 N 个槽位时，第 N+1 个槽位必须还能看见外面的重复项。

    这是 search_limit = len(batch) + 1 里那个 "+1" 存在的理由：本批次的点
    （幂等重跑时已经在库里）会占掉前 N 个结果，不多要一个槽位，外面的重复项
    就被挤出结果集，去重会静默失效。

    注意这个测试**杀不死** `+1` → `+2` 那个变异体 —— 结果按分数降序返回，
    而批次内最多只有 N 个不同的 ID 能出现在结果里，所以第 N+1 位一定是一个
    批次外的点；再多要槽位只会拿到分数更低的结果，排在它后面，`next()` 取到的
    还是同一个。那是等价变异，已写进 pyproject 的 do_not_mutate_patterns。
    """
    batch_hits = [
        scored_point("candidate-a", 0.99),   # 本批次，占槽 1
        scored_point("candidate-b", 0.98),   # 本批次，占槽 2
        scored_point("older-duplicate", 0.97),   # 批次外 → 槽 3（N+1）
        scored_point("even-older", 0.96),        # 批次外 → 槽 4，+2 才看得见
    ]
    client = FakeQdrantClient(
        responses={(1.0, 0.0): batch_hits, (0.0, 1.0): batch_hits}
    )

    kept = similarity.filter_novel(
        ["candidate-a", "candidate-b"],
        [[1.0, 0.0], [0.0, 1.0]],
        client=client,
        collection_name="learning_memory",
        threshold=0.82,
    )

    assert kept == [], "本批次之外的重复项必须被看见，两个候选都该跳过"


def test_search_defaults_to_five_hits():
    """默认 k=5 是对外契约，调用方不传时也必须成立。"""
    client = FakeQdrantClient()

    similarity.search([0.1, 0.2], client=client,
                      collection_name="learning_memory")

    assert client.queries[0]["limit"] == 5


def test_client_factory_points_at_local_qdrant(monkeypatch):
    """_client 必须连本地 Qdrant，且不继承环境代理（FR-011 / 宪法 VI）。"""
    captured = {}

    class Recorder:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(similarity, "QdrantClient", Recorder)
    monkeypatch.setattr(similarity.config, "QDRANT_URL",
                        "http://localhost:6333")

    similarity._client()

    assert captured["url"] == "http://localhost:6333"
    assert captured["trust_env"] is False


def test_filter_novel_prefers_explicit_collection_over_config(monkeypatch):
    """显式传入的 collection_name 必须盖过 config 默认值（`or` 而非 `and`）。"""
    monkeypatch.setattr(similarity.config, "COLLECTION", "default-collection")
    client = FakeQdrantClient(
        responses={(1.0, 0.0): [scored_point("existing", 0.91)]}
    )

    similarity.filter_novel(
        ["candidate"],
        [[1.0, 0.0]],
        client=client,
        collection_name="explicit-collection",
        threshold=0.82,
    )

    assert client.queries[0]["collection_name"] == "explicit-collection"


def test_filter_novel_logs_why_a_candidate_was_skipped(caplog):
    """跳过候选时要留下可排障的记录：候选是谁、撞上谁、分数多少。"""
    client = FakeQdrantClient(
        responses={(1.0, 0.0): [scored_point("existing", 0.91)]}
    )

    with caplog.at_level("INFO", logger="src.similarity"):
        similarity.filter_novel(
            ["candidate"],
            [[1.0, 0.0]],
            client=client,
            collection_name="learning_memory",
            threshold=0.82,
        )

    messages = [record.getMessage() for record in caplog.records]
    assert any(
        message.startswith("similarity: skipped candidate as duplicate of existing")
        for message in messages
    )
    assert any("0.9100" in message for message in messages)


def test_filter_novel_rejects_mismatched_lengths():
    with pytest.raises(ValueError) as excinfo:
        similarity.filter_novel(
            ["candidate"],
            [],
            client=FakeQdrantClient(),
            collection_name="learning_memory",
            threshold=0.82,
        )

    # 文案是给调用方看的，写错半个字也不能算通过
    assert str(excinfo.value) == "point_ids and vectors must have the same length"


def test_filter_novel_ignores_all_current_batch_ids_on_rerun():
    client = FakeQdrantClient(
        responses={
            (1.0, 0.0): [
                scored_point("candidate-a", 1.0),
                scored_point("candidate-b", 0.95),
            ],
            (0.0, 1.0): [
                scored_point("candidate-b", 1.0),
                scored_point("candidate-a", 0.95),
            ],
        }
    )

    kept = similarity.filter_novel(
        ["candidate-a", "candidate-b"],
        [[1.0, 0.0], [0.0, 1.0]],
        client=client,
        collection_name="learning_memory",
        threshold=0.82,
    )

    assert kept == [0, 1]


def test_filter_novel_sees_external_duplicate_after_batch_hits():
    client = FakeQdrantClient(
        responses={
            (1.0, 0.0): [
                scored_point("candidate-a", 1.0),
                scored_point("candidate-b", 0.95),
                scored_point("external-duplicate", 0.9),
            ],
            (0.0, 1.0): [
                scored_point("candidate-b", 1.0),
                scored_point("candidate-a", 0.95),
            ],
        }
    )

    kept = similarity.filter_novel(
        ["candidate-a", "candidate-b"],
        [[1.0, 0.0], [0.0, 1.0]],
        client=client,
        collection_name="learning_memory",
        threshold=0.82,
    )

    assert kept == [1]


# --- T027 关联边构建（FR-017）---


def related_point(point_id, score, related=None):
    """带 related 字段的检索命中：build_related_edges 回填时要读它。"""
    return scored_point(point_id, score, {"related": list(related or [])})


def test_build_related_edges_empty_input_returns_empty():
    edges = similarity.build_related_edges(
        [], [], client=FakeQdrantClient())

    assert edges.new_related == {}
    assert edges.backfill == {}


def test_build_related_edges_rejects_mismatched_lengths():
    with pytest.raises(ValueError) as excinfo:
        similarity.build_related_edges(
            ["candidate"], [], client=FakeQdrantClient())

    assert str(excinfo.value) == "point_ids and vectors must have the same length"


def test_build_related_edges_no_neighbors_when_collection_missing():
    client = FakeQdrantClient(exists=False)

    edges = similarity.build_related_edges(
        ["candidate"], [[1.0, 0.0]], client=client)

    assert edges.new_related == {"candidate": []}
    assert edges.backfill == {}
    assert client.queries == []


def test_build_related_edges_builds_bidirectional_edge():
    client = FakeQdrantClient(responses={
        (1.0, 0.0): [related_point("existing", 0.91, ["other"])],
    })

    edges = similarity.build_related_edges(
        ["candidate"], [[1.0, 0.0]], client=client)

    # N -> E：新条目关联到已有条目
    assert edges.new_related == {"candidate": ["existing"]}
    # E -> N：已有条目回填新条目，且保留它原本的 related
    assert edges.backfill == {"existing": ["other", "candidate"]}


def test_build_related_edges_ignores_score_at_or_below_threshold():
    """FR-017 是「score > 0.75」，0.75 本身和更低的都不建边。"""
    client = FakeQdrantClient(responses={
        (1.0, 0.0): [
            related_point("at-threshold", 0.75),
            related_point("below", 0.74),
            related_point("above", 0.76),
        ],
    })

    edges = similarity.build_related_edges(
        ["candidate"], [[1.0, 0.0]], client=client)

    assert edges.new_related == {"candidate": ["above"]}
    assert edges.backfill == {"above": ["candidate"]}


def test_build_related_edges_skips_backfill_when_neighbor_full():
    """对方 related 已达上限 5：跳过 E -> N 方向，但 N -> E 仍建立。"""
    full = [f"n{i}" for i in range(5)]
    client = FakeQdrantClient(responses={
        (1.0, 0.0): [related_point("full-neighbor", 0.91, full)],
    })

    edges = similarity.build_related_edges(
        ["candidate"], [[1.0, 0.0]], client=client)

    assert edges.new_related == {"candidate": ["full-neighbor"]}
    assert edges.backfill == {}


def test_build_related_edges_excludes_current_batch():
    """幂等：本批 ID 已在库里（重跑）时不能被当成邻居，否则首跑与重跑结果不一致。"""
    client = FakeQdrantClient(responses={
        (1.0, 0.0): [
            scored_point("candidate-a", 0.99),   # 本批
            related_point("external", 0.9),
        ],
        (0.0, 1.0): [
            scored_point("candidate-b", 0.99),   # 本批
            related_point("external", 0.9),
        ],
    })

    edges = similarity.build_related_edges(
        ["candidate-a", "candidate-b"],
        [[1.0, 0.0], [0.0, 1.0]],
        client=client)

    assert edges.new_related == {
        "candidate-a": ["external"],
        "candidate-b": ["external"],
    }
    assert edges.backfill == {"external": ["candidate-a", "candidate-b"]}


def test_build_related_edges_dedupes_backfill_on_rerun():
    """重跑时邻居已含本条目：回填去重，不重复加、不报错。"""
    client = FakeQdrantClient(responses={
        (1.0, 0.0): [related_point("existing", 0.91, ["other", "candidate"])],
    })

    edges = similarity.build_related_edges(
        ["candidate"], [[1.0, 0.0]], client=client)

    assert edges.new_related == {"candidate": ["existing"]}
    assert edges.backfill == {}


def test_build_related_edges_accumulates_backfill_across_batch():
    """同批多个新条目都关联到同一旧条目时，回填要累积，不能互相覆盖。"""
    client = FakeQdrantClient(responses={
        (1.0, 0.0): [related_point("hub", 0.91, ["old"])],
        (0.0, 1.0): [related_point("hub", 0.92, ["old"])],
    })

    edges = similarity.build_related_edges(
        ["candidate-a", "candidate-b"],
        [[1.0, 0.0], [0.0, 1.0]],
        client=client)

    assert edges.new_related == {
        "candidate-a": ["hub"],
        "candidate-b": ["hub"],
    }
    assert edges.backfill == {"hub": ["old", "candidate-a", "candidate-b"]}


def test_build_related_edges_searches_past_the_whole_batch():
    """排除本批后还要拿到 top-5 外部邻居，所以 search limit = len(batch) + 5。"""
    client = FakeQdrantClient(responses={
        (1.0, 0.0): [related_point("external", 0.91)],
    })

    similarity.build_related_edges(
        ["candidate-a", "candidate-b"],
        [[1.0, 0.0], [0.0, 1.0]],
        client=client)

    assert client.queries[0]["limit"] == 2 + 5


# --- T037 重蒸馏对齐（FR-023）---


def aligned_entry(text, refs=("s1",)):
    """align_redistill 只鸭子类型读 .text 与 .source_refs（核心层不能 import Entry）。"""
    return SimpleNamespace(text=text, source_refs=list(refs))


def test_align_redistill_empty_inputs():
    diff = similarity.align_redistill([], [])

    assert diff == {"rewritten": [], "unchanged": [], "added": [], "removed": []}


def test_align_redistill_pairs_shared_ref_with_changed_text_as_rewritten():
    old = [aligned_entry("Learned that X does Y and returns Z")]
    new = [aligned_entry("Learned that X does Y and returns W")]

    diff = similarity.align_redistill(old, new)

    assert diff["rewritten"] == [(old[0], new[0])]
    assert diff["unchanged"] == []
    assert diff["added"] == []
    assert diff["removed"] == []


def test_align_redistill_pairs_identical_text_as_unchanged():
    old = [aligned_entry("Learned that X does Y")]
    new = [aligned_entry("Learned that X does Y")]

    diff = similarity.align_redistill(old, new)

    assert diff["unchanged"] == [(old[0], new[0])]
    assert diff["rewritten"] == []
    assert diff["added"] == []
    assert diff["removed"] == []


def test_align_redistill_marks_unpaired_new_as_added_and_old_as_removed():
    old = [aligned_entry("old lesson", refs=("s1",))]
    new = [aligned_entry("brand new lesson", refs=("s9",))]

    diff = similarity.align_redistill(old, new)

    assert diff["added"] == new
    assert diff["removed"] == old
    assert diff["rewritten"] == []
    assert diff["unchanged"] == []


def test_align_redistill_requires_shared_source_refs():
    """文本完全相同但溯源引用不共享，也不配对（FR-021 两个条件缺一不可）。"""
    old = [aligned_entry("same text", refs=("s1",))]
    new = [aligned_entry("same text", refs=("s2",))]

    diff = similarity.align_redistill(old, new)

    assert diff["added"] == new
    assert diff["removed"] == old


def test_align_redistill_below_threshold_is_not_paired():
    old = [aligned_entry("aaaaabbbbb", refs=("s1",))]
    new = [aligned_entry("cccccddddd", refs=("s1",))]

    diff = similarity.align_redistill(old, new)

    assert diff["added"] == new
    assert diff["removed"] == old


def test_align_redistill_threshold_is_inclusive():
    """文本相似度恰好 0.85 也要配对（FR-021 是 ≥，不是 >）。"""
    old = [aligned_entry("a" * 17 + "x" * 3)]
    new = [aligned_entry("a" * 17 + "y" * 3)]

    diff = similarity.align_redistill(old, new)

    assert diff["rewritten"] == [(old[0], new[0])]
    assert diff["added"] == []
    assert diff["removed"] == []


def test_align_redistill_greedily_pairs_by_highest_similarity():
    """一个旧条目对多个候选时，选相似度最高的配对；其余候选落入 added。"""
    old = [aligned_entry("a" * 20, refs=("s1",))]
    new = [
        aligned_entry("a" * 18 + "bc", refs=("s1",)),   # ratio 0.9
        aligned_entry("a" * 17 + "bcd", refs=("s1",)),  # ratio 0.85
    ]

    diff = similarity.align_redistill(old, new)

    assert diff["rewritten"] == [(old[0], new[0])]
    assert diff["added"] == [new[1]]
    assert diff["removed"] == []


def test_align_redistill_shared_ref_is_intersection_not_subset():
    """共享 = 交集非空：{a,b} 与 {b,c} 也算共享（都有 b）。"""
    old = [aligned_entry("Learned that X does Y", refs=("a", "b"))]
    new = [aligned_entry("Learned that X does Y again", refs=("b", "c"))]

    diff = similarity.align_redistill(old, new)

    assert diff["rewritten"] == [(old[0], new[0])]


def test_align_redistill_scans_past_nonmatching_new():
    """旧条目要扫完全部新条目：前面有 refs 不共享的，后面共享的也要配对。

    这是 `continue` 而非 `break` 的理由 —— break 会在第一个不共享的新条目处
    停下，把本该配对的漏成「消失 + 新增」。
    """
    old = [aligned_entry("Learned that X does Y and returns Z", refs=("s1",))]
    new = [
        aligned_entry("totally different lesson", refs=("s9",)),
        aligned_entry("Learned that X does Y and returns W", refs=("s1",)),
    ]

    diff = similarity.align_redistill(old, new)

    assert diff["rewritten"] == [(old[0], new[1])]
    assert diff["added"] == [new[0]]
    assert diff["removed"] == []


def test_align_redistill_pairs_higher_similarity_across_multiple_old():
    """两个旧条目竞争一个新条目时，相似度更高的配对（贪心按 ratio 降序）。"""
    old = [
        aligned_entry("a" * 18 + "bd", refs=("s1",)),   # ratio 0.95
        aligned_entry("a" * 18 + "zz", refs=("s1",)),   # ratio 0.90
    ]
    new = [aligned_entry("a" * 18 + "bc", refs=("s1",))]

    diff = similarity.align_redistill(old, new)

    assert diff["rewritten"] == [(old[0], new[0])]
    assert diff["removed"] == [old[1]]
    assert diff["added"] == []


def test_build_related_edges_backfills_neighbor_without_related_field():
    """邻居 payload 里没有 related 字段（关联边上线前的老数据）也要能回填。"""
    client = FakeQdrantClient(responses={
        (1.0, 0.0): [scored_point("legacy", 0.91)],  # 无 related key
    })

    edges = similarity.build_related_edges(
        ["candidate"], [[1.0, 0.0]], client=client)

    assert edges.new_related == {"candidate": ["legacy"]}
    assert edges.backfill == {"legacy": ["candidate"]}


def test_build_related_edges_caps_new_related_at_max_edges():
    """N→E 方向单条上限也要守：top_k 给 5 个邻居时，new_related 封顶 max_edges。"""
    hits = [related_point(f"n{i}", 0.9) for i in range(5)]
    client = FakeQdrantClient(responses={(1.0, 0.0): hits})

    edges = similarity.build_related_edges(
        ["candidate"], [[1.0, 0.0]], client=client,
        max_edges=2, top_k=5)

    assert edges.new_related == {"candidate": ["n0", "n1"]}
