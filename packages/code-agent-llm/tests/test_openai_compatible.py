import asyncio
from contextlib import suppress
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, cast

import pytest
from pydantic import SecretStr, ValidationError

from code_agent_llm import (
    FinishReason,
    GenerationConfig,
    Message,
    MessageRole,
    ModelEvent,
    ModelEventType,
    ModelProviderError,
    ModelRequest,
    ModelResponse,
    ModelToolSpec,
    NeverCancelToken,
    OpenAICompatibleConfig,
    OpenAICompatibleProvider,
    ProviderErrorCode,
    ToolCall,
)


class FakeStream:
    def __init__(self, chunks: list[object]) -> None:
        self._chunks = chunks
        self.closed = False

    def __aiter__(self) -> "FakeStream":
        return self

    async def __anext__(self) -> object:
        if not self._chunks:
            raise StopAsyncIteration
        return self._chunks.pop(0)

    async def close(self) -> None:
        self.closed = True


class FakeCompletions:
    def __init__(self, stream: FakeStream | Exception) -> None:
        self.stream = stream
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> FakeStream:
        self.calls.append(kwargs)
        if isinstance(self.stream, Exception):
            raise self.stream
        return self.stream


class FakeChat:
    def __init__(self, completions: FakeCompletions) -> None:
        self._completions = completions

    @property
    def completions(self) -> FakeCompletions:
        return self._completions


class FakeClient:
    def __init__(self, stream: FakeStream | Exception) -> None:
        self.completions = FakeCompletions(stream)
        self._chat = FakeChat(self.completions)

    @property
    def chat(self) -> FakeChat:
        return self._chat


def delta_chunk(
    *,
    content: str | None = None,
    tool_calls: list[object] | None = None,
    finish_reason: str | None = None,
    usage: object | None = None,
) -> object:
    choice = SimpleNamespace(
        finish_reason=finish_reason,
        delta=SimpleNamespace(content=content, tool_calls=tool_calls),
    )
    return SimpleNamespace(choices=[choice], usage=usage)


def tool_delta(
    index: int,
    *,
    call_id: str | None = None,
    name: str | None = None,
    arguments: str | None = None,
) -> object:
    return SimpleNamespace(
        index=index,
        id=call_id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


async def collect(provider: OpenAICompatibleProvider, request: ModelRequest) -> list[ModelEvent]:
    return [event async for event in provider.stream(request, NeverCancelToken())]


def config() -> OpenAICompatibleConfig:
    return OpenAICompatibleConfig(api_key=SecretStr("test-key"))


def test_config_hides_secret_and_protects_custom_endpoint() -> None:
    settings = config()

    assert "test-key" not in repr(settings)
    with pytest.raises(ValidationError, match="HTTPS"):
        OpenAICompatibleConfig(
            api_key=SecretStr("key"),
            base_url="http://example.com/v1",
        )
    with pytest.raises(ValidationError, match="private or restricted"):
        OpenAICompatibleConfig(
            api_key=SecretStr("key"),
            base_url="https://127.0.0.1/v1",
        )
    with pytest.raises(ValidationError, match="explicitly trusted"):
        OpenAICompatibleConfig(
            api_key=SecretStr("key"),
            base_url="https://models.example.com/v1",
        )

    local = OpenAICompatibleConfig(
        api_key=SecretStr("key"),
        allow_insecure_http=True,
        allow_private_base_url=True,
        base_url="http://127.0.0.1:11434/v1/",
    )
    assert local.base_url == "http://127.0.0.1:11434/v1"


def test_stream_normalizes_text_usage_and_request() -> None:
    usage = SimpleNamespace(prompt_tokens=3, completion_tokens=2, total_tokens=5)
    stream = FakeStream(
        [
            delta_chunk(content="hel"),
            delta_chunk(content="lo", finish_reason="stop"),
            SimpleNamespace(choices=[], usage=usage),
        ]
    )
    client = FakeClient(stream)
    provider = OpenAICompatibleProvider(config(), client=client)
    request = ModelRequest(
        messages=(Message(id="message-1", role=MessageRole.USER, content="hi"),),
        model="test-model",
        parameters=GenerationConfig(
            temperature=0.2,
            max_output_tokens=100,
            provider_options={"reasoning_effort": "medium"},
        ),
    )

    events = asyncio.run(collect(provider, request))

    assert [event.type for event in events] == [
        ModelEventType.TEXT_DELTA,
        ModelEventType.TEXT_DELTA,
        ModelEventType.USAGE,
        ModelEventType.COMPLETED,
    ]
    assert events[-1].response == ModelResponse(
        content="hello",
        finish_reason=FinishReason.STOP,
        usage=events[-2].usage,
    )
    sent = client.completions.calls[0]
    assert sent["stream"] is True
    assert sent["messages"] == [{"role": "user", "content": "hi"}]
    assert sent["max_completion_tokens"] == 100
    assert sent["extra_body"] == {"reasoning_effort": "medium"}


def test_stream_assembles_tool_call_without_parsing_arguments() -> None:
    stream = FakeStream(
        [
            delta_chunk(
                tool_calls=[tool_delta(0, call_id="call-1", name="read_file", arguments='{"path":')]
            ),
            delta_chunk(
                tool_calls=[tool_delta(0, arguments='"README.md"}')],
                finish_reason="tool_calls",
            ),
        ]
    )
    client = FakeClient(stream)
    provider = OpenAICompatibleProvider(config(), client=client)
    tool = ModelToolSpec(
        name="read_file",
        description="Read a file",
        input_schema={"type": "object"},
    )
    assistant = Message(
        id="message-1",
        role=MessageRole.ASSISTANT,
        tool_calls=(ToolCall(id="old-call", name="read_file", arguments_json="not-json"),),
    )
    request = ModelRequest(messages=(assistant,), tools=(tool,), model="test-model")

    events = asyncio.run(collect(provider, request))

    assert events[-1].response == ModelResponse(
        tool_calls=(
            ToolCall(
                id="call-1",
                name="read_file",
                arguments_json='{"path":"README.md"}',
            ),
        ),
        finish_reason=FinishReason.TOOL_CALLS,
    )
    sent = client.completions.calls[0]
    assert sent["messages"][0]["tool_calls"][0]["function"]["arguments"] == "not-json"
    assert sent["tools"][0]["function"]["name"] == "read_file"


def test_stream_rejects_data_after_finish_reason() -> None:
    stream = FakeStream(
        [
            delta_chunk(content="done", finish_reason="stop"),
            delta_chunk(content="unexpected"),
        ]
    )
    provider = OpenAICompatibleProvider(config(), client=FakeClient(stream))
    request = ModelRequest(
        messages=(Message(id="message-1", role=MessageRole.USER, content="hi"),),
        model="test-model",
    )

    with pytest.raises(ModelProviderError) as raised:
        asyncio.run(collect(provider, request))

    assert raised.value.code is ProviderErrorCode.MALFORMED_RESPONSE


def test_stream_without_finish_reason_is_malformed() -> None:
    provider = OpenAICompatibleProvider(
        config(),
        client=FakeClient(FakeStream([delta_chunk(content="partial")])),
    )
    request = ModelRequest(
        messages=(Message(id="message-1", role=MessageRole.USER, content="hi"),),
        model="test-model",
    )

    with pytest.raises(ModelProviderError) as raised:
        asyncio.run(collect(provider, request))

    assert raised.value.code is ProviderErrorCode.MALFORMED_RESPONSE


def test_provider_options_cannot_override_protocol_fields() -> None:
    provider = OpenAICompatibleProvider(config(), client=FakeClient(FakeStream([])))
    request = ModelRequest(
        messages=(Message(id="message-1", role=MessageRole.USER, content="hi"),),
        model="test-model",
        parameters=GenerationConfig(provider_options={"n": 2}),
    )

    with pytest.raises(ModelProviderError) as raised:
        asyncio.run(collect(provider, request))

    assert raised.value.code is ProviderErrorCode.INVALID_REQUEST


def test_custom_endpoint_rejects_private_dns_resolution() -> None:
    async def private_resolver(hostname: str) -> tuple[str, ...]:
        assert hostname == "models.example.com"
        return ("10.0.0.5",)

    settings = OpenAICompatibleConfig(
        api_key=SecretStr("key"),
        trusted_base_url_hosts=frozenset({"models.example.com"}),
        base_url="https://models.example.com/v1",
    )
    client = FakeClient(FakeStream([]))
    provider = OpenAICompatibleProvider(
        settings,
        client=client,
        resolver=private_resolver,
    )
    request = ModelRequest(
        messages=(Message(id="message-1", role=MessageRole.USER, content="hi"),),
        model="test-model",
    )

    with pytest.raises(ModelProviderError) as raised:
        asyncio.run(collect(provider, request))

    assert raised.value.code is ProviderErrorCode.INVALID_REQUEST
    assert not client.completions.calls


def test_multiple_choices_are_rejected() -> None:
    first = SimpleNamespace(
        index=0,
        finish_reason="stop",
        delta=SimpleNamespace(content="first", tool_calls=None),
    )
    second = SimpleNamespace(
        index=1,
        finish_reason="stop",
        delta=SimpleNamespace(content="second", tool_calls=None),
    )
    stream = FakeStream([SimpleNamespace(choices=[first, second], usage=None)])
    provider = OpenAICompatibleProvider(config(), client=FakeClient(stream))
    request = ModelRequest(
        messages=(Message(id="message-1", role=MessageRole.USER, content="hi"),),
        model="test-model",
    )

    with pytest.raises(ModelProviderError) as raised:
        asyncio.run(collect(provider, request))

    assert raised.value.code is ProviderErrorCode.MALFORMED_RESPONSE


def test_incomplete_tool_call_is_normalized_as_malformed_response() -> None:
    stream = FakeStream(
        [
            delta_chunk(
                tool_calls=[tool_delta(0, call_id="call-1", arguments="{}")],
                finish_reason="tool_calls",
            )
        ]
    )
    provider = OpenAICompatibleProvider(config(), client=FakeClient(stream))
    request = ModelRequest(
        messages=(Message(id="message-1", role=MessageRole.USER, content="hi"),),
        model="test-model",
    )

    with pytest.raises(ModelProviderError) as raised:
        asyncio.run(collect(provider, request))

    assert raised.value.code is ProviderErrorCode.MALFORMED_RESPONSE


def test_unexpected_client_error_is_sanitized() -> None:
    provider = OpenAICompatibleProvider(config(), client=FakeClient(RuntimeError("secret")))
    request = ModelRequest(
        messages=(Message(id="message-1", role=MessageRole.USER, content="hi"),),
        model="test-model",
    )

    with pytest.raises(ModelProviderError) as raised:
        asyncio.run(collect(provider, request))

    assert raised.value.code is ProviderErrorCode.UNKNOWN
    assert "secret" not in str(raised.value)
    assert "RuntimeError" in str(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


@dataclass
class MutableCancellation:
    is_cancelled: bool = False

    async def wait(self) -> None:
        while not self.is_cancelled:
            await asyncio.sleep(0)


@dataclass
class EventCancellation:
    event: asyncio.Event = field(default_factory=asyncio.Event)

    @property
    def is_cancelled(self) -> bool:
        return self.event.is_set()

    async def wait(self) -> None:
        await self.event.wait()

    def cancel(self) -> None:
        self.event.set()


def test_blocked_stream_read_is_immediately_cancellable() -> None:
    async def scenario() -> tuple[ProviderErrorCode, bool]:
        started = asyncio.Event()
        cancellation = EventCancellation()

        class BlockingStream(FakeStream):
            async def __anext__(self) -> object:
                started.set()
                await asyncio.Event().wait()
                raise AssertionError("unreachable")

        stream = BlockingStream([])
        provider = OpenAICompatibleProvider(config(), client=FakeClient(stream))
        model_request = ModelRequest(
            messages=(Message(id="message-1", role=MessageRole.USER, content="hi"),),
            model="test-model",
        )

        async def consume() -> None:
            async for _ in provider.stream(model_request, cancellation):
                pass

        task = asyncio.create_task(consume())
        await asyncio.wait_for(started.wait(), timeout=1)
        cancellation.cancel()
        try:
            await asyncio.wait_for(task, timeout=1)
        except ModelProviderError as error:
            return error.code, stream.closed
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError, ModelProviderError):
                await task
        raise AssertionError("stream was not cancelled")

    assert asyncio.run(scenario()) == (ProviderErrorCode.CANCELLED, True)


def test_blocked_request_creation_is_immediately_cancellable() -> None:
    async def scenario() -> ProviderErrorCode:
        started = asyncio.Event()
        cancellation = EventCancellation()

        class BlockingCompletions:
            async def create(self, **kwargs: Any) -> FakeStream:
                started.set()
                await asyncio.Event().wait()
                raise AssertionError("unreachable")

        blocking = BlockingCompletions()
        client = SimpleNamespace(chat=SimpleNamespace(completions=blocking))
        provider = OpenAICompatibleProvider(config(), client=cast(Any, client))
        model_request = ModelRequest(
            messages=(Message(id="message-1", role=MessageRole.USER, content="hi"),),
            model="test-model",
        )

        async def consume() -> None:
            async for _ in provider.stream(model_request, cancellation):
                pass

        task = asyncio.create_task(consume())
        await asyncio.wait_for(started.wait(), timeout=1)
        cancellation.cancel()
        try:
            await asyncio.wait_for(task, timeout=1)
        except ModelProviderError as error:
            return error.code
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError, ModelProviderError):
                await task
        raise AssertionError("request creation was not cancelled")

    assert asyncio.run(scenario()) is ProviderErrorCode.CANCELLED


def test_stream_closes_when_cancelled_between_chunks() -> None:
    cancellation = MutableCancellation()

    class CancellingStream(FakeStream):
        async def __anext__(self) -> object:
            chunk = await super().__anext__()
            if len(self._chunks) == 1:
                cancellation.is_cancelled = True
            return chunk

    stream = CancellingStream([delta_chunk(content="first"), delta_chunk(content="second")])
    provider = OpenAICompatibleProvider(config(), client=FakeClient(stream))
    request = ModelRequest(
        messages=(Message(id="message-1", role=MessageRole.USER, content="hi"),),
        model="test-model",
    )

    async def consume() -> None:
        async for _ in provider.stream(request, cancellation):
            pass

    with pytest.raises(ModelProviderError) as raised:
        asyncio.run(consume())

    assert raised.value.code is ProviderErrorCode.CANCELLED
    assert stream.closed
