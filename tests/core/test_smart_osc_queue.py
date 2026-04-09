from __future__ import annotations

import logging
import uuid

from puripuly_heart.core.clock import FakeClock
from puripuly_heart.core.osc.smart_queue import SmartOscQueue
from puripuly_heart.domain.models import OSCMessage
from tests.helpers.fakes import FakeSender


class FakeRuntimeLogging:
    def __init__(self, *, detailed_enabled: bool = False) -> None:
        self.detailed_enabled = detailed_enabled
        self.basic: list[tuple[int, str]] = []
        self.detailed: list[tuple[int, str]] = []

    def emit_basic(self, message: str, *, level: int = logging.INFO) -> None:
        self.basic.append((level, message))

    def emit_detailed(self, message: str, *, level: int = logging.INFO) -> bool:
        if not self.detailed_enabled:
            return False
        self.detailed.append((level, message))
        return True


class FailingSender(FakeSender):
    def send_chatbox(self, text: str) -> None:
        _ = text
        raise OSError("boom")

    def send_typing(self, is_typing: bool) -> None:
        _ = is_typing
        raise OSError("boom")


def test_smart_queue_cooldown_and_flush():
    clock = FakeClock()
    sender = FakeSender()
    queue = SmartOscQueue(sender=sender, clock=clock, cooldown_s=1.5, ttl_s=100.0)

    queue.enqueue(OSCMessage(uuid.uuid4(), text="hello", created_at=clock.now()))
    assert sender.sent == ["hello"]

    clock.advance(0.5)
    queue.enqueue(OSCMessage(uuid.uuid4(), text="world", created_at=clock.now()))
    assert sender.sent == ["hello"]

    clock.advance(1.0)  # now=1.5
    queue.process_due()
    assert sender.sent == ["hello", "world"]


def test_smart_queue_splits_and_carries_over():
    clock = FakeClock()
    sender = FakeSender()
    queue = SmartOscQueue(sender=sender, clock=clock, cooldown_s=1.0, ttl_s=100.0, max_chars=10)

    uid = uuid.uuid4()
    queue.enqueue(OSCMessage(uid, text="one two three four", created_at=clock.now()))
    assert len(sender.sent) == 1

    clock.advance(1.0)
    queue.process_due()
    assert len(sender.sent) == 2


def test_smart_queue_ttl_drop():
    clock = FakeClock()
    sender = FakeSender()
    queue = SmartOscQueue(sender=sender, clock=clock, cooldown_s=1.5, ttl_s=1.0)

    queue.enqueue(OSCMessage(uuid.uuid4(), text="first", created_at=clock.now()))
    clock.advance(0.1)
    queue.enqueue(OSCMessage(uuid.uuid4(), text="stale", created_at=clock.now()))

    clock.advance(2.0)
    queue.process_due()

    assert sender.sent == ["first"]


def test_smart_queue_send_typing():
    clock = FakeClock()
    sender = FakeSender()
    queue = SmartOscQueue(sender=sender, clock=clock, cooldown_s=1.0, ttl_s=1.0)

    queue.send_typing(True)

    assert sender.typing == [True]


def test_smart_queue_basic_mode_keeps_delivery_summary_without_detailed_send_text():
    clock = FakeClock()
    sender = FakeSender()
    runtime_logging = FakeRuntimeLogging()
    queue = SmartOscQueue(
        sender=sender,
        clock=clock,
        cooldown_s=1.0,
        ttl_s=100.0,
        runtime_logging=runtime_logging,
    )

    queue.enqueue(OSCMessage(uuid.uuid4(), text="hello", created_at=clock.now()))

    assert sender.sent == ["hello"]
    assert runtime_logging.basic == [
        (logging.INFO, "[Basic][OSC] send mode=queued status=delivered chars=5 remaining_parts=0")
    ]
    assert runtime_logging.detailed == []


def test_smart_queue_detailed_mode_normalizes_queued_and_immediate_send_logs():
    clock = FakeClock()
    sender = FakeSender()
    runtime_logging = FakeRuntimeLogging(detailed_enabled=True)
    queue = SmartOscQueue(
        sender=sender,
        clock=clock,
        cooldown_s=1.0,
        ttl_s=100.0,
        runtime_logging=runtime_logging,
    )

    queue.enqueue(OSCMessage(uuid.uuid4(), text="hello", created_at=clock.now()))
    sent = queue.send_immediate("world")

    assert sent is True
    assert sender.sent == ["hello", "world"]
    assert runtime_logging.basic == [
        (logging.INFO, "[Basic][OSC] send mode=queued status=delivered chars=5 remaining_parts=0"),
        (
            logging.INFO,
            "[Basic][OSC] send mode=immediate status=delivered chars=5 remaining_parts=0",
        ),
    ]
    assert runtime_logging.detailed == [
        (
            logging.INFO,
            "[Detailed][OSC] send mode=queued status=attempt chars=5 remaining_parts=0 text='hello'",
        ),
        (
            logging.INFO,
            "[Detailed][OSC] send mode=immediate status=attempt chars=5 remaining_parts=0 text='world'",
        ),
    ]


def test_smart_queue_send_failures_and_typing_failures_remain_basic_breadcrumbs():
    clock = FakeClock()
    sender = FailingSender()
    runtime_logging = FakeRuntimeLogging()
    queue = SmartOscQueue(
        sender=sender,
        clock=clock,
        cooldown_s=1.0,
        ttl_s=100.0,
        runtime_logging=runtime_logging,
    )

    queue.enqueue(OSCMessage(uuid.uuid4(), text="hello", created_at=clock.now()))
    sent = queue.send_immediate("world")
    queue.send_typing(True)

    assert sent is False
    assert runtime_logging.basic == [
        (logging.WARNING, "[Basic][OSC] send mode=queued status=failed error=boom"),
        (logging.WARNING, "[Basic][OSC] send mode=immediate status=failed error=boom"),
        (logging.WARNING, "[Basic][OSC] typing status=failed error=boom"),
    ]
    assert runtime_logging.detailed == []
