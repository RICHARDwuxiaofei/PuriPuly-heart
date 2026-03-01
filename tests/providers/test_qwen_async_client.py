from __future__ import annotations

import asyncio

import pytest

from puripuly_heart.providers.llm.qwen_async import HttpxQwenClient


def test_httpx_client_normalizes_language_codes() -> None:
    assert HttpxQwenClient._normalize_language_code("") == "auto"
    assert HttpxQwenClient._normalize_language_code("auto") == "auto"
    assert HttpxQwenClient._normalize_language_code("zh-CN") == "zh"
    assert HttpxQwenClient._normalize_language_code("zh-Hant") == "zh_tw"
    assert HttpxQwenClient._normalize_language_code("zh-TW") == "zh_tw"
    assert HttpxQwenClient._normalize_language_code("ko-KR") == "ko"
    assert HttpxQwenClient._normalize_language_code("en-US") == "en"
    assert HttpxQwenClient._normalize_language_code("ja") == "ja"


class FakeResponse:
    status_code = 200

    def __init__(self, data: dict | None = None):
        self._data = data or {"choices": [{"message": {"content": "OK"}}]}

    def json(self):
        return self._data

    def raise_for_status(self):
        pass


class FakeAsyncClient:
    def __init__(self):
        self.last_request: dict = {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def post(self, url, **kwargs):
        self.last_request = {"url": url, **kwargs}
        return FakeResponse()


@pytest.mark.asyncio
async def test_httpx_client_builds_correct_request(monkeypatch):
    fake_client = FakeAsyncClient()
    monkeypatch.setattr("httpx.AsyncClient", lambda **kw: fake_client)

    client = HttpxQwenClient(api_key="test-key", model="qwen3.5-flash", base_url="https://example")
    result = await client.translate(
        text="hello",
        system_prompt="SYSTEM",
        source_language="ko-KR",
        target_language="en",
        context='- "previous"',
    )

    assert result == "OK"

    # Check URL
    assert fake_client.last_request["url"] == "https://example/chat/completions"

    # Check headers
    headers = fake_client.last_request["headers"]
    assert headers["Authorization"] == "Bearer test-key"
    assert headers["Content-Type"] == "application/json"

    # Check body
    body = fake_client.last_request["json"]
    assert body["model"] == "qwen3.5-flash"
    assert body["enable_thinking"] is False
    assert body["messages"][0]["role"] == "system"
    assert "SYSTEM" in body["messages"][0]["content"]
    assert body["messages"][1]["role"] == "user"
    assert "<context>" in body["messages"][1]["content"]
    assert "Input: hello" in body["messages"][1]["content"]


@pytest.mark.asyncio
async def test_httpx_client_omits_empty_options(monkeypatch):
    fake_client = FakeAsyncClient()
    monkeypatch.setattr("httpx.AsyncClient", lambda **kw: fake_client)

    client = HttpxQwenClient(api_key="k", model="m", base_url="https://example")
    await client.translate(
        text="hello",
        system_prompt="SYSTEM",
        source_language="ko",
        target_language="en",
        context="",
    )

    body = fake_client.last_request["json"]
    assert body["messages"] == [
        {"role": "system", "content": "SYSTEM"},
        {"role": "user", "content": "hello"},
    ]


@pytest.mark.asyncio
async def test_httpx_client_raises_on_empty_choices(monkeypatch):
    class EmptyChoicesFakeAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, **kwargs):
            return FakeResponse({"choices": []})

    monkeypatch.setattr("httpx.AsyncClient", lambda **kw: EmptyChoicesFakeAsyncClient())

    client = HttpxQwenClient(api_key="k", model="m", base_url="https://example")
    with pytest.raises(RuntimeError, match="did not contain choices"):
        await client.translate(
            text="hello",
            system_prompt="SYSTEM",
            source_language="ko",
            target_language="en",
        )


@pytest.mark.asyncio
async def test_httpx_client_raises_on_empty_content(monkeypatch):
    class EmptyContentFakeAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, **kwargs):
            return FakeResponse({"choices": [{"message": {}}]})

    monkeypatch.setattr("httpx.AsyncClient", lambda **kw: EmptyContentFakeAsyncClient())

    client = HttpxQwenClient(api_key="k", model="m", base_url="https://example")
    with pytest.raises(RuntimeError, match="message content"):
        await client.translate(
            text="hello",
            system_prompt="SYSTEM",
            source_language="ko",
            target_language="en",
        )


@pytest.mark.asyncio
async def test_httpx_client_handles_cancellation(monkeypatch):
    class SlowFakeAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, **kwargs):
            await asyncio.sleep(10)  # Long wait
            return FakeResponse()

    monkeypatch.setattr("httpx.AsyncClient", lambda **kw: SlowFakeAsyncClient())

    client = HttpxQwenClient(api_key="k", model="m", base_url="https://example")

    async def translate_task():
        return await client.translate(
            text="hello",
            system_prompt="SYSTEM",
            source_language="ko",
            target_language="en",
        )

    task = asyncio.create_task(translate_task())
    await asyncio.sleep(0.05)  # Let it start
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
