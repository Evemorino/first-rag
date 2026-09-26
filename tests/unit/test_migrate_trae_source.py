"""scripts/migrate_trae_source.py 的单测。

改名迁移里"真正会出错"的部分——新 ID 算得对不对、快照标签改没改全、迁移
有没有静默丢条目——全都能用纯函数表达，所以它们被拆出来单独测。
碰 Qdrant 的 IO 在 main() 里，不在单测射程内。

为什么这件事值得单测：条目 ID = uuid5(source|date|content_hash)，改 source
等于换 ID。算错一个字段，那条素材就在库里变成两份（新 ID 一份、旧 ID 一份孤儿）
或者干脆消失。这两种都不会报错，只会安静地发生。
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import migrate_trae_source as migrate  # noqa: E402  (先补 sys.path 才能导入)
from src import ids  # noqa: E402


def _ref(memory_dir, line=0):
    """一条素材的 ref 形状（真实数据里的样子：目录 + 文件 + `#L<n>`）。"""
    return f"{memory_dir}/projects/some-slug/20260918/session_memory_abc.jsonl#L{line}"


def _payload(text="踩了个坑", date="2026-09-18", source="trae", **extra):
    extra.setdefault("source_refs", [_ref(migrate.OLD_MEMORY_DIR)])
    return {"source": source, "date": date, "text": text, **extra}


# --- 新 ID 怎么算 ---


def test_rekey_matches_the_public_id_contract():
    """迁移算出的新 ID 必须和 ingest 将来写进去的那个逐位相同。

    两边各算各的、算法悄悄分叉，是这类脚本最典型的坏法：迁移当天看不出问题，
    下次 sync 才按另一个 ID 重新入一份。
    """
    assert migrate.rekey(_payload()) == ids.point_id(
        "trae_work_cn", "2026-09-18", "踩了个坑")


def test_rekey_actually_changes_the_id():
    """防呆：如果新 ID 和旧 ID 一样，说明 source 根本没进 identity。"""
    old = ids.point_id("trae", "2026-09-18", "踩了个坑")

    assert migrate.rekey(_payload()) != old


# --- 哪些点需要迁移 ---


def test_plan_covers_only_the_old_source():
    points = [
        ("id-a", _payload()),
        ("id-b", _payload(source="trae_work_cn")),
        ("id-c", _payload(source="claude_code")),
    ]

    plan = migrate.plan_points(points)

    assert [p.old_id for p in plan] == ["id-a"]


def test_plan_is_empty_once_migrated():
    """幂等的前提：已经改过的点不再进迁移计划。"""
    assert migrate.plan_points([("x", _payload(source="trae_work_cn"))]) == []


def test_plan_carries_the_relabelled_payload():
    """写进库里的 payload 必须是改过 source 的那份。

    漏掉这一步会得到一个极隐蔽的坏状态：**ID 换了，标签没换**。
    条目数对、正文对、全库总数对 —— 三条校验里两条全绿，只有"按新来源
    查得到吗"会红。而如果没有那条，这个状态会被当成迁移成功，
    直到下次 sync 按新 ID 再入一份同一素材。
    """
    plan = migrate.plan_points([("id-a", _payload())])

    assert plan[0].payload["source"] == "trae_work_cn"


def test_plan_does_not_mutate_the_caller_payload():
    """dry-run 会先调 plan_points；它要是不小心就地改了 payload，
    dry-run 就成了真改。"""
    payload = _payload()

    migrate.plan_points([("id-a", payload)])

    assert payload["source"] == "trae"


def test_plan_refuses_when_the_target_id_already_exists():
    """目标 ID 已存在 = 库里同一条素材已经有两份了。

    这时候继续 upsert 只会把两份叠成一份，看起来"迁移成功"，实际丢了一份。
    宁可停下让人看一眼。
    """
    target = str(migrate.rekey(_payload()))
    points = [("old", _payload()), (target, _payload(source="trae_work_cn"))]

    with pytest.raises(migrate.MigrationError, match="already"):
        migrate.plan_points(points)


# --- 快照标签 ---


def test_relabel_snapshot_touches_only_the_old_source():
    doc = {"materials": [
        {"source": "trae", "ref": _ref(migrate.OLD_MEMORY_DIR)},
        {"source": "codex", "ref": "/x/y.jsonl#L0"},
        {"source": "trae", "ref": _ref(migrate.OLD_MEMORY_DIR, line=1)}]}

    new_doc, changed = migrate.relabel_snapshot(doc)

    assert changed == 2
    assert [m["source"] for m in new_doc["materials"]] == [
        "trae_work_cn", "codex", "trae_work_cn"]


def test_relabel_snapshot_is_idempotent():
    new_doc, changed = migrate.relabel_snapshot(
        {"materials": [{"source": "trae_work_cn",
                        "ref": _ref(migrate.OLD_MEMORY_DIR)}]})

    assert changed == 0
    assert new_doc["materials"][0]["source"] == "trae_work_cn"


def test_relabel_snapshot_leaves_everything_else_alone():
    """只动 source 这一个字段，其余原样——快照是审计基线，不是草稿。"""
    original = {
        "date": "2026-09-18",
        "distill_run": {"model": "m", "status": "ok"},
        "materials": [{"source": "trae", "text": "t", "meta": {"cwd": "/x"},
                       "ref": _ref(migrate.OLD_MEMORY_DIR)}],
    }

    new_doc, _ = migrate.relabel_snapshot(original)

    assert new_doc["date"] == "2026-09-18"
    assert new_doc["distill_run"] == {"model": "m", "status": "ok"}
    assert new_doc["materials"][0] == {
        "source": "trae_work_cn", "text": "t", "meta": {"cwd": "/x"},
        "ref": _ref(migrate.OLD_MEMORY_DIR)}


def test_relabel_snapshot_does_not_mutate_the_input():
    """改的是副本。传进去的原对象若被就地改掉，dry-run 会变成真改。"""
    original = {"materials": [{"source": "trae",
                               "ref": _ref(migrate.OLD_MEMORY_DIR)}]}

    migrate.relabel_snapshot(original)

    assert original["materials"][0]["source"] == "trae"


# --- 防护：`trae` 这名字被新插件合法复用了 ---
#
# v0.7.1 把旧的 `trae`（其实是 Trae CN 的数据，在 `~/.trae-cn/`）改名成
# `trae_work_cn`，把 `trae` 这个名字**让给了新插件**（读 `~/.trae/`）。
# 于是"source == 'trae'"不再是"待迁的旧数据"的同义词——只看名字的判据
# 会把新插件的素材错标成 `trae_work_cn`。判据必须是**路径**：
# 旧数据在 `~/.trae-cn/`，新数据在 `~/.trae/`。


def test_plan_excludes_trae_records_that_came_from_the_new_plugin():
    points = [
        ("old", _payload()),
        ("new", _payload(source_refs=[_ref(migrate.NEW_MEMORY_DIR)])),
    ]

    assert [p.old_id for p in migrate.plan_points(points)] == ["old"]


def test_plan_excludes_a_point_whose_refs_are_mixed():
    """指向两个目录的点是可疑的，宁可不动。"""
    mixed = _payload(source_refs=[_ref(migrate.OLD_MEMORY_DIR),
                                  _ref(migrate.NEW_MEMORY_DIR)])

    assert migrate.plan_points([("m", mixed)]) == []


def test_plan_excludes_a_point_with_no_refs_at_all():
    """证明不了"这是旧数据"时不许动它——ref 是 RawMaterial 的必填字段，
    真实数据里不会缺，缺了就是异常输入。"""
    assert migrate.plan_points([("bare", _payload(source_refs=[]))]) == []


def test_relabel_snapshot_leaves_new_plugin_materials_alone():
    doc = {"materials": [
        {"source": "trae", "ref": _ref(migrate.NEW_MEMORY_DIR)},
        {"source": "trae", "ref": _ref(migrate.OLD_MEMORY_DIR)}]}

    new_doc, changed = migrate.relabel_snapshot(doc)

    assert changed == 1
    assert [m["source"] for m in new_doc["materials"]] == [
        "trae", "trae_work_cn"]


def test_foreign_refs_names_every_ref_that_belongs_to_the_new_plugin():
    """`main` 靠它决定拒跑，并把"到底是哪几条"说清楚。"""
    points = [("new", _payload(source_refs=[_ref(migrate.NEW_MEMORY_DIR)]))]
    snapshots = [{"materials": [
        {"source": "trae", "ref": _ref(migrate.NEW_MEMORY_DIR, line=7)}]}]

    foreign = migrate.foreign_refs(points, snapshots)

    assert _ref(migrate.NEW_MEMORY_DIR) in foreign
    assert _ref(migrate.NEW_MEMORY_DIR, line=7) in foreign
    assert len(foreign) == 2


def test_foreign_refs_is_empty_on_a_clean_migration():
    assert migrate.foreign_refs([("old", _payload())], []) == []


def test_old_and_new_memory_dirs_are_actually_different():
    """防呆：两个常量若指向同一处，上面所有防护都会静默失效。"""
    assert migrate.OLD_MEMORY_DIR != migrate.NEW_MEMORY_DIR
    assert migrate.NEW_MEMORY_DIR.exists()


# --- 迁移后自证 ---


def test_verify_accepts_matching_fingerprints():
    before = [_payload(), _payload(text="另一条")]
    after = [_payload(source="trae_work_cn", text="另一条"),
             _payload(source="trae_work_cn")]

    migrate.verify(before, after, total_before=10, total_after=10)


def test_verify_rejects_a_dropped_point():
    with pytest.raises(migrate.MigrationError, match="count"):
        migrate.verify([_payload()], [], total_before=10, total_after=10)


def test_verify_rejects_a_changed_body():
    """条目数对得上，但正文变了——同样是坏，而且更难发现。"""
    with pytest.raises(migrate.MigrationError, match="fingerprint"):
        migrate.verify([_payload(text="原文")],
                       [_payload(source="trae_work_cn", text="改过")],
                       total_before=1, total_after=1)


def test_verify_rejects_a_changed_total_count():
    """迁移只该换标签，不该让库里多一条或少一条。"""
    with pytest.raises(migrate.MigrationError, match="total"):
        migrate.verify([_payload()], [_payload(source="trae_work_cn")],
                       total_before=10, total_after=11)


def test_stale_detects_points_left_on_the_old_source():
    """专门盯"ID 换了、标签没换"这个状态。

    2026-09-25 真踩过：upsert 写的是没改 source 的 payload，9 条素材换了新 ID
    却仍标着 trae。计数与指纹全对，只有这条能指出来。
    """
    after = [_payload(source="trae_work_cn"), _payload(source="trae")]

    stale = migrate.stale_points(after)

    assert len(stale) == 1


def test_stale_is_empty_on_a_clean_migration():
    assert migrate.stale_points([_payload(source="trae_work_cn")]) == []
