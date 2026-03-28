from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from uuid import uuid4

import pytest

from puripuly_heart.core.orchestrator.channel_runtime import (
    ChannelRuntime,
    ContextEntry,
    _MergeBuffer,
)
from puripuly_heart.core.orchestrator.hub import ClientHub
from puripuly_heart.domain.models import Transcript


@dataclass
class FakeOscQueue:
    messages: list = field(default_factory=list)

    def enqueue(self, msg) -> None:  # noqa: ANN001
        self.messages.append(msg)

    def send_typing(self, on: bool) -> None:
        _ = on

    def send_immediate(self, text: str) -> bool:
        _ = text
        return True

    def process_due(self) -> None:
        return None


def test_channel_runtime_keeps_merge_and_history_separate_per_channel() -> None:
    self_runtime = ChannelRuntime(channel="self")
    peer_runtime = ChannelRuntime(channel="peer")

    self_runtime.remember_context(
        "hello",
        timestamp=10.0,
        source_language="en",
        target_language="ko",
    )
    peer_runtime.remember_context(
        "world",
        timestamp=12.0,
        source_language="en",
        target_language="ko",
    )
    self_runtime.merge_buffer = _MergeBuffer(merge_id=uuid4())
    peer_runtime.merge_buffer = _MergeBuffer(merge_id=uuid4())
    self_runtime.merge_buffer.parts.append("self part")
    peer_runtime.merge_buffer.parts.append("peer part")

    assert [entry.text for entry in self_runtime.translation_history] == ["hello"]
    assert [entry.text for entry in peer_runtime.translation_history] == ["world"]
    assert self_runtime.merge_buffer.parts == ["self part"]
    assert peer_runtime.merge_buffer.parts == ["peer part"]


def test_client_hub_owns_fixed_self_and_peer_runtimes_while_self_path_stays_stable() -> None:
    hub = ClientHub(stt=None, llm=None, osc=FakeOscQueue())

    assert hub.self_runtime.channel == "self"
    assert hub.peer_runtime.channel == "peer"
    assert hub.active_chatbox_channel == "self"
    assert hub._translation_history is hub.self_runtime.translation_history
    assert hub._translation_tasks is hub.self_runtime.translation_tasks


def test_self_runtime_reassignment_updates_hub_aliases() -> None:
    hub = ClientHub(stt=None, llm=None, osc=FakeOscQueue())
    buffer = _MergeBuffer(merge_id=uuid4())

    hub.self_runtime.merge_buffer = buffer

    assert hub._merge_buffer is buffer


@pytest.mark.asyncio
async def test_peer_transcript_stays_in_peer_runtime() -> None:
    hub = ClientHub(stt=None, llm=None, osc=FakeOscQueue())
    transcript = Transcript(utterance_id=uuid4(), text="peer text", is_final=True, channel="peer")

    await hub._handle_transcript(transcript, is_final=True, source="Peer")

    assert transcript.utterance_id not in hub.self_runtime.utterances
    assert transcript.utterance_id in hub.peer_runtime.utterances
    assert hub.peer_runtime.get_source(transcript.utterance_id) == "Peer"


@pytest.mark.asyncio
async def test_reset_runtime_state_clears_both_channel_runtimes() -> None:
    hub = ClientHub(stt=None, llm=None, osc=FakeOscQueue())
    self_id = uuid4()
    peer_id = uuid4()
    self_task = asyncio.create_task(asyncio.sleep(60.0))
    peer_task = asyncio.create_task(asyncio.sleep(60.0))

    hub.self_runtime.translation_tasks[self_id] = self_task
    hub.peer_runtime.translation_tasks[peer_id] = peer_task
    hub.self_runtime.merge_buffer = _MergeBuffer(merge_id=uuid4(), utterance_ids=[self_id])
    hub.peer_runtime.merge_buffer = _MergeBuffer(merge_id=uuid4(), utterance_ids=[peer_id])
    hub.self_runtime.translation_history.append(
        ContextEntry(
            text="self line",
            source_language="en",
            target_language="ko",
            timestamp=1.0,
            channel="self",
        )
    )
    hub.peer_runtime.translation_history.append(
        ContextEntry(
            text="peer line",
            source_language="en",
            target_language="ko",
            timestamp=1.0,
            channel="peer",
        )
    )

    await hub._reset_stt_runtime_state()

    assert hub.self_runtime.translation_tasks == {}
    assert hub.peer_runtime.translation_tasks == {}
    assert hub.self_runtime.merge_buffer is None
    assert hub.peer_runtime.merge_buffer is None
    assert hub.self_runtime.translation_history == []
    assert hub.peer_runtime.translation_history == []
