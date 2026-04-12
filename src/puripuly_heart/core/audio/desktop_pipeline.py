from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import AsyncIterator

import numpy as np

from puripuly_heart.core.audio.format import float32_to_pcm16le_bytes
from puripuly_heart.core.audio.source import AudioSource
from puripuly_heart.core.audio.streaming_resampler import MonoFirstStreamingResampler

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DesktopPeerAudioFrame:
    samples: np.ndarray
    sample_rate_hz: int
    deepgram_pcm16le: bytes
    channels: int = 1


@dataclass(slots=True)
class DesktopPeerPipeline:
    source: AudioSource
    target_sample_rate_hz: int = 16000
    _logged_formats: set[tuple[int, int]] = field(default_factory=set, init=False, repr=False)

    async def frames(self) -> AsyncIterator[DesktopPeerAudioFrame]:
        resampler: MonoFirstStreamingResampler | None = None
        source_format: tuple[int, int] | None = None

        async for frame in self.source.frames():
            format_key = (frame.sample_rate_hz, frame.channels)
            if format_key not in self._logged_formats:
                self._logged_formats.add(format_key)
                logger.info(
                    "Desktop peer audio format: source_rate=%sHz source_channels=%s -> target_rate=%sHz",
                    frame.sample_rate_hz,
                    frame.channels,
                    self.target_sample_rate_hz,
                )

            frame_format = (frame.sample_rate_hz, frame.channels)
            if source_format is None:
                source_format = frame_format
                resampler = MonoFirstStreamingResampler(
                    input_sample_rate_hz=frame.sample_rate_hz,
                    output_sample_rate_hz=self.target_sample_rate_hz,
                    input_channels=frame.channels,
                )
            elif frame_format != source_format:
                raise ValueError(
                    "source audio format changed during streaming: "
                    f"expected {source_format[0]}Hz/{source_format[1]}ch, "
                    f"got {frame.sample_rate_hz}Hz/{frame.channels}ch"
                )

            assert resampler is not None
            normalized = resampler.resample_chunk(frame.samples)
            if normalized.size:
                yield self._build_output_frame(normalized.reshape(-1))

        if resampler is None:
            return

        tail = resampler.flush()
        if tail.size:
            yield self._build_output_frame(tail.reshape(-1))

    async def close(self) -> None:
        await self.source.close()

    def _build_output_frame(self, samples: np.ndarray) -> DesktopPeerAudioFrame:
        return DesktopPeerAudioFrame(
            samples=samples,
            sample_rate_hz=self.target_sample_rate_hz,
            channels=1,
            deepgram_pcm16le=float32_to_pcm16le_bytes(samples),
        )
