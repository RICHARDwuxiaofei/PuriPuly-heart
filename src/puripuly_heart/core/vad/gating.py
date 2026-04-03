from __future__ import annotations

import logging
import math
import uuid
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

import numpy as np

from puripuly_heart.core.audio.ring_buffer import RingBufferF32

logger = logging.getLogger(__name__)


class VadEngine(Protocol):
    def speech_probability(self, samples: np.ndarray, *, sample_rate_hz: int) -> float: ...
    def reset(self) -> None: ...


@dataclass(frozen=True, slots=True)
class SpeechStart:
    utterance_id: UUID
    pre_roll: np.ndarray
    chunk: np.ndarray


@dataclass(frozen=True, slots=True)
class SpeechChunk:
    utterance_id: UUID
    chunk: np.ndarray


@dataclass(frozen=True, slots=True)
class SpeechEnd:
    utterance_id: UUID
    trailing_silence_ms: int = 0


VadEvent = SpeechStart | SpeechChunk | SpeechEnd


def default_chunk_samples(sample_rate_hz: int) -> int:
    if sample_rate_hz == 16000:
        return 512
    if sample_rate_hz == 8000:
        return 256
    raise ValueError("Silero VAD streaming supports only 8000 or 16000 Hz")


@dataclass(slots=True)
class VadGating:
    engine: VadEngine
    sample_rate_hz: int
    speech_threshold: float
    hangover_chunks: int
    chunk_samples: int
    start_debounce_chunks: int
    start_commit_chunks: int
    candidate_log_label: str | None
    _ring: RingBufferF32
    _in_speech: bool
    _utterance_id: UUID | None
    _silence_run: int
    _pending_start_id: UUID | None
    _pending_start_pre_roll: np.ndarray | None
    _pending_start_prob: float | None
    _pending_start_chunks: list[np.ndarray]
    _pending_debounce_reached: bool

    def __init__(
        self,
        engine: VadEngine,
        *,
        sample_rate_hz: int,
        ring_buffer_ms: int = 500,
        speech_threshold: float = 0.5,
        hangover_ms: int = 1100,
        chunk_samples: int | None = None,
        start_debounce_chunks: int = 1,
        start_commit_chunks: int = 1,
        candidate_log_label: str | None = None,
    ) -> None:
        if sample_rate_hz <= 0:
            raise ValueError("sample_rate_hz must be > 0")
        if ring_buffer_ms <= 0:
            raise ValueError("ring_buffer_ms must be > 0")
        if hangover_ms < 0:
            raise ValueError("hangover_ms must be >= 0")
        if start_debounce_chunks <= 0:
            raise ValueError("start_debounce_chunks must be > 0")
        if start_commit_chunks <= 0:
            raise ValueError("start_commit_chunks must be > 0")
        if start_commit_chunks < start_debounce_chunks:
            raise ValueError("start_commit_chunks must be >= start_debounce_chunks")

        self.engine = engine
        self.sample_rate_hz = sample_rate_hz
        self.speech_threshold = speech_threshold
        self.chunk_samples = chunk_samples or default_chunk_samples(sample_rate_hz)
        self.start_debounce_chunks = start_debounce_chunks
        self.start_commit_chunks = start_commit_chunks
        self.candidate_log_label = candidate_log_label

        chunk_ms = (self.chunk_samples / self.sample_rate_hz) * 1000.0
        self.hangover_chunks = int(math.ceil(hangover_ms / chunk_ms)) if hangover_ms > 0 else 0

        capacity_samples = int(self.sample_rate_hz * (ring_buffer_ms / 1000.0))
        self._ring = RingBufferF32(capacity_samples=capacity_samples)

        self._in_speech = False
        self._utterance_id = None
        self._silence_run = 0
        self._pending_start_id = None
        self._pending_start_pre_roll = None
        self._pending_start_prob = None
        self._pending_start_chunks = []
        self._pending_debounce_reached = False

    @property
    def in_speech(self) -> bool:
        return self._in_speech

    @property
    def utterance_id(self) -> UUID | None:
        return self._utterance_id

    def reset(self) -> None:
        self.engine.reset()
        self._ring.clear()
        self._in_speech = False
        self._utterance_id = None
        self._silence_run = 0
        self._reset_pending_start()

    def process_chunk(self, chunk: np.ndarray) -> list[VadEvent]:
        chunk = np.asarray(chunk, dtype=np.float32).reshape(-1)
        if chunk.size != self.chunk_samples:
            raise ValueError(f"chunk must have {self.chunk_samples} samples")

        prob = self.engine.speech_probability(chunk, sample_rate_hz=self.sample_rate_hz)

        events: list[VadEvent] = []

        if not self._in_speech:
            if prob >= self.speech_threshold:
                events.extend(self._handle_pending_start(chunk, prob))
            else:
                self._drop_pending_start()
            self._ring.append(chunk)
            return events

        # in speech
        events.append(SpeechChunk(self._utterance_id, chunk=chunk.copy()))  # type: ignore[arg-type]

        if prob >= self.speech_threshold:
            self._silence_run = 0
            return events

        self._silence_run += 1
        if self._silence_run >= self.hangover_chunks:
            trailing_silence_ms = int(
                round(self._silence_run * (self.chunk_samples / self.sample_rate_hz) * 1000.0)
            )
            logger.info(
                "[VAD] SpeechEnd: id=%s, trailing_silence_ms=%s",
                str(self._utterance_id)[:8],
                trailing_silence_ms,
            )
            events.append(
                SpeechEnd(self._utterance_id, trailing_silence_ms=trailing_silence_ms)
            )  # type: ignore[arg-type]
            self._in_speech = False
            self._utterance_id = None
            self._silence_run = 0
            self.engine.reset()

        self._ring.append(chunk)
        return events

    def _handle_pending_start(self, chunk: np.ndarray, prob: float) -> list[VadEvent]:
        if self._pending_start_id is None:
            self._pending_start_id = uuid.uuid4()
            self._pending_start_pre_roll = self._ring.get_last_samples(self._ring.capacity_samples)
            self._pending_start_prob = prob
            self._pending_start_chunks = [chunk.copy()]
            self._pending_debounce_reached = self.start_debounce_chunks <= 1
            self._log_candidate("start", prob=prob)
        else:
            self._pending_start_chunks.append(chunk.copy())

        if (
            not self._pending_debounce_reached
            and len(self._pending_start_chunks) >= self.start_debounce_chunks
        ):
            self._pending_debounce_reached = True

        if len(self._pending_start_chunks) < self.start_commit_chunks:
            return []

        utterance_id = self._pending_start_id
        if utterance_id is None:
            return []

        self._in_speech = True
        self._silence_run = 0
        self._utterance_id = utterance_id

        pre_roll = self._pending_start_pre_roll
        if pre_roll is None:
            pre_roll = np.empty((0,), dtype=np.float32)
        start_prob = self._pending_start_prob if self._pending_start_prob is not None else prob
        buffered_chunks = list(self._pending_start_chunks)
        self._log_candidate("committed", buffered_chunks=len(buffered_chunks))
        logger.info("[VAD] SpeechStart: id=%s, prob=%.2f", str(utterance_id)[:8], start_prob)
        self._reset_pending_start()

        events: list[VadEvent] = [
            SpeechStart(utterance_id, pre_roll=pre_roll, chunk=buffered_chunks[0])
        ]
        events.extend(
            SpeechChunk(utterance_id, chunk=buffered.copy()) for buffered in buffered_chunks[1:]
        )
        return events

    def _drop_pending_start(self) -> None:
        if self._pending_start_id is None:
            return
        self._log_candidate("dropped", buffered_chunks=len(self._pending_start_chunks))
        self._reset_pending_start()

    def _reset_pending_start(self) -> None:
        self._pending_start_id = None
        self._pending_start_pre_roll = None
        self._pending_start_prob = None
        self._pending_start_chunks = []
        self._pending_debounce_reached = False

    def _log_candidate(
        self,
        action: str,
        *,
        prob: float | None = None,
        buffered_chunks: int | None = None,
    ) -> None:
        if not self.candidate_log_label:
            return
        utterance = (
            str(self._pending_start_id)[:8] if self._pending_start_id is not None else "unknown"
        )
        if action == "start":
            logger.info(
                "[VAD][TEST] %s candidate start: id=%s, prob=%.2f",
                self.candidate_log_label,
                utterance,
                0.0 if prob is None else prob,
            )
            return
        if action == "dropped":
            logger.info(
                "[VAD][TEST] %s candidate dropped: id=%s, buffered_chunks=%s",
                self.candidate_log_label,
                utterance,
                buffered_chunks,
            )
            return
        if action == "committed":
            logger.info(
                "[VAD][TEST] %s candidate committed: id=%s, buffered_chunks=%s",
                self.candidate_log_label,
                utterance,
                buffered_chunks,
            )


PEER_VAD_SPEECH_THRESHOLD = 0.60
PEER_VAD_START_DEBOUNCE_CHUNKS = 3
PEER_VAD_START_COMMIT_CHUNKS = 3


def create_peer_vad_gating(
    engine: VadEngine,
    *,
    sample_rate_hz: int,
    ring_buffer_ms: int,
    speech_threshold: float = PEER_VAD_SPEECH_THRESHOLD,
    hangover_ms: int,
) -> VadGating:
    return VadGating(
        engine=engine,
        sample_rate_hz=sample_rate_hz,
        ring_buffer_ms=max(1, ring_buffer_ms),
        speech_threshold=speech_threshold,
        hangover_ms=hangover_ms,
        start_debounce_chunks=PEER_VAD_START_DEBOUNCE_CHUNKS,
        start_commit_chunks=PEER_VAD_START_COMMIT_CHUNKS,
        candidate_log_label="Peer",
    )
