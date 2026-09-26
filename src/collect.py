"""Collect the day's raw material into data/raw/YYYY-MM-DD.json (T011).

Sources: all registered plugins (read-only), git repos listed in
config/repos.txt (FR-003), and manual notes in notes/ (FR-004).
Output is the unified snapshot of data-model.md, including the
distill_run meta block (FR-006), written before distillation runs.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from src import config
from src.plugins import RawMaterial, iter_plugins

logger = logging.getLogger(__name__)

# A manual note line: "- [2026-09-18T22:31:00+08:00 #idea] text"
NOTE_LINE = re.compile(r"^\s*-\s*\[([^\]#]+?)\s*(?:#(\w+))?\]\s*(.*)$")


class SnapshotShrinkError(RuntimeError):
    """写快照会让已存在的同一天快照素材数变少。见 `save_snapshot`。"""


@dataclass
class DayRaw:
    """The on-disk snapshot structure (data-model.md)."""

    day: date
    collected_at: datetime
    materials: list[RawMaterial] = field(default_factory=list)
    distill_run: dict = field(default_factory=lambda: {"status": "noop"})

    def to_dict(self) -> dict:
        return {
            "date": self.day.isoformat(),
            "collected_at": self.collected_at.isoformat(),
            "distill_run": self.distill_run,
            "materials": [vars(m) | {"ts": _iso(m.ts)} for m in self.materials],
        }


def _iso(ts: datetime | None) -> str | None:
    return ts.isoformat() if ts else None


def gather(day: date, scope: dict | None = None, *,
           allow_shrink: bool = False) -> DayRaw:
    """Collect all sources for `day` and persist the snapshot (FR-006).

    `scope` (config/scope.json, FR-005) filters tools when present:
    {"tools": {"claude_code": true, ...}, "projects": {...}}.
    Missing sources are no-ops with a log line, never fatal (NFR-004).

    `allow_shrink` 透传给 `save_snapshot`：**窄 scope 重跑是唯一会合法削减
    素材数的路**（还有 `_cap`，但它是确定性的），所以明路的开关开在这里。
    """
    day_raw = DayRaw(day=day, collected_at=datetime.now(tz=config.TZ))
    schema = config.load_schema()

    for plugin in iter_plugins():
        if scope is not None and not scope.get("tools", {}).get(plugin.name, True):
            logger.info("collect: tool '%s' disabled by scope, skipping", plugin.name)
            continue
        try:
            refs = plugin.discover(day)
        except Exception as e:  # noqa: BLE001 — one plugin must not kill sync
            logger.warning("collect: '%s'.discover failed, skipping: %s", plugin.name, e)
            continue
        for ref in refs:
            try:
                day_raw.materials.append(plugin.parse(ref))
            except Exception as e:  # noqa: BLE001
                logger.warning("collect: '%s'.parse failed on %s: %s",
                               plugin.name, ref.ref, e)

    day_raw.materials.extend(_git_materials(day, scope))
    day_raw.materials.extend(_note_materials(day))

    _cap(day_raw, schema["distill"]["max_raw_chars"])
    save_snapshot(day_raw, allow_shrink=allow_shrink)
    return day_raw


# --- git (FR-003) ---

def _git_materials(day: date, scope: dict | None) -> list[RawMaterial]:
    repos_file = config.CONFIG_DIR / "repos.txt"
    if not repos_file.is_file():
        logger.info("collect: %s missing, git source is a no-op", repos_file)
        return []
    materials = []
    for line in repos_file.read_text(encoding="utf-8").splitlines():
        repo = line.strip()
        if not repo or repo.startswith("#"):
            continue
        if scope is not None and not scope.get("projects", {}).get(repo, True):
            logger.info("collect: repo '%s' disabled by scope", repo)
            continue
        materials.extend(_repo_commits(repo, day))
    return materials


def _repo_commits(repo: str, day: date) -> list[RawMaterial]:
    since = f"{day.isoformat()} 00:00 +0800"
    until = f"{(day.isoformat())} 23:59:59 +0800"
    try:
        out = subprocess.run(  # noqa: S603 — fixed argv, user-configured repo list
            ["git", "-C", repo, "log", f"--since={since}", f"--until={until}",
             "--date=iso-strict",
             "--pretty=format:%H%x00%ad%x00%s%x00%b%x1e"],
            capture_output=True, text=True, timeout=30, check=True,
        ).stdout
    except (subprocess.SubprocessError, FileNotFoundError) as e:
        logger.warning("collect: git repo '%s' failed (%s), skipping", repo, e)
        return []
    materials = []
    for record in (r for r in out.split("\x1e") if r.strip()):
        sha, ad, subject, body = (p.strip() for p in record.strip().split("\x00"))
        materials.append(RawMaterial(
            source="git", ref=sha,
            ts=datetime.fromisoformat(ad), kind="commit",
            text=f"{subject}\n{body}".strip(),
            meta={"project": Path(repo).name},
        ))
    return materials


# --- manual notes (FR-004 / FR-013) ---

def _note_materials(day: date) -> list[RawMaterial]:
    materials = []
    inbox = config.NOTES_DIR / "inbox.md"
    if inbox.is_file():
        for line in inbox.read_text(encoding="utf-8").splitlines():
            m = NOTE_LINE.match(line)
            if not m:
                continue
            try:
                ts = datetime.fromisoformat(m.group(1).strip())
            except ValueError:
                # 长得像快记、时间戳却坏了 —— 会丢的是一句人写下的东西，
                # 不能没声息。普通散文行走的是上面 `if not m` 那条，不报。
                logger.warning("collect: dropping inbox line with bad "
                               "timestamp %r: %s", m.group(1).strip(), line)
                continue
            if ts.date() == day:
                materials.append(RawMaterial(
                    source="manual", ref="inbox.md", ts=ts,
                    kind="note", text=m.group(3).strip(),
                    meta={"note_type": m.group(2) or "reflection"},
                ))
    materials.extend(_dated_note_files(day))
    return materials


def _dated_note_files(day: date) -> list[RawMaterial]:
    """Standalone notes whose filename carries the day: notes/2026-09-18*.md."""
    materials = []
    for path in sorted(config.NOTES_DIR.glob(f"{day.isoformat()}*.md")):
        materials.append(RawMaterial(
            source="manual", ref=path.name,
            ts=datetime.fromtimestamp(path.stat().st_mtime, tz=config.TZ),
            kind="note", text=path.read_text(encoding="utf-8").strip(),
            meta={"note_type": "reflection"},
        ))
    return materials


# --- snapshot persistence (FR-006) ---

def _cap(day_raw: DayRaw, max_chars: int) -> None:
    """Truncate oversized days, oldest-source-first is not attempted:
    simply cut the tail materials and note the cut."""
    total = sum(len(m.text) for m in day_raw.materials)
    if total <= max_chars:
        return
    kept, used = [], 0
    for m in day_raw.materials:
        if used + len(m.text) > max_chars:
            remaining = max_chars - used
            if remaining > 100:
                kept.append(RawMaterial(**{**vars(m), "text": m.text[:remaining]}))
            logger.warning("collect: day truncated at %d chars (cap %d)",
                           used, max_chars)
            break
        kept.append(m)
        used += len(m.text)
    day_raw.materials = kept


def snapshot_path(day: date) -> Path:
    return config.RAW_DIR / f"{day.isoformat()}.json"


def save_snapshot(day_raw: DayRaw, *, allow_shrink: bool = False) -> None:
    """Persist the current DayRaw snapshot to data/raw/YYYY-MM-DD.json.

    素材数**变少是拒写的**，除非显式 `allow_shrink=True`。理由见
    `_existing_material_count`：这个文件是 `make redistill` 的重放基线，
    被一份更小的快照盖掉不会报错，只会让日后的 diff 静默失准。
    """
    path = snapshot_path(day_raw.day)
    previous = _existing_material_count(path)
    if previous is not None and previous > len(day_raw.materials) and not allow_shrink:
        raise SnapshotShrinkError(
            f"refusing to shrink {path.name}: on disk {previous} materials, "
            f"new snapshot has {len(day_raw.materials)}. "
            "This file is the replay baseline for `make redistill`; overwriting it "
            "with a smaller day silently makes later diffs wrong. "
            "If the smaller snapshot is intended (e.g. a narrower scope), re-run "
            "with allow_shrink=True (`make sync D=… ALLOW_SHRINK=1`); "
            "if not, investigate first."
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(day_raw.to_dict(), ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    logger.info("collect: %d materials -> %s", len(day_raw.materials), path)


def _existing_material_count(path: Path) -> int | None:
    """已存在快照的素材数；没有文件返回 None。

    **读不出来也算"不许覆盖"**：解析不了就无从判断会不会削减，而这里宁可停下。
    一个坏掉的快照被静默盖掉，正是本函数要防的那类事故 —— 修文件是人的决定，
    不是写入方的默认行为。
    """
    if not path.is_file():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise SnapshotShrinkError(
            f"{path.name} exists but cannot be read ({e}); refusing to overwrite "
            "it. Fix or move the file, then re-run."
        ) from e
    materials = doc.get("materials") if isinstance(doc, dict) else None
    return len(materials) if isinstance(materials, list) else 0
