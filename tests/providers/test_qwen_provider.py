from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

import pytest

from puripuly_heart.providers.llm.qwen import (
    DashScopeQwenClient,
    QwenClient,
    QwenLLMProvider,
)


@dataclass
class FakeQwenClient(QwenClient):
    last_call: dict[str, object] | None = None

    async def translate(
        self,
        *,
        text: str,
        source_language: str,
        target_language: str,
        domain_prompt: str = "",
        context_pairs: list[dict[str, str]] | None = None,
    ) -> str:
        self.last_call = {
            "text": text,
            "source_language": source_language,
            "target_language": target_language,
            "domain_prompt": domain_prompt,
            "context_pairs": context_pairs,
        }
        return "TRANSLATED"


@pytest.mark.asyncio
async def test_qwen_provider_uses_injected_client():
    fake = FakeQwenClient()
    provider = QwenLLMProvider(api_key="k", client=fake)

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
        "source_language": "ko-KR",
        "target_language": "en",
        "domain_prompt": "PROMPT",
        "context_pairs": None,
    }


def test_qwen_client_normalizes_language_codes() -> None:
    client = DashScopeQwenClient(api_key="k", model="m")
    assert client._normalize_language_code("") == "auto"
    assert client._normalize_language_code("auto") == "auto"
    assert client._normalize_language_code("zh-CN") == "zh"
    assert client._normalize_language_code("zh-Hant") == "zh_tw"
    assert client._normalize_language_code("ko-KR") == "ko"


@pytest.mark.asyncio
async def test_qwen_client_translates_with_options(monkeypatch):
    calls: dict[str, object] = {}

    class FakeResponse:
        output = {"choices": [{"message": {"content": "OK"}}]}

    class FakeGeneration:
        @staticmethod
        def call(**kwargs):
            calls.update(kwargs)
            return FakeResponse()

    class FakeDashScope:
        api_key = ""
        base_http_api_url = ""
        Generation = FakeGeneration

    monkeypatch.setitem(__import__("sys").modules, "dashscope", FakeDashScope)

    client = DashScopeQwenClient(api_key="k", model="m", base_url="https://example")
    result = await client.translate(
        text="hello",
        source_language="ko-KR",
        target_language="en",
        domain_prompt="domain",
        context_pairs=[{"source": "a", "target": "b"}],
    )

    assert result == "OK"
    options = calls["translation_options"]
    assert options["source_lang"] == "ko"
    assert options["target_lang"] == "en"
    assert options["domains"] == "domain"
    assert options["tm_list"] == [{"source": "a", "target": "b"}]


@pytest.mark.asyncio
async def test_qwen_client_raises_when_missing_content(monkeypatch):
    class FakeResponse:
        output = {"choices": [{"message": {}}]}

    class FakeGeneration:
        @staticmethod
        def call(**_kwargs):
            return FakeResponse()

    class FakeDashScope:
        api_key = ""
        base_http_api_url = ""
        Generation = FakeGeneration

    monkeypatch.setitem(__import__("sys").modules, "dashscope", FakeDashScope)

    client = DashScopeQwenClient(api_key="k", model="m")
    with pytest.raises(RuntimeError, match="message content"):
        await client.translate(text="hello", source_language="en", target_language="ko")


@pytest.mark.asyncio
async def test_qwen_verify_api_key_handles_status(monkeypatch):
    class FakeResponse:
        status_code = 200

    class FakeGeneration:
        @staticmethod
        def call(**_kwargs):
            return FakeResponse()

    class FakeDashScope:
        api_key = ""
        base_http_api_url = ""
        Generation = FakeGeneration

    monkeypatch.setitem(__import__("sys").modules, "dashscope", FakeDashScope)

    assert await QwenLLMProvider.verify_api_key("secret") is True
