"""OpenAI Chat Completions compatible streaming provider."""

import socket
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import suppress
from ipaddress import ip_address, ip_network
from typing import Any, Protocol, cast
from urllib.parse import urlparse

import openai
from openai import AsyncOpenAI
from pydantic import Field, SecretStr, ValidationError, field_validator

from ..provider import (
    CancellationToken,
    ModelCapability,
    ModelProviderError,
    ProviderErrorCode,
    ProviderInfo,
    await_with_cancellation,
)
from ..types import (
    FinishReason,
    Message,
    MessageRole,
    ModelEvent,
    ModelEventType,
    ModelRequest,
    ModelResponse,
    ProtocolModel,
    TokenUsage,
    ToolCall,
    ToolCallDelta,
)

_RESERVED_OPTIONS = {
    "max_completion_tokens",
    "messages",
    "model",
    "n",
    "seed",
    "stop",
    "stream",
    "stream_options",
    "temperature",
    "tools",
    "top_p",
}
_DENIED_NETWORKS = tuple(
    ip_network(network)
    for network in (
        "9.0.0.0/8",
        "10.0.0.0/8",
        "11.0.0.0/8",
        "21.0.0.0/8",
        "30.0.0.0/8",
    )
)


class _ChatCompletions(Protocol):
    async def create(self, **kwargs: Any) -> Any:
        """Create a streaming chat completion."""
        ...


class _ChatNamespace(Protocol):
    @property
    def completions(self) -> _ChatCompletions:
        """Return the chat completions resource."""
        ...


class _OpenAIClient(Protocol):
    @property
    def chat(self) -> _ChatNamespace:
        """Return the chat resource namespace."""
        ...


class OpenAICompatibleConfig(ProtocolModel):
    """Connection settings for an OpenAI-compatible endpoint."""

    api_key: SecretStr
    allow_insecure_http: bool = False
    allow_private_base_url: bool = False
    trusted_base_url_hosts: frozenset[str] = frozenset()
    base_url: str | None = None
    timeout_seconds: float = Field(default=60.0, gt=0)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str | None, info: Any) -> str | None:
        if value is None:
            return None
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("base_url must be an absolute HTTP(S) URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("base_url cannot contain userinfo, query, or fragment")
        if parsed.scheme != "https" and not bool(info.data.get("allow_insecure_http", False)):
            raise ValueError("base_url must use HTTPS unless explicitly allowed")
        allow_private = bool(info.data.get("allow_private_base_url", False))
        if not allow_private and _is_denied_host(parsed.hostname):
            raise ValueError("private or restricted base_url must be explicitly allowed")
        try:
            ip_address(parsed.hostname)
        except ValueError:
            trusted_hosts = info.data.get("trusted_base_url_hosts", frozenset())
            if parsed.hostname.lower() not in trusted_hosts:
                raise ValueError("custom hostname must be explicitly trusted") from None
        return value.rstrip("/")


HostResolver = Callable[[str], Awaitable[tuple[str, ...]]]


async def _default_resolver(hostname: str) -> tuple[str, ...]:
    import asyncio

    loop = asyncio.get_running_loop()
    records = await loop.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    return tuple(sorted({record[4][0] for record in records}))


def _is_denied_host(hostname: str) -> bool:
    normalized = hostname.rstrip(".").lower()
    if normalized == "localhost" or normalized.endswith(".localhost"):
        return True
    try:
        address = ip_address(normalized)
    except ValueError:
        return False
    return not address.is_global or any(address in network for network in _DENIED_NETWORKS)


class _ToolCallBuffer:
    def __init__(self) -> None:
        self.id: str | None = None
        self.name: str | None = None
        self.arguments: list[str] = []


class OpenAICompatibleProvider:
    """Normalize an OpenAI-compatible streaming API to internal events."""

    def __init__(
        self,
        config: OpenAICompatibleConfig,
        *,
        client: _OpenAIClient | None = None,
        name: str = "openai",
        resolver: HostResolver | None = None,
    ) -> None:
        self._config = config
        self._client = client or cast(
            _OpenAIClient,
            AsyncOpenAI(
                api_key=config.api_key.get_secret_value(),
                base_url=config.base_url,
                timeout=config.timeout_seconds,
                max_retries=0,
                http_client=openai.DefaultAsyncHttpxClient(follow_redirects=False),
            ),
        )
        self._resolver = resolver or _default_resolver
        self.info = ProviderInfo(
            name=name,
            capabilities=frozenset(ModelCapability),
        )

    async def stream(
        self,
        request: ModelRequest,
        cancellation: CancellationToken,
    ) -> AsyncIterator[ModelEvent]:
        stream: AsyncIterator[Any] | None = None
        pending_error: ModelProviderError | None = None
        try:
            await self._validate_endpoint(cancellation)
            stream = cast(
                AsyncIterator[Any],
                await await_with_cancellation(
                    self._client.chat.completions.create(**self._request_kwargs(request)),
                    cancellation,
                ),
            )
            content_parts: list[str] = []
            tool_buffers: dict[int, _ToolCallBuffer] = {}
            usage: TokenUsage | None = None
            finish_reason: FinishReason | None = None

            while True:
                try:
                    chunk = await await_with_cancellation(anext(stream), cancellation)
                except StopAsyncIteration:
                    break

                chunk_usage = getattr(chunk, "usage", None)
                if chunk_usage is not None:
                    usage = TokenUsage(
                        input_tokens=chunk_usage.prompt_tokens,
                        output_tokens=chunk_usage.completion_tokens,
                        total_tokens=chunk_usage.total_tokens,
                    )
                    yield ModelEvent(type=ModelEventType.USAGE, usage=usage)

                choices = getattr(chunk, "choices", ())
                if finish_reason is not None and choices:
                    raise _malformed("provider returned data after the finish reason")
                if len(choices) > 1:
                    raise _malformed("multiple response choices are not supported")
                for choice in choices:
                    if getattr(choice, "index", 0) != 0:
                        raise _malformed("multiple response choices are not supported")
                    if choice.finish_reason is not None:
                        if finish_reason is not None:
                            raise _malformed("provider returned multiple finish reasons")
                        finish_reason = _finish_reason(choice.finish_reason)
                    delta = choice.delta
                    if delta.content:
                        content_parts.append(delta.content)
                        yield ModelEvent(
                            type=ModelEventType.TEXT_DELTA,
                            text_delta=delta.content,
                        )
                    for tool_delta in delta.tool_calls or ():
                        buffer = tool_buffers.setdefault(tool_delta.index, _ToolCallBuffer())
                        _merge_tool_delta(buffer, tool_delta)
                        function = tool_delta.function
                        yield ModelEvent(
                            type=ModelEventType.TOOL_CALL_DELTA,
                            tool_call_delta=ToolCallDelta(
                                index=tool_delta.index,
                                id=tool_delta.id,
                                name=function.name if function is not None else None,
                                arguments_delta=(
                                    function.arguments if function is not None else ""
                                ),
                            ),
                        )

            if finish_reason is None:
                raise _malformed("model stream ended without a finish reason")
            tool_calls = _complete_tool_calls(tool_buffers)
            if (finish_reason is FinishReason.TOOL_CALLS) != bool(tool_calls):
                raise _malformed("finish reason does not match streamed tool calls")
            response = ModelResponse(
                content="".join(content_parts),
                tool_calls=tool_calls,
                finish_reason=finish_reason,
                usage=usage,
            )
            yield ModelEvent(type=ModelEventType.COMPLETED, response=response)
        except ModelProviderError as error:
            pending_error = error
        except (ValidationError, AttributeError, TypeError, ValueError):
            pending_error = _malformed("model provider returned an invalid response")
        except Exception as error:
            pending_error = _normalize_error(error)
        finally:
            if stream is not None:
                await _close_stream(stream)
        if pending_error is not None:
            pending_error.__cause__ = None
            pending_error.__context__ = None
            pending_error.__traceback__ = None
            raise pending_error from None

    async def _validate_endpoint(self, cancellation: CancellationToken) -> None:
        if self._config.base_url is None or self._config.allow_private_base_url:
            return
        hostname = urlparse(self._config.base_url).hostname
        if hostname is None:
            raise ModelProviderError(
                ProviderErrorCode.INVALID_REQUEST,
                "model provider endpoint is invalid",
            )
        addresses = await await_with_cancellation(self._resolver(hostname), cancellation)
        if not addresses or any(_is_denied_host(address) for address in addresses):
            raise ModelProviderError(
                ProviderErrorCode.INVALID_REQUEST,
                "model provider endpoint resolves to a restricted address",
            )

    def _request_kwargs(self, request: ModelRequest) -> dict[str, Any]:
        parameters = request.parameters
        provider_options = dict(parameters.provider_options)
        forbidden = _RESERVED_OPTIONS.intersection(provider_options)
        if forbidden:
            joined = ", ".join(sorted(forbidden))
            raise ModelProviderError(
                ProviderErrorCode.INVALID_REQUEST,
                f"provider_options contains reserved fields: {joined}",
            )
        kwargs: dict[str, Any] = {
            "model": request.model,
            "messages": [_message_to_openai(message) for message in request.messages],
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if request.tools:
            kwargs["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.input_schema,
                    },
                }
                for tool in request.tools
            ]
        if parameters.temperature is not None:
            kwargs["temperature"] = parameters.temperature
        if parameters.top_p is not None:
            kwargs["top_p"] = parameters.top_p
        if parameters.max_output_tokens is not None:
            kwargs["max_completion_tokens"] = parameters.max_output_tokens
        if parameters.stop:
            kwargs["stop"] = list(parameters.stop)
        if parameters.seed is not None:
            kwargs["seed"] = parameters.seed
        if provider_options:
            kwargs["extra_body"] = provider_options
        return kwargs


def _message_to_openai(message: Message) -> dict[str, Any]:
    if message.role is MessageRole.ASSISTANT:
        result: dict[str, Any] = {
            "role": "assistant",
            "content": message.content or None,
        }
        if message.tool_calls:
            result["tool_calls"] = [
                {
                    "id": tool_call.id,
                    "type": "function",
                    "function": {
                        "name": tool_call.name,
                        "arguments": tool_call.arguments_json,
                    },
                }
                for tool_call in message.tool_calls
            ]
        return result
    if message.role is MessageRole.TOOL:
        return {
            "role": "tool",
            "content": message.content,
            "tool_call_id": message.tool_call_id,
        }
    return {"role": message.role.value, "content": message.content}


def _merge_tool_delta(buffer: _ToolCallBuffer, tool_delta: Any) -> None:
    if tool_delta.id:
        if buffer.id is not None and buffer.id != tool_delta.id:
            raise _malformed("streamed tool call changed its id")
        buffer.id = tool_delta.id
    function = tool_delta.function
    if function is None:
        return
    if function.name:
        if buffer.name is not None and buffer.name != function.name:
            raise _malformed("streamed tool call changed its name")
        buffer.name = function.name
    if function.arguments:
        buffer.arguments.append(function.arguments)


def _complete_tool_calls(buffers: dict[int, _ToolCallBuffer]) -> tuple[ToolCall, ...]:
    calls: list[ToolCall] = []
    ids: set[str] = set()
    for index in sorted(buffers):
        buffer = buffers[index]
        if not buffer.id or not buffer.name:
            raise _malformed(f"incomplete streamed tool call at index {index}")
        if buffer.id in ids:
            raise _malformed("provider returned duplicate tool call ids")
        ids.add(buffer.id)
        calls.append(
            ToolCall(
                id=buffer.id,
                name=buffer.name,
                arguments_json="".join(buffer.arguments) or "{}",
            )
        )
    return tuple(calls)


def _finish_reason(value: str) -> FinishReason:
    return {
        "stop": FinishReason.STOP,
        "tool_calls": FinishReason.TOOL_CALLS,
        "length": FinishReason.LENGTH,
        "content_filter": FinishReason.CONTENT_FILTER,
    }.get(value, FinishReason.UNKNOWN)


async def _close_stream(stream: object) -> None:
    close = getattr(stream, "close", None)
    if close is not None:
        with suppress(Exception):
            await close()


def _malformed(message: str) -> ModelProviderError:
    return ModelProviderError(ProviderErrorCode.MALFORMED_RESPONSE, message)


def _normalize_error(error: Exception) -> ModelProviderError:
    if isinstance(error, openai.AuthenticationError | openai.PermissionDeniedError):
        return ModelProviderError(
            ProviderErrorCode.AUTHENTICATION,
            "model provider authentication failed",
            status_code=getattr(error, "status_code", None),
        )
    if isinstance(error, openai.RateLimitError):
        return ModelProviderError(
            ProviderErrorCode.RATE_LIMIT,
            "model provider rate limit exceeded",
            retryable=True,
            status_code=error.status_code,
        )
    if isinstance(error, openai.APITimeoutError):
        return ModelProviderError(
            ProviderErrorCode.TIMEOUT,
            "model provider request timed out",
            retryable=True,
        )
    if isinstance(error, openai.APIConnectionError):
        return ModelProviderError(
            ProviderErrorCode.CONNECTION,
            "could not connect to model provider",
            retryable=True,
        )
    if isinstance(error, openai.BadRequestError):
        return ModelProviderError(
            ProviderErrorCode.INVALID_REQUEST,
            "model provider rejected the request",
            status_code=error.status_code,
        )
    if isinstance(error, openai.APIStatusError):
        if error.status_code == 408:
            return ModelProviderError(
                ProviderErrorCode.TIMEOUT,
                "model provider request timed out",
                retryable=True,
                status_code=error.status_code,
            )
        retryable = error.status_code >= 500
        return ModelProviderError(
            ProviderErrorCode.SERVER if retryable else ProviderErrorCode.UNKNOWN,
            "model provider returned an HTTP error",
            retryable=retryable,
            status_code=error.status_code,
        )
    return ModelProviderError(
        ProviderErrorCode.UNKNOWN,
        f"unexpected model provider error: {type(error).__name__}",
    )
