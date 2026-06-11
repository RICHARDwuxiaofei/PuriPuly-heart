from __future__ import annotations

import asyncio
from collections.abc import Coroutine, Mapping
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, ClassVar, Literal, Protocol
from uuid import UUID, uuid4

from puripuly_heart.core.clock import Clock, SystemClock
from puripuly_heart.core.messages import DiagnosticFieldValue
from puripuly_heart.core.output.models import PeerSubtitlePublication
from puripuly_heart.domain.models import ChannelId, Transcript

AppliedContextMode = Literal["local", "integrated"]


@dataclass(frozen=True, slots=True, kw_only=True)
class OverlayEvent:
    event_id: str
    seq: int
    utterance_id: UUID | None
    channel: ChannelId | None
    created_at: float
    update_id: str | None = None
    origin_wall_clock_ms: int | None = None
    session_scope: str | None = None
    source_text_hash: str | None = None
    source_text_len: int | None = None
    logical_turn_key: str | None = None

    EVENT_TYPE: ClassVar[str] = "overlay_event"

    @property
    def type(self) -> str:
        return self.EVENT_TYPE


@dataclass(frozen=True, slots=True, kw_only=True)
class _TranscriptEvent(OverlayEvent):
    text: str
    source_language: str
    target_language: str
    is_final: bool = True


@dataclass(frozen=True, slots=True, kw_only=True)
class SelfTranscriptFinal(_TranscriptEvent):
    EVENT_TYPE: ClassVar[str] = "self_transcript_final"

    def __post_init__(self) -> None:
        if self.channel != "self":
            raise ValueError("SelfTranscriptFinal requires channel='self'")


@dataclass(frozen=True, slots=True, kw_only=True)
class PeerTranscriptFinal(_TranscriptEvent):
    EVENT_TYPE: ClassVar[str] = "peer_transcript_final"

    def __post_init__(self) -> None:
        if self.channel != "peer":
            raise ValueError("PeerTranscriptFinal requires channel='peer'")


@dataclass(frozen=True, slots=True, kw_only=True)
class SelfActiveUpdate(OverlayEvent):
    text: str
    occupant_key: str
    secondary_text: str = ""
    source_language: str = ""
    target_language: str = ""

    EVENT_TYPE: ClassVar[str] = "self_active_update"

    def __post_init__(self) -> None:
        if self.channel != "self":
            raise ValueError("SelfActiveUpdate requires channel='self'")
        if self.utterance_id is None:
            raise ValueError("SelfActiveUpdate requires utterance_id")
        if not self.occupant_key.strip():
            raise ValueError("SelfActiveUpdate requires non-empty occupant_key")


@dataclass(frozen=True, slots=True, kw_only=True)
class PeerActiveUpdate(OverlayEvent):
    """Reserved compatibility/fallback; not normal source-only peer product flow."""

    text: str
    occupant_key: str
    source_language: str = ""
    target_language: str = ""

    EVENT_TYPE: ClassVar[str] = "peer_active_update"

    def __post_init__(self) -> None:
        if self.channel != "peer":
            raise ValueError("PeerActiveUpdate requires channel='peer'")
        if self.utterance_id is None:
            raise ValueError("PeerActiveUpdate requires utterance_id")
        if not self.occupant_key.strip():
            raise ValueError("PeerActiveUpdate requires non-empty occupant_key")


@dataclass(frozen=True, slots=True, kw_only=True)
class SelfActiveClear(OverlayEvent):
    EVENT_TYPE: ClassVar[str] = "self_active_clear"

    def __post_init__(self) -> None:
        if self.channel != "self":
            raise ValueError("SelfActiveClear requires channel='self'")


@dataclass(frozen=True, slots=True, kw_only=True)
class TranslationStreamUpdate(OverlayEvent):
    text: str
    source_language: str
    target_language: str
    is_final: bool = False
    applied_context_mode: AppliedContextMode | None = None
    source_text: str = ""

    EVENT_TYPE: ClassVar[str] = "translation_stream_update"


@dataclass(frozen=True, slots=True, kw_only=True)
class TranslationFinal(TranslationStreamUpdate):
    is_final: bool = True

    EVENT_TYPE: ClassVar[str] = "translation_final"

    def __post_init__(self) -> None:
        if not self.is_final:
            raise ValueError("TranslationFinal requires is_final=True")


@dataclass(frozen=True, slots=True, kw_only=True)
class UtteranceClosed(OverlayEvent):
    is_final: bool = True

    EVENT_TYPE: ClassVar[str] = "utterance_closed"


OverlayEventUnion = (
    SelfTranscriptFinal
    | PeerTranscriptFinal
    | SelfActiveUpdate
    | PeerActiveUpdate
    | SelfActiveClear
    | TranslationStreamUpdate
    | TranslationFinal
    | UtteranceClosed
)


class OverlaySink(Protocol):
    async def emit(self, event: OverlayEventUnion) -> None: ...


@dataclass(slots=True)
class NullOverlaySink:
    async def emit(self, event: OverlayEventUnion) -> None:
        _ = event


@dataclass(slots=True)
class OverlayStreamCoalescer:
    interval_ms: int = 300
    _pending_event: OverlayEventUnion | None = None
    _flush_task: asyncio.Task[None] | None = None
    task_factory: Any | None = None

    async def push(
        self,
        event: OverlayEventUnion,
        emit: Callable[[OverlayEventUnion], Awaitable[None]],
    ) -> None:
        self._pending_event = event
        if self._flush_task is None or self._flush_task.done():
            self._flush_task = self._create_task(
                self._delayed_flush(emit),
                task_name="overlay-stream-coalescer-flush",
            )

    async def flush(
        self,
        emit: Callable[[OverlayEventUnion], Awaitable[None]],
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
        emit: Callable[[OverlayEventUnion], Awaitable[None]],
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

    def _take_pending_event(self) -> OverlayEventUnion | None:
        pending = self._pending_event
        self._pending_event = None
        return pending

    def _create_task(
        self,
        coroutine: Coroutine[Any, Any, Any],
        *,
        task_name: str,
    ) -> asyncio.Task[Any]:
        if self.task_factory is not None:
            return self.task_factory(coroutine, task_name=task_name)
        return asyncio.create_task(coroutine, name=f"OverlayStreamCoalescer:{task_name}")


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
        created_at: float | None = None,
        update_id: str | None = None,
        origin_wall_clock_ms: int | None = None,
        session_scope: str | None = None,
        source_text_hash: str | None = None,
        source_text_len: int | None = None,
        logical_turn_key: str | None = None,
    ) -> SelfTranscriptFinal | PeerTranscriptFinal:
        common = self._common_event_fields(
            utterance_id=transcript.utterance_id,
            channel=transcript.channel,
            created_at=created_at if created_at is not None else transcript.created_at,
            update_id=update_id,
            origin_wall_clock_ms=origin_wall_clock_ms,
            session_scope=session_scope,
            source_text_hash=source_text_hash,
            source_text_len=source_text_len,
            logical_turn_key=logical_turn_key,
        )
        event_cls = SelfTranscriptFinal if transcript.channel == "self" else PeerTranscriptFinal
        return event_cls(
            **common,
            text=transcript.text,
            source_language=source_language,
            target_language=target_language,
            is_final=True,
        )

    def translation_stream_update(
        self,
        *,
        utterance_id: UUID,
        channel: ChannelId,
        text: str,
        source_text: str = "",
        source_language: str,
        target_language: str,
        applied_context_mode: AppliedContextMode | None,
        created_at: float | None = None,
        update_id: str | None = None,
        origin_wall_clock_ms: int | None = None,
        session_scope: str | None = None,
        source_text_hash: str | None = None,
        source_text_len: int | None = None,
        logical_turn_key: str | None = None,
    ) -> TranslationStreamUpdate:
        return TranslationStreamUpdate(
            **self._common_event_fields(
                utterance_id=utterance_id,
                channel=channel,
                created_at=created_at,
                update_id=update_id or uuid4().hex,
                origin_wall_clock_ms=origin_wall_clock_ms,
                session_scope=session_scope,
                source_text_hash=source_text_hash,
                source_text_len=source_text_len,
                logical_turn_key=logical_turn_key,
            ),
            text=text,
            source_text=source_text,
            source_language=source_language,
            target_language=target_language,
            is_final=False,
            applied_context_mode=applied_context_mode,
        )

    def self_active_update(
        self,
        *,
        text: str,
        utterance_id: UUID,
        secondary_text: str = "",
        occupant_key: str,
        source_language: str = "",
        target_language: str = "",
        created_at: float | None = None,
        update_id: str | None = None,
        origin_wall_clock_ms: int | None = None,
        session_scope: str | None = None,
        source_text_hash: str | None = None,
        source_text_len: int | None = None,
        logical_turn_key: str | None = None,
    ) -> SelfActiveUpdate:
        return SelfActiveUpdate(
            **self._common_event_fields(
                utterance_id=utterance_id,
                channel="self",
                created_at=created_at,
                update_id=update_id,
                origin_wall_clock_ms=origin_wall_clock_ms,
                session_scope=session_scope,
                source_text_hash=source_text_hash,
                source_text_len=source_text_len,
                logical_turn_key=logical_turn_key,
            ),
            text=text,
            secondary_text=secondary_text,
            occupant_key=occupant_key,
            source_language=source_language,
            target_language=target_language,
        )

    def peer_active_update(
        self,
        *,
        text: str,
        utterance_id: UUID,
        occupant_key: str,
        source_language: str = "",
        target_language: str = "",
        created_at: float | None = None,
        update_id: str | None = None,
        origin_wall_clock_ms: int | None = None,
        session_scope: str | None = None,
        source_text_hash: str | None = None,
        source_text_len: int | None = None,
        logical_turn_key: str | None = None,
    ) -> PeerActiveUpdate:
        return PeerActiveUpdate(
            **self._common_event_fields(
                utterance_id=utterance_id,
                channel="peer",
                created_at=created_at,
                update_id=update_id,
                origin_wall_clock_ms=origin_wall_clock_ms,
                session_scope=session_scope,
                source_text_hash=source_text_hash,
                source_text_len=source_text_len,
                logical_turn_key=logical_turn_key,
            ),
            text=text,
            occupant_key=occupant_key,
            source_language=source_language,
            target_language=target_language,
        )

    def self_active_clear(self, *, created_at: float | None = None) -> SelfActiveClear:
        return SelfActiveClear(
            **self._common_event_fields(
                utterance_id=None,
                channel="self",
                created_at=created_at,
            )
        )

    def translation_final(
        self,
        *,
        utterance_id: UUID,
        channel: ChannelId,
        text: str,
        source_text: str = "",
        source_language: str,
        target_language: str,
        applied_context_mode: AppliedContextMode | None,
        created_at: float | None = None,
        update_id: str | None = None,
        origin_wall_clock_ms: int | None = None,
        session_scope: str | None = None,
        source_text_hash: str | None = None,
        source_text_len: int | None = None,
        logical_turn_key: str | None = None,
    ) -> TranslationFinal:
        return TranslationFinal(
            **self._common_event_fields(
                utterance_id=utterance_id,
                channel=channel,
                created_at=created_at,
                update_id=update_id or uuid4().hex,
                origin_wall_clock_ms=origin_wall_clock_ms,
                session_scope=session_scope,
                source_text_hash=source_text_hash,
                source_text_len=source_text_len,
                logical_turn_key=logical_turn_key,
            ),
            text=text,
            source_text=source_text,
            source_language=source_language,
            target_language=target_language,
            is_final=True,
            applied_context_mode=applied_context_mode,
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

    def _common_event_fields(
        self,
        *,
        utterance_id: UUID | None,
        channel: ChannelId | None,
        created_at: float | None,
        update_id: str | None = None,
        origin_wall_clock_ms: int | None = None,
        session_scope: str | None = None,
        source_text_hash: str | None = None,
        source_text_len: int | None = None,
        logical_turn_key: str | None = None,
    ) -> dict[str, object]:
        self._seq += 1
        return {
            "event_id": f"evt-{self._seq}",
            "seq": self._seq,
            "utterance_id": utterance_id,
            "channel": channel,
            "created_at": created_at if created_at is not None else self.clock.now(),
            "update_id": update_id,
            "origin_wall_clock_ms": origin_wall_clock_ms,
            "session_scope": session_scope,
            "source_text_hash": source_text_hash,
            "source_text_len": source_text_len,
            "logical_turn_key": logical_turn_key,
        }


@dataclass(slots=True)
class SubtitleOverlayOutputAdapter:
    sink: OverlaySink
    event_adapter: OverlayEventAdapter = field(default_factory=OverlayEventAdapter)
    applied_context_mode: AppliedContextMode | None = None

    async def publish_peer_subtitle(self, publication: PeerSubtitlePublication) -> None:
        utterance_id = UUID(publication.utterance_id)
        source_text = publication.transcript_text or ""
        translation_text = publication.translation_text or ""
        source_language = publication.source_language or ""
        target_language = publication.target_language or ""
        protocol_metadata = _overlay_protocol_metadata(publication.metadata)

        if translation_text.strip():
            await self.sink.emit(
                self.event_adapter.translation_final(
                    utterance_id=utterance_id,
                    channel="peer",
                    text=translation_text,
                    source_text=source_text,
                    source_language=source_language,
                    target_language=target_language,
                    applied_context_mode=self.applied_context_mode,
                    **protocol_metadata,
                )
            )
            return

        await self.sink.emit(
            self.event_adapter.transcript_final(
                Transcript(
                    utterance_id=utterance_id,
                    text=source_text,
                    is_final=publication.is_final,
                    channel="peer",
                ),
                source_language=source_language,
                target_language=target_language,
                **protocol_metadata,
            )
        )


def _overlay_protocol_metadata(
    metadata: Mapping[str, DiagnosticFieldValue],
) -> dict[str, str | int | float | None]:
    return {
        "created_at": _metadata_float(metadata, "created_at"),
        "update_id": _metadata_str(metadata, "update_id"),
        "origin_wall_clock_ms": _metadata_int(metadata, "origin_wall_clock_ms"),
        "session_scope": _metadata_str(metadata, "session_scope"),
        "source_text_hash": _metadata_str(metadata, "source_text_hash"),
        "source_text_len": _metadata_int(metadata, "source_text_len"),
        "logical_turn_key": _metadata_str(metadata, "logical_turn_key"),
    }


def _metadata_str(metadata: Mapping[str, DiagnosticFieldValue], key: str) -> str | None:
    value = metadata.get(key)
    if isinstance(value, str):
        return value
    return None


def _metadata_int(metadata: Mapping[str, DiagnosticFieldValue], key: str) -> int | None:
    value = metadata.get(key)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def _metadata_float(metadata: Mapping[str, DiagnosticFieldValue], key: str) -> float | None:
    value = metadata.get(key)
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    return None
