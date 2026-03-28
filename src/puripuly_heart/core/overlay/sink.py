from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Protocol
from uuid import UUID

from puripuly_heart.core.clock import Clock, SystemClock
from puripuly_heart.domain.models import ChannelId, Transcript

from .protocol import (
    AppliedContextMode,
    OverlayEventUnion,
    OverlayStateSnapshot,
    PeerTranscriptFinal,
    SelfTranscriptFinal,
    Shutdown,
    TranslationFinal,
    TranslationStreamUpdate,
    UtteranceClosed,
)


class OverlaySink(Protocol):
    async def emit(self, event: OverlayEventUnion) -> None: ...

    def snapshot(self) -> OverlayStateSnapshot: ...


@dataclass(slots=True)
class NullOverlaySink:
    async def emit(self, event: OverlayEventUnion) -> None:
        _ = event

    def snapshot(self) -> OverlayStateSnapshot:
        return OverlayStateSnapshot(events=[])


@dataclass(slots=True)
class OverlayStreamCoalescer:
    interval_ms: int = 300
    _pending_event: TranslationStreamUpdate | None = None
    _flush_task: asyncio.Task[None] | None = None

    async def push(
        self,
        event: TranslationStreamUpdate,
        emit: Callable[[TranslationStreamUpdate], Awaitable[None]],
    ) -> None:
        self._pending_event = event
        if self._flush_task is None or self._flush_task.done():
            self._flush_task = asyncio.create_task(self._delayed_flush(emit))

    async def flush(
        self,
        emit: Callable[[TranslationStreamUpdate], Awaitable[None]],
    ) -> None:
        flush_task = self._flush_task
        self._flush_task = None
        if flush_task is not None and not flush_task.done():
            flush_task.cancel()
            await asyncio.gather(flush_task, return_exceptions=True)

        pending = self._take_pending_event()
        if pending is not None:
            await emit(pending)

    async def cancel(self) -> None:
        flush_task = self._flush_task
        self._flush_task = None
        self._pending_event = None
        if flush_task is not None and not flush_task.done():
            flush_task.cancel()
            await asyncio.gather(flush_task, return_exceptions=True)

    async def _delayed_flush(
        self,
        emit: Callable[[TranslationStreamUpdate], Awaitable[None]],
    ) -> None:
        try:
            await asyncio.sleep(self.interval_ms / 1000.0)
            pending = self._take_pending_event()
            if pending is not None:
                await emit(pending)
        except asyncio.CancelledError:
            raise
        finally:
            self._flush_task = None

    def _take_pending_event(self) -> TranslationStreamUpdate | None:
        pending = self._pending_event
        self._pending_event = None
        return pending


@dataclass(slots=True)
class OverlayEventAdapter:
    clock: Clock = field(default_factory=SystemClock)
    _seq: int = 0

    def transcript_final(
        self,
        transcript: Transcript,
        *,
        source_language: str,
        target_language: str,
    ) -> SelfTranscriptFinal | PeerTranscriptFinal:
        common = self._common_event_fields(
            utterance_id=transcript.utterance_id,
            channel=transcript.channel,
            created_at=transcript.created_at,
        )
        event_cls = SelfTranscriptFinal if transcript.channel == "self" else PeerTranscriptFinal
        return event_cls(
            **common,
            text=transcript.text,
            source_language=source_language,
            target_language=target_language,
            is_final=True,
            speaker_label=transcript.speaker_label,
            peer_epoch=transcript.peer_epoch,
        )

    def translation_stream_update(
        self,
        *,
        utterance_id: UUID,
        channel: ChannelId,
        text: str,
        source_language: str,
        target_language: str,
        applied_context_mode: AppliedContextMode | None,
        speaker_label: str | None = None,
        peer_epoch: int | None = None,
        created_at: float | None = None,
    ) -> TranslationStreamUpdate:
        return TranslationStreamUpdate(
            **self._common_event_fields(
                utterance_id=utterance_id,
                channel=channel,
                created_at=created_at,
            ),
            text=text,
            source_language=source_language,
            target_language=target_language,
            is_final=False,
            applied_context_mode=applied_context_mode,
            speaker_label=speaker_label,
            peer_epoch=peer_epoch,
        )

    def translation_final(
        self,
        *,
        utterance_id: UUID,
        channel: ChannelId,
        text: str,
        source_language: str,
        target_language: str,
        applied_context_mode: AppliedContextMode | None,
        speaker_label: str | None = None,
        peer_epoch: int | None = None,
        created_at: float | None = None,
    ) -> TranslationFinal:
        return TranslationFinal(
            **self._common_event_fields(
                utterance_id=utterance_id,
                channel=channel,
                created_at=created_at,
            ),
            text=text,
            source_language=source_language,
            target_language=target_language,
            is_final=True,
            applied_context_mode=applied_context_mode,
            speaker_label=speaker_label,
            peer_epoch=peer_epoch,
        )

    def utterance_closed(
        self,
        *,
        utterance_id: UUID,
        channel: ChannelId,
        is_final: bool = True,
        created_at: float | None = None,
    ) -> UtteranceClosed:
        return UtteranceClosed(
            **self._common_event_fields(
                utterance_id=utterance_id,
                channel=channel,
                created_at=created_at,
            ),
            is_final=is_final,
        )

    def shutdown(self, *, created_at: float | None = None) -> Shutdown:
        return Shutdown(
            **self._common_event_fields(
                utterance_id=None,
                channel=None,
                created_at=created_at,
            )
        )

    def _common_event_fields(
        self,
        *,
        utterance_id: UUID | None,
        channel: ChannelId | None,
        created_at: float | None,
    ) -> dict[str, object]:
        self._seq += 1
        return {
            "event_id": f"evt-{self._seq}",
            "seq": self._seq,
            "utterance_id": utterance_id,
            "channel": channel,
            "created_at": created_at if created_at is not None else self.clock.now(),
        }
