"""T041: API 四端点冒烟（FastAPI TestClient，AC-001 后半）。

路由层行为验证（校验/状态码/核心调用接线）；核心逻辑由各自模块的
单测与集成测试覆盖。
"""

from datetime import date

import pytest
from fastapi.testclient import TestClient

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
