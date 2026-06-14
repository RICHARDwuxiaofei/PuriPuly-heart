from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

import puripuly_heart.app.headless_stdin as headless_stdin
from puripuly_heart.app.headless_runtime_config import (
    HeadlessLanguageRuntimeConfig,
    HeadlessOSCRuntimeConfig,
    HeadlessStdinRuntimeConfig,
)
from puripuly_heart.app.headless_stdin import HeadlessStdinRunner
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
    ) -> Translation:
        _ = (system_prompt, source_language, target_language, context)
        return Translation(utterance_id=utterance_id, text="OK")

    async def close(self) -> None:
        return


def _stdin_config(
    *,
    chatbox_include_source: bool = False,
    source_language: str = "ko",
    target_language: str = "en",
) -> HeadlessStdinRuntimeConfig:
    return HeadlessStdinRuntimeConfig(
        osc=HeadlessOSCRuntimeConfig(
            host="127.0.0.1",
            port=9000,
            chatbox_address="/chatbox/input",
            chatbox_send=True,
            chatbox_clear=False,
            chatbox_max_chars=144,
            chatbox_include_source=chatbox_include_source,
            vrc_mic_intercept=False,
        ),
        languages=HeadlessLanguageRuntimeConfig(
            source_language=source_language,
            target_language=target_language,
            peer_source_language="en",
        ),
        system_prompt="",
        chatbox_include_source=chatbox_include_source,
    )


@pytest.mark.asyncio
async def test_headless_stdin_enqueues_plain_text(monkeypatch):
    loop = FakeLoop(lines=["hello\n", ""])
    monkeypatch.setattr(asyncio, "get_running_loop", lambda: loop)

    runner = HeadlessStdinRunner(runtime_config=_stdin_config(), llm=None, clock=FakeClock())
    osc = FakeOscQueue()

    await runner._stdin_loop(osc)

    assert [msg.text for msg in osc.messages] == ["hello"]


@pytest.mark.asyncio
async def test_headless_stdin_enqueues_translated_text(monkeypatch):
    loop = FakeLoop(lines=["hello\n", ""])
    monkeypatch.setattr(asyncio, "get_running_loop", lambda: loop)

    runner = HeadlessStdinRunner(runtime_config=_stdin_config(), llm=FakeLLM(), clock=FakeClock())
    osc = FakeOscQueue()

    await runner._stdin_loop(osc)

    assert [msg.text for msg in osc.messages] == ["OK"]


@pytest.mark.asyncio
async def test_headless_stdin_includes_source_when_configured(monkeypatch):
    loop = FakeLoop(lines=["hello\n", ""])
    monkeypatch.setattr(asyncio, "get_running_loop", lambda: loop)

    runner = HeadlessStdinRunner(
        runtime_config=_stdin_config(chatbox_include_source=True),
        llm=FakeLLM(),
        clock=FakeClock(),
    )
    osc = FakeOscQueue()

    await runner._stdin_loop(osc)

    assert [msg.text for msg in osc.messages] == ["hello (OK)"]


@pytest.mark.asyncio
async def test_headless_stdin_run_handles_keyboard_interrupt(monkeypatch):
    sender_ref: dict[str, object] = {}

    class FakeSender:
        def __init__(self, *args, **kwargs):
            sender_ref["instance"] = self
            self.closed = False

        def close(self):
            self.closed = True

    class FakeOsc:
        def __init__(self, *args, **kwargs):
            return None

        def process_due(self):
            return None

    async def fake_stdin_loop(self, osc):
        _ = osc
        raise KeyboardInterrupt

    async def fake_flush_loop(self, osc):
        _ = osc
        return None

    monkeypatch.setattr(headless_stdin, "VrchatOscUdpSender", FakeSender)
    monkeypatch.setattr(headless_stdin, "ChatboxPaginator", FakeOsc)
    monkeypatch.setattr(HeadlessStdinRunner, "_stdin_loop", fake_stdin_loop)
    monkeypatch.setattr(HeadlessStdinRunner, "_flush_loop", fake_flush_loop)

    runner = HeadlessStdinRunner(runtime_config=_stdin_config(), llm=None, clock=FakeClock())
    result = await runner.run()

    assert result == 0
    assert sender_ref["instance"].closed is True
