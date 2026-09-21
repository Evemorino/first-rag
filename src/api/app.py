"""FastAPI 按需薄壳（T040 / FR-024 / 宪法 II）。

路由层只做参数校验与核心层调用，零业务逻辑：
- GET  /health       组件状态（Qdrant 连通、.env 就绪）——AC-001 后半
- POST /log          手动快记（与 CLI 共用 src.log.log）
- POST /sync         后台线程跑 sync.run（互斥由 data/.sync.lock 保证，
                     防 cron 与 API 并发，F2）；GET /sync/status 查进度
- GET  /ask          检索问答（与 CLI 共用 src.ask.query）
"""

from __future__ import annotations

import threading
from dataclasses import asdict
from datetime import date as date_type
from datetime import datetime

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from src import ask, config, log, sync

app = FastAPI(title="first-rag", version="0.1.0")

_state_lock = threading.Lock()
_sync_state: dict = {
    "running": False,
    "started_at": None,
    "last": None,
    "error": None,
}

_ENV_KEYS = ("ARK_API_KEY", "ARK_BASE_URL", "EMBED_MODEL", "CHAT_MODEL")


class LogBody(BaseModel):
    text: str = Field(min_length=1)
    type: str | None = None


class SyncBody(BaseModel):
    date: date_type | None = None


def _qdrant_ok() -> bool:
    from qdrant_client import QdrantClient

    try:
        QdrantClient(url=config.QDRANT_URL, trust_env=False,
                     timeout=2).get_collections()
        return True
    except Exception:  # noqa: BLE001 — 健康检查：任何失败都算不可用
        return False


@app.get("/health")
def health() -> dict:
    config.load_env()
    import os

    env_status = {key: bool(os.environ.get(key)) for key in _ENV_KEYS}
    ok = _qdrant_ok() and all(env_status.values())
    return {"status": "ok" if ok else "degraded",
            "qdrant": _qdrant_ok(), "env": env_status}


@app.post("/log")
def post_log(body: LogBody) -> dict:
    inbox = log.log(body.text, body.type)
    return {"ok": True, "inbox": str(inbox)}


def _run_sync(day: date_type | None) -> None:
    with _state_lock:
        _sync_state.update(running=True, started_at=datetime.now(
            tz=config.TZ).isoformat(timespec="seconds"), last=None, error=None)
    try:
        summary = sync.run(day)
        with _state_lock:
            _sync_state["last"] = summary
    except Exception as exc:  # noqa: BLE001 — 状态面板要展示失败原因
        with _state_lock:
            _sync_state["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        with _state_lock:
            _sync_state["running"] = False


@app.post("/sync", status_code=202)
def post_sync(body: SyncBody) -> dict:
    with _state_lock:
        if _sync_state["running"]:
            raise HTTPException(409, "a sync is already running")
    threading.Thread(target=_run_sync, args=(body.date,), daemon=True).start()
    return {"started": True, "date": body.date.isoformat() if body.date else None}


@app.get("/sync/status")
def sync_status() -> dict:
    with _state_lock:
        return dict(_sync_state)


@app.get("/ask")
def get_ask(
    q: str,
    type: str | None = None,
    project: str | None = None,
    since: str | None = None,
    until: str | None = None,
    expand: bool | None = None,
) -> dict:
    try:
        answer = ask.query(q, type=type, project=project,
                           since=since, until=until, expand=expand)
    except Exception as exc:  # noqa: BLE001 — 入口只转译为 502 + 引导
        raise HTTPException(
            502, f"ask failed: {exc}; check `make up` (Qdrant) and .env"
        ) from exc
    return {
        "answer": answer.text,
        "citations": [asdict(c) for c in answer.citations],
        "expanded": [asdict(c) for c in answer.expanded],
    }
