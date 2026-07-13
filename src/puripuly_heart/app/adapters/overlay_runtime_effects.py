from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from puripuly_heart.app.services.overlay_osc_application_runtime import OverlayLogFacts
from puripuly_heart.core.audio.gate import VrcMicAudioGate
from puripuly_heart.core.osc.receiver import VrcMicState
from puripuly_heart.core.runtime.receiver import VrcMicReceiverRuntime


@dataclass(slots=True)
class ProductionVrcMicrophoneEffects:
    state: VrcMicState = field(default_factory=VrcMicState)
    gate: VrcMicAudioGate = field(init=False)
    receiver: VrcMicReceiverRuntime | None = None
    enabled: bool = False
    closed: bool = False
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        self.gate = VrcMicAudioGate(state=self.state, enabled=False)
        if self.receiver is None:
            self.receiver = VrcMicReceiverRuntime(state=self.state)

    async def apply_vrc_microphone_intercept(self, enabled: bool) -> bool:
        async with self._lock:
            if self.closed:
                return False
            if enabled == self.enabled:
                return True
            self.gate.set_enabled(enabled)
            try:
                if enabled:
                    await self.receiver.start()
                else:
                    await self.receiver.stop()
            except Exception:
                self.gate.set_enabled(False)
                self.gate.set_receiver_active(False)
                self.enabled = False
                self.closed = True
                await self.receiver.close()
                return False
            self.gate.set_receiver_active(self.receiver.receiver is not None)
            self.enabled = enabled
            return True

    async def close(self) -> None:
        async with self._lock:
            if self.closed:
                return
            self.closed = True
            self.gate.set_enabled(False)
            self.gate.set_receiver_active(False)
            self.enabled = False
            await self.receiver.close()


@dataclass(frozen=True, slots=True)
class ProductionOverlaySafeLog:
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger(__name__))

    def publish_overlay_log_facts(self, facts: OverlayLogFacts) -> None:
        self.logger.info(
            "overlay event=%s target=%s instance=%s failure=%s",
            facts.event,
            facts.target,
            facts.overlay_instance_id,
            facts.failure_reason,
        )
