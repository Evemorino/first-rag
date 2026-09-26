"""T024/T025 integration: 四源混跑 + 素材目录消失韧性。

四源 = claude_code / codex / kimi_code / trae_work_cn（+ git + manual 共六种
source）。Qdrant 用 qdrant-client 内嵌模式（:memory:）——与服务器模式
走完全相同的 upsert/query 代码路径；真实服务器验证由
test_ingest_qdrant.py（make up 后自动启用）承担。所有 fixture 写入
系统 tmp（宪法 V）。
"""

import json
import os
import subprocess
from datetime import date, datetime

import pytest
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams

from src import collect, config, distill, ingest, sync
from src.plugins import (
    claude_code,
    codex,
    hermes,
    kimi_code,
    opencode,
    qoder,
    qoder_cn,
    trae,
    trae_work_cn,
    workbuddy_ai,
    zcode,
)

DAY = date(2026, 9, 18)
TEST_COLLECTION = "learning_memory_test_t024"
# 2026-09-17T16:30Z = 2026-09-18 00:30 Shanghai
TS = "2026-09-17T16:30:00.000Z"


@pytest.fixture
def four_sources(tmp_path, tmp_data_dir, monkeypatch):
    """Fixture data for all six sources under tmp, plugins pointed at it."""
    # --- claude_code: one session with a user message ---
    claude_dir = tmp_path / "claude"
    (claude_dir / "proj-a").mkdir(parents=True)
    (claude_dir / "proj-a" / "s1.jsonl").write_text(json.dumps({
        "type": "user", "timestamp": TS, "cwd": "/tmp/proj",
        "message": {"content": "claude: why did upsert 409"},
    }) + "\n", encoding="utf-8")

    # --- codex: one rollout with user + assistant ---
    codex_dir = tmp_path / "codex" / "2026" / "09" / "17"
    codex_dir.mkdir(parents=True)
    lines = [
        {"timestamp": TS, "ordinal": 0, "type": "session_meta",
         "payload": {"session_id": "sid", "cwd": "C:\\tmp\\proj"}},
        {"timestamp": TS, "ordinal": 1, "type": "response_item",
         "payload": {"type": "message", "role": "user",
                     "content": [{"type": "input_text",
                                  "text": "codex: fix the flaky test"}]}},
        {"timestamp": TS, "ordinal": 2, "type": "response_item",
         "payload": {"type": "message", "role": "assistant",
                     "content": [{"type": "output_text",
                                  "text": "codex: root cause was ordering"}]}},
    ]
    (codex_dir / "rollout-a.jsonl").write_text(
        "\n".join(json.dumps(l) for l in lines) + "\n", encoding="utf-8")

    # --- kimi_code: one session with wire events ---
    kimi_session = tmp_path / "kimi" / "wd_a" / "session_1"
    (kimi_session / "agents" / "main").mkdir(parents=True)
    (kimi_session / "state.json").write_text(json.dumps(
        {"cwd": "C:\\tmp\\proj", "title": "kimi task",
         "createdAt": TS}), encoding="utf-8")
    (kimi_session / "agents" / "main" / "wire.jsonl").write_text(
        json.dumps({"timestamp": TS, "role": "user",
                    "content": "kimi: parse the wire format"}) + "\n",
        encoding="utf-8")

    # --- trae_work_cn: one pre-summarized record ---
    trae_dir = tmp_path / "trae_work_cn" / "projects" / "-c-Code-firstrag--p2-abc" / "20260918"
    trae_dir.mkdir(parents=True)
    (trae_dir / "session_memory_x.jsonl").write_text(json.dumps({
        "intent": "import dicom", "actions": ["a"],
        "outcome": "Shipped the importer.",
        "learned": ["DICOM needs whole-folder import"],
        "message_summary_time": "2026-09-18 10:27:48",
    }) + "\n", encoding="utf-8")

    # --- git: one repo with a same-day commit ---
    repo = tmp_path / "repo"
    repo.mkdir()
    def git(*args):
        env = {"GIT_AUTHOR_DATE": "2026-09-18T10:00:00+08:00",
               "GIT_COMMITTER_DATE": "2026-09-18T10:00:00+08:00"}
        return subprocess.run(["git", "-C", str(repo), *args],
                              capture_output=True, text=True, check=True,
                              env={**os.environ, **env})
    git("init", "-q")
    (repo / "f.txt").write_text("x", encoding="utf-8")
    git("add", ".")
    git("-c", "user.name=tester", "-c", "user.email=t@t.io",
        "commit", "-q", "-m", "feat: add ingestion pipeline")

    # --- manual: one inbox note ---
    notes = config.NOTES_DIR
    notes.mkdir(parents=True, exist_ok=True)
    (notes / "inbox.md").write_text(
        "- [2026-09-18T09:00:00+08:00 #idea] quick note about sync design\n",
        encoding="utf-8")

    # --- point config/plugins at the fixture world ---
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "repos.txt").write_text(str(repo) + "\n", encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_DIR", cfg)
    monkeypatch.setattr(claude_code, "SESSIONS_DIR", claude_dir)
    monkeypatch.setattr(codex, "SESSIONS_DIR", tmp_path / "codex")
    monkeypatch.setattr(kimi_code, "SESSIONS_DIR", tmp_path / "kimi")
    monkeypatch.setattr(trae_work_cn, "MEMORY_DIR", tmp_path / "trae_work_cn")
    # 其余插件全部指到 tmp：指漏了就会去读开发机真实的家目录，测试随之变得
    # 不可复现。这不是假想——qoder 与 zcode 在 2026-09-18 当天都有真实会话，
    # 漏掉任一条都会让下面断言的材料数悄悄 +1。
    # v0.7 的 11 个源到 T058 为止已全部纳入隔离面；以后新增源必须同时加到这里。
    monkeypatch.setattr(qoder, "PROJECTS_DIR", tmp_path / "qoder")
    monkeypatch.setattr(qoder_cn, "PROJECTS_DIR", tmp_path / "qoder_cn")
    monkeypatch.setattr(workbuddy_ai, "SESSIONS_DIR", tmp_path / "workbuddy_ai")
    monkeypatch.setattr(trae, "MEMORY_DIR", tmp_path / "trae")
    monkeypatch.setattr(opencode, "DB_PATH", tmp_path / "opencode.db")
    monkeypatch.setattr(zcode, "DB_PATH", tmp_path / "zcode.sqlite")
    monkeypatch.setattr(hermes, "DB_PATH", tmp_path / "hermes-state.db")
    return tmp_path


@pytest.fixture
def pipeline_fakes(tmp_data_dir, monkeypatch):
    """Fake Ark chat/embed; Qdrant in embedded mode with one-hot vectors."""
    monkeypatch.setenv("CHAT_MODEL", "test-model")

    def fake_chat(messages, json_mode=False):
        # Act like the real LLM would: read the newest snapshot's materials
        # (collect just wrote it) and emit one candidate per LLM-path material.
        newest = max(config.RAW_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime)
        snapshot = json.loads(newest.read_text(encoding="utf-8"))
        candidates = []
        for material in snapshot["materials"]:
            if material["kind"] in ("note", "trae_record"):
                continue
            candidates.append({
                "text": f"learned from {material['source']}",
                "type": "progress",
                "tags": ["integration"],
                "source_refs": [material["ref"]],
            })
        return json.dumps({"entries": candidates}, ensure_ascii=False)

    monkeypatch.setattr(distill, "chat", fake_chat)

    def fake_embed(texts):
        # one-hot in a fixed dimension: all vectors mutually orthogonal so
        # novelty filtering never mistakes a different entry for a duplicate
        dim = 16
        assert len(texts) <= dim
        return [[1.0 if i == j else 0.0 for j in range(dim)]
                for i, _ in enumerate(texts)]

    monkeypatch.setattr(ingest, "embed", fake_embed)

    client = QdrantClient(":memory:")
    client.create_collection(
        collection_name=TEST_COLLECTION,
        vectors_config=VectorParams(size=16, distance=Distance.COSINE),
    )
    monkeypatch.setattr(config, "COLLECTION", TEST_COLLECTION)
    monkeypatch.setattr(ingest, "_client", lambda: client)
    yield client
    client.close()


def _scroll_all(client) -> list:
    points, offset = client.scroll(
        collection_name=TEST_COLLECTION, limit=100, with_payload=True)
    result = list(points)
    while offset:
        points, offset = client.scroll(
            collection_name=TEST_COLLECTION, limit=100,
            with_payload=True, offset=offset)
        result.extend(points)
    return result


def _payload_sources(client) -> set[str]:
    return {point.payload["source"] for point in _scroll_all(client)}


def test_four_sources_end_to_end(four_sources, pipeline_fakes, monkeypatch):
    summary = sync.run(DAY)

    assert summary["upserted"] == 6
    assert _payload_sources(pipeline_fakes) == {
        "claude_code", "codex", "kimi_code", "trae_work_cn", "git", "manual",
    }

    # 同日重跑幂等：points 数不变（AC-003 关联面之外的直插面）
    sync.run(DAY)
    assert len(_scroll_all(pipeline_fakes)) == 6


def test_missing_source_dir_sync_still_succeeds(
        four_sources, pipeline_fakes, monkeypatch, caplog):
    """AC-006：某工具素材目录消失 → sync 成功 + 日志，其余来源正常入库。"""
    monkeypatch.setattr(claude_code, "SESSIONS_DIR",
                        four_sources / "claude-renamed-away")

    with caplog.at_level("INFO"):
        summary = sync.run(DAY)

    assert summary["upserted"] == 5  # claude_code 缺席，其余五源正常
    assert "claude_code" not in _payload_sources(pipeline_fakes)
    assert any("missing" in record.message.lower()
               for record in caplog.records
               if record.name.endswith("claude_code"))
