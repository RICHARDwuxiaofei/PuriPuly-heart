from __future__ import annotations

from types import SimpleNamespace

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
