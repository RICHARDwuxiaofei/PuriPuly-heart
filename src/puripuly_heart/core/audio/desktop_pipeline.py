from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import AsyncIterator

import numpy as np

from puripuly_heart.core.audio.format import (
    float32_to_pcm16le_bytes,
    normalize_audio_frame_f32,
)
from puripuly_heart.core.audio.source import AudioSource

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DesktopPeerAudioFrame:
    samples: np.ndarray
    sample_rate_hz: int
    deepgram_pcm16le: bytes


@dataclass(slots=True)
class DesktopPeerPipeline:
    source: AudioSource
    target_sample_rate_hz: int = 16000
    _logged_formats: set[tuple[int, int]] = field(default_factory=set, init=False, repr=False)

    async def frames(self) -> AsyncIterator[DesktopPeerAudioFrame]:
        async for frame in self.source.frames():
            normalized = normalize_audio_frame_f32(
                frame, target_sample_rate_hz=self.target_sample_rate_hz
            )
            format_key = (frame.sample_rate_hz, frame.channels)
            if format_key not in self._logged_formats:
                self._logged_formats.add(format_key)
                logger.info(
                    "Desktop peer audio format: source_rate=%sHz source_channels=%s -> target_rate=%sHz",
                    frame.sample_rate_hz,
                    frame.channels,
                    self.target_sample_rate_hz,
                )
            yield DesktopPeerAudioFrame(
                samples=normalized.samples,
                sample_rate_hz=normalized.sample_rate_hz,
                deepgram_pcm16le=float32_to_pcm16le_bytes(normalized.samples),
            )

    async def close(self) -> None:
        await self.source.close()
