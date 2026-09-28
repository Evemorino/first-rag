"""审阅页与只读端点的契约测试（T098 起，v0.9 由 T116 改口径）。

页面从 v0.9 起是 `web/` 前端工程的**构建产物**：`GET /` 读 `web/dist/index.html`，
静态资源走 `/assets/{path}`。所以这里用 monkeypatch `_WEB_DIST` 构造
"有产物 / 没产物"两种现场 —— 不去依赖仓库里真有没有 build 过。
"""

import re
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api import app as app_module
from src.api.app import app

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import dist_external_url_check as dist_urls  # noqa: E402  (先补 sys.path 才能导入)


@pytest.fixture
def client():
    return TestClient(app, base_url="http://127.0.0.1:8300")


@pytest.fixture
def built_dist(tmp_path, monkeypatch):
    """最小可用的假产物：入口 HTML + 一个带 hash 的资源。"""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(
        '<!doctype html><html><body><div id="root"></div>'
        '<script type="module" src="/assets/index-abc123.js"></script></body></html>',
        encoding="utf-8",
    )
    (dist / "assets" / "index-abc123.js").write_text("console.log('first-rag')\n", encoding="utf-8")
    monkeypatch.setattr(app_module, "_WEB_DIST", dist)
    return dist


# --- GET /：静态单页，零外链 ---


def test_index_serves_the_built_entry(client, built_dist):
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    body = response.text
    assert 'id="root"' in body               # Vite 的挂载点
    assert 'type="module"' in body           # 入口是 ES module，不是内联脚本


def test_assets_are_served_from_the_same_dist(client, built_dist):
    """/assets 必须和 `GET /` 指向同一个目录 —— 分开配置就是"页面能开、资源 404"。"""
    response = client.get("/assets/index-abc123.js")
    assert response.status_code == 200
    assert "first-rag" in response.text


def test_index_has_no_external_urls(client, built_dist):
    """NFR-009：断网也要能用。任何 http(s) 地址、协议相对地址、外链资源都算违规。"""
    body = client.get("/").text
    offenders = re.findall(r"(?:https?:)?//[A-Za-z0-9.-]+", body)
    assert not offenders, f"页面引用了外部地址：{sorted(set(offenders))}"


def test_index_does_not_load_remote_assets(client, built_dist):
    body = client.get("/").text
    for pattern in ("src=\"//", "href=\"//", "url(http", "@import"):
        assert pattern not in body, f"页面含外部资源引用：{pattern}"


def test_the_whole_build_output_has_no_external_urls():
    """整个 `dist/`（html + js + css）都不许有外部地址（T128）。

    上面两条只扫入口 HTML —— 而产物的主体是 `assets/*.js|css`，CDN、字体、
    `import("https://…")` 全藏在那儿。这条把它补齐：**扫整个产物**。

    `web/dist` 不入库，所以本机没构建过就跳过；**CI 里那一步是不可跳过的**
    （`pnpm build` 之后显式跑同一个脚本）—— 跳过的是本地便利，不是那条门禁。
    """
    dist = REPO_ROOT / "web" / "dist"
    if not dist.is_dir():
        pytest.skip("web/dist 还没构建（先 make ui）；CI 上这一步不可跳过")

    found = dist_urls.scan_dir(dist)

    assert not found, f"产物里有外部地址：{found}"


def test_missing_build_gives_503_with_the_next_step(client, tmp_path, monkeypatch):
    """产物没构建时不返回空白页，而是 503 + "先跑 make ui"（T115 的硬性要求）。"""
    monkeypatch.setattr(app_module, "_WEB_DIST", tmp_path / "not-built")
    response = client.get("/")
    assert response.status_code == 503
    assert "make ui" in response.json()["detail"]


def test_asset_path_traversal_is_blocked(client, built_dist, tmp_path):
    """`..` 不许逃出 assets/ —— 逃逸守卫由显式路由提供（不是 StaticFiles 的默认行为）。"""
    (built_dist / "secret.txt").write_text("nope", encoding="utf-8")
    assert client.get("/assets/../secret.txt").status_code == 404
    assert client.get("/assets/%2e%2e/secret.txt").status_code == 404
    assert client.get("/assets/missing.js").status_code == 404


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


def _view(entry_id: str = "abc", **overrides) -> dict:
    """形状完整的视图（T129 的 response_model 是白名单 + 必填，半截 dict 会 500）。"""
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


def test_entries_route_defaults_hide_deleted(client, monkeypatch):
    captured = {}
    monkeypatch.setattr(
        app_module.entries_module, "list_entries",
        lambda **kwargs: captured.update(kwargs) or {
            "entries": [], "total": 0, "page": 1, "page_size": 20,
        },
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
        lambda entry_id: _view(entry_id, prev_id=None, next_id=None),
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
