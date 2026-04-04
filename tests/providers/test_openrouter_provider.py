from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import uuid4

import pytest

from puripuly_heart.providers.llm.openrouter import (
    HttpxOpenRouterClient,
    OpenRouterClient,
    OpenRouterLLMProvider,
)


@dataclass
class FakeOpenRouterClient(OpenRouterClient):
    last_call: dict[str, object] | None = None
    closed: bool = False
    stream_parts: list[str] | None = None

    async def translate(
        self,
        *,
        text: str,
        system_prompt: str,
        source_language: str,
        target_language: str,
        context: str = "",
    ) -> str:
        self.last_call = {
            "text": text,
            "system_prompt": system_prompt,
            "source_language": source_language,
            "target_language": target_language,
            "context": context,
        }
        return "TRANSLATED"

    async def stream_translate(
        self,
        *,
        text: str,
        system_prompt: str,
        source_language: str,
        target_language: str,
        context: str = "",
    ) -> AsyncIterator[str]:
        self.last_call = {
            "text": text,
            "system_prompt": system_prompt,
            "source_language": source_language,
            "target_language": target_language,
            "context": context,
        }
        for part in self.stream_parts or []:
            yield part

    async def close(self) -> None:
        self.closed = True


class FakeResponse:
    status_code = 200

    def __init__(self, data: dict | None = None):
        self._data = data or {"choices": [{"message": {"content": "OK"}}]}

    def json(self):
        return self._data

    def raise_for_status(self):
        pass


class FakeStreamResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        lines: tuple[str, ...] | None = None,
        body: bytes = b"",
    ):
        self.status_code = status_code
        self._lines = lines or (
            'data: {"choices":[{"delta":{"content":"he"}}]}',
            'data: {"choices":[{"delta":{"content":"llo"}}]}',
            "data: [DONE]",
        )
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def aread(self) -> bytes:
        return self._body

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class FakeAsyncClient:
    def __init__(
        self,
        *,
        response_data: dict | None = None,
        stream_response: FakeStreamResponse | None = None,
    ):
        self.last_request: dict = {}
        self.requests: list[dict] = []
        self.stream_requests: list[dict] = []
        self.closed = False
        self._response_data = response_data
        self._stream_response = stream_response or FakeStreamResponse()

    async def aclose(self):
        self.closed = True

    async def post(self, url, **kwargs):
        request = {"url": url, **kwargs}
        self.last_request = request
        self.requests.append(request)
        return FakeResponse(self._response_data)

    def stream(self, method, url, **kwargs):
        request = {"method": method, "url": url, **kwargs}
        self.last_request = request
        self.stream_requests.append(request)
        return self._stream_response


@pytest.mark.asyncio
async def test_openrouter_provider_uses_injected_client() -> None:
    fake = FakeOpenRouterClient()
    provider = OpenRouterLLMProvider(api_key="k", client=fake)

    utterance_id = uuid4()
    out = await provider.translate(
        utterance_id=utterance_id,
        text="hello",
        system_prompt="PROMPT",
        source_language="ko-KR",
        target_language="en",
    )

    assert out.utterance_id == utterance_id
    assert out.text == "TRANSLATED"
    assert fake.last_call == {
        "text": "hello",
        "system_prompt": "PROMPT",
        "source_language": "ko-KR",
        "target_language": "en",
        "context": "",
    }


@pytest.mark.asyncio
async def test_openrouter_provider_stream_translate_yields_cumulative_text() -> None:
    fake = FakeOpenRouterClient(stream_parts=["he", "llo"])
    provider = OpenRouterLLMProvider(api_key="k", client=fake)

    chunks = [
        chunk
        async for chunk in provider.stream_translate(
            utterance_id=uuid4(),
            text="hello",
            system_prompt="PROMPT",
            source_language="ko-KR",
            target_language="en",
        )
    ]

    assert chunks == ["he", "hello"]


@pytest.mark.asyncio
async def test_openrouter_provider_close_cleans_up() -> None:
    fake = FakeOpenRouterClient()
    provider = OpenRouterLLMProvider(api_key="k", client=fake)
    provider._internal_client = fake

    await provider.close()

    assert fake.closed is True
    assert provider._internal_client is None


@pytest.mark.asyncio
async def test_openrouter_verify_api_key_uses_key_endpoint(monkeypatch) -> None:
    seen: dict[str, object] = {}

    class FakeResponse:
        status_code = 200

    class FakeAsyncClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url, **kwargs):
            seen["url"] = url
            seen["headers"] = kwargs["headers"]
            return FakeResponse()

    monkeypatch.setattr("httpx.AsyncClient", FakeAsyncClient)

    ok = await OpenRouterLLMProvider.verify_api_key("secret")

    assert ok is True
    assert seen["url"] == "https://openrouter.ai/api/v1/key"
    assert seen["headers"]["Authorization"] == "Bearer secret"


@pytest.mark.asyncio
async def test_httpx_openrouter_client_builds_reasoning_disabled_request_with_ordered_fallback(
    monkeypatch,
) -> None:
    fake_client = FakeAsyncClient()
    monkeypatch.setattr("httpx.AsyncClient", lambda **_kwargs: fake_client)

    client = HttpxOpenRouterClient(
        api_key="test-key",
        model="google/gemma-4-26b-a4b-it",
        base_url="https://example",
    )
    result = await client.translate(
        text="hello",
        system_prompt="SYSTEM",
        source_language="ko-KR",
        target_language="en",
        context='- "previous"',
    )

    assert result == "OK"
    assert fake_client.last_request["url"] == "https://example/chat/completions"
    headers = fake_client.last_request["headers"]
    assert headers["Authorization"] == "Bearer test-key"
    assert headers["Content-Type"] == "application/json"

    body = fake_client.last_request["json"]
    assert body["model"] == "google/gemma-4-26b-a4b-it"
    assert body["reasoning"] == {"effort": "none"}
    assert body["provider"] == {"order": ["Novita", "Parasail"], "allow_fallbacks": True}
    assert body["messages"][0] == {"role": "system", "content": "SYSTEM"}
    assert body["messages"][1]["role"] == "user"
    assert "<context>" in body["messages"][1]["content"]
    assert "Input: hello" in body["messages"][1]["content"]


@pytest.mark.asyncio
async def test_httpx_openrouter_client_stream_translate_builds_streaming_request(monkeypatch) -> None:
    fake_client = FakeAsyncClient()
    monkeypatch.setattr("httpx.AsyncClient", lambda **_kwargs: fake_client)

    client = HttpxOpenRouterClient(api_key="k", model="m", base_url="https://example")
    chunks = [
        chunk
        async for chunk in client.stream_translate(
            text="hello",
            system_prompt="SYSTEM",
            source_language="ko",
            target_language="en",
            context="",
        )
    ]

    assert chunks == ["he", "llo"]
    assert len(fake_client.stream_requests) == 1
    request = fake_client.stream_requests[0]
    assert request["method"] == "POST"
    assert request["url"] == "https://example/chat/completions"
    assert request["headers"]["Authorization"] == "Bearer k"
    assert request["json"]["stream"] is True
    assert request["json"]["reasoning"] == {"effort": "none"}
    assert request["json"]["provider"] == {
        "order": ["Novita", "Parasail"],
        "allow_fallbacks": True,
    }


@pytest.mark.asyncio
async def test_httpx_openrouter_client_surfaces_stream_error_message(monkeypatch) -> None:
    fake_client = FakeAsyncClient(
        stream_response=FakeStreamResponse(
            status_code=429,
            lines=(),
            body=b'{"error":{"message":"quota exceeded"}}',
        )
    )
    monkeypatch.setattr("httpx.AsyncClient", lambda **_kwargs: fake_client)

    client = HttpxOpenRouterClient(api_key="k", model="m", base_url="https://example")

    with pytest.raises(RuntimeError, match="quota exceeded"):
        async for _chunk in client.stream_translate(
            text="hello",
            system_prompt="SYSTEM",
            source_language="ko",
            target_language="en",
        ):
            pass
