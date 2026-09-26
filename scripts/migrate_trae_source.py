#!/usr/bin/env python3
"""把 `trae` 改名成 `trae_work_cn` —— 一次性、可 dry-run、可回滚的存量迁移。

为什么需要它
------------
条目 ID = uuid5(NAMESPACE_URL, f"{source}|{date}|{content_hash}")。
source 是 ID 的**身份字段之一**，所以"把插件改个名"在库里不是改个标签，
是换了一整套 ID：

    旧 ID：uuid5("trae|2026-09-18|<hash>")          ← 库里现有的 9 条挂这儿
    新 ID：uuid5("trae_work_cn|2026-09-18|<hash>")  ← 新插件将来会写这儿

不迁移就直接启用新插件，同一条素材会以两个 ID 并存：旧的那 9 条变成永远查不到
的孤儿，新的 9 条再花一次 token 蒸馏一遍。两边都不报错。

本机现状（2026-09-25）：9 条，分布在 2026-09-18（5 条）与 2026-09-23（4 条）。

顺带修掉的一个洞
----------------
`data/raw/*.json` 快照里 materials[].source 也是 "trae"。只迁 Qdrant 不管快照的
话，日后 `make redistill D=2026-09-18` 会照着快照重新蒸馏，又把旧 source 的条目
写回库里 —— 迁移被无声地撤销。所以标签要**两处一起换**，快照先备份再改。

改的是什么、不改的是什么
------------------------
改：payload.source、快照的 materials[].source。
不改：正文、日期、向量、payload 其余字段。**向量直接复用不重算** —— source 不影响
embedding 结果，重算一遍既费钱又引入不确定性。

判据是路径，不是 source 名
--------------------------
v0.7.1 之后 `trae` 这个名字被**新插件**（读 `~/.trae/`）合法复用了，而待迁的
旧数据其实是 Trae CN 的、在 `~/.trae-cn/`。所以 `source == "trae"` 不再等于
"待迁数据"：只看名字会把新插件的素材错标成 `trae_work_cn`，且**不报错**。

因此本脚本判 ref **路径**：旧数据在 `~/.trae-cn/memory/` 下才动，其余一律跳过。
而且这条迁移是一次性的、早已执行完 —— 所以 `main` 一旦发现"挂着旧 source 名、
ref 却指向新目录"的条目就**默认拒跑**（退出码 2），要 `--force` 才继续。

安全
----
- 默认 dry-run，只打印不写。要真写必须显式 `--apply`。
- 迁移前的完整状态（旧点含向量 + 快照原文）先落一份到 `data/migrations/`，
  出事可照着还原。
- 不碰任何产品源目录（宪法 V）。

用法::

    python scripts/migrate_trae_source.py            # dry-run，看清单
    python scripts/migrate_trae_source.py --apply    # 真改

退出码：0 成功（含 dry-run）；2 前置不满足（发现新插件数据 / 校验不过）。
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src import config, ids  # noqa: E402  (先补 sys.path 才能导入)
from src.plugins import trae, trae_work_cn  # noqa: E402  (目录常量的单一来源)

OLD_SOURCE = "trae"
NEW_SOURCE = "trae_work_cn"
PAGE = 256

# 判据是**路径**，不是 source 名。v0.7.1 之后 `trae` 这个名字被新插件
# （读 `~/.trae/`）合法复用，而待迁的旧数据其实是 Trae CN 的、在 `~/.trae-cn/`。
# 目录常量从插件取，不在这里另抄一份（宪法 I）。
OLD_MEMORY_DIR = trae_work_cn.MEMORY_DIR   # ~/.trae-cn/memory
NEW_MEMORY_DIR = trae.MEMORY_DIR           # ~/.trae/memory


class MigrationError(RuntimeError):
    """迁移不能安全继续。抛出来是为了停住，不是为了重试。"""


@dataclass(frozen=True)
class PointPlan:
    old_id: str
    new_id: str
    payload: dict


# --- 纯逻辑（单测射程内）---


def rekey(payload: dict) -> uuid.UUID:
    """按新 source 重算条目 ID。

    走 src.ids.point_id 而不是自己拼字符串：将来 ingest 若改算法，这里跟着变，
    两边不会悄悄分叉。
    """
    return ids.point_id(NEW_SOURCE, payload["date"], payload["text"])


def is_old_trae_ref(ref: object) -> bool:
    """这条 ref 指向待迁的旧数据（`~/.trae-cn/`）吗？

    只要 source 名对就迁，会把新 `trae` 的素材错标成 `trae_work_cn`——见
    `foreign_refs` 的长注释。ref 形状是 `…/session_memory_*.jsonl#L<n>`，
    前缀比对路径足够（两族目录名互不为前缀）。
    """
    return isinstance(ref, str) and ref.startswith(str(OLD_MEMORY_DIR) + "/")


def _refs_of(payload: dict) -> list:
    refs = payload.get("source_refs")
    return refs if isinstance(refs, list) else []


def _all_refs_are_old_trae(payload: dict) -> bool:
    """**全部** ref 都指向旧目录才算旧数据。

    混合来源的点是可疑的（说明这条素材横跨了改名那一刻），宁可不动。
    没有 ref 也一样：证明不了就不动。
    """
    refs = _refs_of(payload)
    return bool(refs) and all(is_old_trae_ref(r) for r in refs)


def foreign_refs(points: list[tuple[str, dict]],
                 snapshots: list[dict]) -> list[str]:
    """挂着旧 source 名、ref 却指向新插件目录的 ref 清单。

    这是"防护"的核心。v0.7.1 把旧的 `trae`（实为 Trae CN，`~/.trae-cn/`）
    改名成 `trae_work_cn`，把 `trae` 这名字**让给了新插件**（`~/.trae/`）。
    于是 `source == "trae"` 不再是"待迁数据"的同义词：

    - 只看 source 名 → 新插件的素材会被错标成 `trae_work_cn`
      （ID 跟着变，库里多出一份张冠李戴的条目）；
    - 更糟的是它**不报错**，只会安静地发生。

    所以本脚本的判据是 ref 路径，而**不是** source 名。这条迁移是一次性的、
    早已执行完；`foreign_refs` 非空说明有人在回头重跑它，`main` 因此默认拒跑。
    """
    found: list[str] = []
    for _, payload in points:
        if payload.get("source") != OLD_SOURCE:
            continue
        found += [r for r in _refs_of(payload) if not is_old_trae_ref(r)]
    for doc in snapshots:
        for material in doc.get("materials", []):
            if material.get("source") != OLD_SOURCE:
                continue
            ref = material.get("ref")
            if not is_old_trae_ref(ref):
                found.append(ref if isinstance(ref, str) else repr(ref))
    return found


def plan_points(points: list[tuple[str, dict]]) -> list[PointPlan]:
    """挑出待迁的点并算好新 ID。已经是新 source 的不再进计划（幂等）。"""
    existing = {point_id for point_id, _ in points}
    plan: list[PointPlan] = []
    for point_id, payload in points:
        if payload.get("source") != OLD_SOURCE:
            continue
        if not _all_refs_are_old_trae(payload):
            continue  # 见 foreign_refs：名字对不代表数据对
        new_id = str(rekey(payload))
        if new_id in existing:
            raise MigrationError(
                f"target id {new_id} already exists (old id {point_id})："
                "这条素材在库里已经有两份，继续迁移会把两份叠成一份。"
                "先人工确认要保哪一份。")
        # dict(...) 造副本：dry-run 也走这里，就地改 payload 会让 dry-run 变成真改。
        plan.append(PointPlan(old_id=point_id, new_id=new_id,
                              payload=dict(payload, source=NEW_SOURCE)))
    return plan


def relabel_snapshot(doc: dict) -> tuple[dict, int]:
    """把快照里的 materials[].source 从旧名换成新名，返回 (新文档, 改了几条)。

    只动 source 一个字段，且改的是深拷贝 —— 传进来的原对象保持原样，
    否则 dry-run 会变成真改。

    判据同 `plan_points`：source 名要对，**ref 还得真在旧目录下**。
    快照里的 `trae` 素材如果 ref 指向 `~/.trae/`，那是新插件的数据。
    """
    new_doc = copy.deepcopy(doc)
    changed = 0
    for material in new_doc.get("materials", []):
        if material.get("source") != OLD_SOURCE:
            continue
        if not is_old_trae_ref(material.get("ref")):
            continue
        material["source"] = NEW_SOURCE
        changed += 1
    return new_doc, changed


def fingerprints(points: list[dict]) -> Counter:
    """条目的"身份指纹"：日期 + 正文哈希。用重数（Counter）而非集合，
    因为两条同样的素材本就该算两条。"""
    return Counter(f"{p['date']}|{ids.content_hash(p['text'])}" for p in points)


def stale_points(payloads: list[dict]) -> list[str]:
    """迁移后仍挂着旧 source 的点，按「日期|正文哈希」列出便于人肉定位。

    这个函数是 2026-09-25 真踩之后补的。当时的坏状态是：ID 换了、标签没换 ——
    条数对、指纹对、全库总数也对，前三条校验全绿，只有"按新来源查得到吗"
    会红。有了它，这个状态不会再被当成迁移成功放过去。
    """
    return [f"{p['date']}|{ids.content_hash(p['text'])}"
            for p in payloads if p.get("source") == OLD_SOURCE]


def verify(before: list[dict], after: list[dict], *,
           total_before: int, total_after: int) -> None:
    """迁移后自证：一条不多、一条不少、正文一个字节没变。

    这是本脚本**唯一**能证明自己没搞砸的地方，所以三条都查，且查的是从库里
    重新读回来的现况 —— 不是脚本自己记的中间产物，自己验自己等于没验。
    """
    if len(before) != len(after):
        raise MigrationError(
            f"count mismatch: 迁移前 {len(before)} 条 → 迁移后 {len(after)} 条")
    if fingerprints(before) != fingerprints(after):
        raise MigrationError(
            "fingerprint mismatch: 条目数对得上，但日期/正文集合变了")
    if total_before != total_after:
        raise MigrationError(
            f"total count changed: 迁移前全库 {total_before} 点 → "
            f"迁移后 {total_after} 点（迁移只该换标签，不该增减条目）")


# --- IO ---


def _collection_points(client) -> list[tuple[str, dict, Any]]:
    """整库翻页捞点（含向量）。固定 limit 不翻页只能看到前 256 条。"""
    collected: list[tuple[str, dict, Any]] = []
    offset = None
    while True:
        batch, offset = client.scroll(
            collection_name=config.COLLECTION,
            limit=PAGE,
            offset=offset,
            with_payload=True,
            with_vectors=True,
        )
        collected.extend((str(p.id), p.payload or {}, p.vector) for p in batch)
        if offset is None:
            return collected


def _snapshot_paths(raw_dir: Path) -> list[Path]:
    return sorted(raw_dir.glob("????-??-??.json"))


def _load_raw_doc(path: Path) -> dict:
    """原样读一份快照，不做任何加工 —— 闸门要在"还没决定改不改"时看它。"""
    return json.loads(path.read_text(encoding="utf-8"))


def _load_snapshots(raw_dir: Path) -> list[tuple[Path, dict, dict, int]]:
    """返回 [(路径, 原文档, 改后文档, 改动条数)]，只含有旧 source 的那些。"""
    found = []
    for path in _snapshot_paths(raw_dir):
        doc = json.loads(path.read_text(encoding="utf-8"))
        relabeled, changed = relabel_snapshot(doc)
        if changed:
            found.append((path, doc, relabeled, changed))
    return found


def _write_backup(path: Path, points, snapshots) -> None:
    """把"迁移前"的完整状态落盘。出事时这是唯一的还原依据。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "created_at": datetime.now(tz=config.TZ).isoformat(),
        "old_source": OLD_SOURCE,
        "new_source": NEW_SOURCE,
        "points": [{"id": pid, "payload": payload, "vector": vector}
                   for pid, payload, vector in points],
        "snapshots": [{"path": str(p), "document": doc}
                      for p, doc, _, _ in snapshots],
    }, ensure_ascii=False, indent=2), encoding="utf-8")


def _apply_points(client, plan: list[PointPlan], points) -> None:
    """按新 ID 写入（复用原向量），再删旧点。顺序：先写后删。"""
    from qdrant_client.models import PointStruct

    vectors = {pid: vector for pid, _, vector in points}
    client.upsert(
        collection_name=config.COLLECTION,
        points=[PointStruct(id=p.new_id, vector=vectors[p.old_id],
                            payload=p.payload) for p in plan],
    )
    client.delete(
        collection_name=config.COLLECTION,
        points_selector=[p.old_id for p in plan],
    )


def _restore(client, backup_path: Path, *, apply: bool) -> int:
    """把库还原到备份时的状态：upsert 备份点，删掉备份里没有的点。

    只该在迁移校验没过时用。它按"整库快照"还原，所以对备份**之后**新入的点
    是破坏性的 —— 所以先dry-run 看清要删哪些，再 --apply。
    """
    from qdrant_client.models import PointStruct

    doc = json.loads(backup_path.read_text(encoding="utf-8"))
    saved = doc["points"]
    saved_ids = {p["id"] for p in saved}
    current = _collection_points(client)
    extra = [pid for pid, _, _ in current if pid not in saved_ids]

    print(f"备份时间：{doc.get('created_at')}（{len(saved)} 点）")
    print(f"还原：写回 {len(saved)} 点，删掉备份之后的 {len(extra)} 点")
    for point_id in extra:
        print(f"  将删除 {point_id}")

    if not apply:
        print("\nDRY-RUN：什么都没写。加 --apply 才真还原。")
        return 0

    client.upsert(
        collection_name=config.COLLECTION,
        points=[PointStruct(id=p["id"], vector=p["vector"], payload=p["payload"])
                for p in saved],
    )
    if extra:
        client.delete(collection_name=config.COLLECTION, points_selector=extra)
    print("已还原。")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true",
                        help="真写；不带则只 dry-run 打印清单")
    parser.add_argument("--force", action="store_true",
                        help="即使发现指向新插件目录的条目也继续（只迁旧路径的）")
    parser.add_argument("--raw-dir", type=Path, default=config.RAW_DIR)
    parser.add_argument("--backup-dir", type=Path,
                        default=config.DATA_DIR / "migrations")
    parser.add_argument("--restore", type=Path, metavar="BACKUP",
                        help="把库还原到该备份时的状态（需与 --apply 合用）")
    args = parser.parse_args(argv)

    from qdrant_client import QdrantClient

    client = QdrantClient(url=config.QDRANT_URL, trust_env=False)

    if args.restore:
        return _restore(client, args.restore, apply=args.apply)

    points = _collection_points(client)
    tuples = [(pid, payload) for pid, payload, _ in points]
    raw_snapshots = [_load_raw_doc(p) for p in _snapshot_paths(args.raw_dir)]

    # 闸门：这条迁移一次性、且**早就执行完了**。若现在还能扫出"挂着旧 source
    # 名、ref 却指向新插件目录"的条目，说明是有人在回头重跑它 —— 而旧脚本
    # 会把新插件的素材错标成 trae_work_cn。默认停下，让人先看清是什么。
    if foreign := foreign_refs(tuples, raw_snapshots):
        print(f"拒绝执行：发现 {len(foreign)} 条 source={OLD_SOURCE} 但 ref 指向"
              f"新插件目录的条目。\n"
              f"  新插件目录 = {NEW_MEMORY_DIR}\n"
              f"  这些不是待迁数据，是 v0.7.1 之后新插件写进来的。\n"
              f"  （本脚本的迁移是一次性的，早已执行完；再跑一遍只会张冠李戴。）",
              file=sys.stderr)
        for ref in foreign[:10]:
            print(f"    {ref}", file=sys.stderr)
        if len(foreign) > 10:
            print(f"    …另有 {len(foreign) - 10} 条", file=sys.stderr)
        if not args.force:
            print(f"\n确认无误可加 --force 继续（届时仍只迁 {OLD_MEMORY_DIR} "
                  f"下的条目，其余跳过）。", file=sys.stderr)
            return 2
        print("\n--force 已给：继续，只迁旧目录下的条目。", file=sys.stderr)

    plan = plan_points(tuples)
    snapshots = _load_snapshots(args.raw_dir)
    snapshot_lines = sum(n for _, _, _, n in snapshots)

    print(f"待迁移条目：{len(plan)} 条（source {OLD_SOURCE} → {NEW_SOURCE}）")
    for item in plan:
        print(f"  {item.old_id} → {item.new_id}")
    print(f"待改快照：{len(snapshots)} 个文件、{snapshot_lines} 条素材")
    for path, _, _, n in snapshots:
        print(f"  {path.name}（{n} 条）")

    if not plan and not snapshots:
        print("已经迁过了，无操作。")
        return 0

    if not args.apply:
        print("\nDRY-RUN：什么都没写。加 --apply 才真改。")
        return 0

    stamp = datetime.now(tz=config.TZ).strftime("%Y%m%dT%H%M%S")
    backup = args.backup_dir / f"trae-rename-{stamp}.json"
    _write_backup(backup, points, snapshots)
    print(f"\n迁移前状态已备份：{backup}")

    before_all = [payload for _, payload, _ in points]
    before_trae = [p.payload for p in plan]

    _apply_points(client, plan, points)

    after = _collection_points(client)
    after_payloads = [payload for _, payload, _ in after]
    try:
        verify(before_trae,
               [p for p in after_payloads if p.get("source") == NEW_SOURCE],
               total_before=len(before_all), total_after=len(after_payloads))
        # 第三条校验只查"条数/指纹/总数"，漏得掉"ID 换了标签没换"。
        # 2026-09-25 就是这么漏过一次，所以这条单独查。
        if stale := stale_points(after_payloads):
            raise MigrationError(
                f"{len(stale)} 条迁移后仍挂着旧 source {OLD_SOURCE}：{stale[:5]}")
    except MigrationError as exc:
        print(f"校验未过：{exc}\n快照未改动。可还原："
              f"--restore {backup} --apply", file=sys.stderr)
        return 2

    for path, _, relabeled, _ in snapshots:
        path.write_text(json.dumps(relabeled, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    print(f"完成：{len(plan)} 条条目已改 ID，{len(snapshots)} 个快照已改标签。")
    print("现在才可以启用新的插件（改名步骤见 tasks T048）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
