"""好例子：一个薄、可测试的 OpenAI-compatible API Adapter。

这个文件只用于学习对比，不是 T004 的正式实现。
正式实现仍应由你自己写在 src/ark_client.py 中。
"""

from openai import OpenAI


# ===== 好例子：薄 API Adapter =====


def build_client(api_key: str, base_url: str) -> OpenAI:
    """根据调用方传入的配置创建 OpenAI-compatible client。

    好处：
    1. 密钥不进入源码；
    2. 不依赖模块级全局状态；
    3. 测试时可以只测试这个函数的参数传递，不需要真实网络。
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
    # 空输入没有业务意义，应该在发网络请求前就失败。
    if not texts:
        raise ValueError("texts must not be empty")

    # OpenAI SDK 支持一次传入多个字符串。
    # 这里使用一次批量请求，而不是对每条文本循环调用一次 API。
    response = client.embeddings.create(
        model=model,
        input=list(texts),
    )

    # 返回结果中的每个 item 都有 index 字段。
    # 先按 index 排序，再提取向量，可以确保结果顺序和输入顺序一致。
    ordered = sorted(response.data, key=lambda item: item.index)
    vectors = [item.embedding for item in ordered]

    # 如果 API 返回数量和输入数量不一致，立即失败。
    # 这种数据不能继续入库，否则后续很难排查文本和向量是否错位。
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

    if json_mode:
        # response_format 是官方 SDK 暴露的参数。
        # 这个模块只负责请求 JSON 输出，不负责解析 JSON。
        # JSON 解析属于后续 distill 模块的职责。
        request_options["response_format"] = {"type": "json_object"}

    response = client.chat.completions.create(
        model=model,
        messages=messages,
        **request_options,
    )

    # 只返回调用方需要的文本，不返回完整 SDK response 对象。
    # 这样调用方不需要了解 OpenAI SDK 的内部结构。
    return response.choices[0].message.content or ""
