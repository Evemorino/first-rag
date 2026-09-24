"""T041: API 四端点冒烟（FastAPI TestClient，AC-001 后半）。

路由层行为验证（校验/状态码/核心调用接线）；核心逻辑由各自模块的
单测与集成测试覆盖。
"""

import json
import logging
from datetime import date

import pytest
from fastapi.testclient import TestClient

from src import ask as ask_module
from src.api import app as app_module
from src.api.app import app, _sync_state
from src.ask import Answer, Citation


@pytest.fixture
def client():
    _sync_state.update(running=False, started_at=None, last=None, error=None)
    return TestClient(app)


def test_health_reports_components(client, monkeypatch):
    monkeypatch.setattr(app_module, "_qdrant_ok", lambda: True)
    monkeypatch.setattr(app_module.config, "load_env", lambda: None)
    for key in ("ARK_API_KEY", "ARK_BASE_URL", "EMBED_MODEL", "CHAT_MODEL"):
        monkeypatch.setenv(key, "x")

    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["qdrant"] is True
    assert "ARK_API_KEY" in body["env"]


def test_health_probes_qdrant_only_once(client, monkeypatch):
    """探测两次要付两次代价：Qdrant 挂掉时每次 2s 超时，日志也重复一条。"""
    calls = []
    monkeypatch.setattr(app_module, "_qdrant_ok",
                        lambda: calls.append(1) or True)
    monkeypatch.setattr(app_module.config, "load_env", lambda: None)
    for key in ("ARK_API_KEY", "ARK_BASE_URL", "EMBED_MODEL", "CHAT_MODEL"):
        monkeypatch.setenv(key, "x")

    assert client.get("/health").status_code == 200

    assert len(calls) == 1


def test_qdrant_ok_logs_why_it_failed(monkeypatch, caplog):
    """返回 False 是契约，但"为什么不可用"不能一并咽掉。

    /health 是排查入口，只回一个 `qdrant: false` 等于让用户自己猜。
    断言钉在 WARNING：uvicorn 不跑 CLI 的 basicConfig，root 停在
    默认 WARNING——debug 级写在这里等于没写。
    """
    import qdrant_client

    def boom(*a, **k):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(qdrant_client, "QdrantClient", boom)

    with caplog.at_level(logging.WARNING, logger="src.api.app"):
        assert app_module._qdrant_ok() is False

    records = [r for r in caplog.records if "connection refused" in r.getMessage()]
    assert records, "失败原因没有留下任何记录"
    assert all(r.levelno >= logging.WARNING for r in records), \
        "级别低于 WARNING 时在 uvicorn 下不可见"


def test_log_validates_and_calls_core(client, monkeypatch):
    calls = []
    monkeypatch.setattr(
        app_module.log, "log",
        lambda text, type_=None: calls.append((text, type_)) or "inbox")

    assert client.post("/log", json={"text": ""}).status_code == 422  # 校验

    response = client.post("/log", json={"text": "note", "type": "idea"})
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert calls == [("note", "idea")]


def test_sync_starts_background_and_reports_status(client, monkeypatch):
    monkeypatch.setattr(app_module.sync, "run",
                        lambda day: {"date": str(day), "upserted": 1})

    response = client.post("/sync", json={})
    assert response.status_code == 202

    # 后台线程很快完成；轮询状态直到非 running
    import time
    for _ in range(50):
        status = client.get("/sync/status").json()
        if not status["running"]:
            break
        time.sleep(0.05)
    assert status["last"]["upserted"] == 1
    assert status["error"] is None


def test_sync_rejects_when_already_running(client):
    _sync_state["running"] = True
    assert client.post("/sync", json={}).status_code == 409
    _sync_state["running"] = False


def test_sync_failure_is_logged(monkeypatch, caplog):
    """后台线程里的失败，只写进 /sync/status；没人轮询就彻底没了。

    状态面板那条出口保留（它是对外契约），但服务端也得留一份。
    """
    def boom(*a, **k):
        raise RuntimeError("qdrant 没起")

    monkeypatch.setattr(app_module.sync, "run", boom)

    with caplog.at_level(logging.ERROR, logger="src.api.app"):
        app_module._run_sync(None)

    assert "qdrant 没起" in _sync_state["error"]          # 出口照旧
    assert any(r.exc_info and "qdrant 没起" in str(r.exc_info[1])
               for r in caplog.records)


def test_ask_returns_answer_with_citations(client, monkeypatch):
    citation = Citation(id="i", date="2026-09-18", type="error",
                        text="boom", source="claude_code",
                        source_refs=["s"], score=0.9)
    monkeypatch.setattr(
        app_module.ask, "query",
        lambda q, **kw: Answer(question=q, text="答案 [2026-09-18]",
                               citations=[citation], expanded=[citation]))

    response = client.get("/ask", params={"q": "踩过什么坑", "type": "error"})

    assert response.status_code == 200
    body = response.json()
    assert body["answer"].startswith("答案")
    assert body["citations"][0]["type"] == "error"
    assert body["expanded"][0]["date"] == "2026-09-18"


def test_ask_requires_q(client):
    assert client.get("/ask").status_code == 422


def test_ask_translates_failure_to_502(client, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("ark down")
    monkeypatch.setattr(app_module.ask, "query", boom)

    assert client.get("/ask", params={"q": "x"}).status_code == 502


def test_ask_failure_is_logged_server_side(client, monkeypatch, caplog):
    """502 之外还必须在服务端留下 traceback。

    HTTPException 被 FastAPI 的异常处理器接住，不会进 uvicorn 的错误日志；
    没有这一行，没人在终端盯着的时候报错就彻底查不到。
    """
    def boom(*a, **k):
        raise RuntimeError("ark down")
    monkeypatch.setattr(app_module.ask, "query", boom)

    with caplog.at_level(logging.ERROR, logger="src.api.app"):
        assert client.get("/ask", params={"q": "x"}).status_code == 502

    assert any(r.exc_info and "ark down" in str(r.exc_info[1])
               for r in caplog.records)


# --- GET /ask/stream（SSE）---


def _sse_events(body: str) -> list[tuple[str, str]]:
    """把 SSE 正文解析成 (event, data) 列表，忽略心跳/空行。"""
    events = []
    for block in body.split("\n\n"):
        name, data = None, None
        for line in block.splitlines():
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: "):
                data = line[6:]
        if name and data is not None:
            events.append((name, data))
    return events


def test_ask_stream_emits_citations_then_chunks_then_done(client, monkeypatch):
    """SSE 契约：引用先给（来自检索，不必等生成），再逐块正文，最后 done。

    引用事件放在最前是有意的：客户端可以先渲染出"依据了哪几条"，用户
    在等第一个字的时候就有东西看 —— 这正是流式要解决的问题。
    """
    citation = Citation(id="i", date="2026-09-18", type="error",
                        text="boom", source="claude_code",
                        source_refs=["s"], score=0.9)
    monkeypatch.setattr(
        app_module.ask, "query_stream",
        lambda q, **kw: ask_module.StreamedAnswer(
            question=q, citations=[citation], expanded=[],
            chunks=iter(["409 的", "根因是维度不匹配"])))

    response = client.get("/ask/stream", params={"q": "踩过什么坑"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _sse_events(response.text)
    assert [name for name, _ in events] == ["citations", "chunk", "chunk", "done"]

    cited = json.loads([d for n, d in events if n == "citations"][0])
    chunks = [json.loads(d)["text"] for n, d in events if n == "chunk"]
    assert cited["citations"][0]["date"] == "2026-09-18"
    assert cited["citations"][0]["type"] == "error"
    assert chunks == ["409 的", "根因是维度不匹配"]


def test_ask_stream_reports_generation_failure_as_an_event(client, monkeypatch):
    """生成中途失败时 HTTP 状态码已经发出去了，只能用一个 error 事件收场。

    若这里抛异常，客户端看到的是"连接被切断"——它既不知道失败了，也拿不到
    已收到的部分和失败原因。
    """
    def broken_chunks():
        yield "部分回答"
        raise RuntimeError("ark 半路断了")

    monkeypatch.setattr(
        app_module.ask, "query_stream",
        lambda q, **kw: ask_module.StreamedAnswer(
            question=q, citations=[], expanded=[], chunks=broken_chunks()))

    response = client.get("/ask/stream", params={"q": "x"})

    assert response.status_code == 200
    events = _sse_events(response.text)
    # citations 帧照发（此时为空），随后是已产出的正文块，最后以 error 收场
    assert [name for name, _ in events] == ["citations", "chunk", "error"]
    assert json.loads(events[-2][1])["text"] == "部分回答"
    assert "ark 半路断了" in json.loads(events[-1][1])["message"]


def test_ask_stream_generation_failure_is_logged(client, monkeypatch, caplog):
    """生成中途的失败只以 SSE 事件送出：客户端不读，现场就彻底蒸发。"""
    def broken_chunks():
        yield "部分回答"
        raise RuntimeError("ark 半路断了")

    monkeypatch.setattr(
        app_module.ask, "query_stream",
        lambda q, **kw: ask_module.StreamedAnswer(
            question=q, citations=[], expanded=[], chunks=broken_chunks()))

    with caplog.at_level(logging.ERROR, logger="src.api.app"):
        client.get("/ask/stream", params={"q": "x"})

    assert any(r.exc_info and "ark 半路断了" in str(r.exc_info[1])
               for r in caplog.records)


def test_ask_stream_translates_retrieval_failure_to_502(client, monkeypatch, caplog):
    """检索阶段就失败（还没开始流）时必须给正常的 502，而不是空流。"""
    def boom(*a, **k):
        raise RuntimeError("ark down")
    monkeypatch.setattr(app_module.ask, "query_stream", boom)

    with caplog.at_level(logging.ERROR, logger="src.api.app"):
        assert client.get("/ask/stream", params={"q": "x"}).status_code == 502

    assert any(r.exc_info and "ark down" in str(r.exc_info[1])
               for r in caplog.records)


def test_ask_stream_requires_q(client):
    assert client.get("/ask/stream").status_code == 422


def test_ask_stream_forwards_filters(client, monkeypatch):
    """过滤参数漏传会让流式回答混进不符合条件的条目。"""
    calls = []
    monkeypatch.setattr(
        app_module.ask, "query_stream",
        lambda q, **kw: calls.append((q, kw)) or ask_module.StreamedAnswer(
            question=q, citations=[], expanded=[], chunks=iter([])))

    client.get("/ask/stream", params={
        "q": "x", "type": "error", "project": "first-rag",
        "since": "7d", "expand": False})

    assert calls == [("x", {"type": "error", "project": "first-rag",
                            "since": "7d", "until": None, "expand": False})]
