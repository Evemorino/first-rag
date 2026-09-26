"""`scripts/batch_edge_probe.py` 的单元测试。

这个脚本要回答的是 ADR-12 留下的那条观测项：跨批断裂的关联能不能被库内相似度
补回。读数会进 ADR，所以四件事必须测准：**分组是按什么分的**（同批 / 跨批 / 跨运行
/ 算不出批次）、**阈值是严格大于**（与 `build_related_edges` 同口径）、**边是双向查的**
（payload 里只存一个方向）、以及**它真的只读**（假 client 只实现 scroll，调别的方法就炸）。
"""

import json
import inspect
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import batch_edge_probe as probe  # noqa: E402  (先补 sys.path 才能导入)
from src import similarity  # noqa: E402


class FakePoint:
    def __init__(self, point_id, vector, payload):
        self.id = point_id
        self.vector = vector
        self.payload = payload


class FakeClient:
    """只实现 `scroll`：本脚本是只读的，调任何别的属性都算违约。"""

    def __init__(self, points):
        self.points = points
        self.calls = []

    def scroll(self, collection, **_kwargs):
        self.calls.append(collection)
        return list(self.points), None

    def __getattr__(self, name):
        raise AssertionError(f"只读脚本不该调用 QdrantClient.{name}")


def point(point_id, vector, created, refs, related=(), date="2026-01-02"):
    return FakePoint(
        point_id,
        vector,
        {
            "date": date,
            "created_at": created,
            "source_refs": list(refs),
            "related": list(related),
            "source": "claude_code",
        },
    )


INDEX = {"n_batches": 2, "n_materials": 2, "ref_batch": {"a": 0, "b": 1}}
DAY = "2026-01-02"


def sample_points():
    return [
        point("1", [1.0, 0.0], "T1", ["a"]),
        point("2", [1.0, 0.2], "T1", ["a"]),
        point("3", [0.0, 1.0], "T1", ["b"]),
        point("4", [1.0, 0.2], "T2", ["a"], related=["2"]),
    ]


def test_edge_threshold_follows_the_source_default():
    """阈值只定义一次：跟着 `build_related_edges` 的形参走，不抄第二份硬编码。"""
    assert probe.EDGE_THRESHOLD == similarity.build_related_edges.__kwdefaults__["threshold"]
    # analyze 的默认值也必须出自同一处，否则报告上印的阈值与实际比较用的不是同一个数。
    assert inspect.signature(probe.analyze).parameters["edge_threshold"].default == probe.EDGE_THRESHOLD


def test_batches_of_maps_refs_and_skips_unknown():
    payload = {"source_refs": ["a", "b", "gone"]}
    assert probe.batches_of(payload, INDEX) == frozenset({0, 1})
    assert probe.batches_of({"source_refs": ["gone"]}, INDEX) == frozenset()
    assert probe.batches_of({"source_refs": []}, INDEX) == frozenset()
    assert probe.batches_of(payload, None) == frozenset()


@pytest.mark.parametrize(
    "left,right,expected",
    [
        ({"created_at": "T1", "source_refs": ["a"]}, {"created_at": "T1", "source_refs": ["a"]},
         probe.SAME_BATCH),
        ({"created_at": "T1", "source_refs": ["a"]}, {"created_at": "T1", "source_refs": ["b"]},
         probe.CROSS_BATCH),
        ({"created_at": "T1", "source_refs": ["a"]}, {"created_at": "T2", "source_refs": ["a"]},
         probe.CROSS_RUN),
        ({"created_at": "T1", "source_refs": ["gone"]}, {"created_at": "T1", "source_refs": ["a"]},
         probe.UNKNOWN_BATCH),
        ({"created_at": "T1"}, {"created_at": "T1", "source_refs": ["a"]},
         probe.UNKNOWN_BATCH),
    ],
)
def test_pair_class(left, right, expected):
    assert probe.pair_class(left, right, INDEX) == expected


def test_cross_run_wins_over_batch_question():
    """跨运行的对不问批次：批次只在"同一次运行内部"才有意义。"""
    left = {"created_at": "T1", "source_refs": ["gone"]}
    right = {"created_at": "T2", "source_refs": ["gone"]}
    assert probe.pair_class(left, right, None) == probe.CROSS_RUN


def test_analyze_splits_the_three_groups(tmp_path):
    rows = probe.analyze(sample_points(), {DAY: INDEX})

    assert len(rows) == 1
    row = rows[0]
    assert (row["day"], row["points"], row["unmatched"]) == (DAY, 4, 0)
    stats = row["stats"]
    # (1,2) 同批高相似；(1,3)(2,3) 跨批但一高一低；(1,4)(2,4)(3,4) 跨运行。
    assert stats[probe.SAME_BATCH] == {"pairs": 1, "over": 1, "linked": 0, "max": pytest.approx(0.98058, abs=1e-4)}
    assert stats[probe.CROSS_BATCH]["pairs"] == 2
    assert stats[probe.CROSS_BATCH]["over"] == 0
    assert stats[probe.CROSS_RUN] == {"pairs": 3, "over": 2, "linked": 1, "max": pytest.approx(1.0)}


def test_analyze_finds_edges_stored_in_one_direction():
    """`related` 只存一个方向：查边必须双向 —— 否则补回边会被漏报。"""
    rows = probe.analyze(sample_points(), {DAY: INDEX})
    linked = [p for p in rows[0]["cross_batch"] if p["linked"]]
    assert linked == []  # 本例跨批对确实都没边
    # 反向验证：把边挪到被指向的那条身上，仍然算有边。
    flipped = sample_points()
    flipped[1].payload["related"] = ["4"]
    flipped[3].payload["related"] = []
    rows = probe.analyze(flipped, {DAY: INDEX})
    assert rows[0]["stats"][probe.CROSS_RUN]["linked"] == 1


def test_analyze_lists_cross_batch_pairs_over_threshold_only():
    points = sample_points()
    points[2].vector = [1.0, 0.05]  # 3 往 1/2 靠，跨批对越线
    rows = probe.analyze(points, {DAY: INDEX})
    detail = rows[0]["cross_batch"]
    assert [p["left"] for p in detail] == ["1", "2"]
    assert all(p["linked"] is False for p in detail)
    assert detail[0]["score"] >= detail[1]["score"]  # 按相似度降序
    assert detail[0]["left_batches"] == [0] and detail[0]["right_batches"] == [1]
    assert detail[0]["left_refs"] == ["a"]


def test_analyze_without_snapshot_reports_unknown_instead_of_guessing():
    rows = probe.analyze(sample_points(), {DAY: None})
    stats = rows[0]["stats"]
    assert probe.SAME_BATCH not in stats and probe.CROSS_BATCH not in stats
    assert stats[probe.UNKNOWN_BATCH]["pairs"] == 3  # 三对同运行
    assert stats[probe.CROSS_RUN]["pairs"] == 3
    assert rows[0]["unmatched"] == 4
    assert rows[0]["batches"] is None


def test_load_batch_index_rebuilds_the_boxes(tmp_path):
    raw = tmp_path / f"{DAY}.json"
    raw.write_text(
        json.dumps(
            {
                "materials": [
                    {"ref": "m1", "text": "x" * 60},
                    {"ref": "m2", "text": "y" * 60},
                ]
            }
        ),
        encoding="utf-8",
    )
    index = probe.load_batch_index(DAY, 100, tmp_path)
    assert index["n_batches"] == 2 and index["n_materials"] == 2
    assert index["ref_batch"] == {"m1": 0, "m2": 1}


@pytest.mark.parametrize("kind", ["missing", "broken", "empty"])
def test_load_batch_index_returns_none_when_it_cannot_be_rebuilt(tmp_path, kind):
    if kind == "broken":
        (tmp_path / f"{DAY}.json").write_text("{not json", encoding="utf-8")
    elif kind == "empty":
        (tmp_path / f"{DAY}.json").write_text(json.dumps({"materials": []}), encoding="utf-8")
    assert probe.load_batch_index(DAY, 100, tmp_path) is None


def test_render_marks_the_detail_day_only(tmp_path):
    rows = probe.analyze(sample_points(), {DAY: INDEX})
    text = "\n".join(probe.render(rows, day=DAY, threshold=0.75))
    assert f"== {DAY}: points=4 batches=2 materials=2 refs_unmatched=0" in text
    assert f"== {DAY}" in text and "build_related_edges" in text
    other = "\n".join(probe.render(rows, day="2026-01-03", threshold=0.75))
    assert "* cos=" not in other


def _write_snapshot(tmp_path, refs=("a", "b")):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir(exist_ok=True)
    (raw_dir / f"{DAY}.json").write_text(
        json.dumps({"materials": [{"ref": r, "text": "x" * 10} for r in refs]}),
        encoding="utf-8",
    )
    return raw_dir


def _run_main(monkeypatch, points, argv, raw_dir):
    monkeypatch.setattr(probe, "QdrantClient", lambda **_kwargs: FakeClient(points))
    monkeypatch.setattr(probe, "collect_points", lambda client, collection: client.scroll(collection)[0])
    # 装箱预算压到 10 字符：让 fixture 的两条素材落在两批（真预算 120000 太胖）。
    monkeypatch.setattr(probe.config, "load_schema", lambda: {"distill": {"batch_max_chars": 10}})
    return probe.main([*argv, "--raw-dir", str(raw_dir)])


def test_main_reports_and_stays_read_only(monkeypatch, tmp_path, capsys):
    """端到端：快照在 → 三组都出（含跨批组），且只碰了 scroll。"""
    raw_dir = _write_snapshot(tmp_path)
    code = _run_main(monkeypatch, sample_points(), ["--day", DAY], raw_dir)
    out = capsys.readouterr().out
    assert code == 0
    assert "same-run/unknown-batch" not in out  # 只有快照缺失才会出现这一组
    assert probe.CROSS_BATCH in out
    assert out.count("pairs=") == 3
    assert "refs_unmatched=0" in out


def test_main_reports_unknown_when_the_snapshot_is_gone(monkeypatch, tmp_path, capsys):
    """快照被覆盖的日期（ADR-14）：批次算不出来就单列，不假装算出来了。"""
    code = _run_main(monkeypatch, sample_points(), [], tmp_path / "raw")
    out = capsys.readouterr().out
    assert code == 0
    assert "same-run/unknown-batch" in out
    assert f"batches=? materials=?" in out
    assert "refs_unmatched=4" in out


def test_main_returns_2_when_qdrant_is_unreachable(monkeypatch, tmp_path, capsys):
    def boom(**_kwargs):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(probe, "QdrantClient", boom)
    code = probe.main(["--raw-dir", str(tmp_path)])
    assert code == 2
    assert "make up" in capsys.readouterr().err


def test_main_returns_2_for_a_day_that_is_not_in_the_library(monkeypatch, tmp_path, capsys):
    code = _run_main(monkeypatch, sample_points(), ["--day", "1999-01-01"], tmp_path)
    assert code == 2
    assert "1999-01-01" in capsys.readouterr().err
