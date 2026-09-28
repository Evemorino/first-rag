"""本机边界：**所有**路由都只接受本机 `Host` / 本机 `Origin`（2026-09-28 安全复核的修复）。

背景见 `src/api/app.py` 的 `enforce_loopback` docstring：加固前只有三个写路由挂了
`require_local`，`/types`、`/entries`、`/ask`、`/sync`、`/log` 一个闸都没有 —— 一次
DNS rebinding 就能读全库、触发计费、往 `notes/inbox.md` 投毒（下次 sync 会把它蒸馏进库）。

判据有意做成**遍历 `app.routes`**：新加一条路由会自动掉进这张表里，不需要谁记得补一行
测试。这正是"每个写路由各自挂 `Depends`"那种修法失败的方式（当时就是这么漏掉的），
所以"覆盖全"这件事必须是机械的。
"""

import re

import pytest
from fastapi.testclient import TestClient

from src.api.app import app

LOCAL_HOST = "http://127.0.0.1:8300"
FOREIGN_HOST = {"Host": "evil.example:8300"}


def concrete(path: str) -> str:
    """`/entries/{entry_id}` 这类模板换成具体值 —— 只需要请求能走到中间件。"""
    return re.sub(r"\{[^}]+\}", "x", path)


def routable_paths() -> list[str]:
    return sorted({concrete(route.path) for route in app.routes if route.path.startswith("/")})


def client() -> TestClient:
    """默认 Host 是回环：`TestClient` 的默认 `testserver` 在本机边界下本来就该被拒。"""
    return TestClient(app, base_url=LOCAL_HOST)


@pytest.mark.parametrize("path", routable_paths())
def test_every_route_rejects_a_foreign_host(path):
    """外来 Host → 403，而且是在**处理器之前**拦下的（所以 /sync、/log 不会有副作用）。"""
    response = client().get(path, headers=FOREIGN_HOST)

    assert response.status_code == 403, f"{path} 在外来 Host 下没有被拦"
    assert "本机" in response.json()["detail"]


def test_a_foreign_origin_is_rejected_even_with_a_loopback_host():
    """rebinding 之外的另一半：普通跨站页面（Host 也指向本机时）同样要被 Origin 挡下。"""
    response = client().get(
        "/types", headers={"Origin": "https://evil.example", **FOREIGN_HOST}
    )
    assert response.status_code == 403

    response = client().get("/types", headers={"Origin": "https://evil.example"})
    assert response.status_code == 403


def test_loopback_host_and_origin_still_work():
    """本机页面照常用：没有 Origin（同源 GET）与带本机 Origin 都要能走。"""
    assert client().get("/types").status_code == 200
    assert client().get("/types", headers={"Origin": LOCAL_HOST}).status_code == 200


def test_a_foreign_host_cannot_start_a_sync():
    """最贵的那条路径单独钉一次：中间件在路由之前，所以坏 body 也到不了 `post_sync`。"""
    response = client().post("/sync", json={}, headers=FOREIGN_HOST)

    assert response.status_code == 403
    assert "本机" in response.json()["detail"]
