"""FastAPI 按需薄壳（T040 / FR-024 / 宪法 II）。

路由层只做参数校验与核心层调用，零业务逻辑：
- GET  /health       组件状态（Qdrant 连通、.env 就绪）——AC-001 后半
- POST /log          手动快记（与 CLI 共用 src.log.log）
- POST /sync         后台线程跑 sync.run（互斥由 data/.sync.lock 保证，
                     防 cron 与 API 并发，F2）；GET /sync/status 查进度
- GET  /ask          检索问答（与 CLI 共用 src.ask.query）
- GET  /ask/stream   同上但 SSE 流式（共用 src.ask.query_stream）
- GET  /             本机审阅页（由 web/ 前端工程构建，读 web/dist/index.html；FR-026 / NFR-009）
- GET  /assets/*     构建产物里的静态资源（带 hash 的 JS/CSS）
- GET  /types        配置里的类型枚举（供页面筛选与编辑下拉，FR-016）
- GET  /entries      条目列表（过滤 + 分页，默认隐藏软删条目）
- GET  /entries/{id} 条目详情（生效值 + 原始正文 + 留痕 + 关联边）
- PATCH /entries/{id}          人工编辑（覆写层 + 乐观并发）
- POST /entries/{id}/delete    软删除（可恢复）
- POST /entries/{id}/restore   恢复

写接口额外要求"这请求来自本机页面"（`Origin`/`Host` + 自定义头），理由见
`require_local` —— 无鉴权的本机服务，唯一的实际风险面是浏览器里的别的页面。
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Iterator
from dataclasses import asdict
from datetime import date as date_type
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from src import ask, config, entries as entries_module, log, sync
from src.api import schemas

logger = logging.getLogger(__name__)

app = FastAPI(title="first-rag", version="0.1.0")

# v0.9：页面由 `web/` 前端工程构建，产物不入库（ADR-21）。
# 路径在**每次请求时**读这个模块变量 —— 测试靠 monkeypatch 它来构造"有产物/没产物"两种现场。
_WEB_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"

_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
UI_HEADER = "first-rag-ui"


def _local_host(host_header: str | None) -> bool:
    """`Host` 是不是本机（可带端口；`[::1]:8300` 这种方括号形式也算）。"""
    host = (host_header or "").rsplit(":", 1)[0].strip("[]")
    return host in _LOCAL_HOSTS


def _local_origin(origin: str | None) -> bool:
    """`Origin`（若有）是不是本机。**缺 Origin 不算错** —— 同源 GET 本来就不带它。"""
    return not origin or urlparse(origin).hostname in _LOCAL_HOSTS


@app.middleware("http")
async def enforce_loopback(request: Request, call_next):
    """**所有**路由只接受本机 `Host` / 本机 `Origin`（NFR-010 的边界）。

    为什么从"每个写路由挂 `Depends`"改成中间件：2026-09-28 的安全复核证明了前一种的后果 ——
    `require_local` 只声明在三个写路由上，于是 `/types`、`/entries`、`/entries/{id}`、
    `/ask`、`/ask/stream`、`/sync`、`/log` 一个闸都没有。攻击者页面只要把自己的域名解析到
    `127.0.0.1`（DNS rebinding），浏览器就把它与本机端口当**同源**：没有 CORS 预检，
    自定义头那道闸因此形同虚设，只剩下 `Host`/`Origin` 能区分"真页面"与"攻击者页面"。
    实测后果是能读全库、触发 Ark 计费、往 `notes/inbox.md` 写东西（后者会被下次 sync
    蒸馏进库）。中间件按路由表生效：**新增路由自动被覆盖**，不依赖谁记得加 `Depends`。

    这里直接返回 `JSONResponse` 而不是抛 `HTTPException`：中间件在异常处理器**外层**，
    抛出去会变成 500 而不是 403（FastAPI 的已知坑）。
    """
    if not _local_host(request.headers.get("host")) or not _local_origin(
        request.headers.get("origin")
    ):
        return JSONResponse({"detail": "只接受本机请求"}, status_code=403)
    return await call_next(request)

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


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    """审阅页（FR-026）：返回前端工程的构建产物入口。

    产物缺失时给 **503 + 明确的下一步**，而不是空白页 —— 白屏是最难查的一类失败。
    """
    entry = _WEB_DIST / "index.html"
    if not entry.is_file():
        raise HTTPException(
            503,
            "前端产物缺失：请在仓库根跑 `make ui`（它会构建 web/dist）后再打开本页",
        )
    return entry.read_text(encoding="utf-8")


@app.get("/assets/{path:path}")
def asset(path: str) -> FileResponse:
    """构建产物的静态资源（Vite 生成 `assets/<name>-<hash>.js|css`）。

    用显式路由而不是 `app.mount(StaticFiles(...))`：mount 在 import 时就固定住目录，
    而 `GET /` 是按请求读 `_WEB_DIST` —— 两者一旦指向不同目录，测试抓不到、
    线上表现为"页面能开、资源 404"。同一个变量驱动，才谈得上一致。
    带路径逃逸守卫：`..` 一律 404。
    """
    base = (_WEB_DIST / "assets").resolve()
    target = (base / path).resolve()
    if not target.is_relative_to(base) or not target.is_file():
        raise HTTPException(404, "资源不存在")
    return FileResponse(target)


@app.get("/types", response_model=schemas.TypesView)
def list_types() -> dict:
    """类型枚举来自 config/schema.json —— 页面不硬编码类型表（FR-016 / 宪法 III）。"""
    types = config.load_schema().get("types", [])
    return {"types": [t["name"] if isinstance(t, dict) else str(t) for t in types]}


@app.get("/entries", response_model=schemas.EntryListView)
def list_entries(
    date_from: str | None = None,
    date_to: str | None = None,
    type: str | None = None,
    project: str | None = None,
    source: str | None = None,
    include_deleted: bool = False,
    page: int = 1,
) -> dict:
    """条目列表：过滤 + 日期倒序 + 分页，默认隐藏软删条目（FR-026 / FR-029）。"""
    try:
        return entries_module.list_entries(
            date_from=date_from,
            date_to=date_to,
            type=type,
            project=project,
            source=source,
            include_deleted=include_deleted,
            page=page,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/entries/{entry_id}", response_model=schemas.EntryDetailView)
def get_entry(entry_id: str) -> dict:
    """条目详情（FR-027）：生效值 + 原始正文 + 留痕 + 关联边。"""
    entry = entries_module.get_entry(entry_id)
    if entry is None:
        raise HTTPException(404, "条目不存在")
    return entry


def require_local(request: Request) -> None:
    """写接口额外要求"来自本机页面"（NFR-010 / ADR-20）。

    `Host`/`Origin` 那两条已由 `enforce_loopback` 中间件**对所有路由**执行（2026-09-28 起），
    所以这里只剩写接口独有的那一道：自定义头。它挡的是普通跨站请求 —— 自定义头会触发
    预检，而我们不回答 CORS，所以浏览器发不出去；同源（含 DNS rebinding 后的同源）能伪造
    这个头，那正是中间件管的事。两道互补，缺一不可。
    """
    if request.headers.get("x-requested-with") != UI_HEADER:
        raise HTTPException(403, f"缺少页面标识头（X-Requested-With: {UI_HEADER}）")


class EditBody(BaseModel):
    """人工编辑的入参：只含可改字段 + 版本号（ADR-18）。"""

    rev: int
    text: str | None = None
    type: str | None = None
    tags: list[str] | None = None
    project: str | None = None


class DeleteBody(BaseModel):
    rev: int
    reason: str | None = None


class RevBody(BaseModel):
    rev: int


def _write(call):
    """把核心层的三种业务异常映射成状态码，其余按 502 处理（同 /ask 的文案风格）。"""
    try:
        return call()
    except entries_module.NotFoundError as exc:
        raise HTTPException(404, "条目不存在") from exc
    except entries_module.ConflictError as exc:
        raise HTTPException(409, str(exc)) from exc
    except entries_module.ValidationError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — 嵌入失败等：整条编辑不生效
        logger.exception("entry write failed")
        raise HTTPException(
            502, f"保存失败：{exc}（检查 .env 里的 Ark 配置与 `make up`）"
        ) from exc


@app.patch("/entries/{entry_id}", response_model=schemas.EntryView)
def patch_entry(
    entry_id: str, body: EditBody, _: None = Depends(require_local)
) -> dict:
    """人工编辑：写覆写层 + 留痕，并用生效正文重算向量（FR-028 / ADR-18）。"""
    return _write(
        lambda: entries_module.edit_entry(
            entry_id,
            rev=body.rev,
            text=body.text,
            type=body.type,
            tags=body.tags,
            project=body.project,
        )
    )


@app.post("/entries/{entry_id}/delete", response_model=schemas.EntryView)
def post_delete(
    entry_id: str, body: DeleteBody, _: None = Depends(require_local)
) -> dict:
    """软删除（FR-029 / ADR-19）：可恢复，不摘向量。"""
    return _write(
        lambda: entries_module.delete_entry(entry_id, rev=body.rev, reason=body.reason)
    )


@app.post("/entries/{entry_id}/restore", response_model=schemas.EntryView)
def post_restore(
    entry_id: str, body: RevBody, _: None = Depends(require_local)
) -> dict:
    """恢复软删条目（FR-029）。"""
    return _write(lambda: entries_module.restore_entry(entry_id, rev=body.rev))
