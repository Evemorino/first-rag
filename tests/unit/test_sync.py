"""T019 unit tests for src/sync.py (collect → distill → ingest + lock + retention).

All external effects are faked: gather/distill/upsert are monkeypatched,
so these tests verify orchestration, locking, and retention only.
"""

import json
import sys
from datetime import date, datetime, timedelta, timezone

import pytest

from src import collect, config, distill, ingest, sync
from src.collect import DayRaw
from src.ingest import Entry, Report
from src.plugins import RawMaterial

DAY = date(2026, 9, 20)


@pytest.fixture
def pipeline(tmp_data_dir, monkeypatch):
    """Fake the three pipeline stages and record the call order."""
    calls = []

    def fake_gather(day, scope=None, **kwargs):
        calls.append(("gather", day, scope, kwargs))
        return DayRaw(
            day=day,
            collected_at=datetime(2026, 9, 20, 12, 0, 0),
            materials=[RawMaterial(
                source="claude_code", ref="s1",
                ts=datetime(2026, 9, 20, 10, 0, 0),
                kind="message", text="hello")],
        )

    def fake_distill(day_raw):
        calls.append(("distill", day_raw.day))
        return [make_entry(day_raw.day)]

    def fake_upsert(entries):
        calls.append(("upsert", len(entries)))
        return Report(upserted=len(entries))

    monkeypatch.setattr(sync.collect, "gather", fake_gather)
    monkeypatch.setattr(sync.distill, "distill", fake_distill)
    monkeypatch.setattr(sync.ingest, "upsert", fake_upsert)
    return calls


def make_entry(day: date, source: str = "claude_code") -> Entry:
    return Entry(
        text="distilled lesson",
        date=day.isoformat(),
        type="progress",
        tags=["t"],
        source=source,
        project=None,
        created_at=datetime(2026, 9, 20, 12, 0, 0),
        source_refs=["s1"],
        distill_version="test+rubric@abcd1234",
    )


def make_material(source: str, ref: str = "s1") -> RawMaterial:
    return RawMaterial(
        source=source,
        ref=ref,
        ts=datetime(2026, 9, 20, 10, 0, 0),
        kind="message",
        text="hello",
    )


# --- pipeline orchestration ---


def test_run_chains_gather_distill_ingest_in_order(pipeline):
    summary = sync.run(DAY)

    assert [name for name, *_ in pipeline] == ["gather", "distill", "upsert"]
    assert pipeline[0][1] == DAY
    assert summary == {
        "date": "2026-09-20",
        "materials": 1,
        "entries": 1,
        "upserted": 1,
        "raw_removed": 0,
        "materials_by_source": {"claude_code": 1},
        "entries_by_source": {"claude_code": 1},
        "skipped_by_source": {},
    }


def test_run_defaults_to_today(pipeline):
    from src import config as cfg

    sync.run(None)
    today = datetime.now(tz=cfg.TZ).date()
    assert pipeline[0][1] == today


# --- 按源记账（断链排查补的）---
#
# 光有总数看不出"谁采到了却没能进库"：09-25 那次 summary 是 53 素材 / 30 条目，
# 数字都对，qoder 采到了却被熔断饿死这件事一个数都看不出来 —— 最后是翻库加重跑
# discover 才查明的。这两个键让"采到的源"与"进库的源"能直接对账。


def test_summary_accounts_materials_and_entries_by_source(pipeline, monkeypatch):
    monkeypatch.setattr(sync.collect, "gather", lambda day, scope=None, **kw: DayRaw(
        day=day,
        collected_at=datetime(2026, 9, 20, 12, 0, 0),
        materials=[make_material("claude_code", "a"),
                   make_material("claude_code", "b"),
                   make_material("qoder", "c")]))
    monkeypatch.setattr(sync.distill, "distill",
                        lambda day_raw: [make_entry(day_raw.day)])

    summary = sync.run(DAY)

    # 素材侧两个源都在，条目侧只剩 claude_code —— qoder 半路掉了，一眼可见。
    assert summary["materials_by_source"] == {"claude_code": 2, "qoder": 1}
    assert summary["entries_by_source"] == {"claude_code": 1}


def test_summary_lists_the_biggest_source_first(pipeline, monkeypatch):
    """多的源排前面：熔断按序砍尾时，第一眼要看见谁吃掉了当天的额度。

    顺序是契约的一部分（谁读 summary 都是拿眼睛读的），所以按条数降序、
    同数按名字升序 —— 定死顺序，重排了就得有人说话。
    """
    monkeypatch.setattr(sync.collect, "gather", lambda day, scope=None, **kw: DayRaw(
        day=day,
        collected_at=datetime(2026, 9, 20, 12, 0, 0),
        materials=[make_material("codex", "a"),
                   make_material("claude_code", "b"),
                   make_material("claude_code", "c"),
                   make_material("claude_code", "d")]))

    summary = sync.run(DAY)

    assert list(summary["materials_by_source"]) == ["claude_code", "codex"]


def test_summary_orders_equal_sources_by_name_not_by_first_seen(pipeline, monkeypatch):
    """条数优先于名字；条数并列时按名字升序，**不按"谁先出现"**。

    上面那条一个人测不出来：夹具里"字母序"恰好与"条数序"一致，于是把 key
    整个丢掉、或并列时拿条数比，三种写法给出同一个答案 —— 那三个变异体因此
    全活着。这条故意让两个顺序打架。

    对应三个存活体：`sync.x__by_source__mutmut_5` / `_7`（丢了 key，退化成按
    名字排）与 `_11`（并列时拿条数比，退化成"先来后到"）。
    """
    monkeypatch.setattr(sync.collect, "gather", lambda day, scope=None, **kw: DayRaw(
        day=day,
        collected_at=datetime(2026, 9, 20, 12, 0, 0),
        # 出现顺序是 zeta, beta, alpha —— 与"按名字"和"按条数"都不同。
        materials=[make_material("zeta", "a"),
                   make_material("zeta", "b"),
                   make_material("zeta", "c"),
                   make_material("beta", "d"),
                   make_material("alpha", "e")]))

    summary = sync.run(DAY)

    assert list(summary["materials_by_source"]) == ["zeta", "alpha", "beta"]


# --- file lock (F2) ---


def test_lock_is_held_while_pipeline_runs(pipeline):
    """Inside gather the lock must already block a second acquisition."""
    observed = {}

    def probing_gather(day, scope=None, **kwargs):
        try:
            with sync._lock(config.SYNC_LOCK_PATH):
                observed["second"] = "acquired"
        except sync.SyncInProgressError:
            observed["second"] = "rejected"
        return DayRaw(day=day, collected_at=datetime.now())

    pipeline and None  # keep fixture ordering side effects
    sync.collect.gather = probing_gather
    sync.run(DAY)
    assert observed["second"] == "rejected"


def test_second_sync_rejected_immediately(pipeline):
    with sync._lock(config.SYNC_LOCK_PATH):
        with pytest.raises(sync.SyncInProgressError) as excinfo:
            sync.run(DAY)

    # 锁路径必须写进消息：并发撞锁时，用户得知道去哪个文件删。
    assert str(excinfo.value) == (
        f"another sync is already running (lock: {config.SYNC_LOCK_PATH})"
    )
    # after release the sync can run again
    assert sync.run(DAY)["upserted"] == 1


def test_lock_released_after_failed_pipeline(pipeline, monkeypatch):
    def boom(entries):
        raise RuntimeError("qdrant down")

    monkeypatch.setattr(sync.ingest, "upsert", boom)
    with pytest.raises(RuntimeError) as excinfo:
        sync.run(DAY)

    # 原样向上抛，不许包装成 SyncError —— 真正的故障原因不能被吞掉。
    assert str(excinfo.value) == "qdrant down"
    # lock must not stay behind after a crash inside the pipeline
    with sync._lock(config.SYNC_LOCK_PATH):
        pass


# --- retention (FR-022) ---


@pytest.fixture
def raw_dir(tmp_data_dir):
    raw = config.RAW_DIR
    raw.mkdir(parents=True, exist_ok=True)
    return raw


def write_snapshot(raw_dir, name: str) -> None:
    (raw_dir / name).write_text("{}", encoding="utf-8")


def test_cleanup_removes_only_expired_snapshots(raw_dir, monkeypatch):
    monkeypatch.setattr(sync.config, "load_schema",
                        lambda: {"raw_retention_days": 90})
    write_snapshot(raw_dir, "2026-09-20.json")   # today
    write_snapshot(raw_dir, "2026-07-01.json")   # 81 days old: keep
    write_snapshot(raw_dir, "2026-05-01.json")   # 142 days old: delete
    write_snapshot(raw_dir, "not-a-date.json")   # never touched

    removed = sync.cleanup_raw(now=date(2026, 9, 20))

    assert removed == ["2026-05-01.json"]
    assert (raw_dir / "2026-09-20.json").exists()
    assert (raw_dir / "2026-07-01.json").exists()
    assert (raw_dir / "not-a-date.json").exists()


def test_cleanup_zero_retention_removes_all_past_days(raw_dir, monkeypatch):
    monkeypatch.setattr(sync.config, "load_schema",
                        lambda: {"raw_retention_days": 0})
    write_snapshot(raw_dir, "2026-09-20.json")
    write_snapshot(raw_dir, "2026-09-19.json")

    removed = sync.cleanup_raw(now=date(2026, 9, 20))

    assert removed == ["2026-09-19.json"]
    assert (raw_dir / "2026-09-20.json").exists()


def test_cleanup_none_retention_keeps_everything(raw_dir, monkeypatch):
    monkeypatch.setattr(sync.config, "load_schema",
                        lambda: {"raw_retention_days": None})
    write_snapshot(raw_dir, "2020-01-01.json")

    assert sync.cleanup_raw(now=date(2026, 9, 20)) == []
    assert (raw_dir / "2020-01-01.json").exists()


def test_run_invokes_retention_cleanup(pipeline, raw_dir, monkeypatch):
    monkeypatch.setattr(sync.config, "load_schema",
                        lambda: {"raw_retention_days": 0})
    write_snapshot(raw_dir, "2026-09-19.json")

    summary = sync.run(DAY)

    assert summary["raw_removed"] == 1
    assert not (raw_dir / "2026-09-19.json").exists()


# --- scope.json (FR-005, feeding T035) ---


def test_run_passes_scope_json_to_gather(pipeline, tmp_data_dir, monkeypatch):
    scope = {"tools": {"claude_code": False}, "projects": {}}
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_data_dir / "config")
    (tmp_data_dir / "config").mkdir()
    (tmp_data_dir / "config" / "scope.json").write_text(
        json.dumps(scope), encoding="utf-8")

    sync.run(DAY)

    assert pipeline[0][2] == scope


def test_run_ignores_broken_scope_json(pipeline, tmp_data_dir, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_data_dir / "config")
    (tmp_data_dir / "config").mkdir()
    (tmp_data_dir / "config" / "scope.json").write_text("{oops", encoding="utf-8")

    sync.run(DAY)

    assert pipeline[0][2] is None


def test_run_without_scope_json_passes_none(pipeline):
    sync.run(DAY)
    assert pipeline[0][2] is None


# --- 快照写保护的明路（ALLOW_SHRINK=1）---
#
# 写保护本身在 `collect.save_snapshot`；这里测的是**它接得上 CLI** ——
# 一道拦得住但没法放行的闸门，最后只会逼人去删文件，而删文件更糟。


def test_run_forwards_allow_shrink_to_gather(pipeline):
    sync.run(DAY, allow_shrink=True)

    assert pipeline[0][3] == {"allow_shrink": True}


def test_run_defaults_to_not_allowing_shrink(pipeline):
    """默认必须是**不让**——防护的意义就在默认那一边。"""
    sync.run(DAY)

    assert pipeline[0][3] == {"allow_shrink": False}


def test_parse_allow_shrink_accepts_the_make_style_flag():
    assert sync._parse_allow_shrink(["sync", "ALLOW_SHRINK=1"]) is True


def test_parse_allow_shrink_is_false_when_absent():
    assert sync._parse_allow_shrink(["sync"]) is False


def test_allow_shrink_flag_coexists_with_the_date_flag():
    """`make sync D=2026-09-18 ALLOW_SHRINK=1` —— 两个参数得能同时出现。"""
    argv = ["sync", "D=2026-09-18", "ALLOW_SHRINK=1"]

    assert sync._parse_day(argv) == date(2026, 9, 18)
    assert sync._parse_allow_shrink(argv) is True


# --- CLI entry (FR-025) ---


def test_main_parses_d_equals_date(pipeline, monkeypatch):
    code = sync.main(["sync", "D=2026-09-18"])
    assert code == 0
    assert pipeline[0][1] == date(2026, 9, 18)


def test_main_parses_plain_date(pipeline):
    assert sync.main(["sync", "2026-09-18"]) == 0
    assert pipeline[0][1] == date(2026, 9, 18)


def test_main_defaults_to_today(pipeline):
    assert sync.main(["sync"]) == 0
    assert pipeline[0][1] == datetime.now(tz=config.TZ).date()


def test_main_exits_nonzero_on_pipeline_failure(pipeline, monkeypatch):
    monkeypatch.setattr(sync.ingest, "upsert",
                        lambda entries: (_ for _ in ()).throw(RuntimeError("x")))
    assert sync.main(["sync"]) == 1


def test_main_exits_nonzero_when_sync_already_running(pipeline):
    # 退出码要能区分"有人在跑"(2) 和"跑失败了"(1)：cron 撞锁是正常情况，
    # 不该和真故障混成一个码。断言写成 != 0 的话，2 改成 3 也没人发现。
    with sync._lock(config.SYNC_LOCK_PATH):
        assert sync.main(["sync"]) == 2


def test_main_prints_summary_json_to_stdout(pipeline, capsys):
    """stdout 是给 shell/Makefile 消费的（make sync | jq），格式就是契约。"""
    assert sync.main(["sync", "D=2026-09-18"]) == 0

    assert json.loads(capsys.readouterr().out) == {
        "date": "2026-09-18",
        "materials": 1,
        "entries": 1,
        "upserted": 1,
        "raw_removed": 0,
        "materials_by_source": {"claude_code": 1},
        "entries_by_source": {"claude_code": 1},
        "skipped_by_source": {},
    }


def test_summary_reports_entries_skipped_by_novelty(pipeline, monkeypatch):
    """入库侧被新颖度拦掉的条目要出现在汇总里（AC-015 的另一半）。

    `per_source`（快照，蒸馏侧）说「蒸馏产出了几条」，这里说「其中几条没进库」——
    两者合起来才能把「有素材 → 0 点」拆成蒸馏侧还是入库侧的问题。
    """
    monkeypatch.setattr(
        sync.ingest,
        "upsert",
        lambda entries: Report(upserted=0, skipped_by_source={"claude_code": 1}),
    )

    summary = sync.run(DAY)

    assert summary["skipped_by_source"] == {"claude_code": 1}


def test_main_rejects_invalid_date(pipeline):
    assert sync.main(["sync", "D=not-a-date"]) == 1


def test_main_reads_sys_argv_when_argv_not_passed(pipeline, monkeypatch):
    """`main()` 不传参数时必须去读 sys.argv —— `make sync D=…` 走的就是这条路。

    这条在变异测试里是存活的（`sys.argv if (argv is None) and False else argv`
    把条件恒判成假，于是永远用传入的 argv）。其余 main 测试全都显式传了
    `["sync", …]`，所以"从真实命令行取参数"这一整条路径其实一个测试都没有 ——
    测试名里带 main，看着像覆盖了，实际覆盖的是另一件事。
    """
    monkeypatch.setattr(sys, "argv", ["sync", "D=2026-09-18"])

    assert sync.main() == 0
    assert pipeline[0][1] == date(2026, 9, 18)


# --- 变异测试分诊后补的：时区与"坏数据在前" ---


class _UtcLocalClock:
    """假装进程本地时区是 UTC 的钟。

    本机时区恰好也是 +08，所以 `datetime.now(tz=config.TZ)` 改成 `tz=None`
    在真实钟上给出**同一个日期** —— 那 3 个变异体因此天然杀不掉，而 PRD 的
    "归属日按 Asia/Shanghai 算"其实一条都没被测过。用这个钟把两个时区拆开：
    2026-09-20T17:00Z 在上海已经是 09-21。
    """

    INSTANT = datetime(2026, 9, 20, 17, 0, tzinfo=timezone.utc)

    @classmethod
    def now(cls, tz=None):
        return cls.INSTANT.astimezone(tz) if tz else cls.INSTANT.replace(tzinfo=None)


def test_run_defaults_to_the_shanghai_day_not_the_local_one(pipeline, monkeypatch):
    """不传日期时，归属日必须由 Asia/Shanghai 决定。"""
    monkeypatch.setattr(sync, "datetime", _UtcLocalClock)

    assert sync.main(["sync"]) == 0
    assert pipeline[0][1] == date(2026, 9, 21)


def test_cleanup_raw_cutoff_uses_the_shanghai_day(raw_dir, monkeypatch):
    """retention=0 时，"上海已是明天"就该让昨天的快照过期。

    同一个钟下，naive 本地时间还停在 09-20，于是 `tz=None` 的版本不会删这个文件
    —— 这条断言测的是 cutoff 到底按哪个时区算，不是测"删了几个"。
    """
    monkeypatch.setattr(sync, "datetime", _UtcLocalClock)
    write_snapshot(raw_dir, "2026-09-20.json")

    removed = sync.cleanup_raw(retention_days=0, raw_dir=raw_dir)

    assert removed == ["2026-09-20.json"]


def test_cleanup_raw_keeps_scanning_past_an_unparsable_day_file(raw_dir, tmp_path):
    """坏日期在前、过期快照在后：`continue` 改成 `break` 就会漏删。

    对应存活的 sync.x_cleanup_raw__mutmut_23。两个坑都在这条上：
    ① 名字必须**匹配** `????-??-??.json` 这个 glob，否则压根进不了循环
      （`not-a-date.json` 就是错的夹具 —— 它测不到任何东西）；
    ② 坏名字必须**排在前面**，否则 break 与 continue 的结果仍然一样。
    """
    write_snapshot(raw_dir, "0000-00-00.json")
    write_snapshot(raw_dir, "2026-09-18.json")

    removed = sync.cleanup_raw(
        now=date(2026, 9, 20), retention_days=0, raw_dir=raw_dir)

    assert removed == ["2026-09-18.json"]
    assert (raw_dir / "0000-00-00.json").exists()  # 读不懂的不删，但也不能终止扫描


def test_run_hands_cleanup_raw_the_shanghai_day(pipeline, monkeypatch):
    """sync.run 传给 cleanup_raw 的"今天"也必须按上海时区算。

    对应存活的 sync.x_run__mutmut_16（`cleanup_raw(now=datetime.now(tz=None)...)`）。
    上面那条测的是 run 自己的归属日，这一条测的是**传给清理的那颗钟**：两者是
    不同的调用点，只测一个的话另一个照样能错 —— 错完昨天的 raw 会被多留一天。
    """
    seen = {}
    monkeypatch.setattr(sync, "datetime", _UtcLocalClock)
    monkeypatch.setattr(
        sync, "cleanup_raw",
        lambda **kw: seen.update(kw) or [])

    assert sync.main(["sync"]) == 0
    assert seen["now"] == date(2026, 9, 21)
