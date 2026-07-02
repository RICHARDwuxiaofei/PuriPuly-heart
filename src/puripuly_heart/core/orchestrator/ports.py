from __future__ import annotations

from collections.abc import Callable
from typing import Protocol
from uuid import UUID

from puripuly_heart.domain.models import ChannelId, OSCMessage, Transcript


class HubRuntimeLoggingPort(Protocol):
    @property
    def mode(self) -> object: ...

    def emit_basic(self, message: str, *, level: int = ...) -> None: ...

    def emit_detailed(self, message: str, *, level: int = ...) -> bool: ...

    def emit_detailed_lazy(
        self,
        build_message: Callable[[], str],
        *,
        level: int = ...,
    ) -> bool: ...


class HubChatboxPort(Protocol):
    def enqueue(self, message: OSCMessage) -> None: ...
    def send_typing(self, is_typing: bool) -> None: ...
    def process_due(self) -> None: ...
    def send_immediate(self, text: str) -> bool: ...


class HubOverlaySinkPort(Protocol):
    async def emit(self, event: object) -> None: ...
    def active_self_overlay_metadata(self) -> object | None: ...


class HubOverlayEventFactoryPort(Protocol):
    def transcript_final(
        self,
        transcript: Transcript,
        *,
        source_language: str,
        target_language: str,
    ) -> object: ...

    def translation_final(
        self,
        *,
        utterance_id: UUID,
        channel: ChannelId,
        text: str,
        source_language: str,
        target_language: str,
        applied_context_mode: object | None,
        created_at: float,
        source_text: str | None = ...,
        update_id: str | None = ...,
        origin_wall_clock_ms: int | None = ...,
        session_scope: str | None = ...,
        source_text_hash: str | None = ...,
        source_text_len: int | None = ...,
        logical_turn_key: str | None = ...,
    ) -> object: ...

    def utterance_closed(
        self,
        *,
        utterance_id: UUID,
        channel: ChannelId,
        is_final: bool,
    ) -> object: ...

    def self_active_update(
        self,
        *,
        text: str,
        utterance_id: UUID,
        secondary_text: str,
        occupant_key: str,
        source_language: str,
        target_language: str,
        created_at: float,
        update_id: str | None = ...,
        origin_wall_clock_ms: int | None = ...,
        session_scope: str | None = ...,
        source_text_hash: str | None = ...,
        source_text_len: int | None = ...,
        logical_turn_key: str | None = ...,
    ) -> object: ...


def runtime_logging_mode_is_detailed(mode: object) -> bool:
    return str(getattr(mode, "value", mode)) == "detailed"


def format_basic_latency_summary(
    *,
    channel: str,
    e2e_ms: int,
) -> str:
    parts = [
        f"channel={channel}",
        f"e2e_ms={e2e_ms}",
    ]
    return f"[Basic][Latency] {' '.join(parts)}"


def format_detailed_latency_trace(
    *,
    channel: str,
    utterance_id: str,
    stage: str,
    elapsed_ms: int,
) -> str:
    return (
        f"[Detailed][Latency] channel={channel} utterance_id={utterance_id} "
        f"stage={stage} elapsed_ms={elapsed_ms}"
    )


def format_detailed_latency_breakdown(
    *,
    channel: str,
    e2e_ms: int,
    speech_end_to_stt_final_ms: int | None = None,
    stt_final_to_final_output_ms: int | None = None,
) -> str:
    parts = [
        f"channel={channel}",
        f"e2e_ms={e2e_ms}",
    ]
    if speech_end_to_stt_final_ms is not None:
        parts.append(f"speech_end_to_stt_final_ms={speech_end_to_stt_final_ms}")
    if stt_final_to_final_output_ms is not None:
        parts.append(f"stt_final_to_final_output_ms={stt_final_to_final_output_ms}")
    return f"[Detailed][LatencyBreakdown] {' '.join(parts)}"


def format_translation_ready_for_output(
    *,
    channel: str,
    utterance_id: str,
    update_id: str,
    origin_wall_clock_ms: int | None,
    session_scope: str | None,
    source_text_hash: str | None,
    source_text_len: int | None,
    logical_turn_key: str | None,
    translation_len: int,
    elapsed_ms: int | None,
) -> str:
    parts = [
        "[Detailed][Hub] translation_ready_for_output",
        f"channel={channel}",
        f"utterance_id={utterance_id}",
        f"update_id={update_id}",
        f"origin_wall_clock_ms={origin_wall_clock_ms}",
        f"session_scope={session_scope}",
        f"source_text_hash={source_text_hash}",
        f"source_text_len={source_text_len}",
        f"logical_turn_key={logical_turn_key}",
        f"translation_len={translation_len}",
    ]
    if elapsed_ms is not None:
        parts.append(f"elapsed_ms={elapsed_ms}")
    return " ".join(parts)


__all__ = [
    "HubChatboxPort",
    "HubOverlayEventFactoryPort",
    "HubOverlaySinkPort",
    "HubRuntimeLoggingPort",
    "format_basic_latency_summary",
    "format_detailed_latency_breakdown",
    "format_detailed_latency_trace",
    "format_translation_ready_for_output",
    "runtime_logging_mode_is_detailed",
]
