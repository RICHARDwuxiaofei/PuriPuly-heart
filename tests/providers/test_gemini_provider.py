from __future__ import annotations

import sys
from dataclasses import dataclass
from types import ModuleType, SimpleNamespace
from uuid import uuid4

import pytest

from puripuly_heart.providers.llm.gemini import (
    GeminiClient,
    GeminiLLMProvider,
    GoogleGenaiGeminiClient,
)


@dataclass
class FakeGeminiClient(GeminiClient):
    last_call: dict[str, str] | None = None
    closed: bool = False

    async def translate(
        self,
        *,
        text: str,
        system_prompt: str,
        source_language: str,
        target_language: str,
        context: str = "",
        context_pairs: list[dict[str, str]] | None = None,
    ) -> str:
        _ = context_pairs
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


@pytest.mark.asyncio
async def test_gemini_provider_uses_injected_client():
    fake = FakeGeminiClient()
    provider = GeminiLLMProvider(api_key="k", client=fake)

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


def _install_fake_google(monkeypatch, *, response_text: str | None) -> dict[str, object]:
    state: dict[str, object] = {}

    class FakeGenerateContentConfig:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class FakeThinkingConfig:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class FakeAutomaticFunctionCallingConfig:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class FakeThinkingLevel:
        MINIMAL = "minimal"

    types_module = ModuleType("google.genai.types")
    types_module.GenerateContentConfig = FakeGenerateContentConfig
    types_module.ThinkingConfig = FakeThinkingConfig
    types_module.AutomaticFunctionCallingConfig = FakeAutomaticFunctionCallingConfig
    types_module.ThinkingLevel = FakeThinkingLevel

    class FakeModels:
        async def generate_content(self, **kwargs):
            state.update(kwargs)
            return SimpleNamespace(text=response_text)

    class FakeAio:
        def __init__(self):
            self.models = FakeModels()

    class FakeClient:
        def __init__(self, api_key):
            self.api_key = api_key
            self.aio = FakeAio()

    genai_module = ModuleType("google.genai")
    genai_module.Client = FakeClient
    genai_module.types = types_module

    google_module = ModuleType("google")
    google_module.genai = genai_module

    monkeypatch.setitem(sys.modules, "google", google_module)
    monkeypatch.setitem(sys.modules, "google.genai", genai_module)
    monkeypatch.setitem(sys.modules, "google.genai.types", types_module)

    return state


@pytest.mark.asyncio
async def test_gemini_provider_warmup_and_close_uses_client():
    fake = FakeGeminiClient()
    provider = GeminiLLMProvider(api_key="k", client=fake)

    await provider.warmup()

    assert fake.last_call is not None
    assert fake.last_call["text"] == "warmup"
    assert fake.last_call["system_prompt"] == "Reply with OK only."

    provider._internal_client = fake
    await provider.close()
    assert fake.closed is True
    assert provider._internal_client is None


@pytest.mark.asyncio
async def test_google_genai_client_formats_prompt_and_context(monkeypatch):
    state = _install_fake_google(monkeypatch, response_text=" OK ")

    client = GoogleGenaiGeminiClient(api_key="k", model="m")
    result = await client.translate(
        text="hello",
        system_prompt="Translate {source_language} to {target_language}.",
        source_language="ko",
        target_language="en",
        context="a -> b",
    )

    assert result == "OK"
    assert state["contents"] == "<context>\na -> b\n</context>\nInput: hello"
    assert state["config"].system_instruction == "Translate ko to en."


@pytest.mark.asyncio
async def test_google_genai_client_raises_on_empty_response(monkeypatch):
    _install_fake_google(monkeypatch, response_text=None)

    client = GoogleGenaiGeminiClient(api_key="k", model="m")
    with pytest.raises(RuntimeError, match="Gemini response did not contain text"):
        await client.translate(
            text="hello",
            system_prompt="PROMPT",
            source_language="en",
            target_language="ko",
        )
