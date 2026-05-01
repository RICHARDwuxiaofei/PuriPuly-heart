from __future__ import annotations

import asyncio
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
    try:
        stream = stream_ref["stream"]
        stream.callback(np.ones((4,), dtype=np.float32), None, None, "warn")
        stream.callback(np.ones((4,), dtype=np.float32), None, None, None)

        frame = await source.frames().__anext__()
        assert frame.sample_rate_hz == 48000
        assert frame.channels == 1
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
    finally:
        await source.close()


def test_sounddevice_audio_source_does_not_pass_wasapi_settings_by_default(monkeypatch):
    stream_kwargs: dict[str, object] = {}

    class FakeInputStream:
        def __init__(self, **kwargs):
            stream_kwargs.update(kwargs)
            self.samplerate = 48000

        def start(self):
            pass

        def stop(self):
            pass

        def close(self):
            pass

    fake_sd = SimpleNamespace(InputStream=FakeInputStream)
    monkeypatch.setitem(__import__("sys").modules, "sounddevice", fake_sd)

    source = SoundDeviceAudioSource()
    try:
        assert "extra_settings" not in stream_kwargs
        assert stream_kwargs["blocksize"] == 0
        assert stream_kwargs["samplerate"] is None
        assert stream_kwargs["channels"] == 1
        assert stream_kwargs["dtype"] == "float32"
    finally:
        asyncio.run(source.close())


def test_sounddevice_audio_source_passes_wasapi_auto_convert_settings(monkeypatch):
    stream_kwargs: dict[str, object] = {}
    wasapi_settings: list[object] = []

    class FakeWasapiSettings:
        def __init__(self, *, exclusive, auto_convert):
            self.exclusive = exclusive
            self.auto_convert = auto_convert
            wasapi_settings.append(self)

    class FakeInputStream:
        def __init__(self, **kwargs):
            stream_kwargs.update(kwargs)
            self.samplerate = 48000

        def start(self):
            pass

        def stop(self):
            pass

        def close(self):
            pass

    fake_sd = SimpleNamespace(
        InputStream=FakeInputStream,
        WasapiSettings=FakeWasapiSettings,
    )
    monkeypatch.setitem(__import__("sys").modules, "sounddevice", fake_sd)

    source = SoundDeviceAudioSource(wasapi_auto_convert=True, wasapi_exclusive=False)
    try:
        assert len(wasapi_settings) == 1
        assert stream_kwargs["extra_settings"] is wasapi_settings[0]
        assert wasapi_settings[0].exclusive is False
        assert wasapi_settings[0].auto_convert is True
    finally:
        asyncio.run(source.close())


def test_sounddevice_audio_source_rejects_wasapi_settings_when_unavailable(monkeypatch):
    stream_created = False

    class FakeInputStream:
        def __init__(self, **kwargs):
            nonlocal stream_created
            stream_created = True
            self.samplerate = 48000

        def start(self):
            pass

        def stop(self):
            pass

        def close(self):
            pass

    fake_sd = SimpleNamespace(InputStream=FakeInputStream)
    monkeypatch.setitem(__import__("sys").modules, "sounddevice", fake_sd)

    with pytest.raises(RuntimeError, match="WASAPI settings support is unavailable"):
        SoundDeviceAudioSource(wasapi_auto_convert=True)

    assert stream_created is False
