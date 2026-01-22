from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

import pytest

from puripuly_heart.providers.llm.qwen_async import (
    AsyncQwenClient,
    AsyncQwenLLMProvider,
)


@dataclass
class FakeAsyncQwenClient(AsyncQwenClient):
    last_call: dict[str, object] | None = None
    closed: bool = False

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

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_async_qwen_provider_uses_injected_client():
    fake = FakeAsyncQwenClient()
    provider = AsyncQwenLLMProvider(api_key="k", client=fake)

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


@pytest.mark.asyncio
async def test_async_qwen_provider_passes_context_pairs():
    fake = FakeAsyncQwenClient()
    provider = AsyncQwenLLMProvider(api_key="k", client=fake)

    context_pairs = [{"source": "원문", "target": "translation"}]
    await provider.translate(
        utterance_id=uuid4(),
        text="hello",
        system_prompt="PROMPT",
        source_language="ko",
        target_language="en",
        context_pairs=context_pairs,
    )

    assert fake.last_call is not None
    assert fake.last_call["context_pairs"] == context_pairs


@pytest.mark.asyncio
async def test_async_qwen_provider_close_cleans_up():
    fake = FakeAsyncQwenClient()
    provider = AsyncQwenLLMProvider(api_key="k", client=fake)
    provider._internal_client = fake

    await provider.close()

    assert fake.closed is True
    assert provider._internal_client is None
