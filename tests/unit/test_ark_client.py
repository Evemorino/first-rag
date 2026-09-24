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


class FakeStreamChatCompletions(FakeChatCompletions):
    """流式版：create 返回一个 chunk 可迭代对象，而不是完整响应。

    故意混进空串与 None：真实流里首尾经常吐空 delta（role 帧、收尾帧），
    上层不该把它们当成"有内容"而多打印空行。
    """

    def __init__(self):
        super().__init__()
        self.deltas = ["Hel", "", None, "lo", " world"]

    def create(self, **kwargs):
        self.calls.append(kwargs)

        def gen():
            for delta in self.deltas:
                yield SimpleNamespace(
                    choices=[SimpleNamespace(
                        delta=SimpleNamespace(content=delta))])

        return gen()


class FakeClient:
    def __init__(self):
        self.embeddings = FakeEmbeddings()
        self.chat = SimpleNamespace(completions=FakeChatCompletions())


class FakeStreamClient(FakeClient):
    def __init__(self):
        super().__init__()
        self.chat = SimpleNamespace(completions=FakeStreamChatCompletions())


@pytest.fixture
def fake_client():
    return FakeClient()


@pytest.fixture
def fake_stream_client():
    return FakeStreamClient()


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


def test_chat_returns_empty_string_when_model_sends_no_content(
    fake_client, ark_env, monkeypatch
):
    """模型返回 content=None 时归一成空串，而不是把 None 交给上层。

    上层（distill 的 JSON 解析、ask 的回答渲染）拿到 None 会在更远的地方炸，
    排查成本比在这里做一次归一高得多。
    """
    monkeypatch.setattr(ark_client, "_client", lambda: fake_client)
    fake_client.chat.completions.content = None

    assert ark_client.chat([{"role": "user", "content": "hi"}]) == ""


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


def test_chat_forwards_max_tokens_when_provided(fake_client, ark_env, monkeypatch):
    """max_tokens 显式传入时透传给 SDK，用于 ask 的限长回答。

    默认 None 时整个调用字典里不能冒出 max_tokens —— 那会改变所有不传参的
    调用方（蒸馏的 json_mode 路径尤其不能限长，见 T031 截断坑），已由上面
    test_chat_defaults_to_plain_text... 的全字典比对间接钉住。
    """
    monkeypatch.setattr(ark_client, "_client", lambda: fake_client)
    messages = [{"role": "user", "content": "hello"}]

    ark_client.chat(messages, max_tokens=600)

    assert fake_client.chat.completions.calls == [
        {"model": "test-chat-model", "messages": messages, "max_tokens": 600}
    ]


def test_chat_disables_thinking_when_asked(fake_client, ark_env, monkeypatch):
    """thinking=False 时用 extra_body 关掉模型的思考链。

    这是 ask 延迟的真正杠杆：2026-09-25 实测同一个 doubao-seed-2.1-lite，
    思考开着 33.4s、关掉 1.6–4.1s（12×），而引用标记照常正确。原因是响应里
    一直带着 reasoning_content —— 首 token 前要先想完，思考长度与答案长度
    无关，所以此前"限长压耗时"的思路（max_tokens）根本够不着它。
    """
    monkeypatch.setattr(ark_client, "_client", lambda: fake_client)
    messages = [{"role": "user", "content": "hello"}]

    ark_client.chat(messages, thinking=False)

    assert fake_client.chat.completions.calls == [
        {
            "model": "test-chat-model",
            "messages": messages,
            "extra_body": {"thinking": {"type": "disabled"}},
        }
    ]


def test_chat_leaves_thinking_on_by_default(fake_client, ark_env, monkeypatch):
    """默认不碰 thinking —— 蒸馏依赖它，关掉会改变抽取质量。

    实测同一批 126k 字符素材：思考开 9 条 / 关 28 条。条目更多不等于更好
    （蒸馏是质量敏感环节），所以默认必须是"什么都不传"，让服务端保持原样；
    只有显式 thinking=False 的调用方（ask）才关。
    """
    monkeypatch.setattr(ark_client, "_client", lambda: fake_client)
    messages = [{"role": "user", "content": "hello"}]

    ark_client.chat(messages)

    assert fake_client.chat.completions.calls == [
        {"model": "test-chat-model", "messages": messages}
    ]


def test_chat_stream_yields_content_deltas_in_order(fake_stream_client, ark_env, monkeypatch):
    """流式接口逐块吐出增量文本，顺序与到达顺序一致。

    空串与 None 的 delta 必须被丢掉：真实流的首尾帧经常是空的，原样交给
    CLI 会打出多余空行，交给 SSE 会多推空事件。
    """
    monkeypatch.setattr(ark_client, "_client", lambda: fake_stream_client)
    messages = [{"role": "user", "content": "hello"}]

    chunks = list(ark_client.chat_stream(messages))

    assert chunks == ["Hel", "lo", " world"]
    assert fake_stream_client.chat.completions.calls == [
        {"model": "test-chat-model", "messages": messages, "stream": True}
    ]


def test_chat_stream_forwards_max_tokens_and_thinking(fake_stream_client, ark_env, monkeypatch):
    """流式的 max_tokens / thinking 与一次性接口同义，不能各自为政。"""
    monkeypatch.setattr(ark_client, "_client", lambda: fake_stream_client)
    messages = [{"role": "user", "content": "hello"}]

    list(ark_client.chat_stream(messages, max_tokens=600, thinking=False))

    assert fake_stream_client.chat.completions.calls == [
        {
            "model": "test-chat-model",
            "messages": messages,
            "stream": True,
            "max_tokens": 600,
            "extra_body": {"thinking": {"type": "disabled"}},
        }
    ]
