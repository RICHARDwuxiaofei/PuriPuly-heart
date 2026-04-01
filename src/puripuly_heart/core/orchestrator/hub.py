from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID, uuid4

logger = logging.getLogger(__name__)

from puripuly_heart.core.clock import Clock, SystemClock
from puripuly_heart.core.language import get_llm_language_name
from puripuly_heart.core.llm.provider import LLMProvider
from puripuly_heart.core.orchestrator.channel_runtime import (
    ChannelRuntime,
    ContextEntry,
    _MergeBuffer,
)
from puripuly_heart.core.orchestrator.context import ContextMode, ContextResolver
from puripuly_heart.core.osc.smart_queue import SmartOscQueue
from puripuly_heart.core.overlay.sink import (
    OverlayEventAdapter,
    OverlaySink,
    OverlayStreamCoalescer,
)
from puripuly_heart.core.vad.gating import SpeechChunk, SpeechEnd, SpeechStart, VadEvent
from puripuly_heart.domain.events import (
    STTErrorEvent,
    STTFinalEvent,
    STTPartialEvent,
    STTSessionState,
    STTSessionStateEvent,
    UIEvent,
    UIEventType,
)
from puripuly_heart.domain.models import (
    ChannelId,
    OSCMessage,
    Transcript,
    Translation,
    UtteranceBundle,
)


class STTProvider(Protocol):
    async def handle_vad_event(self, event: VadEvent) -> None: ...
    async def close(self) -> None: ...
    def events(self): ...


_PROMO_INTERVAL_SEC: float = 300.0  # 5 minutes
_RELAXED_OVERLAP_MIN_CHARS: int = 3
_BOUNDARY_PUNCT = {".", ",", ";", ":", "!", "?"}
_SOFT_REUSE_PUNCT = {".", ",", "…", "。", "，", "、"}
_SELF_RUNTIME_FIELDS = {
    "stt": "stt",
    "_stt_task": "stt_task",
    "_utterances": "utterances",
    "_translation_tasks": "translation_tasks",
    "_utterance_sources": "utterance_sources",
    "_utterance_start_times": "utterance_start_times",
    "_translation_history": "translation_history",
    "_speech_ended_ids": "speech_ended_ids",
    "_merge_buffer": "merge_buffer",
}


@dataclass(slots=True)
class ClientHub:
    stt: STTProvider | None
    llm: LLMProvider | None
    osc: SmartOscQueue
    peer_stt: STTProvider | None = None
    overlay_sink: OverlaySink | None = None
    clock: Clock = SystemClock()

    source_language: str = "ko"
    target_language: str = "en"
    system_prompt: str = ""
    fallback_transcript_only: bool = False
    translation_enabled: bool = True
    peer_translation_enabled: bool = False
    integrated_context_enabled: bool = False
    hangover_s: float = 1.1  # VAD hangover in seconds (for E2E latency calculation)

    # Context memory settings
    context_time_window_s: float = 30.0  # Only include entries within this time window
    context_max_entries: int = 3  # Maximum number of context entries to include
    integrated_context_time_window_s: float = 60.0
    integrated_context_max_entries: int = 6
    low_latency_mode: bool = False
    low_latency_merge_gap_ms: int = 600
    low_latency_spec_retry_max: int = 1
    low_latency_finalize_wait_ms: int = 400
    low_latency_awaiting_vad_timeout_s: float = 3.0  # Timeout for awaiting_vad_end state

    ui_events: asyncio.Queue[UIEvent] = field(default_factory=asyncio.Queue)

    _utterances: dict[UUID, UtteranceBundle] = field(default_factory=dict)
    _translation_tasks: dict[UUID, asyncio.Task[None]] = field(default_factory=dict)
    _utterance_sources: dict[UUID, str] = field(default_factory=dict)
    _utterance_start_times: dict[UUID, float] = field(
        default_factory=dict
    )  # For E2E latency tracking
    _translation_history: list[ContextEntry] = field(default_factory=list)  # Context memory
    _speech_ended_ids: set[UUID] = field(default_factory=set)  # Track SpeechEnd arrivals
    _stt_task: asyncio.Task[None] | None = None
    _peer_stt_task: asyncio.Task[None] | None = None
    _osc_flush_task: asyncio.Task[None] | None = None
    _running: bool = False
    _last_promo_time: float | None = None
    _promo_eligible: bool = False
    _merge_buffer: _MergeBuffer | None = None
    self_runtime: ChannelRuntime = field(init=False)
    peer_runtime: ChannelRuntime = field(init=False)
    context_resolver: ContextResolver = field(init=False)
    active_chatbox_channel: ChannelId = field(init=False, default="self")
    overlay_event_adapter: OverlayEventAdapter = field(init=False)
    _overlay_active_self_text: str | None = field(init=False, default=None)
    overlay_stream_coalesce_ms: int = 300
    last_error_source: str | None = None

    def __post_init__(self) -> None:
        self.overlay_event_adapter = OverlayEventAdapter(clock=self.clock)
        self.self_runtime = ChannelRuntime(
            channel="self",
            stt=self.stt,
            stt_task=self._stt_task,
            utterances=self._utterances,
            translation_tasks=self._translation_tasks,
            utterance_sources=self._utterance_sources,
            utterance_start_times=self._utterance_start_times,
            translation_history=self._translation_history,
            speech_ended_ids=self._speech_ended_ids,
            merge_buffer=self._merge_buffer,
            alias_target=self,
        )
        self.peer_runtime = ChannelRuntime(channel="peer", stt=self.peer_stt)
        self.context_resolver = ContextResolver(
            clock=self.clock,
            local_time_window_s=self.context_time_window_s,
            local_max_entries=self.context_max_entries,
            integrated_time_window_s=self.integrated_context_time_window_s,
            integrated_max_entries=self.integrated_context_max_entries,
        )
        self._sync_self_runtime_aliases()

    def __setattr__(self, name: str, value: object) -> None:
        object.__setattr__(self, name, value)
        if name in {
            "clock",
            "context_time_window_s",
            "context_max_entries",
            "integrated_context_time_window_s",
            "integrated_context_max_entries",
        }:
            try:
                resolver = object.__getattribute__(self, "context_resolver")
            except AttributeError:
                resolver = None
            try:
                overlay_event_adapter = object.__getattribute__(self, "overlay_event_adapter")
            except AttributeError:
                overlay_event_adapter = None
            if resolver is not None:
                if name == "clock":
                    resolver.clock = value  # type: ignore[assignment]
                elif name == "context_time_window_s":
                    resolver.local_time_window_s = value  # type: ignore[assignment]
                elif name == "context_max_entries":
                    resolver.local_max_entries = value  # type: ignore[assignment]
                elif name == "integrated_context_time_window_s":
                    resolver.integrated_time_window_s = value  # type: ignore[assignment]
                elif name == "integrated_context_max_entries":
                    resolver.integrated_max_entries = value  # type: ignore[assignment]
            if name == "clock" and overlay_event_adapter is not None:
                overlay_event_adapter.clock = value  # type: ignore[assignment]
        runtime_field = _SELF_RUNTIME_FIELDS.get(name)
        if runtime_field is None:
            return
        try:
            runtime = object.__getattribute__(self, "self_runtime")
        except AttributeError:
            return
        object.__setattr__(runtime, runtime_field, value)

    def _sync_self_runtime_aliases(self) -> None:
        self._stt_task = self.self_runtime.stt_task
        self._utterances = self.self_runtime.utterances
        self._translation_tasks = self.self_runtime.translation_tasks
        self._utterance_sources = self.self_runtime.utterance_sources
        self._utterance_start_times = self.self_runtime.utterance_start_times
        self._translation_history = self.self_runtime.translation_history
        self._speech_ended_ids = self.self_runtime.speech_ended_ids
        self._merge_buffer = self.self_runtime.merge_buffer

    async def start(self, *, auto_flush_osc: bool = False) -> None:
        if self._running:
            return
        self._running = True
        if self.stt is not None:
            self._stt_task = asyncio.create_task(self._run_stt_event_loop(self.stt))
        if self.peer_stt is not None:
            self._peer_stt_task = asyncio.create_task(self._run_stt_event_loop(self.peer_stt))
        if auto_flush_osc:
            self._osc_flush_task = asyncio.create_task(self._run_osc_flush_loop())

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False

        if self._osc_flush_task:
            self._osc_flush_task.cancel()
            await asyncio.gather(self._osc_flush_task, return_exceptions=True)
            self._osc_flush_task = None

        await self._stop_stt_event_loop()
        await self.reset_overlay_preview()
        await self._reset_stt_runtime_state()

        if self.stt is not None:
            await self.stt.close()
        if self.peer_stt is not None:
            await self.peer_stt.close()

        if self.llm is not None:
            await self.llm.close()

    async def replace_stt_provider(self, stt: STTProvider | None) -> None:
        old_stt = self.stt
        await self._stop_stt_task("_stt_task")
        await self.reset_overlay_preview()
        await self.self_runtime.reset_runtime_state()
        self._sync_self_runtime_aliases()

        if old_stt is not None:
            await old_stt.close()

        self.stt = stt
        self.self_runtime.stt = stt
        if self._running and self.stt is not None:
            self._stt_task = asyncio.create_task(self._run_stt_event_loop(self.stt))

    async def replace_peer_stt_provider(self, stt: STTProvider | None) -> None:
        old_stt = self.peer_stt
        await self._stop_stt_task("_peer_stt_task")
        await self.peer_runtime.reset_runtime_state()

        if old_stt is not None:
            await old_stt.close()

        self.peer_stt = stt
        self.peer_runtime.stt = stt
        if self._running and self.peer_stt is not None:
            self._peer_stt_task = asyncio.create_task(self._run_stt_event_loop(self.peer_stt))

    def mark_promo_eligible(self) -> None:
        """Mark that user clicked STT button. Next STREAMING state will send promo."""
        self._promo_eligible = True

    def clear_context(self) -> None:
        """Clear the translation context history."""
        self.self_runtime.clear_context()
        self.peer_runtime.clear_context()
        logger.info("[Hub] Context history cleared")

    def _get_valid_context(self) -> list[ContextEntry]:
        """Get context entries within time window and max entries limit."""
        return self.context_resolver.get_local_entries(
            runtime=self.self_runtime,
            source_language=self.source_language,
            target_language=self.target_language,
        )

    def _format_context_for_llm(self, context: list[ContextEntry]) -> str:
        """Format context entries as a string for LLM prompt."""
        return self.context_resolver.format_local(context)

    def _remember_context_entry(
        self,
        text: str,
        timestamp: float,
        *,
        runtime: ChannelRuntime | None = None,
        speaker_label: str | None = None,
        peer_epoch: int | None = None,
    ) -> None:
        runtime = runtime or self.self_runtime
        runtime.remember_context(
            text,
            timestamp=timestamp,
            source_language=self.source_language,
            target_language=self.target_language,
            max_entries=max(self.context_max_entries, self.integrated_context_max_entries),
            speaker_label=speaker_label,
            peer_epoch=peer_epoch,
        )

    async def handle_vad_event(self, event: VadEvent) -> None:
        if isinstance(event, SpeechStart):
            if self.low_latency_mode:
                self._mark_resume_pending(event)

        if isinstance(event, SpeechChunk):
            if self.low_latency_mode:
                self._maybe_confirm_resume(event)

        # Record start time for E2E latency tracking (from speech end)
        if isinstance(event, SpeechEnd):
            self.osc.send_typing(True)
            self._utterance_start_times[event.utterance_id] = self.clock.now()
            self._speech_ended_ids.add(event.utterance_id)
            if self.low_latency_mode:
                self._maybe_update_buffer_end_time(event.utterance_id)
                self._maybe_start_finalize_wait(event.utterance_id)
                await self._maybe_clear_resume_on_end(event)

        if self.stt is not None:
            await self.stt.handle_vad_event(event)

    async def handle_peer_vad_event(self, event: VadEvent) -> None:
        if self.peer_stt is not None:
            await self.peer_stt.handle_vad_event(event)

    async def submit_text(self, text: str, *, source: str = "You") -> UUID:
        text = text.strip()
        if not text:
            raise ValueError("text must be non-empty")

        utterance_id = uuid4()
        self._remember_source(utterance_id, source)

        transcript = Transcript(
            utterance_id=utterance_id,
            text=text,
            is_final=True,
            created_at=self.clock.now(),
        )
        await self._handle_transcript(transcript, is_final=True, source=source)

        if self.llm is None or not self.translation_enabled:
            await self._enqueue_osc(utterance_id, transcript_text=text, translation_text=None)
        else:
            await self._ensure_translation(transcript)

        return utterance_id

    def _runtime_for_channel(self, channel: ChannelId) -> ChannelRuntime:
        return self.self_runtime if channel == "self" else self.peer_runtime

    def _runtime_for_utterance(
        self, utterance_id: UUID, *, default_channel: ChannelId = "self"
    ) -> ChannelRuntime:
        if utterance_id in self.self_runtime.utterances:
            return self.self_runtime
        if utterance_id in self.peer_runtime.utterances:
            return self.peer_runtime
        return self._runtime_for_channel(default_channel)

    def get_or_create_bundle(
        self, utterance_id: UUID, *, channel: ChannelId = "self"
    ) -> UtteranceBundle:
        return self._runtime_for_utterance(
            utterance_id, default_channel=channel
        ).get_or_create_bundle(utterance_id)

    async def _run_stt_event_loop(self, provider: STTProvider) -> None:
        try:
            async for ev in provider.events():
                await self._handle_stt_event(ev)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("[Hub] STT event loop crashed")
            raise

    async def _stop_stt_event_loop(self) -> None:
        await self._stop_stt_task("_stt_task")
        await self._stop_stt_task("_peer_stt_task")

    async def _stop_stt_task(self, attr_name: str) -> None:
        task = getattr(self, attr_name)
        if task is None:
            return
        setattr(self, attr_name, None)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def _reset_stt_runtime_state(self) -> None:
        await self.self_runtime.reset_runtime_state()
        await self.peer_runtime.reset_runtime_state()
        self._sync_self_runtime_aliases()

    async def _handle_stt_event(self, event: object) -> None:
        if isinstance(event, STTSessionStateEvent):
            await self.ui_events.put(
                UIEvent(
                    type=UIEventType.SESSION_STATE_CHANGED,
                    payload=event.state,
                    channel=event.channel,
                )
            )
            if event.state == STTSessionState.STREAMING and event.channel == "self":
                self._send_stt_connected_notification()
            return

        if isinstance(event, STTErrorEvent):
            await self.ui_events.put(
                UIEvent(
                    type=UIEventType.ERROR,
                    payload=event.message,
                    source="Peer" if event.channel == "peer" else "Mic",
                    channel=event.channel,
                )
            )
            return

        if isinstance(event, STTPartialEvent):
            if event.channel == "peer":
                return
            self._send_stt_connected_notification()
            if self.low_latency_mode:
                return
            logger.debug(
                f"[Hub] STT Partial: '{event.transcript.text[:50]}...' id={str(event.transcript.utterance_id)[:8]}"
            )
            await self._handle_transcript(event.transcript, is_final=False, source="Mic")
            return

        if isinstance(event, STTFinalEvent):
            runtime = self._runtime_for_channel(event.channel)
            source = "Peer" if runtime.channel == "peer" else "Mic"
            if runtime.channel == "self":
                self._send_stt_connected_notification()
            if self.low_latency_mode and runtime.channel == "self":
                await self._handle_low_latency_final(event.transcript)
                return
            await self._handle_transcript(event.transcript, is_final=True, source=source)
            if self.llm is None or not self._translation_enabled_for_runtime(runtime):
                logger.info(
                    "[Hub] Skipping translation (llm=%s, self_enabled=%s, peer_enabled=%s, channel=%s)",
                    self.llm is not None,
                    self.translation_enabled,
                    self.peer_translation_enabled,
                    runtime.channel,
                )
                if self._should_publish_to_chatbox(runtime):
                    await self._enqueue_osc(
                        event.transcript.utterance_id,
                        transcript_text=event.transcript.text,
                        translation_text=None,
                    )
            else:
                await self._ensure_translation(event.transcript)
            return

    def _send_stt_connected_notification(self) -> None:
        """Send promo message when STT connects (only if user clicked button)."""
        if not self._promo_eligible:
            return  # Skip if not triggered by user button click
        self._promo_eligible = False

        now = self.clock.now()
        if self._last_promo_time is not None:
            if now - self._last_promo_time < _PROMO_INTERVAL_SEC:
                return
        if self.osc.send_immediate("PuriPuly ON!"):
            self._last_promo_time = now

    async def _handle_transcript(
        self, transcript: Transcript, *, is_final: bool, source: str | None
    ) -> None:
        bundle = self.get_or_create_bundle(transcript.utterance_id, channel=transcript.channel)
        bundle.with_transcript(transcript)
        self._remember_source(transcript.utterance_id, source, channel=transcript.channel)
        await self.ui_events.put(
            UIEvent(
                type=UIEventType.TRANSCRIPT_FINAL if is_final else UIEventType.TRANSCRIPT_PARTIAL,
                utterance_id=transcript.utterance_id,
                payload=transcript,
                source=source,
            )
        )
        if is_final:
            await self._emit_final_transcript_to_overlay(transcript)

    async def _emit_final_transcript_to_overlay(self, transcript: Transcript) -> None:
        if self.overlay_sink is None:
            return
        await self._emit_overlay_event(
            self.overlay_event_adapter.transcript_final(
                transcript,
                source_language=self.source_language,
                target_language=self.target_language,
            )
        )

    async def _emit_translation_to_overlay(
        self,
        *,
        translation: Translation,
        applied_context_mode: ContextMode | None,
    ) -> None:
        if self.overlay_sink is None:
            return

        await self._emit_overlay_event(
            self.overlay_event_adapter.translation_final(
                utterance_id=translation.utterance_id,
                channel=translation.channel,
                text=translation.text,
                source_language=self.source_language,
                target_language=self.target_language,
                applied_context_mode=applied_context_mode,
                speaker_label=translation.speaker_label,
                peer_epoch=translation.peer_epoch,
                created_at=translation.created_at,
            )
        )

    async def _emit_overlay_event(self, event: object) -> None:
        if self.overlay_sink is None:
            return
        try:
            await self.overlay_sink.emit(event)  # type: ignore[arg-type]
        except Exception:
            self.last_error_source = "overlay_sink"
            logger.exception("[Hub] Overlay sink emit failed")

    async def _emit_overlay_active_self_event(self, event: object) -> None:
        await self._emit_overlay_event(event)
        if getattr(event, "type", None) == "self_active_update":
            self._overlay_active_self_text = getattr(event, "text", None)
        elif getattr(event, "type", None) == "self_active_clear":
            self._overlay_active_self_text = None

    async def _sync_overlay_active_self(
        self, buffer: _MergeBuffer | None, *, created_at: float | None = None
    ) -> None:
        if self.overlay_sink is None or buffer is None:
            return

        active_text = self._merge_text(buffer.parts)
        if not active_text:
            return
        if active_text == self._overlay_active_self_text:
            return

        await self._emit_overlay_active_self_event(
            self.overlay_event_adapter.self_active_update(
                text=active_text,
                created_at=created_at,
            )
        )

    async def reset_overlay_preview(self) -> None:
        if self._overlay_active_self_text is None:
            return
        if self.overlay_sink is None:
            self._overlay_active_self_text = None
            return
        await self._emit_overlay_active_self_event(self.overlay_event_adapter.self_active_clear())

    def _merge_text(self, parts: list[str]) -> str:
        merged = ""
        for part in parts:
            part_clean = part.strip()
            if not part_clean:
                continue
            if not merged:
                merged = part_clean
                continue
            merged = self._merge_with_overlap(merged, part_clean)
        return merged.strip()

    def _merge_with_overlap(self, existing: str, addition: str) -> str:
        if not existing:
            return addition
        if not addition:
            return existing
        if existing.endswith(addition):
            return existing

        max_overlap = min(len(existing), len(addition))
        overlap_len = 0
        for i in range(1, max_overlap + 1):
            if existing[-i:] == addition[:i]:
                overlap_len = i
        if overlap_len:
            return existing + addition[overlap_len:]

        relaxed_merge = self._relaxed_overlap_merge(existing, addition)
        if relaxed_merge is not None:
            return relaxed_merge

        if self._needs_space(existing, addition):
            return f"{existing} {addition}"
        return f"{existing}{addition}"

    def _relaxed_overlap_merge(self, existing: str, addition: str) -> str | None:
        if not existing or not addition:
            return None

        left_trimmed, left_trimmed_len = self._strip_trailing_boundary(existing)
        right_trimmed, right_trimmed_len = self._strip_leading_boundary(addition)
        if left_trimmed_len == 0 and right_trimmed_len == 0:
            return None
        if not left_trimmed or not right_trimmed:
            return None

        max_overlap = min(len(left_trimmed), len(right_trimmed))
        overlap_len = 0
        for i in range(1, max_overlap + 1):
            if left_trimmed[-i:] == right_trimmed[:i]:
                overlap_len = i

        if overlap_len < _RELAXED_OVERLAP_MIN_CHARS:
            return None

        cut = right_trimmed_len + overlap_len
        if cut <= 0 or cut > len(addition):
            return None

        base = existing[:-left_trimmed_len] if left_trimmed_len else existing
        if cut >= len(addition):
            return base
        return f"{base}{addition[cut:]}"

    def _strip_trailing_boundary(self, text: str) -> tuple[str, int]:
        idx = len(text)
        while idx > 0 and self._is_boundary_char(text[idx - 1]):
            idx -= 1
        return text[:idx], len(text) - idx

    def _strip_leading_boundary(self, text: str) -> tuple[str, int]:
        idx = 0
        while idx < len(text) and self._is_boundary_char(text[idx]):
            idx += 1
        return text[idx:], idx

    def _is_boundary_char(self, ch: str) -> bool:
        return ch.isspace() or ch in _BOUNDARY_PUNCT

    def _soft_reuse_mode(self, spec_text: str | None, final_text: str) -> str | None:
        if spec_text is None:
            return None
        if spec_text == final_text:
            return "exact"

        normalized_spec = self._normalize_soft_reuse_text(spec_text)
        normalized_final = self._normalize_soft_reuse_text(final_text)
        if not normalized_spec or not normalized_final:
            return None
        if normalized_spec == normalized_final:
            return "soft_boundary"
        return None

    def _normalize_soft_reuse_text(self, text: str) -> str:
        start = 0
        end = len(text)
        while start < end and self._is_soft_reuse_boundary_char(text[start]):
            start += 1
        while end > start and self._is_soft_reuse_boundary_char(text[end - 1]):
            end -= 1
        return text[start:end]

    def _is_soft_reuse_boundary_char(self, ch: str) -> bool:
        return ch.isspace() or ch in _SOFT_REUSE_PUNCT

    def _needs_space(self, left: str, right: str) -> bool:
        if not left or not right:
            return False
        left_ch = left[-1]
        right_ch = right[0]
        if self._is_ascii_alnum(left_ch) and self._is_ascii_alnum(right_ch):
            return True
        if (" " in left or " " in right) and left_ch.isalnum() and right_ch.isalnum():
            return True
        return False

    def _is_ascii_alnum(self, ch: str) -> bool:
        return ord(ch) < 128 and ch.isalnum()

    def _upsert_merge_part(self, buffer: _MergeBuffer, utterance_id: UUID, text: str) -> None:
        if not text:
            return
        for idx in range(len(buffer.utterance_ids) - 1, -1, -1):
            if buffer.utterance_ids[idx] == utterance_id:
                existing = buffer.parts[idx]
                if existing == text:
                    return
                if text in existing:
                    return
                if existing in text:
                    merged = text
                else:
                    merged = self._merge_with_overlap(existing, text)
                if merged != existing:
                    buffer.parts[idx] = merged
                    logger.debug(
                        "[Metric] final_update id=%s index=%s text_len=%s",
                        str(buffer.merge_id)[:8],
                        idx,
                        len(merged),
                    )
                return
        buffer.parts.append(text)
        buffer.utterance_ids.append(utterance_id)

    def _clear_resume_state(self, buffer: _MergeBuffer) -> None:
        buffer.resume_pending = False
        buffer.resume_confirmed = False
        buffer.resume_utterance_id = None
        buffer.resume_chunk_count = 0
        buffer.resume_started_at = None
        self._cancel_resume_end_timeout(buffer)

    def _maybe_update_buffer_end_time(self, utterance_id: UUID) -> None:
        buffer = self._merge_buffer
        if buffer is None or utterance_id not in buffer.utterance_ids:
            return
        end_time = self._utterance_start_times.get(utterance_id)
        if end_time is None:
            return
        if buffer.start_time is None or end_time < buffer.start_time:
            buffer.start_time = end_time
        if buffer.last_end_time is None or end_time > buffer.last_end_time:
            buffer.last_end_time = end_time

    def _cancel_finalize_wait(self, buffer: _MergeBuffer) -> None:
        task = buffer.finalize_wait_task
        if task is not None and task is not asyncio.current_task():
            if not task.done():
                task.cancel()
        buffer.finalize_wait_task = None
        buffer.finalize_wait_started_at = None

    def _maybe_start_finalize_wait(self, utterance_id: UUID) -> None:
        buffer = self._merge_buffer
        if buffer is None:
            return
        if not buffer.awaiting_vad_end or buffer.awaiting_vad_utterance_id != utterance_id:
            return
        buffer.awaiting_vad_end = False
        buffer.awaiting_vad_utterance_id = None
        self._cancel_awaiting_vad_timeout(buffer)
        self._restart_post_end_grace(buffer)

    def _cancel_awaiting_vad_timeout(self, buffer: _MergeBuffer) -> None:
        task = buffer.awaiting_vad_timeout_task
        if task is not None and task is not asyncio.current_task():
            if not task.done():
                task.cancel()
        buffer.awaiting_vad_timeout_task = None

    def _start_awaiting_vad_timeout(self, buffer: _MergeBuffer) -> None:
        if self.low_latency_awaiting_vad_timeout_s <= 0:
            return
        self._cancel_awaiting_vad_timeout(buffer)
        buffer.awaiting_vad_timeout_task = asyncio.create_task(
            self._awaiting_vad_timeout(buffer.merge_id)
        )

    async def _awaiting_vad_timeout(self, merge_id: UUID) -> None:
        try:
            await asyncio.sleep(self.low_latency_awaiting_vad_timeout_s)
        except asyncio.CancelledError:
            return
        buffer = self._merge_buffer
        if buffer is None or buffer.merge_id != merge_id:
            return
        if not buffer.awaiting_vad_end:
            return
        logger.debug(
            "[Metric] awaiting_vad_timeout id=%s timeout_s=%s",
            str(merge_id)[:8],
            self.low_latency_awaiting_vad_timeout_s,
        )
        buffer.awaiting_vad_end = False
        buffer.awaiting_vad_utterance_id = None
        buffer.awaiting_vad_timeout_task = None
        self._restart_post_end_grace(buffer)

    def _cancel_resume_end_timeout(self, buffer: _MergeBuffer) -> None:
        task = buffer.resume_end_timeout_task
        if task is not None and task is not asyncio.current_task():
            if not task.done():
                task.cancel()
        buffer.resume_end_timeout_task = None
        buffer.resume_end_utterance_id = None

    def _start_resume_end_timeout(self, buffer: _MergeBuffer, utterance_id: UUID) -> None:
        self._cancel_resume_end_timeout(buffer)
        buffer.resume_end_utterance_id = utterance_id
        buffer.resume_end_timeout_task = asyncio.create_task(
            self._resume_end_timeout(buffer.merge_id, utterance_id)
        )

    async def _resume_end_timeout(self, merge_id: UUID, utterance_id: UUID) -> None:
        try:
            await asyncio.sleep(self.low_latency_awaiting_vad_timeout_s)
        except asyncio.CancelledError:
            return
        buffer = self._merge_buffer
        if buffer is None or buffer.merge_id != merge_id:
            return
        if buffer.resume_end_utterance_id != utterance_id:
            return
        if not buffer.resume_confirmed:
            return
        logger.debug(
            "[Metric] resume_end_timeout id=%s vad_id=%s timeout_s=%s",
            str(merge_id)[:8],
            str(utterance_id)[:8],
            self.low_latency_awaiting_vad_timeout_s,
        )
        self._clear_resume_state(buffer)
        self._cancel_finalize_wait(buffer)
        await self._try_commit_after_spec(buffer, reason="resume_end_timeout", allow_fallback=True)

    def _restart_post_end_grace(self, buffer: _MergeBuffer) -> None:
        if self.low_latency_finalize_wait_ms <= 0:
            self._cancel_finalize_wait(buffer)
            return
        self._cancel_finalize_wait(buffer)
        buffer.finalize_wait_started_at = self.clock.now()
        buffer.finalize_wait_task = asyncio.create_task(
            self._finalize_wait_timeout(buffer.merge_id, buffer.finalize_wait_started_at)
        )
        logger.debug(
            "[Metric] post_end_grace_start id=%s wait_ms=%s",
            str(buffer.merge_id)[:8],
            self.low_latency_finalize_wait_ms,
        )

    async def _finalize_wait_timeout(self, merge_id: UUID, started_at: float) -> None:
        try:
            await asyncio.sleep(self.low_latency_finalize_wait_ms / 1000.0)
        except asyncio.CancelledError:
            return
        buffer = self._merge_buffer
        if buffer is None or buffer.merge_id != merge_id:
            return
        if buffer.finalize_wait_started_at != started_at:
            return
        buffer.finalize_wait_task = None
        buffer.finalize_wait_started_at = None
        logger.debug(
            "[Metric] post_end_grace_timeout id=%s wait_ms=%s",
            str(merge_id)[:8],
            self.low_latency_finalize_wait_ms,
        )
        if self.llm is None or not self.translation_enabled:
            await self._commit_merge(buffer, reason="post_end_grace")
            return
        await self._try_commit_after_spec(buffer, reason="post_end_grace", allow_fallback=False)

    def _mark_resume_pending(self, event: SpeechStart) -> None:
        buffer = self._merge_buffer
        if buffer is None:
            return
        if buffer.resume_pending and buffer.resume_utterance_id == event.utterance_id:
            return
        # 새 resume 시작 시 이전 타임아웃 취소
        self._cancel_resume_end_timeout(buffer)
        buffer.resume_pending = True
        buffer.resume_confirmed = False
        buffer.resume_utterance_id = event.utterance_id
        buffer.resume_chunk_count = 0
        buffer.resume_started_at = self.clock.now()
        logger.debug(
            "[Metric] resume_pending id=%s vad_id=%s",
            str(buffer.merge_id)[:8],
            str(event.utterance_id)[:8],
        )

    def _maybe_confirm_resume(self, event: SpeechChunk) -> None:
        buffer = self._merge_buffer
        if buffer is None or not buffer.resume_pending:
            return
        if buffer.resume_utterance_id != event.utterance_id:
            return
        if buffer.resume_confirmed:
            return
        buffer.resume_chunk_count += 1
        if buffer.resume_chunk_count < 3:
            return
        buffer.resume_confirmed = True
        confirm_ms = 0
        if buffer.resume_started_at is not None:
            confirm_ms = int((self.clock.now() - buffer.resume_started_at) * 1000)
        logger.debug(
            "[Metric] resume_confirmed id=%s confirm_ms=%s chunk_count=%s",
            str(buffer.merge_id)[:8],
            confirm_ms,
            buffer.resume_chunk_count,
        )
        if buffer.spec_task is not None and not buffer.spec_task.done():
            buffer.spec_task.cancel()
            logger.debug(
                "[Metric] spec_cancel id=%s reason=resume_confirmed",
                str(buffer.merge_id)[:8],
            )
        elif buffer.spec_translation is not None:
            logger.debug(
                "[Metric] spec_cancel id=%s reason=resume_confirmed",
                str(buffer.merge_id)[:8],
            )
        buffer.spec_task = None
        buffer.spec_translation = None
        buffer.spec_text = None
        buffer.spec_started_at = None
        buffer.spec_done_at = None

    async def _maybe_clear_resume_on_end(self, event: SpeechEnd) -> None:
        buffer = self._merge_buffer
        if buffer is None:
            return
        if buffer.resume_utterance_id != event.utterance_id:
            return
        if buffer.resume_confirmed:
            # resume_confirmed 상태에서 SpeechEnd → STT Final 대기 타임아웃 시작
            self._start_resume_end_timeout(buffer, event.utterance_id)
            return
        if not buffer.resume_pending:
            return
        false_ms = 0
        if buffer.resume_started_at is not None:
            false_ms = int((self.clock.now() - buffer.resume_started_at) * 1000)
        logger.debug(
            "[Metric] resume_false_start id=%s false_ms=%s chunk_count=%s",
            str(buffer.merge_id)[:8],
            false_ms,
            buffer.resume_chunk_count,
        )
        self._clear_resume_state(buffer)
        await self._try_commit_after_spec(buffer, reason="resume_false_start", allow_fallback=True)

    async def _handle_low_latency_final(self, transcript: Transcript) -> None:
        text = transcript.text.strip()
        if not text:
            return

        now = self.clock.now()
        buffer = self._merge_buffer
        if buffer is None:
            buffer = _MergeBuffer(merge_id=uuid4(), start_time=now, last_final_at=now)
            self._merge_buffer = buffer
        if buffer.resume_pending or buffer.resume_confirmed:
            self._clear_resume_state(buffer)
        self._upsert_merge_part(buffer, transcript.utterance_id, text)
        buffer.last_final_at = now
        await self._sync_overlay_active_self(buffer, created_at=transcript.created_at)

        end_time = self._utterance_start_times.get(transcript.utterance_id)
        speech_already_ended = transcript.utterance_id in self._speech_ended_ids

        if end_time is None and not speech_already_ended:
            # SpeechEnd has not arrived yet - wait for it
            buffer.awaiting_vad_end = True
            buffer.awaiting_vad_utterance_id = transcript.utterance_id
            self._cancel_finalize_wait(buffer)
            self._start_awaiting_vad_timeout(buffer)
            logger.debug(
                "[Metric] final_phase id=%s phase=pre_end vad_id=%s",
                str(buffer.merge_id)[:8],
                str(transcript.utterance_id)[:8],
            )
        else:
            # SpeechEnd already arrived (or end_time exists) - proceed to post_end
            self._maybe_update_buffer_end_time(transcript.utterance_id)
            if (
                buffer.awaiting_vad_end
                and buffer.awaiting_vad_utterance_id == transcript.utterance_id
            ):
                buffer.awaiting_vad_end = False
                buffer.awaiting_vad_utterance_id = None
            self._restart_post_end_grace(buffer)
            logger.debug(
                "[Metric] final_phase id=%s phase=post_end vad_id=%s",
                str(buffer.merge_id)[:8],
                str(transcript.utterance_id)[:8],
            )

        if self.llm is None or not self.translation_enabled:
            await self._commit_merge(buffer, reason="final_no_llm")
            return

        await self._maybe_restart_spec(buffer)

    async def _commit_merge(self, buffer: _MergeBuffer, *, reason: str) -> None:
        if buffer.resume_pending or buffer.resume_confirmed:
            hold_ms = 0
            if buffer.spec_done_at is not None:
                hold_ms = int((self.clock.now() - buffer.spec_done_at) * 1000)
            logger.debug(
                "[Metric] commit_blocked id=%s reason=%s hold_ms=%s",
                str(buffer.merge_id)[:8],
                reason,
                hold_ms,
            )
            return
        if buffer.awaiting_vad_end:
            hold_ms = 0
            if buffer.finalize_wait_started_at is not None:
                hold_ms = int((self.clock.now() - buffer.finalize_wait_started_at) * 1000)
            logger.debug(
                "[Metric] commit_blocked id=%s reason=await_vad_end hold_ms=%s",
                str(buffer.merge_id)[:8],
                hold_ms,
            )
            return
        if buffer.finalize_wait_task is not None:
            hold_ms = 0
            if buffer.finalize_wait_started_at is not None:
                hold_ms = int((self.clock.now() - buffer.finalize_wait_started_at) * 1000)
            logger.debug(
                "[Metric] commit_deferred id=%s reason=post_end_grace hold_ms=%s",
                str(buffer.merge_id)[:8],
                hold_ms,
            )
            return
        self._cancel_finalize_wait(buffer)
        buffer.awaiting_vad_end = False
        buffer.awaiting_vad_utterance_id = None
        for utterance_id in buffer.utterance_ids:
            self._utterance_start_times.pop(utterance_id, None)
            self._speech_ended_ids.discard(utterance_id)
        if self._merge_buffer is buffer:
            self._merge_buffer = None

        await self.reset_overlay_preview()

        final_text = self._merge_text(buffer.parts)
        if not final_text:
            return

        if buffer.spec_task is not None and not buffer.spec_task.done():
            buffer.spec_task.cancel()

        if buffer.last_end_time is not None:
            self._utterance_start_times[buffer.merge_id] = buffer.last_end_time
        elif buffer.start_time is not None:
            self._utterance_start_times[buffer.merge_id] = buffer.start_time

        transcript = Transcript(
            utterance_id=buffer.merge_id,
            text=final_text,
            is_final=True,
            created_at=self.clock.now(),
        )
        await self._handle_transcript(transcript, is_final=True, source="Mic")

        if self.llm is None or not self.translation_enabled:
            logger.info(
                "[Hub] Skipping translation (llm=%s, enabled=%s)",
                self.llm is not None,
                self.translation_enabled,
            )
            await self._enqueue_osc(
                buffer.merge_id, transcript_text=final_text, translation_text=None
            )
            return

        reuse_mode = None
        if buffer.spec_translation is not None:
            reuse_mode = self._soft_reuse_mode(buffer.spec_text, final_text)
        reuse_spec = reuse_mode is not None
        commit_delay_ms = 0
        if buffer.start_time is not None:
            commit_delay_ms = int((self.clock.now() - buffer.start_time) * 1000)
        logger.debug(
            "[Metric] merge_commit id=%s used_spec=%s parts=%s text_len=%s commit_delay_ms=%s reason=%s",
            str(buffer.merge_id)[:8],
            reuse_spec,
            len(buffer.parts),
            len(final_text),
            commit_delay_ms,
            reason,
        )
        if reuse_spec:
            translation = buffer.spec_translation
            if translation is not None:
                logger.debug(
                    "[Metric] spec_reuse id=%s translation_len=%s after_final=%s",
                    str(buffer.merge_id)[:8],
                    len(translation.text),
                    True,
                )
                bundle = self.get_or_create_bundle(buffer.merge_id)
                bundle.with_translation(translation)
                bundle.with_translation(translation)
                self._remember_context_entry(final_text, self.clock.now())
                await self.ui_events.put(
                    UIEvent(
                        type=UIEventType.TRANSLATION_DONE,
                        utterance_id=buffer.merge_id,
                        payload=translation,
                        source=self._get_source(buffer.merge_id),
                    )
                )
                await self._emit_translation_to_overlay(
                    translation=translation,
                    applied_context_mode=None,
                )
                await self._enqueue_osc(
                    buffer.merge_id,
                    transcript_text=final_text,
                    translation_text=translation.text,
                )
                return

        if buffer.spec_translation is not None and reuse_mode is None:
            logger.debug(
                "[Metric] spec_cancel id=%s reason=final_mismatch", str(buffer.merge_id)[:8]
            )

        await self._translate_and_enqueue(buffer.merge_id, final_text)

    async def _maybe_restart_spec(self, buffer: _MergeBuffer) -> None:
        if self.llm is None or not self.translation_enabled:
            return

        if buffer.spec_task is not None:
            if not buffer.spec_task.done():
                buffer.spec_task.cancel()
                logger.debug(
                    "[Metric] spec_cancel id=%s reason=spec_retry", str(buffer.merge_id)[:8]
                )
            elif buffer.spec_translation is not None:
                logger.debug(
                    "[Metric] spec_cancel id=%s reason=spec_retry", str(buffer.merge_id)[:8]
                )
            buffer.spec_task = None
            buffer.spec_translation = None
            buffer.spec_text = None
            buffer.spec_started_at = None
            buffer.spec_done_at = None

        merged_text = self._merge_text(buffer.parts)
        if not merged_text:
            return

        buffer.spec_attempts += 1
        buffer.spec_text = merged_text
        buffer.spec_started_at = self.clock.now()
        logger.debug(
            "[Metric] spec_start id=%s text_len=%s attempt=%s",
            str(buffer.merge_id)[:8],
            len(merged_text),
            buffer.spec_attempts,
        )
        buffer.spec_task = asyncio.create_task(
            self._run_spec_translation(buffer.merge_id, merged_text, buffer.spec_attempts)
        )

    async def _run_spec_translation(self, merge_id: UUID, text: str, attempt: int) -> None:
        if self.llm is None:
            return
        try:
            translation = await self._translate_text(merge_id, text)
        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.error(f"[Hub] Spec translation failed: {exc}")
            buffer = self._merge_buffer
            if buffer is None or buffer.merge_id != merge_id:
                return
            if buffer.spec_text != text or buffer.spec_attempts != attempt:
                return
            buffer.spec_done_at = self.clock.now()
            await self._try_commit_after_spec(buffer, reason="spec_failed", allow_fallback=True)
            return

        buffer = self._merge_buffer
        if buffer is None or buffer.merge_id != merge_id:
            return
        if buffer.spec_text != text or buffer.spec_attempts != attempt:
            return

        buffer.spec_translation = translation
        buffer.spec_done_at = self.clock.now()
        if buffer.spec_started_at is None:
            latency_ms = 0
        else:
            latency_ms = int((self.clock.now() - buffer.spec_started_at) * 1000)
        logger.debug(
            "[Metric] spec_done id=%s spec_latency_ms=%s translation_len=%s",
            str(merge_id)[:8],
            latency_ms,
            len(translation.text),
        )
        await self._try_commit_after_spec(buffer, reason="spec_done", allow_fallback=False)

    async def _try_commit_after_spec(
        self, buffer: _MergeBuffer, *, reason: str, allow_fallback: bool
    ) -> None:
        if self._merge_buffer is None or self._merge_buffer is not buffer:
            return
        if buffer.resume_pending or buffer.resume_confirmed:
            hold_ms = 0
            if buffer.spec_done_at is not None:
                hold_ms = int((self.clock.now() - buffer.spec_done_at) * 1000)
            logger.debug(
                "[Metric] commit_blocked id=%s reason=%s hold_ms=%s",
                str(buffer.merge_id)[:8],
                reason,
                hold_ms,
            )
            return
        if buffer.awaiting_vad_end:
            hold_ms = 0
            if buffer.finalize_wait_started_at is not None:
                hold_ms = int((self.clock.now() - buffer.finalize_wait_started_at) * 1000)
            logger.debug(
                "[Metric] commit_blocked id=%s reason=await_vad_end hold_ms=%s",
                str(buffer.merge_id)[:8],
                hold_ms,
            )
            return
        if buffer.finalize_wait_task is not None:
            hold_ms = 0
            if buffer.finalize_wait_started_at is not None:
                hold_ms = int((self.clock.now() - buffer.finalize_wait_started_at) * 1000)
            logger.debug(
                "[Metric] commit_deferred id=%s reason=post_end_grace hold_ms=%s",
                str(buffer.merge_id)[:8],
                hold_ms,
            )
            return

        final_text = self._merge_text(buffer.parts)
        if not final_text:
            return

        if buffer.spec_translation is None:
            if not allow_fallback:
                return
            await self._commit_merge(buffer, reason=reason)
            return

        if self._soft_reuse_mode(buffer.spec_text, final_text) is None:
            return

        await self._commit_merge(buffer, reason=reason)

    def _remember_source(
        self,
        utterance_id: UUID,
        source: str | None,
        *,
        channel: ChannelId = "self",
    ) -> None:
        self._runtime_for_utterance(utterance_id, default_channel=channel).remember_source(
            utterance_id, source
        )

    def _get_source(self, utterance_id: UUID, *, channel: ChannelId = "self") -> str | None:
        runtime = self._runtime_for_utterance(utterance_id, default_channel=channel)
        source = runtime.get_source(utterance_id)
        if source is not None:
            return source
        other_runtime = self.peer_runtime if runtime is self.self_runtime else self.self_runtime
        return other_runtime.get_source(utterance_id)

    def _format_system_prompt(self) -> str:
        formatted_prompt = self.system_prompt
        formatted_prompt = formatted_prompt.replace(
            "${sourceName}", get_llm_language_name(self.source_language)
        )
        formatted_prompt = formatted_prompt.replace(
            "${targetName}", get_llm_language_name(self.target_language)
        )
        return formatted_prompt

    def _other_runtime(self, runtime: ChannelRuntime) -> ChannelRuntime:
        return self.peer_runtime if runtime is self.self_runtime else self.self_runtime

    def _should_publish_to_chatbox(self, runtime: ChannelRuntime) -> bool:
        return runtime.channel == self.active_chatbox_channel

    def _translation_enabled_for_runtime(self, runtime: ChannelRuntime) -> bool:
        if runtime.channel == "peer":
            return self.translation_enabled and self.peer_translation_enabled
        return self.translation_enabled

    def advance_peer_session_epoch(self) -> int:
        self.peer_runtime.peer_epoch += 1
        return self.peer_runtime.peer_epoch

    def reset_peer_session_for_test(self) -> int:
        return self.advance_peer_session_epoch()

    def _expected_peer_epoch(
        self,
        runtime: ChannelRuntime,
        *,
        explicit_peer_epoch: int | None = None,
    ) -> int | None:
        if explicit_peer_epoch is not None:
            return explicit_peer_epoch
        if runtime.channel == "peer":
            return runtime.peer_epoch
        return self.peer_runtime.peer_epoch

    def _prepare_llm_request(
        self,
        text: str,
        *,
        runtime: ChannelRuntime | None = None,
        expected_peer_epoch: int | None = None,
    ) -> tuple[str, str, float]:
        formatted_prompt, context_str, now, _ = self._prepare_llm_request_with_mode(
            text,
            runtime=runtime,
            expected_peer_epoch=expected_peer_epoch,
        )
        return formatted_prompt, context_str, now

    def _prepare_llm_request_with_mode(
        self,
        text: str,
        *,
        runtime: ChannelRuntime | None = None,
        expected_peer_epoch: int | None = None,
    ) -> tuple[str, str, float, ContextMode]:
        _ = text
        runtime = runtime or self.self_runtime
        requested_mode: ContextMode = "integrated" if self.integrated_context_enabled else "local"
        now = self.clock.now()
        context_str, applied_mode = self.context_resolver.resolve_for_request(
            runtime=runtime,
            other_runtime=self._other_runtime(runtime),
            requested_mode=requested_mode,
            peer_translation_enabled=self.peer_translation_enabled,
            source_language=self.source_language,
            target_language=self.target_language,
            expected_peer_epoch=self._expected_peer_epoch(
                runtime,
                explicit_peer_epoch=expected_peer_epoch,
            ),
        )
        logger.info("[Hub] Context mode: %s", applied_mode)
        formatted_prompt = self._format_system_prompt()
        return formatted_prompt, context_str, now, applied_mode

    def _normalize_translation(
        self,
        translation: Translation,
        *,
        runtime: ChannelRuntime,
        text: str,
        speaker_label: str | None,
        peer_epoch: int | None,
    ) -> Translation:
        return Translation(
            utterance_id=translation.utterance_id,
            translated_text=translation.text,
            source_text=text,
            source_language=self.source_language,
            target_language=self.target_language,
            channel=runtime.channel,
            speaker_label=speaker_label,
            peer_epoch=peer_epoch,
            created_at=translation.created_at,
        )

    async def _translate_text(
        self,
        utterance_id: UUID,
        text: str,
        *,
        runtime: ChannelRuntime | None = None,
        speaker_label: str | None = None,
        peer_epoch: int | None = None,
    ) -> Translation:
        if self.llm is None:
            raise RuntimeError("LLM is not configured")

        runtime = runtime or self.self_runtime
        formatted_prompt, context_str, _ = self._prepare_llm_request(
            text,
            runtime=runtime,
            expected_peer_epoch=peer_epoch,
        )
        translation = await self.llm.translate(
            utterance_id=utterance_id,
            text=text,
            system_prompt=formatted_prompt,
            source_language=self.source_language,
            target_language=self.target_language,
            context=context_str,
        )
        return self._normalize_translation(
            translation,
            runtime=runtime,
            text=text,
            speaker_label=speaker_label,
            peer_epoch=peer_epoch,
        )

    async def _ensure_translation(self, transcript: Transcript) -> None:
        if self.llm is None:
            return
        runtime = self._runtime_for_channel(transcript.channel)
        if not self._translation_enabled_for_runtime(runtime):
            return
        utterance_id = transcript.utterance_id
        if utterance_id in runtime.translation_tasks:
            return
        task = asyncio.create_task(
            self._translate_and_enqueue(
                utterance_id,
                transcript.text,
                runtime=runtime,
                speaker_label=transcript.speaker_label,
                peer_epoch=transcript.peer_epoch,
            )
        )
        runtime.translation_tasks[utterance_id] = task
        task.add_done_callback(lambda _t: runtime.translation_tasks.pop(utterance_id, None))

    async def _translate_and_enqueue(
        self,
        utterance_id: UUID,
        text: str,
        *,
        runtime: ChannelRuntime | None = None,
        speaker_label: str | None = None,
        peer_epoch: int | None = None,
    ) -> None:
        if self.llm is None:
            return
        runtime = runtime or self.self_runtime
        applied_mode: ContextMode | None = None
        try:
            formatted_prompt, context_str, now, applied_mode = self._prepare_llm_request_with_mode(
                text,
                runtime=runtime,
                expected_peer_epoch=peer_epoch,
            )

            # Add current text to context history at REQUEST time
            self._remember_context_entry(
                text,
                now,
                runtime=runtime,
                speaker_label=speaker_label,
                peer_epoch=peer_epoch,
            )

            if runtime.channel == "peer" and self.overlay_sink is not None:
                translation = await self._stream_peer_translation_to_overlay(
                    utterance_id=utterance_id,
                    text=text,
                    system_prompt=formatted_prompt,
                    context=context_str,
                    runtime=runtime,
                    speaker_label=speaker_label,
                    peer_epoch=peer_epoch,
                    applied_mode=applied_mode,
                )
            else:
                raw_translation = await self.llm.translate(
                    utterance_id=utterance_id,
                    text=text,
                    system_prompt=formatted_prompt,
                    source_language=self.source_language,
                    target_language=self.target_language,
                    context=context_str,
                )
                translation = self._normalize_translation(
                    raw_translation,
                    runtime=runtime,
                    text=text,
                    speaker_label=speaker_label,
                    peer_epoch=peer_epoch,
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(f"[Hub] Translation failed: {exc}")
            await self.ui_events.put(
                UIEvent(
                    type=UIEventType.ERROR,
                    utterance_id=utterance_id,
                    payload=str(exc),
                    source=self._get_source(utterance_id, channel=runtime.channel),
                )
            )
            if self.fallback_transcript_only and self._should_publish_to_chatbox(runtime):
                await self._enqueue_osc(
                    utterance_id,
                    transcript_text=text,
                    translation_text=None,
                )
            return

        bundle = self.get_or_create_bundle(utterance_id, channel=runtime.channel)
        bundle.with_translation(translation)
        await self.ui_events.put(
            UIEvent(
                type=UIEventType.TRANSLATION_DONE,
                utterance_id=utterance_id,
                payload=translation,
                source=self._get_source(utterance_id, channel=runtime.channel),
            )
        )
        if runtime.channel == "self":
            await self._emit_translation_to_overlay(
                translation=translation,
                applied_context_mode=applied_mode,
            )
        if self._should_publish_to_chatbox(runtime):
            await self._enqueue_osc(
                utterance_id,
                transcript_text=text,
                translation_text=translation.text,
            )

    async def _stream_peer_translation_to_overlay(
        self,
        *,
        utterance_id: UUID,
        text: str,
        system_prompt: str,
        context: str,
        runtime: ChannelRuntime,
        speaker_label: str | None,
        peer_epoch: int | None,
        applied_mode: ContextMode,
    ) -> Translation:
        if self.llm is None:
            raise RuntimeError("LLM is not configured")

        latest_snapshot = ""
        coalescer = OverlayStreamCoalescer(interval_ms=self.overlay_stream_coalesce_ms)
        try:
            async for snapshot in self.llm.stream_translate(
                utterance_id=utterance_id,
                text=text,
                system_prompt=system_prompt,
                source_language=self.source_language,
                target_language=self.target_language,
                context=context,
            ):
                latest_snapshot = snapshot
                await coalescer.push(
                    self.overlay_event_adapter.translation_stream_update(
                        utterance_id=utterance_id,
                        channel=runtime.channel,
                        text=snapshot,
                        source_language=self.source_language,
                        target_language=self.target_language,
                        applied_context_mode=applied_mode,
                        speaker_label=speaker_label,
                        peer_epoch=peer_epoch,
                    ),
                    self._emit_overlay_event,
                )

            await coalescer.flush(self._emit_overlay_event)
            translation = self._normalize_translation(
                Translation(
                    utterance_id=utterance_id,
                    translated_text=latest_snapshot,
                    source_text=text,
                    source_language=self.source_language,
                    target_language=self.target_language,
                    channel=runtime.channel,
                    speaker_label=speaker_label,
                    peer_epoch=peer_epoch,
                    created_at=self.clock.now(),
                ),
                runtime=runtime,
                text=text,
                speaker_label=speaker_label,
                peer_epoch=peer_epoch,
            )
            await self._emit_overlay_event(
                self.overlay_event_adapter.translation_final(
                    utterance_id=utterance_id,
                    channel=runtime.channel,
                    text=translation.text,
                    source_language=self.source_language,
                    target_language=self.target_language,
                    applied_context_mode=applied_mode,
                    speaker_label=speaker_label,
                    peer_epoch=peer_epoch,
                )
            )
            await self._emit_overlay_event(
                self.overlay_event_adapter.utterance_closed(
                    utterance_id=utterance_id,
                    channel=runtime.channel,
                    is_final=True,
                )
            )
            return translation
        except asyncio.CancelledError:
            await coalescer.cancel()
            raise
        except Exception:
            await coalescer.flush(self._emit_overlay_event)
            if latest_snapshot:
                await self._emit_overlay_event(
                    self.overlay_event_adapter.utterance_closed(
                        utterance_id=utterance_id,
                        channel=runtime.channel,
                        is_final=False,
                    )
                )
            raise

    async def handle_peer_transcript_final_for_test(
        self,
        *,
        text: str,
        speaker_label: str | None = None,
        peer_epoch: int | None = None,
        source: str = "Peer",
    ) -> UUID:
        utterance_id = uuid4()
        transcript = Transcript(
            utterance_id=utterance_id,
            text=text,
            is_final=True,
            created_at=self.clock.now(),
            channel="peer",
            speaker_label=speaker_label,
            peer_epoch=self._expected_peer_epoch(
                self.peer_runtime,
                explicit_peer_epoch=peer_epoch,
            ),
        )
        await self._handle_transcript(transcript, is_final=True, source=source)
        if self.llm is not None and self.peer_translation_enabled:
            await self._ensure_translation(transcript)
        return utterance_id

    async def translate_peer_text_for_test(
        self,
        text: str,
        *,
        speaker_label: str | None = None,
        peer_epoch: int | None = None,
    ) -> UUID:
        utterance_id = await self.handle_peer_transcript_final_for_test(
            text=text,
            speaker_label=speaker_label,
            peer_epoch=peer_epoch,
        )
        if self.peer_runtime.translation_tasks:
            await asyncio.gather(
                *self.peer_runtime.translation_tasks.values(), return_exceptions=True
            )
        return utterance_id

    async def _enqueue_osc(
        self,
        utterance_id: UUID,
        *,
        transcript_text: str,
        translation_text: str | None,
    ) -> None:
        if translation_text is None:
            merged = transcript_text
        else:
            merged = f"{transcript_text} ({translation_text})"

        msg = OSCMessage(utterance_id=utterance_id, text=merged, created_at=self.clock.now())

        # Calculate and log E2E latency (includes hangover time)
        start_time = self._utterance_start_times.pop(utterance_id, None)
        if start_time is not None:
            processing_latency = self.clock.now() - start_time
            total_e2e = processing_latency + self.hangover_s
            logger.info(
                f"[Hub] OSC enqueue: '{merged[:50]}...' id={str(utterance_id)[:8]} (Latency: {total_e2e:.2f}s)"
            )
        else:
            logger.info(f"[Hub] OSC enqueue: '{merged[:50]}...' id={str(utterance_id)[:8]}")

        self.osc.enqueue(msg)

        # Stop typing indicator after message is sent
        self.osc.send_typing(False)

        await self.ui_events.put(
            UIEvent(
                type=UIEventType.OSC_SENT,
                utterance_id=utterance_id,
                payload=msg,
                source=self._get_source(utterance_id),
            )
        )

    async def _run_osc_flush_loop(self) -> None:
        try:
            while True:
                self.osc.process_due()
                await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            raise
