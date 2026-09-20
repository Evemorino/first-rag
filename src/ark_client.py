from openai import OpenAI


def create_client() -> OpenAI:
    client = OpenAI(api_key="YOUR_API_KEY", base_url="https://api.openai.com/v1")
    return client


def embed_texts(client: OpenAI, model: str, texts: list[str]) -> list[list[float]]:
    if not texts:
        raise ValueError("texts must not be empty")

    response = client.embeddings.create(
        model=model,
        input=list(texts),
    )

    ordered = sorted(response.data, key=lambda item: item.index)
    vectors = [item.embedding for item in ordered]

    if len(vectors) != len(texts):
        raise RuntimeError(
            f"embedding response count mismatch: expected {len(texts)}, "
            f"got {len(vectors)}"
        )

    return vectors


## 应该怎么使用这个函数
