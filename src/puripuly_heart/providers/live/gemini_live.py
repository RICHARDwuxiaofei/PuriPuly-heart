from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import numpy as np

from puripuly_heart.core.clock import Clock, SystemClock
from puripuly_heart.core.language import get_llm_language_name
from puripuly_heart.core.vad.gating import SpeechChunk, SpeechEnd, SpeechStart, VadEvent
from puripuly_heart.domain.events import (
    STTErrorEvent,
    STTIntegratedFinalEvent,
    STTPartialEvent,
    STTSessionState,
    STTSessionStateEvent,
)
from puripuly_heart.domain.models import Transcript

logger = logging.getLogger(__name__)

_CONNECT_RETRY_DELAYS_S = (0.25, 0.5, 1.0)


@dataclass(slots=True)
class GeminiLiveIntegratedProvider:
    api_key: str
    sample_rate_hz: int
    source_language: str
    target_language: str
    system_prompt: str
    model: str = "gemini-3.1-flash-live-preview"
    channel: str = "self"
    clock: Clock = field(default_factory=SystemClock)
    client: Any | None = None

    _events: asyncio.Queue[object] = field(default_factory=asyncio.Queue, init=False, repr=False)
    _session_context: Any | None = field(default=None, init=False, repr=False)
    _session: Any | None = field(default=None, init=False, repr=False)
    _receiver_task: asyncio.Task[None] | None = field(default=None, init=False, repr=False)
    _current_utterance_id: UUID | None = field(default=None, init=False, repr=False)
    _source_transcript_text: str = field(default="", init=False, repr=False)
    _translation_text: str = field(default="", init=False, repr=False)
    _last_partial_text: str = field(default="", init=False, repr=False)
    _resumption_handle: str | None = field(default=None, init=False, repr=False)
    _resumption_resumable: bool = field(default=False, init=False, repr=False)
    _closed: bool = field(default=False, init=False, repr=False)

    async def handle_vad_event(self, event: VadEvent) -> None:
        if isinstance(event, SpeechStart):
            await self._on_speech_start(event)
            return
        if isinstance(event, SpeechChunk):
            await self._on_speech_chunk(event)
            return
        if isinstance(event, SpeechEnd):
            await self._on_speech_end(event)
            return
        raise TypeError(f"Unknown VadEvent: {type(event)}")

    async def events(self) -> AsyncIterator[object]:
        while True:
            item = await self._events.get()
            yield item

    async def warmup(self) -> None:
        if await self._ensure_session():
            logger.info("[GeminiLive] Session pre-warmed")

    async def close(self) -> None:
        self._closed = True
        self._reset_turn_buffers()
        await self._shutdown_session(emit_state=True)

    def _get_client(self) -> Any:
        if self.client is not None:
            return self.client
        if self.client is None:
            from google import genai  # type: ignore

            self.client = genai.Client(api_key=self.api_key)
        return self.client

    def _build_system_instruction(self) -> str:
        prompt = (
            self.system_prompt.replace("${sourceName}", get_llm_language_name(self.source_language))
            .replace("${targetName}", get_llm_language_name(self.target_language))
            .strip()
        )
        constraint = (
            "Return only the translated utterance. Do not add explanations, notes, or speaker"
            " labels."
        )
        return f"{prompt}\n\n{constraint}" if prompt else constraint

    def _build_connect_config(self) -> Any:
        from google.genai import types  # type: ignore

        return types.LiveConnectConfig(
            response_modalities=[types.Modality.AUDIO],
            system_instruction=self._build_system_instruction(),
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
            thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel.MINIMAL),
            realtime_input_config=types.RealtimeInputConfig(
                automatic_activity_detection=types.AutomaticActivityDetection(disabled=True),
                activity_handling=types.ActivityHandling.START_OF_ACTIVITY_INTERRUPTS,
                turn_coverage=types.TurnCoverage.TURN_INCLUDES_ONLY_ACTIVITY,
            ),
            context_window_compression=types.ContextWindowCompressionConfig(
                sliding_window=types.SlidingWindow()
            ),
            session_resumption=types.SessionResumptionConfig(handle=self._resumption_handle),
        )

    async def _ensure_session(self) -> bool:
        if self._session is not None:
            return True

        await self._events.put(
            STTSessionStateEvent(state=STTSessionState.CONNECTING, channel=self.channel)
        )

        last_error: Exception | None = None
        for delay_s in _CONNECT_RETRY_DELAYS_S:
            try:
                await self._connect_session()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover - exercised via tests
                last_error = exc
                await self._shutdown_session(emit_state=False)
                await asyncio.sleep(delay_s)
                continue

            await self._events.put(
                STTSessionStateEvent(state=STTSessionState.STREAMING, channel=self.channel)
            )
            return True

        message = (
            f"Gemini Live connection failed: {last_error}"
            if last_error is not None
            else "Gemini Live connection failed"
        )
        await self._events.put(
            STTErrorEvent(
                message=message,
                utterance_id=self._current_utterance_id,
                channel=self.channel,
            )
        )
        await self._events.put(
            STTSessionStateEvent(state=STTSessionState.DISCONNECTED, channel=self.channel)
        )
        return False

    async def _connect_session(self) -> None:
        client = self._get_client()
        self._session_context = client.aio.live.connect(
            model=self.model,
            config=self._build_connect_config(),
        )
        self._session = await self._session_context.__aenter__()
        self._receiver_task = asyncio.create_task(self._run_receive_loop())

    async def _shutdown_session(self, *, emit_state: bool) -> None:
        receiver_task = self._receiver_task
        session_context = self._session_context
        self._receiver_task = None
        self._session_context = None
        self._session = None

        if receiver_task is not None and receiver_task is not asyncio.current_task():
            receiver_task.cancel()
            await asyncio.gather(receiver_task, return_exceptions=True)

        if session_context is not None:
            with contextlib.suppress(Exception):
                await session_context.__aexit__(None, None, None)

        if emit_state:
            await self._events.put(
                STTSessionStateEvent(state=STTSessionState.DISCONNECTED, channel=self.channel)
            )

    async def _run_receive_loop(self) -> None:
        try:
            while self._session is not None and not self._closed:
                assert self._session is not None
                async for message in self._session.receive():
                    await self._handle_server_message(message)
                await asyncio.sleep(0)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._handle_receive_failure(exc)

    async def _handle_receive_failure(self, exc: Exception) -> None:
        utterance_id = self._current_utterance_id
        logger.warning("[GeminiLive] Receive loop failed: %s", exc)
        await self._shutdown_session(emit_state=True)
        if utterance_id is not None:
            await self._events.put(
                STTErrorEvent(
                    message=f"Gemini Live session failed: {exc}",
                    utterance_id=utterance_id,
                    channel=self.channel,
                )
            )
        self._reset_turn_buffers()

    async def _on_speech_start(self, event: SpeechStart) -> None:
        self._current_utterance_id = event.utterance_id
        self._reset_turn_buffers(preserve_utterance=True)
        if not await self._ensure_session():
            return
        await self._session.send_realtime_input(activity_start=self._activity_start())
        await self._send_audio(event.pre_roll)
        await self._send_audio(event.chunk)

    async def _on_speech_chunk(self, event: SpeechChunk) -> None:
        self._current_utterance_id = event.utterance_id
        if not await self._ensure_session():
            return
        await self._send_audio(event.chunk)

    async def _on_speech_end(self, event: SpeechEnd) -> None:
        self._current_utterance_id = event.utterance_id
        if not await self._ensure_session():
            return
        await self._session.send_realtime_input(activity_end=self._activity_end())

    async def _send_audio(self, samples_f32: np.ndarray) -> None:
        blob = self._audio_blob(samples_f32)
        if blob is None or self._session is None:
            return
        await self._session.send_realtime_input(audio=blob)

    def _audio_blob(self, samples_f32: np.ndarray) -> Any | None:
        samples = np.asarray(samples_f32, dtype=np.float32).reshape(-1)
        if samples.size == 0:
            return None

        from google.genai import types  # type: ignore

        clipped = np.clip(samples, -1.0, 1.0)
        pcm16 = np.asarray(clipped * 32767.0, dtype="<i2")
        return types.Blob(data=pcm16.tobytes(), mime_type=f"audio/pcm;rate={self.sample_rate_hz}")

    def _activity_start(self) -> Any:
        from google.genai import types  # type: ignore

        return types.ActivityStart()

    def _activity_end(self) -> Any:
        from google.genai import types  # type: ignore

        return types.ActivityEnd()

    async def _handle_server_message(self, message: object) -> None:
        session_resumption = getattr(message, "session_resumption_update", None)
        if session_resumption is not None:
            new_handle = getattr(session_resumption, "new_handle", None)
            if isinstance(new_handle, str) and new_handle:
                self._resumption_handle = new_handle
            resumable = getattr(session_resumption, "resumable", None)
            if resumable is not None:
                self._resumption_resumable = bool(resumable)
                if not self._resumption_resumable:
                    self._resumption_handle = None

        server_content = getattr(message, "server_content", None)
        if server_content is None:
            return

        input_text = self._message_text(getattr(server_content, "input_transcription", None))
        if input_text and input_text != self._source_transcript_text:
            self._source_transcript_text = input_text
            if self._current_utterance_id is not None and input_text != self._last_partial_text:
                self._last_partial_text = input_text
                await self._events.put(
                    STTPartialEvent(
                        utterance_id=self._current_utterance_id,
                        transcript=Transcript(
                            utterance_id=self._current_utterance_id,
                            text=input_text,
                            is_final=False,
                            created_at=self.clock.now(),
                            channel=self.channel,
                        ),
                    )
                )

        output_text = self._message_text(getattr(server_content, "output_transcription", None))
        if output_text:
            self._translation_text = output_text

        if getattr(server_content, "turn_complete", False):
            await self._emit_turn_complete()

    async def _emit_turn_complete(self) -> None:
        utterance_id = self._current_utterance_id
        if utterance_id is None:
            return

        transcript_text = self._source_transcript_text.strip()
        translation_text = self._translation_text.strip()
        if transcript_text or translation_text:
            await self._events.put(
                STTIntegratedFinalEvent(
                    utterance_id=utterance_id,
                    transcript_text=transcript_text,
                    translation_text=translation_text,
                    channel=self.channel,
                    created_at=self.clock.now(),
                )
            )
        self._reset_turn_buffers()

    def _message_text(self, transcription: object | None) -> str:
        if transcription is None:
            return ""
        text = getattr(transcription, "text", "")
        return text.strip() if isinstance(text, str) else ""

    def _reset_turn_buffers(self, *, preserve_utterance: bool = False) -> None:
        if not preserve_utterance:
            self._current_utterance_id = None
        self._source_transcript_text = ""
        self._translation_text = ""
        self._last_partial_text = ""
