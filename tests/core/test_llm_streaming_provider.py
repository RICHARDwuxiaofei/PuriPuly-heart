from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest

from puripuly_heart.core.llm.provider import LLMProvider, SemaphoreLLMProvider
from puripuly_heart.domain.models import Translation


@dataclass(slots=True)
class StubStreamingLLMProvider(LLMProvider):
    chunks: list[str]

    async def stream_translate(
        self,
        *,
        utterance_id: UUID,
        text: str,
        system_prompt: str,
        source_language: str,
        target_language: str,
        context: str = "",
    ) -> AsyncIterator[str]:
        _ = (utterance_id, text, system_prompt, source_language, target_language, context)
        for chunk in self.chunks:
            yield chunk

    async def close(self) -> None:
        return


class MissingStreamingLLMProvider(LLMProvider):
    async def close(self) -> None:
        return


@dataclass(slots=True)
class TranslateOnlyLLMProvider(LLMProvider):
    translated_text: str = "hello"
    calls: list[dict[str, object]] | None = None

    def __post_init__(self) -> None:
        if self.calls is None:
            self.calls = []

    async def translate(
        self,
        *,
        utterance_id: UUID,
        text: str,
        system_prompt: str,
        source_language: str,
        target_language: str,
        context: str = "",
    ) -> Translation:
        assert self.calls is not None
        self.calls.append(
            {
                "utterance_id": utterance_id,
                "text": text,
                "system_prompt": system_prompt,
                "source_language": source_language,
                "target_language": target_language,
                "context": context,
            }
        )
        return Translation(
            utterance_id=utterance_id,
            translated_text=self.translated_text,
            source_text=text,
            source_language=source_language,
            target_language=target_language,
        )

    async def close(self) -> None:
        return


@pytest.mark.asyncio
async def test_stream_translate_yields_cumulative_snapshots():
    provider = StubStreamingLLMProvider(chunks=["h", "he", "hello"])

    chunks = [
        chunk
        async for chunk in provider.stream_translate(
            utterance_id=uuid4(),
            text="안녕",
            system_prompt="PROMPT",
            source_language="ko",
            target_language="en",
        )
    ]

    assert chunks == ["h", "he", "hello"]


@pytest.mark.asyncio
async def test_translate_aggregates_stream_to_final_translation():
    provider = StubStreamingLLMProvider(chunks=["h", "he", "hello"])

    result = await provider.translate(
        utterance_id=uuid4(),
        text="안녕",
        system_prompt="PROMPT",
        source_language="ko",
        target_language="en",
    )

    assert result.translated_text == "hello"


@pytest.mark.asyncio
async def test_semaphore_provider_stream_translate_wraps_inner_stream():
    inner = StubStreamingLLMProvider(chunks=["h", "he", "hello"])
    provider = SemaphoreLLMProvider(inner=inner, semaphore=asyncio.Semaphore(1))

    chunks = [
        chunk
        async for chunk in provider.stream_translate(
            utterance_id=uuid4(),
            text="안녕",
            system_prompt="PROMPT",
            source_language="ko",
            target_language="en",
        )
    ]

    assert chunks == ["h", "he", "hello"]


@pytest.mark.asyncio
async def test_missing_stream_contract_returns_async_iterator_and_raises_on_iteration():
    provider = MissingStreamingLLMProvider()

    stream = provider.stream_translate(
        utterance_id=uuid4(),
        text="안녕",
        system_prompt="PROMPT",
        source_language="ko",
        target_language="en",
    )

    assert hasattr(stream, "__aiter__")

    with pytest.raises(NotImplementedError):
        await anext(stream)


@pytest.mark.asyncio
async def test_semaphore_provider_stream_translate_falls_back_for_translate_only_inner():
    inner = TranslateOnlyLLMProvider(translated_text="fallback")
    provider = SemaphoreLLMProvider(inner=inner, semaphore=asyncio.Semaphore(1))

    chunks = [
        chunk
        async for chunk in provider.stream_translate(
            utterance_id=uuid4(),
            text="안녕",
            system_prompt="PROMPT",
            source_language="ko",
            target_language="en",
        )
    ]

    assert chunks == ["fallback"]
    assert inner.calls == [
        {
            "utterance_id": inner.calls[0]["utterance_id"],
            "text": "안녕",
            "system_prompt": "PROMPT",
            "source_language": "ko",
            "target_language": "en",
            "context": "",
        }
    ]
