from __future__ import annotations

import numpy as np
import pytest

import puripuly_heart.app.headless_mic as headless_mic
import puripuly_heart.core.audio.streaming_resampler as streaming_resampler
from puripuly_heart.core.audio.desktop_pipeline import DesktopPeerPipeline
from puripuly_heart.core.audio.format import AudioFrameF32, float32_to_pcm16le_bytes
from puripuly_heart.core.audio.streaming_resampler import MonoFirstStreamingResampler


def test_resample_chunk_mixes_down_before_streaming_soxr_with_mq_quality(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stream_calls: list[tuple[object, ...]] = []

    class FakeResampleStream:
        def __init__(
            self,
            in_rate: int,
            out_rate: int,
            channels: int,
            *,
            dtype: str,
            quality: str,
        ) -> None:
            stream_calls.append(("init", in_rate, out_rate, channels, dtype, quality))

        def resample_chunk(self, samples: np.ndarray, *, last: bool = False) -> np.ndarray:
            stream_calls.append(("chunk", samples.copy(), last, None, None))
            return np.asarray(samples * 2.0, dtype=np.float32)

    monkeypatch.setattr(streaming_resampler.soxr, "ResampleStream", FakeResampleStream)

    resampler = MonoFirstStreamingResampler(
        input_sample_rate_hz=48000,
        output_sample_rate_hz=16000,
        input_channels=2,
    )

    output = resampler.resample_chunk(np.array([0.0, 1.0, 1.0, 0.0], dtype=np.float32))

    assert stream_calls[0] == ("init", 48000, 16000, 1, "float32", "MQ")
    np.testing.assert_allclose(stream_calls[1][1], np.array([0.5, 0.5], dtype=np.float32))
    assert stream_calls[1][2] is False
    np.testing.assert_allclose(output, np.array([1.0, 1.0], dtype=np.float32))
    assert output.dtype == np.float32


def test_16khz_noop_path_mixdowns_without_building_soxr_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_on_stream_init(*args: object, **kwargs: object) -> None:
        pytest.fail("16k no-op path should not create a soxr stream")

    monkeypatch.setattr(streaming_resampler.soxr, "ResampleStream", fail_on_stream_init)

    resampler = MonoFirstStreamingResampler(
        input_sample_rate_hz=16000,
        output_sample_rate_hz=16000,
        input_channels=2,
    )

    output = resampler.resample_chunk(np.array([[0.0, 1.0], [1.0, 0.0]], dtype=np.float32))

    np.testing.assert_allclose(output, np.array([0.5, 0.5], dtype=np.float32))
    assert output.dtype == np.float32


def test_flush_uses_last_true_and_rejects_future_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[np.ndarray, bool]] = []

    class FakeResampleStream:
        def __init__(
            self,
            in_rate: int,
            out_rate: int,
            channels: int,
            *,
            dtype: str,
            quality: str,
        ) -> None:
            assert (in_rate, out_rate, channels, dtype, quality) == (
                48000,
                16000,
                1,
                "float32",
                "MQ",
            )

        def resample_chunk(self, samples: np.ndarray, *, last: bool = False) -> np.ndarray:
            calls.append((samples.copy(), last))
            if last:
                return np.array([0.25, -0.25], dtype=np.float32)
            return np.empty((0,), dtype=np.float32)

    monkeypatch.setattr(streaming_resampler.soxr, "ResampleStream", FakeResampleStream)

    resampler = MonoFirstStreamingResampler(input_sample_rate_hz=48000, output_sample_rate_hz=16000)

    first = resampler.resample_chunk(np.array([0.0, 1.0], dtype=np.float32))
    tail = resampler.flush()

    assert first.size == 0
    assert calls[0][1] is False
    assert calls[1][1] is True
    assert calls[1][0].dtype == np.float32
    assert calls[1][0].size == 0
    np.testing.assert_allclose(tail, np.array([0.25, -0.25], dtype=np.float32))

    with pytest.raises(RuntimeError, match="already been flushed"):
        resampler.resample_chunk(np.array([0.0], dtype=np.float32))


class _StubAudioSource:
    def __init__(self, frames: list[AudioFrameF32]) -> None:
        self._frames = frames
        self.closed = False

    async def frames(self):
        for frame in self._frames:
            yield frame

    async def close(self) -> None:
        self.closed = True


class _StubVad:
    def __init__(self, *, chunk_samples: int) -> None:
        self.chunk_samples = chunk_samples
        self.chunks: list[np.ndarray] = []

    def process_chunk(self, chunk: np.ndarray) -> list[np.ndarray]:
        copied = chunk.copy()
        self.chunks.append(copied)
        return [copied]


class _StubSink:
    def __init__(self) -> None:
        self.events: list[np.ndarray] = []

    async def handle_vad_event(self, event: np.ndarray) -> None:
        self.events.append(event)


@pytest.mark.asyncio
async def test_run_audio_vad_loop_uses_one_streaming_resampler_and_flushes_tail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[object, ...]] = []

    class FakeResampler:
        def __init__(
            self,
            input_sample_rate_hz: int,
            output_sample_rate_hz: int = 16000,
            input_channels: int = 1,
        ) -> None:
            assert (input_sample_rate_hz, output_sample_rate_hz, input_channels) == (
                48000,
                16000,
                2,
            )
            self._chunk_calls = 0
            calls.append(("init", input_sample_rate_hz, output_sample_rate_hz, input_channels))

        def resample_chunk(self, samples: np.ndarray) -> np.ndarray:
            self._chunk_calls += 1
            calls.append(("chunk", self._chunk_calls, samples.copy()))
            if self._chunk_calls == 1:
                return np.array([0.25], dtype=np.float32)
            return np.empty((0,), dtype=np.float32)

        def flush(self) -> np.ndarray:
            calls.append(("flush",))
            return np.array([0.75], dtype=np.float32)

    monkeypatch.setattr(headless_mic, "MonoFirstStreamingResampler", FakeResampler, raising=False)

    source = _StubAudioSource(
        [
            AudioFrameF32(
                samples=np.array([0.0, 1.0, 1.0, 0.0], dtype=np.float32),
                sample_rate_hz=48000,
                channels=2,
            ),
            AudioFrameF32(
                samples=np.array([1.0, 0.0, 0.0, 1.0], dtype=np.float32),
                sample_rate_hz=48000,
                channels=2,
            ),
        ]
    )
    vad = _StubVad(chunk_samples=2)
    sink = _StubSink()

    await headless_mic.run_audio_vad_loop(
        source=source,
        vad=vad,
        sink=sink,
        target_sample_rate_hz=16000,
    )

    assert [call[0] for call in calls] == ["init", "chunk", "chunk", "flush"]
    assert len(vad.chunks) == 1
    np.testing.assert_allclose(vad.chunks[0], np.array([0.25, 0.75], dtype=np.float32))
    assert len(sink.events) == 1
    np.testing.assert_allclose(sink.events[0], np.array([0.25, 0.75], dtype=np.float32))


@pytest.mark.asyncio
async def test_desktop_pipeline_uses_one_streaming_resampler_and_yields_flush_tail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[object, ...]] = []

    class FakeResampler:
        def __init__(
            self,
            input_sample_rate_hz: int,
            output_sample_rate_hz: int = 16000,
            input_channels: int = 1,
        ) -> None:
            assert (input_sample_rate_hz, output_sample_rate_hz, input_channels) == (
                48000,
                16000,
                2,
            )
            self._chunk_calls = 0
            calls.append(("init", input_sample_rate_hz, output_sample_rate_hz, input_channels))

        def resample_chunk(self, samples: np.ndarray) -> np.ndarray:
            self._chunk_calls += 1
            calls.append(("chunk", self._chunk_calls, samples.copy()))
            if self._chunk_calls == 2:
                return np.array([0.1, -0.1], dtype=np.float32)
            return np.empty((0,), dtype=np.float32)

        def flush(self) -> np.ndarray:
            calls.append(("flush",))
            return np.array([0.25], dtype=np.float32)

    monkeypatch.setattr(
        "puripuly_heart.core.audio.desktop_pipeline.MonoFirstStreamingResampler",
        FakeResampler,
    )

    pipeline = DesktopPeerPipeline(
        source=_StubAudioSource(
            [
                AudioFrameF32(
                    samples=np.array([0.0, 1.0, 1.0, 0.0], dtype=np.float32),
                    sample_rate_hz=48000,
                    channels=2,
                ),
                AudioFrameF32(
                    samples=np.array([1.0, 0.0, 0.0, 1.0], dtype=np.float32),
                    sample_rate_hz=48000,
                    channels=2,
                ),
            ]
        ),
        target_sample_rate_hz=16000,
    )

    frames = [frame async for frame in pipeline.frames()]

    assert [call[0] for call in calls] == ["init", "chunk", "chunk", "flush"]
    assert len(frames) == 2
    np.testing.assert_allclose(frames[0].samples, np.array([0.1, -0.1], dtype=np.float32))
    assert frames[0].sample_rate_hz == 16000
    assert frames[0].deepgram_pcm16le == float32_to_pcm16le_bytes(frames[0].samples)
    np.testing.assert_allclose(frames[1].samples, np.array([0.25], dtype=np.float32))
    assert frames[1].sample_rate_hz == 16000
    assert frames[1].deepgram_pcm16le == float32_to_pcm16le_bytes(frames[1].samples)


@pytest.mark.asyncio
async def test_desktop_pipeline_output_integrates_with_run_audio_vad_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeDesktopResampler:
        def __init__(
            self,
            input_sample_rate_hz: int,
            output_sample_rate_hz: int = 16000,
            input_channels: int = 1,
        ) -> None:
            assert (input_sample_rate_hz, output_sample_rate_hz, input_channels) == (
                48000,
                16000,
                2,
            )

        def resample_chunk(self, samples: np.ndarray) -> np.ndarray:
            return np.array([0.4, 0.6], dtype=np.float32)

        def flush(self) -> np.ndarray:
            return np.empty((0,), dtype=np.float32)

    downstream_calls: list[tuple[object, ...]] = []

    class FakeVadLoopResampler:
        def __init__(
            self,
            input_sample_rate_hz: int,
            output_sample_rate_hz: int = 16000,
            input_channels: int = 1,
        ) -> None:
            downstream_calls.append(
                ("init", input_sample_rate_hz, output_sample_rate_hz, input_channels)
            )

        def resample_chunk(self, samples: np.ndarray) -> np.ndarray:
            downstream_calls.append(("chunk", samples.copy()))
            return np.asarray(samples, dtype=np.float32)

        def flush(self) -> np.ndarray:
            downstream_calls.append(("flush",))
            return np.empty((0,), dtype=np.float32)

    monkeypatch.setattr(
        "puripuly_heart.core.audio.desktop_pipeline.MonoFirstStreamingResampler",
        FakeDesktopResampler,
    )
    monkeypatch.setattr(
        headless_mic,
        "MonoFirstStreamingResampler",
        FakeVadLoopResampler,
        raising=False,
    )

    pipeline = DesktopPeerPipeline(
        source=_StubAudioSource(
            [
                AudioFrameF32(
                    samples=np.array([0.0, 1.0, 1.0, 0.0], dtype=np.float32),
                    sample_rate_hz=48000,
                    channels=2,
                )
            ]
        ),
        target_sample_rate_hz=16000,
    )
    vad = _StubVad(chunk_samples=2)
    sink = _StubSink()

    await headless_mic.run_audio_vad_loop(
        source=pipeline,
        vad=vad,
        sink=sink,
        target_sample_rate_hz=16000,
    )

    assert downstream_calls[0] == ("init", 16000, 16000, 1)
    np.testing.assert_allclose(downstream_calls[1][1], np.array([0.4, 0.6], dtype=np.float32))
    assert downstream_calls[2] == ("flush",)
    np.testing.assert_allclose(vad.chunks[0], np.array([0.4, 0.6], dtype=np.float32))
    np.testing.assert_allclose(sink.events[0], np.array([0.4, 0.6], dtype=np.float32))


@pytest.mark.asyncio
async def test_run_audio_vad_loop_raises_on_source_sample_rate_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeResampler:
        def __init__(
            self,
            input_sample_rate_hz: int,
            output_sample_rate_hz: int = 16000,
            input_channels: int = 1,
        ) -> None:
            assert (input_sample_rate_hz, output_sample_rate_hz, input_channels) == (
                48000,
                16000,
                2,
            )

        def resample_chunk(self, samples: np.ndarray) -> np.ndarray:
            return np.asarray(samples[:0], dtype=np.float32)

        def flush(self) -> np.ndarray:
            return np.empty((0,), dtype=np.float32)

    monkeypatch.setattr(headless_mic, "MonoFirstStreamingResampler", FakeResampler, raising=False)

    source = _StubAudioSource(
        [
            AudioFrameF32(
                samples=np.array([0.0, 1.0, 1.0, 0.0], dtype=np.float32),
                sample_rate_hz=48000,
                channels=2,
            ),
            AudioFrameF32(
                samples=np.array([1.0, 0.0, 0.0, 1.0], dtype=np.float32),
                sample_rate_hz=44100,
                channels=2,
            ),
        ]
    )

    with pytest.raises(ValueError, match="source audio format changed"):
        await headless_mic.run_audio_vad_loop(
            source=source,
            vad=_StubVad(chunk_samples=2),
            sink=_StubSink(),
            target_sample_rate_hz=16000,
        )


@pytest.mark.asyncio
async def test_desktop_pipeline_raises_on_source_channel_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeResampler:
        def __init__(
            self,
            input_sample_rate_hz: int,
            output_sample_rate_hz: int = 16000,
            input_channels: int = 1,
        ) -> None:
            assert (input_sample_rate_hz, output_sample_rate_hz, input_channels) == (
                48000,
                16000,
                2,
            )

        def resample_chunk(self, samples: np.ndarray) -> np.ndarray:
            return np.asarray(samples[:0], dtype=np.float32)

        def flush(self) -> np.ndarray:
            return np.empty((0,), dtype=np.float32)

    monkeypatch.setattr(
        "puripuly_heart.core.audio.desktop_pipeline.MonoFirstStreamingResampler",
        FakeResampler,
    )

    pipeline = DesktopPeerPipeline(
        source=_StubAudioSource(
            [
                AudioFrameF32(
                    samples=np.array([0.0, 1.0, 1.0, 0.0], dtype=np.float32),
                    sample_rate_hz=48000,
                    channels=2,
                ),
                AudioFrameF32(
                    samples=np.array([0.0, 1.0], dtype=np.float32),
                    sample_rate_hz=48000,
                    channels=1,
                ),
            ]
        ),
        target_sample_rate_hz=16000,
    )

    with pytest.raises(ValueError, match="source audio format changed"):
        _ = [frame async for frame in pipeline.frames()]
