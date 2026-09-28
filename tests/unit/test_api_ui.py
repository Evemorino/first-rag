"""审阅页与只读端点的契约测试（T098 / PRD AC-018、NFR-009、NFR-010）。

这一片是"只看不写"：页面能打开、能列条目、能看详情，且**不出本机、不拉外链**。
真正写库的能力（编辑/软删）在片 2，本文件刻意不覆盖。
"""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api import app as app_module
from src.api.app import app

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def client():
    return TestClient(app)


# --- GET /：静态单页，零外链 ---


def test_index_serves_the_review_page(client):
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    body = response.text
    assert "<title>" in body and "条目审阅" in body
    assert 'fetch("/entries?' in body or "fetch('/entries?" in body


def test_index_wires_related_and_neighbour_navigation(client):
    """FR-027：关联边与同日前后翻要能点 —— 页面里得有这两处接线（哪怕只是模板串）。"""
    body = client.get("/").text
    assert 'class="entry-link"' in body
    assert 'id="prev-entry"' in body and 'id="next-entry"' in body


def test_index_has_no_external_urls(client):
    """NFR-009：断网也要能用。任何 http(s) 地址、协议相对地址、外链资源都算违规。"""
    body = client.get("/").text
    offenders = re.findall(r"(?:https?:)?//[A-Za-z0-9.-]+", body)
    assert not offenders, f"页面引用了外部地址：{sorted(set(offenders))}"


def test_index_does_not_load_remote_assets(client):
    body = client.get("/").text
    for pattern in ("src=\"//", "href=\"//", "url(http", "@import"):
        assert pattern not in body, f"页面含外部资源引用：{pattern}"


# --- GET /types：类型枚举来自配置 ---


def test_types_come_from_config(client, monkeypatch):
    monkeypatch.setattr(
        app_module.config, "load_schema",
        lambda: {"types": [{"name": "error"}, {"name": "progress"}]},
    )
    assert client.get("/types").json() == {"types": ["error", "progress"]}


def test_types_tolerates_bare_strings(client, monkeypatch):
    monkeypatch.setattr(app_module.config, "load_schema", lambda: {"types": ["idea"]})
    assert client.get("/types").json() == {"types": ["idea"]}


# --- GET /entries：转发过滤条件、错误映射 ---


def test_entries_route_forwards_filters(client, monkeypatch):
    captured = {}

    def fake_list(**kwargs):
        captured.update(kwargs)
        return {"total": 0, "page": 1, "page_size": 50, "entries": []}

    monkeypatch.setattr(app_module.entries_module, "list_entries", fake_list)
    response = client.get(
        "/entries",
        params={
            "date_from": "2026-09-01",
            "date_to": "2026-09-30",
            "type": "error",
            "project": "first-rag",
            "source": "claude_code",
            "include_deleted": "true",
            "page": "3",
        },
    )

    assert response.status_code == 200
    assert captured == {
        "date_from": "2026-09-01",
        "date_to": "2026-09-30",
        "type": "error",
        "project": "first-rag",
        "source": "claude_code",
        "include_deleted": True,
        "page": 3,
    }


def test_entries_route_defaults_hide_deleted(client, monkeypatch):
    captured = {}
    monkeypatch.setattr(
        app_module.entries_module, "list_entries",
        lambda **kwargs: captured.update(kwargs) or {"entries": [], "total": 0},
    )
    client.get("/entries")
    assert captured["include_deleted"] is False


def test_entries_route_maps_bad_page_to_400(client, monkeypatch):
    def boom(**_kwargs):
        raise ValueError("page 必须 ≥ 1")

    monkeypatch.setattr(app_module.entries_module, "list_entries", boom)
    response = client.get("/entries", params={"page": "0"})
    assert response.status_code == 400
    assert "page" in response.json()["detail"]


# --- GET /entries/{id} ---


def test_entry_detail_returns_payload(client, monkeypatch):
    monkeypatch.setattr(
        app_module.entries_module, "get_entry",
        lambda entry_id: {"id": entry_id, "text": "正文", "rev": 0},
    )
    response = client.get("/entries/abc")
    assert response.status_code == 200
    assert response.json()["id"] == "abc"


def test_entry_detail_missing_is_404(client, monkeypatch):
    monkeypatch.setattr(app_module.entries_module, "get_entry", lambda _id: None)
    assert client.get("/entries/nope").status_code == 404


# --- NFR-010：服务不出本机 ---


def _make_recipe(target: str) -> str:
    lines = (REPO_ROOT / "Makefile").read_text(encoding="utf-8").splitlines()
    recipe: list[str] = []
    collecting = False
    for line in lines:
        if line.startswith(f"{target}:"):
            collecting = True
            continue
        if collecting:
            if not line.startswith("\t"):
                break
            recipe.append(line)
    return "\n".join(recipe)


def test_serve_recipe_does_not_bind_all_interfaces():
    """写能力上线后，页面能改库；只要它还无鉴权（NG-005 未撤销），就不许绑 0.0.0.0。"""
    recipe = _make_recipe("serve")
    assert recipe, "没解析到 serve 配方 —— 检查器自己瞎了"
    assert "0.0.0.0" not in recipe
    if "--host" in recipe:
        assert "127.0.0.1" in recipe
