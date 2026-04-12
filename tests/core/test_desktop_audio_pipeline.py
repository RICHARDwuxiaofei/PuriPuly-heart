from __future__ import annotations

import numpy as np
import pytest

from puripuly_heart.core.audio.desktop_pipeline import DesktopPeerPipeline
from puripuly_heart.core.audio.format import AudioFrameF32


class StubDesktopAudioSource:
    def __init__(self, frames):
        self._frames = frames
        self.closed = False

    async def frames(self):
        for frame in self._frames:
            yield frame

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_desktop_pipeline_outputs_16khz_vad_ready_frames():
    source = StubDesktopAudioSource(
        frames=[
            AudioFrameF32(
                sample_rate_hz=48000,
                samples=np.ones(4800, dtype=np.float32),
            )
        ]
    )
    pipeline = DesktopPeerPipeline(source=source)

    frames = [frame async for frame in pipeline.frames()]
    combined = np.concatenate([frame.samples for frame in frames])
    combined_pcm = b"".join(frame.deepgram_pcm16le for frame in frames)

    assert len(frames) >= 1
    assert all(frame.sample_rate_hz == 16000 for frame in frames)
    assert all(frame.samples.dtype == np.float32 for frame in frames)
    assert all(frame.samples.ndim == 1 for frame in frames)
    assert combined.shape == (1600,)
    assert len(combined_pcm) == 3200


@pytest.mark.asyncio
async def test_desktop_pipeline_downmixes_interleaved_multichannel_frames():
    source = StubDesktopAudioSource(
        frames=[
            AudioFrameF32(
                sample_rate_hz=16000,
                channels=2,
                samples=np.array([0.0, 1.0, 1.0, 0.0], dtype=np.float32),
            )
        ]
    )
    pipeline = DesktopPeerPipeline(source=source)

    frame = await pipeline.frames().__anext__()

    assert frame.sample_rate_hz == 16000
    assert np.allclose(frame.samples, np.array([0.5, 0.5], dtype=np.float32))


@pytest.mark.asyncio
async def test_desktop_pipeline_close_closes_underlying_source():
    source = StubDesktopAudioSource(frames=[])
    pipeline = DesktopPeerPipeline(source=source)

    await pipeline.close()

    assert source.closed is True
