from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import UUID

from puripuly_heart.domain.models import Translation


class LLMProvider:
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
        if False:
            yield ""
        raise NotImplementedError

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
        translated_text = ""
        async for snapshot in self.stream_translate(
            utterance_id=utterance_id,
            text=text,
            system_prompt=system_prompt,
            source_language=source_language,
            target_language=target_language,
            context=context,
        ):
            translated_text = snapshot

        return Translation(
            utterance_id=utterance_id,
            translated_text=translated_text,
            source_text=text,
            source_language=source_language,
            target_language=target_language,
        )

    async def close(self) -> None:
        raise NotImplementedError


@dataclass(slots=True)
class SemaphoreLLMProvider(LLMProvider):
    inner: LLMProvider
    semaphore: asyncio.Semaphore

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
        async with self.semaphore:
            stream_translate = getattr(self.inner, "stream_translate", None)
            inner_stream_method = getattr(type(self.inner), "stream_translate", None)
            if stream_translate is None or inner_stream_method is LLMProvider.stream_translate:
                translation = await self.inner.translate(
                    utterance_id=utterance_id,
                    text=text,
                    system_prompt=system_prompt,
                    source_language=source_language,
                    target_language=target_language,
                    context=context,
                )
                yield translation.text
                return

            async for snapshot in stream_translate(
                utterance_id=utterance_id,
                text=text,
                system_prompt=system_prompt,
                source_language=source_language,
                target_language=target_language,
                context=context,
            ):
                yield snapshot

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
        async with self.semaphore:
            return await self.inner.translate(
                utterance_id=utterance_id,
                text=text,
                system_prompt=system_prompt,
                source_language=source_language,
                target_language=target_language,
                context=context,
            )

    async def close(self) -> None:
        await self.inner.close()
