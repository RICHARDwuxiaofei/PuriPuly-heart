from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from puripuly_heart.core.audio.source import (
    SoundDeviceAudioSource,
    resolve_sounddevice_input_device,
)


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"sample_rate_hz": 0}, "sample_rate_hz"),
        ({"channels": 0}, "channels"),
        ({"max_queue_frames": 0}, "max_queue_frames"),
    ],
)
def test_sounddevice_audio_source_rejects_invalid_params(kwargs, error):
    with pytest.raises(ValueError, match=error):
        SoundDeviceAudioSource(**kwargs)


def test_resolve_sounddevice_input_device_prefers_hostapi_default(monkeypatch):
    fake_sd = SimpleNamespace(
        query_hostapis=lambda: [{"name": "WASAPI", "default_input_device": 1}],
        query_devices=lambda: [
            {"max_input_channels": 0, "hostapi": 0, "name": "Out"},
            {"max_input_channels": 2, "hostapi": 0, "name": "Mic"},
        ],
    )
    monkeypatch.setitem(__import__("sys").modules, "sounddevice", fake_sd)

    assert resolve_sounddevice_input_device(host_api="WASAPI") == 1


def test_resolve_sounddevice_input_device_by_name(monkeypatch):
    fake_sd = SimpleNamespace(
        query_hostapis=lambda: [{"name": "ALSA", "default_input_device": 0}],
        query_devices=lambda: [
            {"max_input_channels": 2, "hostapi": 0, "name": "Mic"},
            {"max_input_channels": 0, "hostapi": 0, "name": "Out"},
        ],
    )
    monkeypatch.setitem(__import__("sys").modules, "sounddevice", fake_sd)

    assert resolve_sounddevice_input_device(device="Mic") == 0


def test_resolve_sounddevice_input_device_returns_none_when_blank() -> None:
    assert resolve_sounddevice_input_device() is None


def test_resolve_sounddevice_input_device_by_index_with_hostapi(monkeypatch):
    fake_sd = SimpleNamespace(
        query_hostapis=lambda: [{"name": "WASAPI", "default_input_device": 1}],
        query_devices=lambda: [
            {"max_input_channels": 2, "hostapi": 0, "name": "Mic0"},
            {"max_input_channels": 2, "hostapi": 0, "name": "Mic1"},
        ],
    )
    monkeypatch.setitem(__import__("sys").modules, "sounddevice", fake_sd)

    assert resolve_sounddevice_input_device(host_api="WASAPI", device="1") == 1


def test_resolve_sounddevice_input_device_rejects_mismatched_index(monkeypatch):
    fake_sd = SimpleNamespace(
        query_hostapis=lambda: [
            {"name": "ALSA", "default_input_device": 0},
            {"name": "WASAPI", "default_input_device": 1},
        ],
        query_devices=lambda: [
            {"max_input_channels": 0, "hostapi": 0, "name": "Out"},
            {"max_input_channels": 2, "hostapi": 1, "name": "Mic"},
        ],
    )
    monkeypatch.setitem(__import__("sys").modules, "sounddevice", fake_sd)

    assert resolve_sounddevice_input_device(host_api="ALSA", device="1") is None


def test_resolve_sounddevice_input_device_matches_name_with_hostapi(monkeypatch):
    fake_sd = SimpleNamespace(
        query_hostapis=lambda: [{"name": "ALSA", "default_input_device": 0}],
        query_devices=lambda: [
            {"max_input_channels": 2, "hostapi": 0, "name": "Mic"},
            {"max_input_channels": 2, "hostapi": 0, "name": "Mic2"},
        ],
    )
    monkeypatch.setitem(__import__("sys").modules, "sounddevice", fake_sd)

    assert resolve_sounddevice_input_device(host_api="ALSA", device="mic2") == 1


def test_resolve_sounddevice_input_device_handles_missing_hostapi(monkeypatch):
    fake_sd = SimpleNamespace(
        query_hostapis=lambda: [{"name": "ALSA", "default_input_device": 0}],
        query_devices=lambda: [
            {"max_input_channels": 1, "hostapi": None, "name": "Mic"},
        ],
    )
    monkeypatch.setitem(__import__("sys").modules, "sounddevice", fake_sd)

    assert resolve_sounddevice_input_device(device="0") == 0
    assert resolve_sounddevice_input_device(host_api="ALSA", device="Mic") is None


@pytest.mark.asyncio
async def test_sounddevice_audio_source_frames_and_close(monkeypatch):
    stream_ref: dict[str, object] = {}

    class FakeInputStream:
        def __init__(self, *, samplerate, channels, dtype, callback, device, blocksize):
            _ = (channels, dtype, device, blocksize)
            self.callback = callback
            self.samplerate = samplerate or 48000
            self.started = False
            self.stopped = False
            self.closed = False
            stream_ref["stream"] = self

        def start(self):
            self.started = True

        def stop(self):
            self.stopped = True

        def close(self):
            self.closed = True

    fake_sd = SimpleNamespace(InputStream=FakeInputStream)
    monkeypatch.setitem(__import__("sys").modules, "sounddevice", fake_sd)

    source = SoundDeviceAudioSource(sample_rate_hz=None, channels=1, max_queue_frames=1)
    stream = stream_ref["stream"]
    stream.callback(np.ones((4,), dtype=np.float32), None, None, "warn")
    stream.callback(np.ones((4,), dtype=np.float32), None, None, None)

    frame = await source.frames().__anext__()
    assert frame.sample_rate_hz == 48000
    np.testing.assert_allclose(frame.samples, np.ones((4,), dtype=np.float32))

    stopped_frames = source.frames()
    source._queue.sync_q.put_nowait(None)
    with pytest.raises(StopAsyncIteration):
        await stopped_frames.__anext__()

    source._queue.sync_q.put_nowait(None)
    await source.close()
    assert stream.stopped is True
    assert stream.closed is True

    await source.close()
    stream.callback(np.ones((2,), dtype=np.float32), None, None, None)
