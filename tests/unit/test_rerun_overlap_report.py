"""`scripts/rerun_overlap_report.py` 的单元测试。

这个脚本要回答的是「同日重跑多出来的条目算不算近似重复」—— 而 FR-007/AC-003
的裁决就压在这个答案上。所以四件事必须测准：**比较的是谁**（跨运行那一组必须
排除本簇，与 `filter_novel` 同口径）、**对照组是另一组**（簇内近邻单独算，
不能被跨簇的分数顶替）、**阈值是严格大于**（与 `filter_novel` 的 `>` 一致）、
以及**连不上 Qdrant 时给出路而不是栈**（它是给人做裁决前跑的工具）。
"""

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import rerun_overlap_report as report  # noqa: E402  (先补 sys.path 才能导入)


class FakePoint:
    def __init__(self, point_id: str, vector: list[float], payload: dict):
        self.id = point_id
        self.vector = vector
        self.payload = payload


class FakeHit:
    def __init__(self, point_id: str, score: float):
        self.id = point_id
        self.score = score


class FakeResponse:
    def __init__(self, hits: list[FakeHit]):
        self.points = hits


class FakeClient:
    """只实现本脚本用到的两个只读方法。`fail` 模拟 Qdrant 没起。"""

    def __init__(self, points: list[FakePoint], fail: bool = False):
        self._points = points
        self._fail = fail
        self.scroll_calls = 0
        self.query_calls = 0

    def scroll(self, collection, limit=1000, offset=None, with_payload=True,
               with_vectors=True):
        if self._fail:
            raise RuntimeError("connection refused")
        self.scroll_calls += 1
        return self._points, None

    def query_points(self, collection_name, query, limit=2, with_payload=False):
        self.query_calls += 1
        scored = [
            (report.cosine(query, p.vector), p.id) for p in self._points
        ]
        scored.sort(reverse=True)
        return FakeResponse([FakeHit(pid, score) for score, pid in scored[:limit]])


def point(date: str, vector: list[float], created_at: str | None = "2026-09-24T22:52:19"):
    payload = {"date": date}
    if created_at is not None:
        payload["created_at"] = created_at
    return FakePoint(f"{date}-{id(vector)}", vector, payload)


# --- cosine -----------------------------------------------------------------


def test_cosine_of_identical_vectors_is_one():
    assert report.cosine([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == 1.0


def test_cosine_of_orthogonal_vectors_is_zero():
    assert report.cosine([1.0, 0.0], [0.0, 1.0]) == 0.0


def test_cosine_of_opposite_vectors_is_minus_one():
    assert report.cosine([1.0, 0.0], [-1.0, 0.0]) == -1.0


def test_cosine_returns_zero_when_either_vector_is_zero():
    """零向量没有方向 —— 不能除出 nan 来污染整张报告。"""
    assert report.cosine([0.0, 0.0], [1.0, 1.0]) == 0.0
    assert report.cosine([1.0, 1.0], [0.0, 0.0]) == 0.0


def test_cosine_is_scale_invariant():
    """cosine 的意义就在于与长度无关：缩放不该改变读数。"""
    assert report.cosine([1.0, 1.0], [10.0, 10.0]) == 1.0


# --- percentile -------------------------------------------------------------


def test_percentile_uses_floor_index():
    """口径固定成 int(q*(n-1))：四个数的中位数取下标 1（不是下标 2 的那种取法）。"""
    assert report.percentile([1.0, 2.0, 3.0, 4.0], 0.5) == 2.0
    assert report.percentile([1.0, 2.0, 3.0, 4.0], 0.0) == 1.0
    assert report.percentile([1.0, 2.0, 3.0, 4.0], 1.0) == 4.0


def test_percentile_of_one_element_is_that_element():
    assert report.percentile([7.5], 0.5) == 7.5
    assert report.percentile([7.5], 1.0) == 7.5


def test_percentile_of_nothing_is_nan():
    value = report.percentile([], 0.5)
    assert value != value  # nan


# --- 运行指纹 ---------------------------------------------------------------


def test_run_key_truncates_created_at_to_seconds():
    assert report.run_key({"created_at": "2026-09-24T22:52:19.123456"}) == "2026-09-24T22:52:19"


def test_run_key_falls_back_to_unknown_instead_of_dropping_the_point():
    assert report.run_key({}) == "<unknown>"


def test_group_runs_splits_by_created_at_and_keeps_order():
    groups = report.group_runs(
        [
            {"created_at": "2026-09-24T22:52:19.1"},
            {"created_at": "2026-09-24T23:41:25.0"},
            {"created_at": "2026-09-24T22:52:19.9"},
        ]
    )
    assert groups == {"2026-09-24T22:52:19": [0, 2], "2026-09-24T23:41:25": [1]}


# --- 跨运行最近邻（本脚本的核心口径）----------------------------------------


def test_external_nn_ignores_points_inside_the_same_run():
    """本簇内的近邻**不算** —— `filter_novel` 正是把本批排除在比较之外的。

    构造：簇 A 里两条几乎同向（同簇相似度 ~1.0），簇 B 里一条与它们正交。
    A 的跨簇最近邻应当是 0.0（来自 B），而不是簇内那 1.0。
    """
    vectors = [[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]]
    groups = {"A": [0, 1], "B": [2]}
    assert report.external_nn_scores(vectors, groups) == [0.0, 0.0, 0.0]


def test_external_nn_reports_the_best_other_run_neighbour():
    """跨簇最近邻取的是**另一**簇里的最好成绩，同簇的更高分不算数。"""
    vectors = [[1.0, 0.0], [0.9, 0.1], [0.0, 1.0]]
    groups = {"A": [0, 2], "B": [1]}
    scores = report.external_nn_scores(vectors, groups)
    assert scores[0] == pytest.approx(report.cosine([1.0, 0.0], [0.9, 0.1]))
    assert scores[1] == pytest.approx(report.cosine([0.9, 0.1], [1.0, 0.0]))  # 对上了 A 的第 0 条
    assert scores[2] == pytest.approx(report.cosine([0.0, 1.0], [0.9, 0.1]))
    assert scores[1] > scores[2]  # 两条都在 A 里，但 B 那条只与其中一条像


def test_external_nn_is_empty_when_there_is_only_one_run():
    """只有一次运行就没有"跨运行"可言 —— 空列表比编一个 0.0 更诚实。"""
    assert report.external_nn_scores([[1.0, 0.0], [0.0, 1.0]], {"A": [0, 1]}) == []


# --- 同一次运行内部的最近邻（对照组：阈值不该碰的地方）----------------------


def test_internal_nn_ignores_points_from_other_runs():
    """跨簇的近邻**不算** —— 这一组要的是"同一次蒸馏写出的条目之间有多像"。

    它们是必须共存的不同条目，理论上界就是判据不该碰的地方。构造：簇 A 内两条
    互相正交（簇内相似度 0.0），簇 B 里有一条与 A 的第 0 条同向 —— 簇内的读数
    必须是那个 0.0，而不是跨簇的 1.0。
    """
    vectors = [[1.0, 0.0], [0.0, 1.0], [1.0, 0.0]]
    groups = {"A": [0, 1], "B": [2]}
    assert report.internal_nn_scores(vectors, groups) == [0.0, 0.0]


def test_internal_nn_reports_the_best_same_run_neighbour():
    """簇内最近邻取的是**同一**簇里的最好成绩，不管它是哪一条给的。"""
    vectors = [[1.0, 0.0], [0.9, 0.1], [0.0, 1.0]]
    groups = {"A": [0, 1, 2]}
    scores = report.internal_nn_scores(vectors, groups)
    assert scores[0] == pytest.approx(report.cosine([1.0, 0.0], [0.9, 0.1]))
    assert scores[1] == pytest.approx(report.cosine([0.9, 0.1], [1.0, 0.0]))
    assert scores[2] == pytest.approx(report.cosine([0.0, 1.0], [0.9, 0.1]))
    assert scores[0] > scores[2]  # 第 2 条与谁都近得有限，另两条几乎同向


def test_internal_nn_is_empty_when_every_run_is_a_singleton():
    """每条各自成簇就没有"簇内其它条目"可言 —— 与 external 同一条规矩。"""
    vectors = [[1.0, 0.0], [1.0, 0.0]]
    assert report.internal_nn_scores(vectors, {"A": [0], "B": [1]}) == []


# --- summarize / max_abs_delta ---------------------------------------------


def test_summarize_counts_above_threshold_strictly():
    """阈值上的相等**不算**超 —— 必须与 `filter_novel` 的严格 `>` 一致。"""
    summary = report.summarize([0.82, 0.8200001, 0.9], 0.82)
    assert summary["above"] == 2
    assert summary["n"] == 3
    assert summary["max"] == 0.9


def test_summarize_of_nothing_is_nan_not_zero():
    summary = report.summarize([], 0.82)
    assert summary["n"] == 0
    assert summary["max"] != summary["max"]
    assert summary["above"] == 0


def test_max_abs_delta():
    assert report.max_abs_delta([(0.5, 0.5001), (0.2, 0.2)]) == pytest.approx(1e-4)
    assert report.max_abs_delta([]) == 0.0


# --- analyze ----------------------------------------------------------------


def test_analyze_defaults_to_the_day_with_most_points():
    points = [
        point("2026-09-23", [1.0, 0.0]),
        point("2026-09-24", [1.0, 0.0], "2026-09-24T22:52:19"),
        point("2026-09-24", [0.0, 1.0], "2026-09-24T23:41:25"),
    ]
    rows, day, detail = report.analyze(points, 0.82)
    assert day == "2026-09-24"
    assert {r["date"] for r in rows} == {"2026-09-23", "2026-09-24"}
    assert any("2 次运行" in line for line in detail)


def test_analyze_reports_the_run_count_per_day():
    points = [
        point("2026-09-24", [1.0, 0.0], "2026-09-24T22:52:19"),
        point("2026-09-24", [0.0, 1.0], "2026-09-24T22:52:19"),
        point("2026-09-24", [1.0, 1.0], "2026-09-24T23:41:25"),
    ]
    rows, _, _ = report.analyze(points, 0.82)
    assert [r["runs"] for r in rows] == [2]


def test_analyze_keeps_the_internal_and_external_neighbours_apart():
    """两天各一条不能顶替：IN 记的是簇内，EX 记的是跨运行 —— 两者可以完全不同。"""
    points = [
        point("2026-09-24", [1.0, 0.0], "2026-09-24T22:52:19"),
        point("2026-09-24", [0.0, 1.0], "2026-09-24T22:52:19"),
        point("2026-09-24", [1.0, 1.0], "2026-09-24T23:41:25"),
    ]
    rows, _, detail = report.analyze(points, 0.82)
    row = rows[0]
    assert row["internal"]["n"] == 2  # 第一批那两条互相有伴
    assert row["external"]["n"] == 3  # 三条都能在另一批里找到近邻
    assert any("同一次运行内部最近邻" in line for line in detail)
    assert any("跨运行最近邻" in line for line in detail)


def test_analyze_counts_the_day_points_not_the_scored_pairs():
    """`pts` 列是当天的点数：孤条不进 IN 的分母，但它仍占一天一条。"""
    points = [
        point("2026-09-24", [1.0, 0.0], "2026-09-24T22:52:19"),
        point("2026-09-24", [0.0, 1.0], "2026-09-24T23:41:25"),
        point("2026-09-24", [0.5, 0.5], "2026-09-24T23:59:59"),
    ]
    rows, _, _ = report.analyze(points, 0.82)
    assert rows[0]["points"] == 3
    assert rows[0]["internal"]["n"] == 0  # 三次运行各一条，簇内谁都没伴


def test_analyze_with_an_unknown_day_returns_no_detail():
    points = [point("2026-09-24", [1.0, 0.0])]
    rows, day, detail = report.analyze(points, 0.82, day="2026-01-01")
    assert day is None
    assert detail == []
    assert len(rows) == 1


def test_analyze_of_no_points_is_an_empty_report():
    assert report.analyze([], 0.82) == ([], None, [])


# --- render_overview --------------------------------------------------------


def test_render_overview_shows_in_and_ex_side_by_side():
    """两组必须并排出现，并带上读法：只给一组数字时"该降阈值吗"无从判断。"""
    rows = [
        {
            "date": "2026-09-24",
            "runs": 3,
            "points": 87,
            "internal": report.summarize([0.12, 0.75], 0.82),
            "external": report.summarize([0.2, 0.81], 0.82),
        }
    ]
    text = "\n".join(report.render_overview(rows, 0.82))
    assert "IN max" in text and "EX max" in text
    assert "87" in text
    assert "0.750" in text and "0.810" in text
    assert "先看 IN 再看 EX" in text


def test_render_overview_marks_the_external_columns_na_for_a_single_run_day():
    """一天只跑过一次时没有"跨运行"可比 —— 记 n/a，而不是编一个 0.0 出来。"""
    rows = [
        {
            "date": "2026-09-18",
            "runs": 1,
            "points": 27,
            "internal": report.summarize([0.3, 0.836], 0.82),
            "external": report.summarize([], 0.82),
        }
    ]
    text = "\n".join(report.render_overview(rows, 0.82))
    assert "n/a" in text
    assert "0.836" in text  # 簇内那一列照样有数


# --- main（含"连不上"的出路）------------------------------------------------


def test_main_prints_the_report_and_exits_zero(monkeypatch, capsys):
    client = FakeClient(
        [
            point("2026-09-24", [1.0, 0.0], "2026-09-24T22:52:19"),
            point("2026-09-24", [1.0, 0.0], "2026-09-24T23:41:25"),
        ]
    )
    monkeypatch.setattr(report, "QdrantClient", lambda **kwargs: client)
    assert report.main([]) == 0
    out = capsys.readouterr().out
    assert "2026-09-24" in out
    assert "novelty_threshold" in out


def test_main_uses_the_threshold_from_the_schema_by_default(monkeypatch, capsys):
    client = FakeClient([point("2026-09-24", [1.0, 0.0])])
    monkeypatch.setattr(report, "QdrantClient", lambda **kwargs: client)
    monkeypatch.setattr(report.config, "load_schema", lambda: {"distill": {"novelty_threshold": 0.77}})
    assert report.main([]) == 0
    assert "0.77" in capsys.readouterr().out


def test_main_threshold_flag_overrides_the_schema(monkeypatch, capsys):
    """试算另一个阈值时**不能**改配置：配置是 schema.json 的事。"""
    client = FakeClient([point("2026-09-24", [1.0, 0.0])])
    monkeypatch.setattr(report, "QdrantClient", lambda **kwargs: client)
    monkeypatch.setattr(report.config, "load_schema", lambda: {"distill": {"novelty_threshold": 0.77}})
    assert report.main(["--threshold", "0.5"]) == 0
    assert "0.5" in capsys.readouterr().out


def test_main_exits_two_with_a_way_out_when_qdrant_is_down(monkeypatch, capsys):
    monkeypatch.setattr(report, "QdrantClient", lambda **kwargs: FakeClient([], fail=True))
    assert report.main([]) == 2
    err = capsys.readouterr().err
    assert "make up" in err


def test_main_says_so_when_the_collection_is_empty(monkeypatch, capsys):
    monkeypatch.setattr(report, "QdrantClient", lambda **kwargs: FakeClient([]))
    assert report.main([]) == 0
    assert "没有点" in capsys.readouterr().out


def test_main_cross_checks_the_local_cosine_against_qdrant(monkeypatch, capsys):
    """自校走通了就把它打出来 —— 数字能不能与判据直接比，靠这一行交代。"""
    client = FakeClient(
        [
            point("2026-09-24", [1.0, 0.0]),
            point("2026-09-24", [0.0, 1.0]),
            point("2026-09-24", [1.0, 1.0]),
        ]
    )
    monkeypatch.setattr(report, "QdrantClient", lambda **kwargs: client)
    assert report.main([]) == 0
    out = capsys.readouterr().out
    assert "最大偏差 0.00e+00" in out
    assert client.query_calls > 0


def test_main_survives_a_failing_cross_check(monkeypatch, capsys):
    """自校只是佐证 —— 它炸了不能把主报告一起带走。"""

    class HalfBroken(FakeClient):
        def query_points(self, *args, **kwargs):
            raise RuntimeError("query not supported")

    monkeypatch.setattr(
        report, "QdrantClient",
        lambda **kwargs: HalfBroken(
            [point("2026-09-24", [1.0, 0.0]), point("2026-09-24", [0.0, 1.0])]
        ),
    )
    assert report.main([]) == 0
    out = capsys.readouterr().out
    assert "自校失败" in out
    assert "2026-09-24" in out
