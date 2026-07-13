import asyncio
import sys
from types import SimpleNamespace

import pytest

from puripuly_heart.app.adapters.audio_device_query import ProductionAudioDeviceQuery
from puripuly_heart.app.ports.ui_settings import InteractionStatus


@pytest.mark.asyncio
async def test_audio_query_matches_settings_enumeration_and_closes_loopback(monkeypatch):
    terminated = []

    class Manager:
        def get_loopback_device_info_generator(self):
            return iter(({"name": "Speakers (loopback)"}, {"name": "Speakers (loopback)"}))

        def terminate(self):
            terminated.append(True)

    sounddevice = SimpleNamespace(
        query_hostapis=lambda: (
            {"name": "MME"},
            {"name": "Windows WASAPI"},
            {"name": "Windows DirectSound"},
        ),
        query_devices=lambda: (
            {"name": "MME Mic", "hostapi": 0, "max_input_channels": 1},
            {"name": "WASAPI Mic", "hostapi": 1, "max_input_channels": 2},
            {"name": "Missing Host", "max_input_channels": 1},
            {"name": "None Host", "hostapi": None, "max_input_channels": 1},
            {"name": "Output", "hostapi": 1, "max_input_channels": 0},
        ),
    )
    monkeypatch.setitem(sys.modules, "sounddevice", sounddevice)
    monkeypatch.setitem(sys.modules, "pyaudiowpatch", SimpleNamespace(PyAudio=Manager))

    result = await ProductionAudioDeviceQuery().query("Windows WASAPI (Compatibility Mode)")

    assert result.status == InteractionStatus.APPLIED
    assert result.host_apis == (
        "",
        "MME",
        "Windows WASAPI",
        "Windows WASAPI (Compatibility Mode)",
        "Windows DirectSound",
    )
    assert tuple(item.device_id for item in result.inputs) == ("", "WASAPI Mic")
    assert tuple(item.device_id for item in result.outputs) == ("", "Speakers (loopback)")
    assert result.inputs[0].is_default is True
    assert result.outputs[0].is_default is True
    assert terminated == [True]

    mme = await ProductionAudioDeviceQuery().query("MME")
    assert tuple(item.device_id for item in mme.inputs) == ("", "MME Mic")


@pytest.mark.asyncio
async def test_audio_query_maps_unavailable_platform(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "sounddevice",
        SimpleNamespace(query_hostapis=lambda: (_ for _ in ()).throw(RuntimeError("missing"))),
    )
    result = await ProductionAudioDeviceQuery().query()
    assert result.status == InteractionStatus.UNAVAILABLE
    assert result.detail_code == "sounddevice_unavailable"


@pytest.mark.asyncio
async def test_audio_query_cancellation_does_not_block_event_loop():
    class SlowQuery:
        async def query(self, host_api=""):
            _ = host_api
            await asyncio.sleep(30)

    task = asyncio.create_task(SlowQuery().query())
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
