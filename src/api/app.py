"""FastAPI 按需薄壳（T040 / FR-024 / 宪法 II）。

路由层只做参数校验与核心层调用，零业务逻辑：
- GET  /health       组件状态（Qdrant 连通、.env 就绪）——AC-001 后半
- POST /log          手动快记（与 CLI 共用 src.log.log）
- POST /sync         后台线程跑 sync.run（互斥由 data/.sync.lock 保证，
                     防 cron 与 API 并发，F2）；GET /sync/status 查进度
- GET  /ask          检索问答（与 CLI 共用 src.ask.query）
- GET  /ask/stream   同上但 SSE 流式（共用 src.ask.query_stream）
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Iterator
from dataclasses import asdict
from datetime import date as date_type
from datetime import datetime

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from src import ask, config, log, sync

logger = logging.getLogger(__name__)

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
    except Exception as exc:  # noqa: BLE001 — 健康检查：任何失败都算不可用
        # 返回 False 是契约，但"为什么"得留下：/health 是排查入口，
        # 只回一个 qdrant: false 等于让用户自己猜。
        # 用 WARNING 而非 debug：uvicorn 不跑 CLI 那几处 basicConfig，
        # root 停在默认 WARNING，debug 一次都不会显示。这是个按需工具，
        # 一次健康检查一条日志，不算刷屏。
        logger.warning("qdrant health probe failed: %s: %s",
                       type(exc).__name__, exc)
        return False


@app.get("/health")
def health() -> dict:
    config.load_env()
    import os

    env_status = {key: bool(os.environ.get(key)) for key in _ENV_KEYS}
    qdrant = _qdrant_ok()
    ok = qdrant and all(env_status.values())
    return {"status": "ok" if ok else "degraded",
            "qdrant": qdrant, "env": env_status}


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
        # 状态面板是对外出口，但后台线程里没人轮询 /sync/status 时
        # 这次失败就等于没发生——服务端自己再留一份。
        logger.exception("sync failed")
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
        # HTTPException 会被 FastAPI 的异常处理器接住，不会进 uvicorn 的
        # 错误日志——不在这里落一条，没人在终端盯着时就查不到现场。
        logger.exception("ask failed")
        raise HTTPException(
            502, f"ask failed: {exc}; check `make up` (Qdrant) and .env"
        ) from exc
    return {
        "answer": answer.text,
        "citations": [asdict(c) for c in answer.citations],
        "expanded": [asdict(c) for c in answer.expanded],
    }


def _sse(event: str, payload: dict) -> str:
    """一条 SSE 消息。json.dumps 顺带把换行转义掉，不会撕裂 data 行。"""
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _stream_events(streamed: ask.StreamedAnswer) -> Iterator[str]:
    """引用 → 正文块 → done；生成中途出错则补一个 error 事件。

    这里不能抛异常：响应头（200）早就发出去了，异常只会表现为连接被切断，
    客户端既拿不到已收到的正文，也不知道为什么断。前端约定收到 done 才算
    完整回答，收到 error 则展示已有部分 + 失败原因。
    """
    yield _sse("citations", {
        "citations": [asdict(c) for c in streamed.citations],
        "expanded": [asdict(c) for c in streamed.expanded],
    })
    try:
        for chunk in streamed.chunks:
            yield _sse("chunk", {"text": chunk})
    except Exception as exc:  # noqa: BLE001 — 见 docstring：只能转成事件
        # 这条失败只以 SSE 事件送出，客户端不读现场就没了，先落日志。
        logger.exception("ask stream generation failed")
        yield _sse("error", {"message": f"{type(exc).__name__}: {exc}"})
        return
    yield _sse("done", {})


@app.get("/ask/stream")
def get_ask_stream(
    q: str,
    type: str | None = None,
    project: str | None = None,
    since: str | None = None,
    until: str | None = None,
    expand: bool | None = None,
) -> StreamingResponse:
    """SSE 版检索问答。检索阶段失败给正常 502；生成阶段失败走 error 事件。"""
    try:
        streamed = ask.query_stream(q, type=type, project=project,
                                   since=since, until=until, expand=expand)
    except Exception as exc:  # noqa: BLE001 — 与 /ask 的 502 文案保持一致
        logger.exception("ask stream retrieval failed")
        raise HTTPException(
            502, f"ask failed: {exc}; check `make up` (Qdrant) and .env"
        ) from exc

    return StreamingResponse(
        _stream_events(streamed),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
