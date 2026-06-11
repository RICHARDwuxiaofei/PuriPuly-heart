from __future__ import annotations

import asyncio
import importlib
from dataclasses import dataclass, field
from uuid import uuid4

import pytest

from puripuly_heart.core.clock import FakeClock
from puripuly_heart.domain.models import OSCMessage


def _output_runtime_class():
    runtime_module = importlib.import_module("puripuly_heart.core.runtime")
    output_runtime = getattr(runtime_module, "OutputRuntime", None)
    assert output_runtime is not None
    return output_runtime


@dataclass(slots=True)
class RecordingChatbox:
    messages: list[OSCMessage] = field(default_factory=list)
    typing: list[bool] = field(default_factory=list)
    process_due_calls: int = 0
    drop_pending_calls: int = 0

    def enqueue(self, message: OSCMessage) -> None:
        self.messages.append(message)

    def send_typing(self, is_typing: bool) -> None:
        self.typing.append(is_typing)

    def process_due(self) -> None:
        self.process_due_calls += 1

    def drop_pending(self) -> None:
        self.drop_pending_calls += 1


class DropPendingFailsOnceChatbox(RecordingChatbox):
    def __init__(self) -> None:
        super().__init__()
        self.fail_next_drop = True

    def drop_pending(self) -> None:
        self.drop_pending_calls += 1
        if self.fail_next_drop:
            self.fail_next_drop = False
            raise RuntimeError("drop pending failed")


class FailingFlushChatbox(RecordingChatbox):
    def process_due(self) -> None:
        self.process_due_calls += 1
        raise RuntimeError("flush task failed")


class CloseAwareBridge:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.close_calls = 0

    async def run(self) -> None:
        self.started.set()
        await asyncio.Event().wait()

    def close(self) -> None:
        self.close_calls += 1


class CloseFailsOnceBridge(CloseAwareBridge):
    def __init__(self) -> None:
        super().__init__()
        self.fail_next_close = True

    def close(self) -> None:
        self.close_calls += 1
        if self.fail_next_close:
            self.fail_next_close = False
            raise RuntimeError("bridge close failed")


class FailingRunBridge(CloseAwareBridge):
    async def run(self) -> None:
        self.started.set()
        raise RuntimeError("bridge run failed")


def test_output_runtime_exposes_lifecycle_inventory_and_policy() -> None:
    OutputRuntime = _output_runtime_class()
    owner = OutputRuntime(chatbox=RecordingChatbox(), clock=FakeClock(_now=10.0))

    snapshot = owner.lifecycle_owner_snapshot()

    assert snapshot["owner"] == "OutputRuntime"
    assert "_osc_flush_task" in snapshot["resource_fields"]
    assert "overlay_event_adapter" in snapshot["resource_fields"]
    assert "UIEventBridge.run task" in snapshot["resource_fields"]
    assert "conversation adapter" in snapshot["resource_fields"]
    assert snapshot["stop_ingress"] == "stop accepting output publications"
    assert "chatbox: drop pending pages/messages on close" in snapshot["shutdown_policy"]
    assert snapshot["late_callback_rule"] == (
        "output after close returns denied/skipped observer decisions without user text"
    )


@pytest.mark.asyncio
async def test_output_runtime_starts_flush_loop_and_drops_chatbox_backlog_on_close() -> None:
    OutputRuntime = _output_runtime_class()
    chatbox = RecordingChatbox()
    owner = OutputRuntime(chatbox=chatbox, clock=FakeClock(_now=10.0), flush_interval_s=0)

    await owner.start(auto_flush_chatbox=True)
    task = owner.chatbox_flush_task
    await asyncio.sleep(0)

    assert task is not None
    assert chatbox.process_due_calls > 0

    await owner.close()

    assert task.done()
    assert owner.chatbox_flush_task is None
    assert chatbox.drop_pending_calls == 1
    assert owner.state == "closed"


@pytest.mark.asyncio
async def test_output_runtime_denies_peer_chatbox_without_user_text() -> None:
    OutputRuntime = _output_runtime_class()
    chatbox = RecordingChatbox()
    owner = OutputRuntime(chatbox=chatbox, clock=FakeClock(_now=10.0))
    utterance_id = uuid4()

    await owner.start()
    result = await owner.publish_chatbox(
        publication_id=utterance_id,
        channel="peer",
        transcript_text="secret peer transcript",
        translation_text="secret peer translation",
        include_source=True,
    )

    assert result.message is None
    assert result.decision.decision == "denied"
    assert result.decision.route == "self_chatbox"
    assert result.decision.publication_id == str(utterance_id)
    assert result.decision.publication_kind == "peer_subtitle"
    assert result.decision.reason == "peer_chatbox_denied"
    assert chatbox.messages == []
    assert chatbox.typing == []
    assert owner.routing_decisions[-1] == result.decision
    assert "secret peer transcript" not in repr(result.decision)
    assert "secret peer translation" not in repr(result.decision)


@pytest.mark.asyncio
async def test_output_runtime_skips_chatbox_after_close_without_user_text() -> None:
    OutputRuntime = _output_runtime_class()
    chatbox = RecordingChatbox()
    owner = OutputRuntime(chatbox=chatbox, clock=FakeClock(_now=10.0))
    utterance_id = uuid4()

    await owner.start()
    await owner.close()
    result = await owner.publish_chatbox(
        publication_id=utterance_id,
        channel="self",
        transcript_text="secret closed transcript",
        translation_text="secret closed translation",
        include_source=True,
    )

    assert result.message is None
    assert result.decision.decision == "skipped"
    assert result.decision.reason == "output_runtime_closed"
    assert chatbox.messages == []
    assert chatbox.typing == []
    assert "secret closed transcript" not in repr(result.decision)
    assert "secret closed translation" not in repr(result.decision)


@pytest.mark.asyncio
async def test_output_runtime_owns_ui_event_bridge_task_and_closes_adapter() -> None:
    OutputRuntime = _output_runtime_class()
    owner = OutputRuntime(chatbox=RecordingChatbox(), clock=FakeClock(_now=10.0))
    bridge = CloseAwareBridge()

    await owner.start()
    task = owner.start_ui_event_bridge(bridge)
    await asyncio.wait_for(bridge.started.wait(), timeout=0.5)

    await owner.close()

    assert task.done()
    assert owner.ui_event_bridge_task is None
    assert bridge.close_calls == 1


@pytest.mark.asyncio
async def test_output_runtime_drop_pending_failure_retries_before_closed() -> None:
    OutputRuntime = _output_runtime_class()
    chatbox = DropPendingFailsOnceChatbox()
    owner = OutputRuntime(chatbox=chatbox, clock=FakeClock(_now=10.0))

    await owner.start()
    with pytest.raises(RuntimeError, match="drop pending failed"):
        await owner.close()

    assert owner.state != "closed"
    assert chatbox.drop_pending_calls == 1

    await owner.close()

    assert owner.state == "closed"
    assert chatbox.drop_pending_calls == 2


@pytest.mark.asyncio
async def test_output_runtime_bridge_close_failure_retains_adapter_for_retry() -> None:
    OutputRuntime = _output_runtime_class()
    owner = OutputRuntime(chatbox=RecordingChatbox(), clock=FakeClock(_now=10.0))
    bridge = CloseFailsOnceBridge()

    await owner.start()
    owner.start_ui_event_bridge(bridge)
    await asyncio.wait_for(bridge.started.wait(), timeout=0.5)

    with pytest.raises(RuntimeError, match="bridge close failed"):
        await owner.close()

    assert owner.state != "closed"
    assert bridge.close_calls == 1

    await owner.close()

    assert owner.state == "closed"
    assert bridge.close_calls == 2
    assert owner.ui_event_bridge_task is None


@pytest.mark.asyncio
async def test_output_runtime_start_after_close_does_not_reopen_or_accept_publications() -> None:
    OutputRuntime = _output_runtime_class()
    chatbox = RecordingChatbox()
    owner = OutputRuntime(chatbox=chatbox, clock=FakeClock(_now=10.0))

    await owner.start()
    await owner.close()

    with pytest.raises(RuntimeError, match="closed"):
        await owner.start(auto_flush_chatbox=True)
    result = await owner.publish_chatbox(
        publication_id=uuid4(),
        channel="self",
        transcript_text="closed start secret",
        translation_text=None,
        include_source=True,
    )

    assert result.decision.decision == "skipped"
    assert result.decision.reason == "output_runtime_closed"
    assert chatbox.messages == []


@pytest.mark.asyncio
async def test_output_runtime_redacts_unsafe_system_disclosure_before_chatbox() -> None:
    OutputRuntime = _output_runtime_class()
    chatbox = RecordingChatbox()
    owner = OutputRuntime(chatbox=chatbox, clock=FakeClock(_now=10.0))

    await owner.start()
    result = owner.publish_system_disclosure_chatbox(
        text="provider_response_body={'token':'chatbox-disclosure-secret'}",
    )

    assert result.decision.decision == "published"
    assert len(chatbox.messages) == 1
    published_text = chatbox.messages[0].text
    assert "chatbox-disclosure-secret" not in published_text
    assert "provider_response_body" not in published_text
    assert "[provider-response-body-redacted]" in published_text


@pytest.mark.asyncio
async def test_output_runtime_start_after_failed_close_does_not_reopen() -> None:
    OutputRuntime = _output_runtime_class()
    chatbox = DropPendingFailsOnceChatbox()
    owner = OutputRuntime(chatbox=chatbox, clock=FakeClock(_now=10.0))

    await owner.start()
    with pytest.raises(RuntimeError, match="drop pending failed"):
        await owner.close()

    with pytest.raises(RuntimeError, match="closing"):
        await owner.start(auto_flush_chatbox=True)
    result = await owner.publish_chatbox(
        publication_id=uuid4(),
        channel="self",
        transcript_text="failed close secret",
        translation_text=None,
        include_source=True,
    )

    assert result.decision.decision == "skipped"
    assert result.decision.reason == "output_runtime_closing"
    assert chatbox.messages == []


@pytest.mark.asyncio
async def test_output_runtime_reports_flush_task_failure_replaced_before_close() -> None:
    OutputRuntime = _output_runtime_class()
    chatbox = FailingFlushChatbox()
    owner = OutputRuntime(chatbox=chatbox, clock=FakeClock(_now=10.0), flush_interval_s=0)

    await owner.start(auto_flush_chatbox=True)
    failed_task = owner.chatbox_flush_task
    assert failed_task is not None
    await asyncio.wait_for(_wait_for_done(failed_task), timeout=0.5)
    await owner.start(auto_flush_chatbox=True)

    with pytest.raises(RuntimeError, match="flush task failed"):
        await owner.close()


@pytest.mark.asyncio
async def test_output_runtime_reports_ui_bridge_task_failure_replaced_before_close() -> None:
    OutputRuntime = _output_runtime_class()
    owner = OutputRuntime(chatbox=RecordingChatbox(), clock=FakeClock(_now=10.0))
    failing_bridge = FailingRunBridge()
    replacement_bridge = CloseAwareBridge()

    await owner.start()
    failed_task = owner.start_ui_event_bridge(failing_bridge)
    await asyncio.wait_for(failing_bridge.started.wait(), timeout=0.5)
    await asyncio.wait_for(_wait_for_done(failed_task), timeout=0.5)
    owner.start_ui_event_bridge(replacement_bridge)
    await asyncio.wait_for(replacement_bridge.started.wait(), timeout=0.5)

    with pytest.raises(RuntimeError, match="bridge run failed"):
        await owner.close()


async def _wait_for_done(task: asyncio.Task[object]) -> None:
    while not task.done():
        await asyncio.sleep(0)
