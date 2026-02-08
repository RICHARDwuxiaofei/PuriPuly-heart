from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID, uuid4

logger = logging.getLogger(__name__)

from puripuly_heart.config.prompts import load_qwen_few_shot
from puripuly_heart.core.clock import Clock, SystemClock
from puripuly_heart.core.language import get_llm_language_name
from puripuly_heart.core.llm.provider import LLMProvider
from puripuly_heart.core.osc.smart_queue import SmartOscQueue
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
    OSCMessage,
    Transcript,
    Translation,
    UtteranceBundle,
)


@dataclass(frozen=True, slots=True)
class ContextEntry:
    """Represents a recent utterance for context memory."""

    text: str  # Original text
    source_language: str
    target_language: str
    timestamp: float  # When the translation was requested


@dataclass(slots=True)
class _MergeBuffer:
    merge_id: UUID
    parts: list[str] = field(default_factory=list)
    utterance_ids: list[UUID] = field(default_factory=list)
    start_time: float | None = None
    last_end_time: float | None = None
    last_final_at: float = 0.0
    spec_task: asyncio.Task[None] | None = None
    spec_text: str | None = None
    spec_translation: Translation | None = None
    spec_attempts: int = 0
    spec_started_at: float | None = None
    spec_done_at: float | None = None
    resume_pending: bool = False
    resume_confirmed: bool = False
    resume_utterance_id: UUID | None = None
    resume_chunk_count: int = 0
    resume_started_at: float | None = None
    awaiting_vad_end: bool = False
    awaiting_vad_utterance_id: UUID | None = None
    awaiting_vad_timeout_task: asyncio.Task[None] | None = None
    finalize_wait_task: asyncio.Task[None] | None = None
    finalize_wait_started_at: float | None = None
    resume_end_timeout_task: asyncio.Task[None] | None = None
    resume_end_utterance_id: UUID | None = None


class STTProvider(Protocol):
    async def handle_vad_event(self, event: VadEvent) -> None: ...
    async def close(self) -> None: ...
    def events(self): ...


_PROMO_INTERVAL_SEC: float = 300.0  # 5 minutes
_RELAXED_OVERLAP_MIN_CHARS: int = 3
_BOUNDARY_PUNCT = {".", ",", ";", ":", "!", "?"}


@dataclass(slots=True)
class ClientHub:
    stt: STTProvider | None
    llm: LLMProvider | None
    osc: SmartOscQueue
    clock: Clock = SystemClock()

    source_language: str = "ko"
    target_language: str = "en"
    system_prompt: str = ""
    fallback_transcript_only: bool = False
    translation_enabled: bool = True
    hangover_s: float = 1.1  # VAD hangover in seconds (for E2E latency calculation)

    # Context memory settings
    context_time_window_s: float = 30.0  # Only include entries within this time window
    context_max_entries: int = 3  # Maximum number of context entries to include
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
    _qwen_few_shot: list[dict[str, str]] = field(default_factory=load_qwen_few_shot)
    _speech_ended_ids: set[UUID] = field(default_factory=set)  # Track SpeechEnd arrivals
    _stt_task: asyncio.Task[None] | None = None
    _osc_flush_task: asyncio.Task[None] | None = None
    _running: bool = False
    _last_promo_time: float | None = None
    _promo_eligible: bool = False
    _merge_buffer: _MergeBuffer | None = None

    async def start(self, *, auto_flush_osc: bool = False) -> None:
        if self._running:
            return
        self._running = True
        if self.stt is not None:
            self._stt_task = asyncio.create_task(self._run_stt_event_loop())
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

        if self._stt_task:
            self._stt_task.cancel()
            await asyncio.gather(self._stt_task, return_exceptions=True)
            self._stt_task = None

        for task in list(self._translation_tasks.values()):
            task.cancel()
        await asyncio.gather(*self._translation_tasks.values(), return_exceptions=True)
        self._translation_tasks.clear()

        if self._merge_buffer is not None:
            merge_tasks = [
                self._merge_buffer.spec_task,
                self._merge_buffer.finalize_wait_task,
                self._merge_buffer.awaiting_vad_timeout_task,
                self._merge_buffer.resume_end_timeout_task,
            ]
            for task in merge_tasks:
                if task is not None and not task.done():
                    task.cancel()
            await asyncio.gather(*(t for t in merge_tasks if t is not None), return_exceptions=True)
            self._merge_buffer = None

        if self.stt is not None:
            await self.stt.close()

        if self.llm is not None:
            await self.llm.close()

    def mark_promo_eligible(self) -> None:
        """Mark that user clicked STT button. Next STREAMING state will send promo."""
        self._promo_eligible = True

    def clear_context(self) -> None:
        """Clear the translation context history."""
        self._translation_history.clear()
        logger.info("[Hub] Context history cleared")

    def _get_valid_context(self) -> list[ContextEntry]:
        """Get context entries within time window and max entries limit."""
        now = self.clock.now()
        # Filter by time window and limit to max entries
        valid = [
            entry
            for entry in self._translation_history[-self.context_max_entries :]
            if (now - entry.timestamp) < self.context_time_window_s
            and entry.source_language == self.source_language
            and entry.target_language == self.target_language
            and len(entry.text) >= 2
        ]
        return valid

    def _format_context_for_llm(self, context: list[ContextEntry]) -> str:
        """Format context entries as a string for LLM prompt."""
        if not context:
            return ""
        lines = []
        for entry in context:
            lines.append(f'- "{entry.text}"')
        return "\n".join(lines)

    def _get_tm_list(self) -> list[dict[str, str]]:
        """Return the fixed few-shot list for Qwen."""
        return self._qwen_few_shot

    def set_qwen_few_shots(self, shots: list[dict[str, str]]) -> None:
        """Update Qwen few-shot examples dynamically."""
        self._qwen_few_shot = shots

    def _remember_context_entry(self, text: str, timestamp: float) -> None:
        text_clean = text.strip()
        if len(text_clean) < 2:
            return
        self._translation_history.append(
            ContextEntry(
                text=text_clean,
                source_language=self.source_language,
                target_language=self.target_language,
                timestamp=timestamp,
            )
        )
        if len(self._translation_history) > self.context_max_entries:
            self._translation_history.pop(0)

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

    def get_or_create_bundle(self, utterance_id: UUID) -> UtteranceBundle:
        bundle = self._utterances.get(utterance_id)
        if bundle is None:
            bundle = UtteranceBundle(utterance_id=utterance_id)
            self._utterances[utterance_id] = bundle
        return bundle

    async def _run_stt_event_loop(self) -> None:
        try:
            async for ev in self.stt.events():
                await self._handle_stt_event(ev)
        except asyncio.CancelledError:
            raise

    async def _handle_stt_event(self, event: object) -> None:
        if isinstance(event, STTSessionStateEvent):
            await self.ui_events.put(
                UIEvent(type=UIEventType.SESSION_STATE_CHANGED, payload=event.state)
            )
            if event.state == STTSessionState.STREAMING:
                self._send_stt_connected_notification()
            return

        if isinstance(event, STTErrorEvent):
            await self.ui_events.put(
                UIEvent(type=UIEventType.ERROR, payload=event.message, source="Mic")
            )
            return

        if isinstance(event, STTPartialEvent):
            self._send_stt_connected_notification()
            if self.low_latency_mode:
                return
            logger.debug(
                f"[Hub] STT Partial: '{event.transcript.text[:50]}...' id={str(event.transcript.utterance_id)[:8]}"
            )
            await self._handle_transcript(event.transcript, is_final=False, source="Mic")
            return

        if isinstance(event, STTFinalEvent):
            self._send_stt_connected_notification()
            if self.low_latency_mode:
                await self._handle_low_latency_final(event.transcript)
                return
            await self._handle_transcript(event.transcript, is_final=True, source="Mic")
            if self.llm is None or not self.translation_enabled:
                logger.info(
                    f"[Hub] Skipping translation (llm={self.llm is not None}, enabled={self.translation_enabled})"
                )
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
        bundle = self.get_or_create_bundle(transcript.utterance_id)
        bundle.with_transcript(transcript)
        self._remember_source(transcript.utterance_id, source)
        await self.ui_events.put(
            UIEvent(
                type=UIEventType.TRANSCRIPT_FINAL if is_final else UIEventType.TRANSCRIPT_PARTIAL,
                utterance_id=transcript.utterance_id,
                payload=transcript,
                source=source,
            )
        )

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
                    logger.info(
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
        logger.info(
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
        logger.info(
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
        logger.info(
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
        logger.info(
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
        logger.info(
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
        logger.info(
            "[Metric] resume_confirmed id=%s confirm_ms=%s chunk_count=%s",
            str(buffer.merge_id)[:8],
            confirm_ms,
            buffer.resume_chunk_count,
        )
        if buffer.spec_task is not None and not buffer.spec_task.done():
            buffer.spec_task.cancel()
            logger.info(
                "[Metric] spec_cancel id=%s reason=resume_confirmed",
                str(buffer.merge_id)[:8],
            )
        elif buffer.spec_translation is not None:
            logger.info(
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
        logger.info(
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

        end_time = self._utterance_start_times.get(transcript.utterance_id)
        speech_already_ended = transcript.utterance_id in self._speech_ended_ids

        if end_time is None and not speech_already_ended:
            # SpeechEnd has not arrived yet - wait for it
            buffer.awaiting_vad_end = True
            buffer.awaiting_vad_utterance_id = transcript.utterance_id
            self._cancel_finalize_wait(buffer)
            self._start_awaiting_vad_timeout(buffer)
            logger.info(
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
            logger.info(
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
            logger.info(
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
            logger.info(
                "[Metric] commit_blocked id=%s reason=await_vad_end hold_ms=%s",
                str(buffer.merge_id)[:8],
                hold_ms,
            )
            return
        if buffer.finalize_wait_task is not None:
            hold_ms = 0
            if buffer.finalize_wait_started_at is not None:
                hold_ms = int((self.clock.now() - buffer.finalize_wait_started_at) * 1000)
            logger.info(
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

        reuse_spec = buffer.spec_translation is not None and buffer.spec_text == final_text
        commit_delay_ms = 0
        if buffer.start_time is not None:
            commit_delay_ms = int((self.clock.now() - buffer.start_time) * 1000)
        logger.info(
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
                logger.info(
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
                await self._enqueue_osc(
                    buffer.merge_id,
                    transcript_text=final_text,
                    translation_text=translation.text,
                )
                return

        if buffer.spec_translation is not None and buffer.spec_text != final_text:
            logger.info(
                "[Metric] spec_cancel id=%s reason=final_mismatch", str(buffer.merge_id)[:8]
            )

        await self._translate_and_enqueue(buffer.merge_id, final_text)

    async def _maybe_restart_spec(self, buffer: _MergeBuffer) -> None:
        if self.llm is None or not self.translation_enabled:
            return

        if buffer.spec_task is not None:
            if not buffer.spec_task.done():
                buffer.spec_task.cancel()
                logger.info(
                    "[Metric] spec_cancel id=%s reason=spec_retry", str(buffer.merge_id)[:8]
                )
            elif buffer.spec_translation is not None:
                logger.info(
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
        logger.info(
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
        logger.info(
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
            logger.info(
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
            logger.info(
                "[Metric] commit_blocked id=%s reason=await_vad_end hold_ms=%s",
                str(buffer.merge_id)[:8],
                hold_ms,
            )
            return
        if buffer.finalize_wait_task is not None:
            hold_ms = 0
            if buffer.finalize_wait_started_at is not None:
                hold_ms = int((self.clock.now() - buffer.finalize_wait_started_at) * 1000)
            logger.info(
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

        if buffer.spec_text != final_text:
            return

        await self._commit_merge(buffer, reason=reason)

    def _remember_source(self, utterance_id: UUID, source: str | None) -> None:
        if not source:
            return
        self._utterance_sources[utterance_id] = source

    def _get_source(self, utterance_id: UUID) -> str | None:
        return self._utterance_sources.get(utterance_id)

    def _format_system_prompt(self) -> str:
        formatted_prompt = self.system_prompt
        formatted_prompt = formatted_prompt.replace(
            "${sourceName}", get_llm_language_name(self.source_language)
        )
        formatted_prompt = formatted_prompt.replace(
            "${targetName}", get_llm_language_name(self.target_language)
        )
        return formatted_prompt

    def _prepare_llm_request(self, text: str) -> tuple[str, str, list[dict[str, str]], float]:
        _ = text
        valid_context = self._get_valid_context()
        now = self.clock.now()

        logger.info(
            f"[Hub] Context: {len(valid_context)} entries within {self.context_time_window_s}s window"
        )
        for i, entry in enumerate(valid_context):
            age = now - entry.timestamp
            logger.info(f'[Hub] Context[{i}]: "{entry.text}" ({age:.1f}s ago)')

        context_str = self._format_context_for_llm(valid_context)
        tm_list = self._get_tm_list()
        formatted_prompt = self._format_system_prompt()
        return formatted_prompt, context_str, tm_list, now

    async def _translate_text(self, utterance_id: UUID, text: str) -> Translation:
        if self.llm is None:
            raise RuntimeError("LLM is not configured")

        formatted_prompt, context_str, tm_list, _ = self._prepare_llm_request(text)
        return await self.llm.translate(
            utterance_id=utterance_id,
            text=text,
            system_prompt=formatted_prompt,
            source_language=self.source_language,
            target_language=self.target_language,
            context=context_str,
            context_pairs=tm_list,
        )

    async def _ensure_translation(self, transcript: Transcript) -> None:
        if self.llm is None:
            return
        utterance_id = transcript.utterance_id
        if utterance_id in self._translation_tasks:
            return
        task = asyncio.create_task(self._translate_and_enqueue(utterance_id, transcript.text))
        self._translation_tasks[utterance_id] = task
        task.add_done_callback(lambda _t: self._translation_tasks.pop(utterance_id, None))

    async def _translate_and_enqueue(self, utterance_id: UUID, text: str) -> None:
        if self.llm is None:
            return
        try:
            formatted_prompt, context_str, tm_list, now = self._prepare_llm_request(text)

            # Add current text to context history at REQUEST time
            self._remember_context_entry(text, now)

            translation = await self.llm.translate(
                utterance_id=utterance_id,
                text=text,
                system_prompt=formatted_prompt,
                source_language=self.source_language,
                target_language=self.target_language,
                context=context_str,
                context_pairs=tm_list,
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
                    source=self._get_source(utterance_id),
                )
            )
            if self.fallback_transcript_only:
                await self._enqueue_osc(utterance_id, transcript_text=text, translation_text=None)
            return

        bundle = self.get_or_create_bundle(utterance_id)
        bundle.with_translation(translation)
        bundle.with_translation(translation)
        await self.ui_events.put(
            UIEvent(
                type=UIEventType.TRANSLATION_DONE,
                utterance_id=utterance_id,
                payload=translation,
                source=self._get_source(utterance_id),
            )
        )
        await self._enqueue_osc(
            utterance_id, transcript_text=text, translation_text=translation.text
        )

    async def _enqueue_osc(
        self, utterance_id: UUID, *, transcript_text: str, translation_text: str | None
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
