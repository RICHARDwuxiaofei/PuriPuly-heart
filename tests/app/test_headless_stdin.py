from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from puripuly_heart.app.headless_stdin import HeadlessStdinRunner
from puripuly_heart.config.settings import AppSettings
from puripuly_heart.core.clock import FakeClock
from puripuly_heart.domain.models import OSCMessage, Translation


@dataclass(slots=True)
class FakeOscQueue:
    messages: list[OSCMessage]

    def __init__(self) -> None:
        self.messages = []

    def enqueue(self, msg: OSCMessage) -> None:
        self.messages.append(msg)


@dataclass(slots=True)
class FakeLoop:
    lines: list[str]

    async def run_in_executor(self, _executor, _func) -> str:
        if self.lines:
            return self.lines.pop(0)
        return ""


@dataclass(slots=True)
class FakeLLM:
    async def translate(
        self,
        *,
        utterance_id,
        text: str,
        system_prompt: str,
        source_language: str,
        target_language: str,
        context: str = "",
        context_pairs: list[dict[str, str]] | None = None,
    ) -> Translation:
        _ = (system_prompt, source_language, target_language, context, context_pairs)
        return Translation(utterance_id=utterance_id, text="OK")

    async def close(self) -> None:
        return


@pytest.mark.asyncio
async def test_headless_stdin_enqueues_plain_text(monkeypatch):
    loop = FakeLoop(lines=["hello\n", ""])
    monkeypatch.setattr(asyncio, "get_running_loop", lambda: loop)

    runner = HeadlessStdinRunner(settings=AppSettings(), llm=None, clock=FakeClock())
    osc = FakeOscQueue()

    await runner._stdin_loop(osc)

    assert [msg.text for msg in osc.messages] == ["hello"]


@pytest.mark.asyncio
async def test_headless_stdin_enqueues_translated_text(monkeypatch):
    loop = FakeLoop(lines=["hello\n", ""])
    monkeypatch.setattr(asyncio, "get_running_loop", lambda: loop)

    runner = HeadlessStdinRunner(settings=AppSettings(), llm=FakeLLM(), clock=FakeClock())
    osc = FakeOscQueue()

    await runner._stdin_loop(osc)

    assert [msg.text for msg in osc.messages] == ["hello (OK)"]
