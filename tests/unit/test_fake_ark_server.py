"""`scripts/fake_ark_server.py` 的单元测试。

为什么给一个"测试替身"写单测：它是冒烟能不能在 CI 上跑起来的前提（CI 没有
ARK_API_KEY，第 ④ 步编辑必须有个东西应答嵌入请求）。它自己坏掉时的表现是
"e2e 第 ④ 步 500"，看不出根因。这里钉三件事：维度跟着环境变量走、向量数与
输入严格对应、**只服务 embeddings** —— 最后一条是契约：冒烟哪天悄悄依赖上
chat，就该在这里红，而不是在线上没人发现。
"""

import sys
from pathlib import Path

from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import fake_ark_server as fake  # noqa: E402  (先补 sys.path 才能导入)


def test_default_dim_is_what_ac_001_measured():
    """默认维度必须与集合一致，否则编辑会因"维度不匹配"而失败。"""
    assert fake.DEFAULT_DIM == 2048


def test_dim_follows_the_environment(monkeypatch):
    """维度可配：换了嵌入模型（集合维度跟着换）时，冒烟改一个数就能跟上。"""
    monkeypatch.setenv("FAKE_ARK_DIM", "8")

    body = TestClient(fake.app).post("/v1/embeddings", json={"input": ["a"]}).json()

    assert len(body["data"][0]["embedding"]) == 8


def test_one_vector_per_input_keeps_the_input_order(monkeypatch):
    """ark_client 按 index 排序后对齐文本；顺序错位会让向量贴到别的条目上。"""
    monkeypatch.setenv("FAKE_ARK_DIM", "4")

    body = TestClient(fake.app).post(
        "/v1/embeddings", json={"input": ["a", "b", "c"]}
    ).json()

    assert [item["index"] for item in body["data"]] == [0, 1, 2]
    assert len(body["data"]) == 3


def test_health_reports_the_dim(monkeypatch):
    """Playwright 的 webServer 拿 `/` 判桩起来了没有。"""
    monkeypatch.setenv("FAKE_ARK_DIM", "16")

    assert TestClient(fake.app).get("/").json() == {"status": "ok", "dim": 16}


def test_chat_is_not_served_on_purpose():
    """chat 没实现是**故意**的（脚本头写了为什么），这条把它变成机械判据。"""
    response = TestClient(fake.app).post(
        "/v1/chat/completions", json={"model": "fake-chat", "messages": []}
    )

    assert response.status_code == 404
