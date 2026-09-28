"""本地假 Ark：给 e2e 冒烟用的 OpenAI-compatible embeddings 桩。

为什么需要它
------------
8 步验收里第 ④ 步"编辑"会走 `src/entries.py:edit_entry` → `ark_client.embed`
重新算向量。这一步在开发者机器上打的是真 Ark，于是：

* CI 没有 `ARK_API_KEY`，编辑必然 500（真踩过 —— 修完"空库没有集合"那关，
  下一关就是它）；
* 本地每跑一次冒烟就花真钱、要联网，而它验的是 UI 行为，不是嵌入模型。

所以冒烟把 `ARK_BASE_URL` 指到本桩：向量固定为 FAKE_ARK_DIM 维的零向量
（默认 2048，AC-001 实测的 doubao-embedding-vision 维度）。**只做 embeddings**，
其它路径一律 404 —— 免得哪天有人让冒烟悄悄依赖上 chat，而没人看得出它没被验过。

它不碰 Qdrant、不写任何文件：写入边界门禁不管它，因为它压根没有写入点。

用法::

    python scripts/fake_ark_server.py [--port 8322] [--dim 2048]

正常由 `web/playwright.config.ts` 的 webServer 拉起来，不需要手动跑。
"""

from __future__ import annotations

import argparse
import os

from fastapi import FastAPI
from pydantic import BaseModel

DEFAULT_DIM = 2048
DEFAULT_PORT = 8322

app = FastAPI(title="fake-ark")


class EmbeddingsRequest(BaseModel):
    input: list[str] | str
    model: str | None = None


def _dim() -> int:
    """维度从环境变量读，所以测试改 env 就能改它，不必重启进程。"""
    return int(os.environ.get("FAKE_ARK_DIM", str(DEFAULT_DIM)))


@app.get("/")
def health() -> dict[str, object]:
    """Playwright 的 webServer 靠它判断桩起来了没有。"""
    return {"status": "ok", "dim": _dim()}


@app.post("/v1/embeddings")
def embeddings(request: EmbeddingsRequest) -> dict[str, object]:
    texts = [request.input] if isinstance(request.input, str) else request.input
    return {
        "object": "list",
        "model": request.model or "fake-embed",
        "data": [
            {"object": "embedding", "index": index, "embedding": [0.0] * _dim()}
            for index in range(len(texts))
        ],
        "usage": {"prompt_tokens": 0, "total_tokens": 0},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--dim", type=int, default=DEFAULT_DIM)
    args = parser.parse_args(argv)

    os.environ["FAKE_ARK_DIM"] = str(args.dim)

    import uvicorn

    print(f"fake ark listening on http://127.0.0.1:{args.port} (dim={args.dim})")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
