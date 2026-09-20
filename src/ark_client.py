"""火山方舟 OpenAI-compatible API 的薄客户端。

这个模块只负责 API 调用，不包含蒸馏、入库、检索等业务逻辑。
配置全部来自 .env；密钥和模型 ID 不硬编码在源码中。
"""

from openai import OpenAI

from src import config


def _client() -> OpenAI:
    """读取环境变量并创建 Ark OpenAI-compatible client。

    为什么每次调用时才创建 client：
    1. import 模块不会产生网络或环境读取副作用；
    2. 测试可以用 fake client 替换这个函数；
    3. 配置缺失时错误会在真正调用 API 前暴露。
    """
    config.load_env()
    return OpenAI(
        api_key=config.env("ARK_API_KEY"),
        base_url=config.env("ARK_BASE_URL"),
    )


def embed(texts: list[str]) -> list[list[float]]:
    """批量生成 embedding，并保持输出顺序与输入顺序一致。

    OpenAI Python SDK 的 embeddings.create 支持 Sequence[str]，
    因此这里一次请求处理全部文本，而不是每条文本单独调用一次。
    """
    if not texts:
        raise ValueError("texts must not be empty")

    response = _client().embeddings.create(
        model=config.env("EMBED_MODEL"),
        input=list(texts),
    )

    # API 返回项带有 index 字段。先按 index 排序，再提取向量，
    # 可以确保第 i 个向量对应第 i 条输入文本。
    ordered = sorted(response.data, key=lambda item: item.index)
    vectors = [item.embedding for item in ordered]

    if len(vectors) != len(texts):
        raise RuntimeError(
            f"embedding response count mismatch: expected {len(texts)}, "
            f"got {len(vectors)}"
        )

    return vectors


def chat(
    messages: list[dict[str, str]],
    json_mode: bool = False,
) -> str:
    """调用 chat completion 接口，并返回助手文本。

    json_mode=True 时只向 API 请求 JSON 输出格式；
    JSON 字符串的解析职责留给后续 distill 模块。
    """
    request_options: dict[str, object] = {}
    if json_mode:
        request_options["response_format"] = {"type": "json_object"}

    response = _client().chat.completions.create(
        model=config.env("CHAT_MODEL"),
        messages=messages,
        **request_options,
    )

    # 只返回调用方需要的文本，避免上层依赖 SDK 的完整响应结构。
    return response.choices[0].message.content or ""
