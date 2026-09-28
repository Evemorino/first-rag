"""写端点的契约与防护测试（T099/T100/T103；ADR-18～20）。

两组事：① 三个写端点把参数转发对、把核心层的业务异常映射成语义化状态码；
② **只接受来自本机页面的请求** —— 无鉴权服务里这是唯一防线，必须逐条钉住。
"""

import pytest
from fastapi.testclient import TestClient

from src.api import app as app_module
from src.api.app import app

LOCAL_HEADERS = {"host": "127.0.0.1:8300", "x-requested-with": "first-rag-ui"}


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def fake_edit(monkeypatch):
    captured = {}

    def edit(entry_id, **kwargs):
        captured["entry_id"] = entry_id
        captured.update(kwargs)
        return _view(entry_id, rev=kwargs["rev"] + 1, text=kwargs.get("text"))

    monkeypatch.setattr(app_module.entries_module, "edit_entry", edit)
    return captured


# --- 转发与状态码 ---


def _view(entry_id: str = "abc", **overrides) -> dict:
    """一个**形状完整**的视图，供桩函数返回。

    T129 给路由挂了 response_model 之后，它同时是白名单与必填表 —— 半截 dict
    （`{"id": ...}`）会被 FastAPI 判成 500 ResponseValidationError。这些用例本来
    只关心"参数有没有转发出去"，但既然路由会校验返回值，桩就得给出真实形状。
    """
    view = {
        "id": entry_id,
        "text": "正文",
        "type": "progress",
        "tags": ["t"],
        "project": None,
        "date": "2026-09-18",
        "source": "claude_code",
        "created_at": None,
        "source_refs": [],
        "distill_version": None,
        "related": [],
        "original_text": "正文",
        "edited": False,
        "edited_at": None,
        "deleted_at": None,
        "deleted_reason": None,
        "rev": 0,
    }
    return {**view, **overrides}


def test_patch_forwards_only_editable_fields(client, fake_edit):
    response = client.patch(
        "/entries/abc",
        json={"rev": 2, "text": "改了", "type": "idea", "tags": ["a"], "project": "p"},
        headers=LOCAL_HEADERS,
    )
    assert response.status_code == 200
    assert fake_edit == {
        "entry_id": "abc",
        "rev": 2,
        "text": "改了",
        "type": "idea",
        "tags": ["a"],
        "project": "p",
    }
    assert response.json()["rev"] == 3


@pytest.mark.parametrize(
    "error,status",
    [
        ("ConflictError", 409),
        ("ValidationError", 400),
        ("NotFoundError", 404),
    ],
)
def test_patch_maps_business_errors(client, monkeypatch, error, status):
    def boom(*_args, **_kwargs):
        raise getattr(app_module.entries_module, error)("业务上不行")

    monkeypatch.setattr(app_module.entries_module, "edit_entry", boom)
    response = client.patch(
        "/entries/abc", json={"rev": 0, "text": "x"}, headers=LOCAL_HEADERS
    )
    assert response.status_code == status
    assert "业务上不行" in response.json()["detail"] or status == 404


def test_patch_embedding_failure_is_502(client, monkeypatch):
    """Ark 挂了 = 整条编辑不生效，必须让人看见原因，而不是静默存文本。"""

    def boom(*_args, **_kwargs):
        raise RuntimeError("Ark timeout")

    monkeypatch.setattr(app_module.entries_module, "edit_entry", boom)
    response = client.patch(
        "/entries/abc", json={"rev": 0, "text": "x"}, headers=LOCAL_HEADERS
    )
    assert response.status_code == 502
    assert "Ark timeout" in response.json()["detail"]


def test_delete_and_restore_forward_rev(client, monkeypatch):
    deleted, restored = {}, {}
    monkeypatch.setattr(
        app_module.entries_module, "delete_entry",
        lambda entry_id, **kw: deleted.update(kw, id=entry_id) or _view(entry_id),
    )
    monkeypatch.setattr(
        app_module.entries_module, "restore_entry",
        lambda entry_id, **kw: restored.update(kw, id=entry_id) or _view(entry_id),
    )

    assert client.post(
        "/entries/abc/delete", json={"rev": 1, "reason": "错了"}, headers=LOCAL_HEADERS
    ).status_code == 200
    assert deleted == {"id": "abc", "rev": 1, "reason": "错了"}

    assert client.post(
        "/entries/abc/restore", json={"rev": 2}, headers=LOCAL_HEADERS
    ).status_code == 200
    assert restored == {"id": "abc", "rev": 2}


def test_patch_requires_rev(client, client_headers=None):
    response = client.patch("/entries/abc", json={"text": "x"}, headers=LOCAL_HEADERS)
    assert response.status_code == 422  # pydantic：rev 是必填


# --- 只接受本机页面（NFR-010 / ADR-20）---


@pytest.mark.parametrize(
    "headers",
    [
        {"host": "192.168.1.9:8300", "x-requested-with": "first-rag-ui"},
        {"host": "127.0.0.1:8300"},
        {"host": "127.0.0.1:8300", "x-requested-with": "first-rag-ui",
         "origin": "https://evil.example"},
    ],
)
def test_writes_reject_non_local_callers(client, headers):
    response = client.patch(
        "/entries/abc", json={"rev": 0, "text": "x"}, headers=headers
    )
    assert response.status_code == 403


def test_writes_accept_localhost_origin(client, monkeypatch):
    monkeypatch.setattr(
        app_module.entries_module, "edit_entry",
        lambda entry_id, **_kw: _view(entry_id),
    )
    response = client.patch(
        "/entries/abc",
        json={"rev": 0, "text": "x"},
        headers={**LOCAL_HEADERS, "origin": "http://localhost:8300"},
    )
    assert response.status_code == 200


def test_reads_do_not_require_the_header(client):
    """只读端点不设这道闸：它不改任何东西，加了只会让 curl 调试变难。"""
    assert client.get("/types").status_code == 200
