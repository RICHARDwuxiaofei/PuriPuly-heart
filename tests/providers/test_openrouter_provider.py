from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import uuid4

import pytest

from puripuly_heart.config.settings import OpenRouterProviderRouting, OpenRouterRoutingMode
from puripuly_heart.core.llm.attempt_metadata import (
    LLMAttemptMetadataRecorder,
    attempt_metadata_context,
)
from puripuly_heart.core.llm.routing_telemetry import OpenRouterGenerationTelemetry
from puripuly_heart.providers.llm.openrouter import (
    HttpxOpenRouterClient,
    OpenRouterClient,
    OpenRouterKeyMetadata,
    OpenRouterLLMProvider,
)


@dataclass
class FakeOpenRouterClient(OpenRouterClient):
    last_call: dict[str, object] | None = None
    closed: bool = False

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

    async def close(self) -> None:
        self.closed = True


class SpyRuntimeLogging:
    def __init__(self, *, detailed_return: bool = False) -> None:
        self.detailed_return = detailed_return
        self.detailed_messages: list[tuple[str, int]] = []
        self.basic_messages: list[tuple[str, int]] = []

    def emit_detailed(self, message: str, *, level: int = logging.INFO) -> bool:
        self.detailed_messages.append((message, level))
        return self.detailed_return

    def emit_basic(self, message: str, *, level: int = logging.INFO) -> None:
        self.basic_messages.append((message, level))


class FakeResponse:
    status_code = 200

    def __init__(
        self,
        data: dict | None = None,
        *,
        status_code: int | None = None,
        headers: dict[str, str] | None = None,
    ):
        self._data = data or {"choices": [{"message": {"content": "OK"}}]}
        if status_code is not None:
            self.status_code = status_code
        self.headers = headers or {}

    def json(self):
        return self._data

    def raise_for_status(self):
        pass


class FakeAsyncClient:
    def __init__(
        self,
        *,
        response_data: dict | None = None,
        generation_response_data: dict | None = None,
    ):
        self.last_request: dict = {}
        self.requests: list[dict] = []
        self.closed = False
        self._response_data = response_data
        self._generation_response_data = generation_response_data

    async def aclose(self):
        self.closed = True

    async def post(self, url, **kwargs):
        request = {"url": url, **kwargs}
        self.last_request = request
        self.requests.append(request)
        return FakeResponse(self._response_data)

    async def get(self, url, **kwargs):
        request = {"url": url, **kwargs}
        self.last_request = request
        self.requests.append(request)
        return FakeResponse(self._generation_response_data)


class FakeRoutingTelemetry:
    active = True

    def __init__(self) -> None:
        self.registered: list[str] = []
        self.generations: list[tuple[str, OpenRouterGenerationTelemetry]] = []
        self.failures: list[tuple[str, str, str]] = []

    def register_openrouter_generation(self, generation_id: str) -> str | None:
        self.registered.append(generation_id)
        return "capture-1"

    def record_openrouter_generation(
        self,
        capture_id: str,
        record: OpenRouterGenerationTelemetry,
    ) -> None:
        self.generations.append((capture_id, record))

    def record_openrouter_generation_failure(
        self,
        capture_id: str,
        generation_id: str,
        error_type: str,
    ) -> None:
        self.failures.append((capture_id, generation_id, error_type))


@pytest.mark.asyncio
async def test_httpx_openrouter_client_sends_models_for_combined_gemma_profile(
    monkeypatch,
) -> None:
    fake_client = FakeAsyncClient()
    monkeypatch.setattr("httpx.AsyncClient", lambda **_kwargs: fake_client)
    client = HttpxOpenRouterClient(
        api_key="test-key",
        model="google/gemma-4-26b-a4b-it",
        models=(
            "google/gemma-4-26b-a4b-it",
            "google/gemma-4-31b-it",
        ),
        provider_routing=OpenRouterProviderRouting.GEMMA4_26B_31B_LATENCY,
    )

    await client.translate(
        text="hello",
        system_prompt="SYSTEM",
        source_language="ko",
        target_language="en",
    )

    body = fake_client.last_request["json"]
    assert "model" not in body
    assert body["models"] == [
        "google/gemma-4-26b-a4b-it",
        "google/gemma-4-31b-it",
    ]
    assert body["provider"] == {
        "only": ["cloudflare", "coreweave/bf16", "open-inference/bf16"],
        "sort": {"by": "latency", "partition": "none"},
        "allow_fallbacks": True,
    }


@pytest.mark.asyncio
async def test_httpx_openrouter_client_second_fallback_has_single_cerebras_route(
    monkeypatch,
) -> None:
    fake_client = FakeAsyncClient()
    monkeypatch.setattr("httpx.AsyncClient", lambda **_kwargs: fake_client)
    client = HttpxOpenRouterClient(
        api_key="test-key",
        model="google/gemma-4-31b-it",
        provider_routing=OpenRouterProviderRouting.SECOND_FALLBACK,
    )

    await client.translate(
        text="hello",
        system_prompt="SYSTEM",
        source_language="ko",
        target_language="en",
    )

    body = fake_client.last_request["json"]
    assert body["model"] == "google/gemma-4-31b-it"
    assert "models" not in body
    assert body["provider"] == {
        "only": ["cerebras/fp16"],
        "allow_fallbacks": False,
    }


@pytest.mark.asyncio
async def test_httpx_openrouter_client_records_response_model_and_provider(
    monkeypatch,
) -> None:
    fake_client = FakeAsyncClient(
        response_data={
            "id": "gen-123",
            "model": "google/gemma-4-31b-it",
            "provider": "CoreWeave",
            "usage": {
                "prompt_tokens": 20,
                "completion_tokens": 8,
                "total_tokens": 28,
                "cost": 0.00042,
            },
            "choices": [{"message": {"content": "OK"}}],
        }
    )
    monkeypatch.setattr("httpx.AsyncClient", lambda **_kwargs: fake_client)
    client = HttpxOpenRouterClient(
        api_key="test-key",
        model="google/gemma-4-31b-it",
    )
    recorder = LLMAttemptMetadataRecorder()

    with attempt_metadata_context(recorder):
        await client.translate(
            text="hello",
            system_prompt="SYSTEM",
            source_language="ko",
            target_language="en",
        )

    assert recorder.response is not None
    assert recorder.response.model == "google/gemma-4-31b-it"
    assert recorder.response.provider == "CoreWeave"
    assert recorder.response.generation_id == "gen-123"
    assert recorder.response.prompt_tokens == 20
    assert recorder.response.completion_tokens == 8
    assert recorder.response.total_tokens == 28
    assert recorder.response.cost_usd == "0.00042"


@pytest.mark.asyncio
async def test_httpx_openrouter_client_enriches_generation_cost_when_capture_is_active(
    monkeypatch,
) -> None:
    fake_client = FakeAsyncClient(
        response_data={
            "id": "gen-456",
            "choices": [{"message": {"content": "OK"}}],
        },
        generation_response_data={
            "data": {
                "total_cost": 0.00123,
                "upstream_inference_cost": 0.0008,
                "provider_name": "CoreWeave",
                "model": "google/gemma-4-31b-it",
                "latency": 184,
                "generation_time": 90,
                "tokens_prompt": 31,
                "tokens_completion": 9,
                "cancelled": False,
                "is_byok": False,
                "finish_reason": "stop",
            }
        },
    )
    telemetry = FakeRoutingTelemetry()
    monkeypatch.setattr("httpx.AsyncClient", lambda **_kwargs: fake_client)
    client = HttpxOpenRouterClient(
        api_key="test-key",
        model="google/gemma-4-31b-it",
        routing_telemetry=telemetry,
    )

    await client.translate(
        text="hello",
        system_prompt="SYSTEM",
        source_language="ko",
        target_language="en",
    )
    await client.close()

    assert telemetry.registered == ["gen-456"]
    assert telemetry.failures == []
    assert len(telemetry.generations) == 1
    capture_id, generation = telemetry.generations[0]
    assert capture_id == "capture-1"
    assert generation.generation_id == "gen-456"
    assert generation.total_cost_usd == "0.00123"
    assert generation.upstream_inference_cost_usd == "0.0008"
    assert generation.provider_name == "CoreWeave"
    assert generation.prompt_tokens == 31
    assert generation.completion_tokens == 9


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
async def test_openrouter_provider_close_cleans_up() -> None:
    fake = FakeOpenRouterClient()
    provider = OpenRouterLLMProvider(api_key="k", client=fake)
    provider._internal_client = fake

    await provider.close()

    assert fake.closed is True
    assert provider._internal_client is None


def test_openrouter_provider_passes_max_tokens_to_internal_httpx_client() -> None:
    provider = OpenRouterLLMProvider(api_key="k", max_tokens=17)

    client = provider._get_client()

    assert isinstance(client, HttpxOpenRouterClient)
    assert client.max_tokens == 17


def test_openrouter_provider_passes_user_identifier_to_internal_httpx_client() -> None:
    provider = OpenRouterLLMProvider(api_key="k", user_identifier="user-123")

    client = provider._get_client()

    assert isinstance(client, HttpxOpenRouterClient)
    assert client.user_identifier == "user-123"


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
async def test_openrouter_fetch_key_metadata_uses_key_endpoint(monkeypatch) -> None:
    seen: dict[str, object] = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                "data": {
                    "limit": 0.07,
                    "limit_remaining": 0.05,
                    "usage": 0.02,
                }
            }

        def raise_for_status(self):
            return None

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

    metadata = await OpenRouterLLMProvider.fetch_key_metadata("secret")

    assert metadata == OpenRouterKeyMetadata(limit_usd=0.07, remaining_usd=0.05, usage_usd=0.02)
    assert seen["url"] == "https://openrouter.ai/api/v1/key"
    assert seen["headers"]["Authorization"] == "Bearer secret"


@pytest.mark.asyncio
async def test_httpx_openrouter_client_builds_reasoning_disabled_request_with_latency_sort(
    monkeypatch,
) -> None:
    fake_client = FakeAsyncClient()
    monkeypatch.setattr("httpx.AsyncClient", lambda **_kwargs: fake_client)

    client = HttpxOpenRouterClient(
        api_key="test-key",
        model="google/gemma-4-26b-a4b-it",
        base_url="https://example",
        user_identifier="  managed-user-123  ",
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
    assert body["max_tokens"] == 100
    assert body["reasoning"] == {"effort": "none"}
    assert body["user"] == "managed-user-123"
    assert body["provider"] == {
        "only": ["cloudflare", "parasail/bf16"],
        "sort": {"by": "latency"},
        "allow_fallbacks": True,
    }
    assert body["messages"][0] == {"role": "system", "content": "SYSTEM"}
    assert body["messages"][1]["role"] == "user"
    assert "<context>" in body["messages"][1]["content"]
    assert "</context>" in body["messages"][1]["content"]
    assert "<input>\nhello\n</input>" in body["messages"][1]["content"]
    assert "Input: hello" not in body["messages"][1]["content"]


@pytest.mark.asyncio
async def test_httpx_openrouter_client_gemma_uses_wafer_cloudflare_deepinfra_routing(
    monkeypatch,
) -> None:
    fake_client = FakeAsyncClient()
    monkeypatch.setattr("httpx.AsyncClient", lambda **_kwargs: fake_client)

    client = HttpxOpenRouterClient(
        api_key="test-key",
        model="google/gemma-4-26b-a4b-it",
        base_url="https://example",
    )
    await client.translate(
        text="hello",
        system_prompt="SYSTEM",
        source_language="ko-KR",
        target_language="en",
    )

    body = fake_client.last_request["json"]
    assert body["provider"] == {
        "only": ["cloudflare", "parasail/bf16"],
        "sort": {"by": "latency"},
        "allow_fallbacks": True,
    }


@pytest.mark.asyncio
async def test_httpx_openrouter_client_google_gemini_latency_denies_data_collection(
    monkeypatch,
) -> None:
    fake_client = FakeAsyncClient()
    monkeypatch.setattr("httpx.AsyncClient", lambda **_kwargs: fake_client)

    client = HttpxOpenRouterClient(
        api_key="test-key",
        model="google/gemini-3.1-flash-lite",
        base_url="https://example",
        provider_routing=OpenRouterProviderRouting.GOOGLE_GEMINI_LATENCY,
    )
    await client.translate(
        text="hello",
        system_prompt="SYSTEM",
        source_language="ko-KR",
        target_language="en",
    )

    body = fake_client.last_request["json"]
    assert body["provider"] == {
        "sort": "latency",
        "only": ["google-vertex", "google-ai-studio"],
        "allow_fallbacks": True,
        "data_collection": "deny",
    }


@pytest.mark.asyncio
async def test_httpx_openrouter_client_deepseek_only_routing_uses_deepseek_baidu_order(
    monkeypatch,
) -> None:
    fake_client = FakeAsyncClient()
    monkeypatch.setattr("httpx.AsyncClient", lambda **_kwargs: fake_client)

    client = HttpxOpenRouterClient(
        api_key="test-key",
        model="deepseek/deepseek-v4-flash",
        base_url="https://example",
        routing_mode=OpenRouterRoutingMode.LATENCY,
        provider_routing=OpenRouterProviderRouting.DEEPSEEK_ONLY,
    )
    await client.translate(
        text="hello",
        system_prompt="SYSTEM",
        source_language="ko-KR",
        target_language="zh-CN",
    )

    body = fake_client.last_request["json"]
    assert body["provider"] == {
        "only": ["baidu", "deepseek", "cloudflare"],
        "sort": {"by": "latency"},
        "allow_fallbacks": True,
    }


@pytest.mark.asyncio
async def test_httpx_openrouter_client_deepseek_default_uses_cloudflare_first_routing(
    monkeypatch,
) -> None:
    fake_client = FakeAsyncClient()
    monkeypatch.setattr("httpx.AsyncClient", lambda **_kwargs: fake_client)

    client = HttpxOpenRouterClient(
        api_key="test-key",
        model="deepseek/deepseek-v4-flash",
        base_url="https://example",
    )
    await client.translate(
        text="hello",
        system_prompt="SYSTEM",
        source_language="ko-KR",
        target_language="zh-CN",
    )

    body = fake_client.last_request["json"]
    assert body["provider"] == {
        "only": ["baidu", "deepseek", "cloudflare"],
        "sort": {"by": "latency"},
        "allow_fallbacks": True,
    }


@pytest.mark.asyncio
async def test_httpx_openrouter_client_gemma_order_ignores_explicit_latency_routing_mode(
    monkeypatch,
) -> None:
    fake_client = FakeAsyncClient()
    monkeypatch.setattr("httpx.AsyncClient", lambda **_kwargs: fake_client)

    client = HttpxOpenRouterClient(
        api_key="test-key",
        model="google/gemma-4-26b-a4b-it",
        base_url="https://example",
        routing_mode=OpenRouterRoutingMode.LATENCY,
    )
    result = await client.translate(
        text="hello",
        system_prompt="SYSTEM",
        source_language="ko-KR",
        target_language="en",
    )

    assert result == "OK"
    body = fake_client.last_request["json"]
    assert body["provider"] == {
        "only": ["cloudflare", "parasail/bf16"],
        "sort": {"by": "latency"},
        "allow_fallbacks": True,
    }


@pytest.mark.asyncio
async def test_httpx_openrouter_client_translate_raises_on_length_finish_reason(
    monkeypatch,
) -> None:
    fake_client = FakeAsyncClient(
        response_data={
            "choices": [
                {
                    "message": {"content": "partial"},
                    "finish_reason": "length",
                }
            ]
        }
    )
    monkeypatch.setattr("httpx.AsyncClient", lambda **_kwargs: fake_client)

    client = HttpxOpenRouterClient(api_key="k", model="m", base_url="https://example")

    with pytest.raises(RuntimeError, match="truncated"):
        await client.translate(
            text="hello",
            system_prompt="SYSTEM",
            source_language="ko",
            target_language="en",
        )


@pytest.mark.asyncio
async def test_httpx_openrouter_client_logs_basic_translate_success_without_runtime_logging(
    monkeypatch, caplog: pytest.LogCaptureFixture
) -> None:
    fake_client = FakeAsyncClient()
    monkeypatch.setattr("httpx.AsyncClient", lambda **_kwargs: fake_client)

    client = HttpxOpenRouterClient(api_key="k", model="m", base_url="https://example")

    with caplog.at_level(logging.INFO, logger="puripuly_heart.providers.llm.openrouter"):
        result = await client.translate(
            text="hello",
            system_prompt="SYSTEM",
            source_language="ko",
            target_language="en",
            context='- "previous"',
        )

    assert result == "OK"
    assert caplog.messages == [
        "[Basic][LLM] OpenRouter request [translate][context=yes] ko -> en: 'hello'",
        "[Basic][LLM] OpenRouter response [translate]: 'OK'",
    ]


@pytest.mark.asyncio
async def test_httpx_openrouter_client_logs_basic_translate_failure_without_runtime_logging(
    monkeypatch, caplog: pytest.LogCaptureFixture
) -> None:
    class ErrorResponse(FakeResponse):
        status_code = 429

        def __init__(self):
            super().__init__({"error": {"message": "quota exceeded"}})

        def raise_for_status(self):
            raise RuntimeError("quota exceeded")

    class ErrorAsyncClient(FakeAsyncClient):
        async def post(self, url, **kwargs):
            request = {"url": url, **kwargs}
            self.last_request = request
            self.requests.append(request)
            return ErrorResponse()

    fake_client = ErrorAsyncClient()
    monkeypatch.setattr("httpx.AsyncClient", lambda **_kwargs: fake_client)

    client = HttpxOpenRouterClient(api_key="k", model="m", base_url="https://example")

    with caplog.at_level(logging.INFO, logger="puripuly_heart.providers.llm.openrouter"):
        with pytest.raises(RuntimeError, match="OpenRouter request failed \\(status=429\\)"):
            await client.translate(
                text="hello",
                system_prompt="SYSTEM",
                source_language="ko",
                target_language="en",
            )

    assert caplog.messages == [
        "[Basic][LLM] OpenRouter request [translate][context=no] ko -> en: 'hello'",
        "[Basic][LLM] OpenRouter request failed [translate]: category=rate_limit code=provider.rate_limit status=429",
    ]
    assert "quota exceeded" not in "\n".join(caplog.messages)


@pytest.mark.asyncio
async def test_httpx_openrouter_client_runtime_logging_logs_basic_translate_success(
    monkeypatch, caplog: pytest.LogCaptureFixture
) -> None:
    fake_client = FakeAsyncClient()
    monkeypatch.setattr("httpx.AsyncClient", lambda **_kwargs: fake_client)
    runtime_logging = SpyRuntimeLogging(detailed_return=False)

    client = HttpxOpenRouterClient(
        api_key="k",
        model="m",
        base_url="https://example",
        runtime_logging=runtime_logging,
    )

    with caplog.at_level(logging.INFO, logger="puripuly_heart.providers.llm.openrouter"):
        result = await client.translate(
            text="hello",
            system_prompt="SYSTEM",
            source_language="ko",
            target_language="en",
            context='- "previous"',
        )

    assert result == "OK"
    assert runtime_logging.basic_messages == [
        (
            "[Basic][LLM] OpenRouter request [translate][context=yes] ko -> en: 'hello'",
            logging.INFO,
        ),
        ("[Basic][LLM] OpenRouter response [translate]: 'OK'", logging.INFO),
    ]
    assert runtime_logging.detailed_messages == []
    assert caplog.messages == []


@pytest.mark.asyncio
async def test_httpx_openrouter_client_runtime_logging_logs_basic_translate_failure(
    monkeypatch, caplog: pytest.LogCaptureFixture
) -> None:
    class ErrorResponse(FakeResponse):
        status_code = 429

        def __init__(self):
            super().__init__({"error": {"message": "quota exceeded"}})

    class ErrorAsyncClient(FakeAsyncClient):
        async def post(self, url, **kwargs):
            request = {"url": url, **kwargs}
            self.last_request = request
            self.requests.append(request)
            return ErrorResponse()

    fake_client = ErrorAsyncClient()
    monkeypatch.setattr("httpx.AsyncClient", lambda **_kwargs: fake_client)
    runtime_logging = SpyRuntimeLogging(detailed_return=False)

    client = HttpxOpenRouterClient(
        api_key="k",
        model="m",
        base_url="https://example",
        runtime_logging=runtime_logging,
    )

    with caplog.at_level(logging.INFO, logger="puripuly_heart.providers.llm.openrouter"):
        with pytest.raises(RuntimeError, match="OpenRouter request failed \\(status=429\\)"):
            await client.translate(
                text="hello",
                system_prompt="SYSTEM",
                source_language="ko",
                target_language="en",
            )

    assert runtime_logging.detailed_messages == []
    assert runtime_logging.basic_messages == [
        (
            "[Basic][LLM] OpenRouter request [translate][context=no] ko -> en: 'hello'",
            logging.INFO,
        ),
        (
            "[Basic][LLM] OpenRouter request failed [translate]: category=rate_limit code=provider.rate_limit status=429",
            logging.ERROR,
        ),
    ]
    assert "quota exceeded" not in repr(runtime_logging.basic_messages)
    assert caplog.messages == []
