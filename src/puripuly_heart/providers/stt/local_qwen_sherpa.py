from __future__ import annotations

import asyncio
import importlib
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator

from puripuly_heart.core.audio.format import pcm16le_bytes_to_float32, resample_f32_linear
from puripuly_heart.core.local_stt_assets import (
    validate_local_stt_runtime_ready,
)
from puripuly_heart.core.stt.backend import (
    STTBackend,
    STTBackendSession,
    STTBackendTranscriptEvent,
)

DEFAULT_SHERPA_NUM_THREADS = 3
LOCAL_QWEN_RECOGNIZER_SAMPLE_RATE_HZ = 16000
logger = logging.getLogger(__name__)


class LocalQwenSherpaLoadError(RuntimeError):
    """Raised when the local sherpa recognizer cannot be initialized."""


class LocalQwenSherpaInferenceError(RuntimeError):
    """Raised when local sherpa inference fails for an utterance."""


def _log_prefix(stream_label: str | None) -> str:
    prefix = "[STT][local_qwen]"
    if stream_label:
        return f"{prefix}[{stream_label}]"
    return prefix


def _pcm16le_duration_ms(pcm16le_size_bytes: int, sample_rate_hz: int) -> float:
    if pcm16le_size_bytes <= 0 or sample_rate_hz <= 0:
        return 0.0
    sample_count = pcm16le_size_bytes / 2.0
    return sample_count * 1000.0 / float(sample_rate_hz)


def create_local_qwen_sherpa_recognizer(
    *,
    model_dir: Path,
    num_threads: int,
    sample_rate_hz: int = 16000,
    feature_dim: int = 128,
    provider: str = "cpu",
) -> object:
    import sherpa_onnx

    qwen3_config = sherpa_onnx.OfflineQwen3ASRModelConfig(
        conv_frontend=str(model_dir / "conv_frontend.onnx"),
        encoder=str(model_dir / "encoder.int8.onnx"),
        decoder=str(model_dir / "decoder.int8.onnx"),
        tokenizer=str(model_dir / "tokenizer"),
        max_total_len=512,
        max_new_tokens=128,
        temperature=1e-6,
        top_p=0.8,
        seed=42,
    )
    model_config = sherpa_onnx.OfflineModelConfig(
        qwen3_asr=qwen3_config,
        num_threads=num_threads,
        debug=False,
        provider=provider,
    )
    feat_config = sherpa_onnx.FeatureExtractorConfig(
        sampling_rate=sample_rate_hz,
        feature_dim=feature_dim,
    )
    recognizer_config = sherpa_onnx.OfflineRecognizerConfig(
        feat_config=feat_config,
        model_config=model_config,
        decoding_method="greedy_search",
    )
    recognizer_module = importlib.import_module("sherpa_onnx.offline_recognizer")
    recognizer_cls = getattr(recognizer_module, "_Recognizer")
    return recognizer_cls(recognizer_config)


@dataclass(slots=True)
class LocalQwenSherpaSTTBackend(STTBackend):
    model_dir: Path
    sample_rate_hz: int = 16000
    num_threads: int = DEFAULT_SHERPA_NUM_THREADS
    feature_dim: int = 128
    provider: str = "cpu"
    stream_label: str | None = None
    language_hint: str | None = None
    hotwords: tuple[str, ...] = ()
    _recognizer: object | None = field(init=False, default=None, repr=False)
    _load_lock: asyncio.Lock = field(init=False, repr=False)
    _decode_lock: asyncio.Lock = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._load_lock = asyncio.Lock()
        self._decode_lock = asyncio.Lock()

    async def open_session(self) -> STTBackendSession:
        if self.sample_rate_hz not in (8000, 16000):
            raise ValueError("sample_rate_hz must be 8000 or 16000")
        if self.num_threads <= 0:
            raise ValueError("num_threads must be > 0")

        await self._ensure_recognizer()
        return _LocalQwenSherpaSession(backend=self)

    async def close(self) -> None:
        self._recognizer = None

    async def _ensure_recognizer(self) -> object:
        if self._recognizer is not None:
            return self._recognizer

        async with self._load_lock:
            if self._recognizer is not None:
                return self._recognizer
            await asyncio.to_thread(validate_local_stt_runtime_ready, self.model_dir)
            self._recognizer = await asyncio.to_thread(self._create_recognizer)
            return self._recognizer

    def _create_recognizer(self) -> object:
        try:
            return create_local_qwen_sherpa_recognizer(
                model_dir=self.model_dir,
                num_threads=self.num_threads,
                sample_rate_hz=LOCAL_QWEN_RECOGNIZER_SAMPLE_RATE_HZ,
                feature_dim=self.feature_dim,
                provider=self.provider,
            )
        except (
            ImportError
        ) as exc:  # pragma: no cover - import path exercised via load error wrapper
            raise LocalQwenSherpaLoadError("failed to import sherpa_onnx") from exc
        except Exception as exc:
            raise LocalQwenSherpaLoadError(str(exc)) from exc

    async def decode_pcm16le(self, pcm16le: bytes) -> str:
        recognizer = await self._ensure_recognizer()
        async with self._decode_lock:
            try:
                return await asyncio.to_thread(
                    self._decode_pcm16le_sync,
                    recognizer,
                    pcm16le,
                )
            except Exception as exc:
                raise LocalQwenSherpaInferenceError(str(exc)) from exc

    def _decode_pcm16le_sync(self, recognizer: object, pcm16le: bytes) -> str:
        samples = pcm16le_bytes_to_float32(pcm16le)
        if self.sample_rate_hz != LOCAL_QWEN_RECOGNIZER_SAMPLE_RATE_HZ:
            samples = resample_f32_linear(
                samples,
                from_rate_hz=self.sample_rate_hz,
                to_rate_hz=LOCAL_QWEN_RECOGNIZER_SAMPLE_RATE_HZ,
            )
        stream = recognizer.create_stream()
        set_option = getattr(stream, "set_option", None)
        if callable(set_option):
            if self.language_hint:
                set_option("language", self.language_hint)
            if self.hotwords:
                set_option("hotwords", ",".join(self.hotwords))
        stream.accept_waveform(LOCAL_QWEN_RECOGNIZER_SAMPLE_RATE_HZ, samples)
        recognizer.decode_stream(stream)
        result = getattr(stream, "result", None)
        text = getattr(result, "text", "")
        return str(text).strip()


@dataclass(slots=True)
class _LocalQwenSherpaSession(STTBackendSession):
    backend: LocalQwenSherpaSTTBackend
    _buffer: bytearray = field(init=False, repr=False)
    _events: asyncio.Queue[STTBackendTranscriptEvent | BaseException | None] = field(
        init=False,
        repr=False,
    )
    _closed: bool = field(init=False, default=False, repr=False)
    _closed_event_enqueued: bool = field(init=False, default=False, repr=False)
    _utterances: int = field(init=False, default=0, repr=False)
    _total_audio_ms: float = field(init=False, default=0.0, repr=False)
    _total_inference_ms: float = field(init=False, default=0.0, repr=False)
    _total_rtf: float = field(init=False, default=0.0, repr=False)
    _summary_logged: bool = field(init=False, default=False, repr=False)

    def __post_init__(self) -> None:
        self._buffer = bytearray()
        self._events = asyncio.Queue()

    async def send_audio(self, pcm16le: bytes) -> None:
        if self._closed:
            return
        self._buffer.extend(pcm16le)

    async def on_speech_end(self, *, trailing_silence_ms: int | None = None) -> None:
        _ = trailing_silence_ms
        if self._closed or not self._buffer:
            return

        pcm16le = bytes(self._buffer)
        self._buffer.clear()
        audio_ms = _pcm16le_duration_ms(len(pcm16le), self.backend.sample_rate_hz)

        try:
            started_at = time.perf_counter()
            text = await self.backend.decode_pcm16le(pcm16le)
            inference_ms = (time.perf_counter() - started_at) * 1000.0
        except Exception as exc:
            await self._events.put(exc)
            return

        rtf = inference_ms / audio_ms if audio_ms > 0 else 0.0
        self._utterances += 1
        self._total_audio_ms += audio_ms
        self._total_inference_ms += inference_ms
        self._total_rtf += rtf

        if text:
            logger.info(
                "%s Transcript: '%s' (final, audio_ms=%.1f, inference_ms=%.1f, rtf=%.3f)",
                _log_prefix(self.backend.stream_label),
                text,
                audio_ms,
                inference_ms,
                rtf,
            )
            await self._events.put(STTBackendTranscriptEvent(text=text, is_final=True))

    async def stop(self) -> None:
        self._log_summary_once()
        await self.close()

    async def close(self) -> None:
        self._log_summary_once()
        self._closed = True
        self._buffer.clear()
        if self._closed_event_enqueued:
            return
        self._closed_event_enqueued = True
        await self._events.put(None)

    async def events(self) -> AsyncIterator[STTBackendTranscriptEvent]:
        while True:
            event = await self._events.get()
            if event is None:
                break
            if isinstance(event, BaseException):
                raise event
            yield event

    def _log_summary_once(self) -> None:
        if self._summary_logged or self._utterances == 0:
            return
        self._summary_logged = True
        weighted_total_rtf = (
            self._total_inference_ms / self._total_audio_ms if self._total_audio_ms > 0 else 0.0
        )
        mean_rtf = self._total_rtf / self._utterances if self._utterances > 0 else 0.0
        logger.info(
            "%s Session summary: utterances=%s total_audio_ms=%.1f total_inference_ms=%.1f weighted_total_rtf=%.3f mean_rtf=%.3f",
            _log_prefix(self.backend.stream_label),
            self._utterances,
            self._total_audio_ms,
            self._total_inference_ms,
            weighted_total_rtf,
            mean_rtf,
        )
