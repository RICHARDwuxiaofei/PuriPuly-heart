from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

import pytest

from puripuly_heart.core.clock import FakeClock
from puripuly_heart.core.orchestrator.hub import ClientHub
from puripuly_heart.domain.models import Translation


@dataclass
class FakeOscQueue:
    messages: list = None

    def __post_init__(self) -> None:
        if self.messages is None:
            self.messages = []

    def enqueue(self, msg) -> None:
        self.messages.append(msg)

    def send_typing(self, on: bool) -> None:
        _ = on

    def process_due(self) -> None:
        return


@dataclass
class FakeLLMProvider:
    last_prompt: str | None = None

    async def translate(
        self,
        *,
        utterance_id,
        text: str,
        system_prompt: str,
        source_language: str,
        target_language: str,
        context: str = "",
    ) -> Translation:
        _ = (text, source_language, target_language, context)
        self.last_prompt = system_prompt
        return Translation(utterance_id=utterance_id, text="ok")

    async def close(self) -> None:
        return


@pytest.mark.asyncio
async def test_hub_substitutes_language_placeholders() -> None:
    fake_llm = FakeLLMProvider()
    hub = ClientHub(
        stt=None,
        llm=fake_llm,
        osc=FakeOscQueue(),
        clock=FakeClock(),
        source_language="ko",
        target_language="en",
        system_prompt="Translate ${sourceName} to ${targetName}.",
    )

    await hub._translate_and_enqueue(uuid4(), "hello")

    assert fake_llm.last_prompt is not None
    assert "${sourceName}" not in fake_llm.last_prompt
    assert "${targetName}" not in fake_llm.last_prompt
    assert "Korean" in fake_llm.last_prompt
    assert "English" in fake_llm.last_prompt
