from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from puripuly_heart.core.audio.desktop_source import (
    DesktopLoopbackAudioSource,
    DesktopLoopbackDeviceResolver,
)


@pytest.mark.asyncio
async def test_desktop_loopback_source_yields_float32_frames(monkeypatch):
    stream_ref: dict[str, object] = {}
    manager_ref: dict[str, object] = {}

    class FakeStream:
        def __init__(self, *, stream_callback, **kwargs):
            self.stream_callback = stream_callback
            self.kwargs = kwargs
            self.started = False
            self.stopped = False
            self.closed = False
            stream_ref["stream"] = self

        def start_stream(self):
            self.started = True

        def stop_stream(self):
            self.stopped = True

        def close(self):
            self.closed = True

    class FakePyAudioManager:
        def __init__(self):
            self.terminated = False
            manager_ref["manager"] = self

        def get_loopback_device_info_generator(self):
            yield {
                "index": 7,
                "name": "Headphones (Loopback)",
                "maxInputChannels": 2,
                "defaultSampleRate": 48000.0,
            }

        def get_default_wasapi_loopback(self):
            return {
                "index": 7,
                "name": "Headphones (Loopback)",
                "maxInputChannels": 2,
                "defaultSampleRate": 48000.0,
            }

        def open(self, **kwargs):
            return FakeStream(**kwargs)

        def terminate(self):
            self.terminated = True

    fake_pyaudio = SimpleNamespace(
        PyAudio=FakePyAudioManager,
        paContinue=0,
        paFloat32=1,
    )

    monkeypatch.setattr("platform.system", lambda: "Windows")
    monkeypatch.setitem(__import__("sys").modules, "pyaudiowpatch", fake_pyaudio)

    source = DesktopLoopbackAudioSource(device_name="Headphones (Loopback)")
    stream = stream_ref["stream"]
    samples = np.array([0.25, -0.25, 0.5, -0.5], dtype=np.float32)
    stream.stream_callback(samples.tobytes(), 2, None, 0)

    frame = await source.frames().__anext__()
    assert source.resolved_device_name == "Headphones (Loopback)"
    assert frame.sample_rate_hz == 48000
    assert frame.channels == 2
    assert frame.samples.dtype == np.float32
    assert frame.samples.ndim == 1
    np.testing.assert_allclose(frame.samples, samples)

    await source.close()
    assert stream.started is True
    assert stream.stopped is True
    assert stream.closed is True
    assert manager_ref["manager"].terminated is True


def test_desktop_loopback_source_falls_back_to_default_output_when_saved_device_missing():
    resolver = DesktopLoopbackDeviceResolver(
        devices=["Default Speakers"],
        default_device="Default Speakers",
    )

    resolved = resolver.resolve(saved_device_name="Missing Headphones")

    assert resolved == "Default Speakers"


def test_desktop_loopback_source_prefers_exact_saved_device_match():
    resolver = DesktopLoopbackDeviceResolver(
        devices=["Default Speakers", "Headphones (Loopback)"],
        default_device="Default Speakers",
    )

    resolved = resolver.resolve(saved_device_name="Headphones (Loopback)")

    assert resolved == "Headphones (Loopback)"


@pytest.mark.asyncio
async def test_desktop_loopback_source_uses_output_channel_count_when_input_channels_missing(
    monkeypatch,
):
    stream_ref: dict[str, object] = {}

    class FakeStream:
        def __init__(self, *, stream_callback, **kwargs):
            self.stream_callback = stream_callback
            self.kwargs = kwargs
            stream_ref["stream"] = self

        def start_stream(self):
            return None

        def stop_stream(self):
            return None

        def close(self):
            return None

    class FakePyAudioManager:
        def get_loopback_device_info_generator(self):
            yield {
                "index": 11,
                "name": "Speakers (Loopback)",
                "maxOutputChannels": 6,
                "defaultSampleRate": 48000.0,
            }

        def get_default_wasapi_loopback(self):
            return {
                "index": 11,
                "name": "Speakers (Loopback)",
                "maxOutputChannels": 6,
                "defaultSampleRate": 48000.0,
            }

        def open(self, **kwargs):
            return FakeStream(**kwargs)

        def terminate(self):
            return None

    fake_pyaudio = SimpleNamespace(
        PyAudio=FakePyAudioManager,
        paContinue=0,
        paFloat32=1,
    )

    monkeypatch.setattr("platform.system", lambda: "Windows")
    monkeypatch.setitem(__import__("sys").modules, "pyaudiowpatch", fake_pyaudio)

    source = DesktopLoopbackAudioSource(device_name="Speakers (Loopback)")
    assert source.resolved_device_name == "Speakers (Loopback)"
    assert stream_ref["stream"].kwargs["channels"] == 6
    await source.close()
