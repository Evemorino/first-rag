"""好例子：一个薄、可测试的 OpenAI-compatible API Adapter。

来源依据：
1. openai-python 开源仓库：
   https://github.com/openai/openai-python/blob/main/src/openai/resources/embeddings.py
   其中 Embeddings.create 的 input 参数支持 Sequence[str]，也就是一次传入多条文本。
2. openai-python 的返回类型：
   https://github.com/openai/openai-python/blob/main/src/openai/types/embedding.py
   其中 Embedding 包含 embedding: List[float] 和 index: int。
3. OpenAI 官方文档：
   https://developers.openai.com/api/docs/guides/embeddings
   官方说明 embeddings 可以批量传入文本。

这个文件是学习对照，不是 T004 的正式实现。
"""

from openai import OpenAI


# ===== 好例子：薄 API Adapter =====


def build_client(api_key: str, base_url: str) -> OpenAI:
    """根据调用方传入的配置创建 OpenAI-compatible client。

    好在哪里：
    1. 密钥不进入源码，符合安全边界；
    2. base_url 不硬编码，可以指向 Ark 或其他兼容服务；
    3. 不依赖模块级全局状态，测试时可以直接传假配置；
    4. 函数只做一件事：创建 client。
    """
    return OpenAI(
        api_key=api_key,
        base_url=base_url,
    )


def embed_texts(
    client: OpenAI,
    model: str,
    texts: list[str],
) -> list[list[float]]:
    """批量生成 embedding，并保持输出顺序与输入顺序一致。"""
    # 好在哪里：空输入在发网络请求前失败，避免无意义的 API 调用。
    if not texts:
        raise ValueError("texts must not be empty")

    # 好在哪里：官方 SDK 支持批量 input。
    # 这里一次请求处理全部文本，而不是循环调用 N 次网络接口。
    response = client.embeddings.create(
        model=model,
        input=list(texts),
    )

    # 好在哪里：返回项自带 index 字段。
    # 先按 index 排序，再提取向量，确保第 i 个向量对应第 i 条输入文本。
    ordered = sorted(response.data, key=lambda item: item.index)
    vectors = [item.embedding for item in ordered]

    # 好在哪里：数量不一致时立即失败，避免把错误数据继续传给 Qdrant。
    if len(vectors) != len(texts):
        raise RuntimeError(
            f"embedding response count mismatch: expected {len(texts)}, "
            f"got {len(vectors)}"
        )

    return vectors


def chat_text(
    client: OpenAI,
    model: str,
    messages: list[dict[str, str]],
    *,
    json_mode: bool = False,
) -> str:
    """调用 chat completion 接口，并返回助手文本内容。"""
    request_options: dict[str, object] = {}

    # 好在哪里：使用官方 SDK 暴露的 response_format 参数。
    # 这个 adapter 只负责请求 JSON 输出，不负责解析 JSON。
    if json_mode:
        request_options["response_format"] = {"type": "json_object"}

    response = client.chat.completions.create(
        model=model,
        messages=messages,
        **request_options,
    )

    # 好在哪里：只返回调用方需要的文本，不暴露 SDK 的完整响应结构。
    return response.choices[0].message.content or ""
