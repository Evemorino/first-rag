"""T004 的 Ark client 契约测试。

这些测试使用 fake client，不访问真实 Ark API，也不读取真实密钥。
"""

from types import SimpleNamespace

import pytest

from src import ark_client, config


class FakeEmbedding:
    def __init__(self, index: int, vector: list[float]):
        self.index = index
        self.embedding = vector


class FakeEmbeddings:
    def __init__(self):
        self.calls = []
        self.response_data = []

    def create(self, *, model: str, input: list[str]):
        self.calls.append({"model": model, "input": input})
        return SimpleNamespace(data=list(self.response_data))


class FakeChatCompletions:
    def __init__(self):
        self.calls = []
        self.content = "assistant text"

    def create(self, **kwargs):
        self.calls.append(kwargs)
        message = SimpleNamespace(content=self.content)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class FakeClient:
    def __init__(self):
        self.embeddings = FakeEmbeddings()
        self.chat = SimpleNamespace(completions=FakeChatCompletions())


@pytest.fixture
def fake_client():
    return FakeClient()


@pytest.fixture
def ark_env(monkeypatch):
    monkeypatch.setenv("ARK_API_KEY", "test-key")
    monkeypatch.setenv("ARK_BASE_URL", "https://ark.example.test/api/v3")
    monkeypatch.setenv("EMBED_MODEL", "test-embedding-model")
    monkeypatch.setenv("CHAT_MODEL", "test-chat-model")
    monkeypatch.setattr(config, "load_env", lambda: None)


def test_client_reads_ark_configuration_from_environment(monkeypatch, ark_env):
    created = {}

    class RecordedOpenAI:
        def __init__(self, **kwargs):
            created.update(kwargs)

    monkeypatch.setattr(ark_client, "OpenAI", RecordedOpenAI)

    ark_client._client()

    assert created == {
        "api_key": "test-key",
        "base_url": "https://ark.example.test/api/v3",
    }


def test_embed_rejects_empty_input_before_network_call(fake_client, ark_env, monkeypatch):
    def fail_client():
        raise AssertionError("client must not be created for empty input")

    monkeypatch.setattr(ark_client, "_client", fail_client)

    with pytest.raises(ValueError) as excinfo:
        ark_client.embed([])

    assert str(excinfo.value) == "texts must not be empty"


def test_embed_batches_texts_and_preserves_request_order(fake_client, ark_env, monkeypatch):
    monkeypatch.setattr(ark_client, "_client", lambda: fake_client)
    fake_client.embeddings.response_data = [
        FakeEmbedding(1, [0.2, 0.3]),
        FakeEmbedding(0, [0.0, 0.1]),
    ]

    vectors = ark_client.embed(["first", "second"])

    assert vectors == [[0.0, 0.1], [0.2, 0.3]]
    assert fake_client.embeddings.calls == [
        {"model": "test-embedding-model", "input": ["first", "second"]}
    ]


def test_embed_fails_when_response_count_mismatches(fake_client, ark_env, monkeypatch):
    monkeypatch.setattr(ark_client, "_client", lambda: fake_client)
    fake_client.embeddings.response_data = [FakeEmbedding(0, [0.1])]

    with pytest.raises(RuntimeError) as excinfo:
        ark_client.embed(["first", "second"])

    assert str(excinfo.value) == (
        "embedding response count mismatch: expected 2, got 1"
    )


def test_chat_returns_assistant_text_and_supports_json_mode(fake_client, ark_env, monkeypatch):
    monkeypatch.setattr(ark_client, "_client", lambda: fake_client)
    messages = [{"role": "user", "content": "hello"}]

    result = ark_client.chat(messages, json_mode=True)

    assert result == "assistant text"
    assert fake_client.chat.completions.calls == [
        {
            "model": "test-chat-model",
            "messages": messages,
            "response_format": {"type": "json_object"},
        }
    ]


def test_chat_defaults_to_plain_text_when_json_mode_is_omitted(
    fake_client, ark_env, monkeypatch
):
    """默认值是公开契约的一部分：不传 json_mode 就不该要 JSON 输出。

    之前只测了显式 `json_mode=True`，于是 `json_mode: bool = False` 被改成
    `True` 时测试全绿 —— 而那会改变所有不显式传参的调用方的行为。
    """
    monkeypatch.setattr(ark_client, "_client", lambda: fake_client)
    messages = [{"role": "user", "content": "hello"}]

    result = ark_client.chat(messages)

    assert result == "assistant text"
    # 整个调用字典都比对：多一个 response_format 就会失败
    assert fake_client.chat.completions.calls == [
        {"model": "test-chat-model", "messages": messages}
    ]
