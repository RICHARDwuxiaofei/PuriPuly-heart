from __future__ import annotations

import asyncio
import contextlib

from puripuly_heart.app.ports.ui_settings import (
    AudioDeviceOption,
    AudioDeviceQueryResult,
    InteractionStatus,
)
from puripuly_heart.config.audio_host_api import (
    WINDOWS_DIRECTSOUND_HOST_API,
    WINDOWS_MME_HOST_API,
    WINDOWS_WASAPI_COMPATIBILITY_HOST_API,
    WINDOWS_WASAPI_HOST_API,
    normalize_input_host_api,
)


class ProductionAudioDeviceQuery:
    async def query(self, host_api: str = "") -> AudioDeviceQueryResult:
        return await asyncio.to_thread(self._query, host_api)

    @staticmethod
    def _query(host_api: str) -> AudioDeviceQueryResult:
        try:
            import sounddevice

            raw_host_apis = tuple(sounddevice.query_hostapis())
            raw_devices = tuple(sounddevice.query_devices())
        except Exception:
            return AudioDeviceQueryResult(
                InteractionStatus.UNAVAILABLE, detail_code="sounddevice_unavailable"
            )
        available = {
            str(item.get("name", "") or "").strip().casefold(): index
            for index, item in enumerate(raw_host_apis)
        }
        host_apis = [""]
        for name in (
            WINDOWS_MME_HOST_API,
            WINDOWS_WASAPI_HOST_API,
            WINDOWS_WASAPI_COMPATIBILITY_HOST_API,
            WINDOWS_DIRECTSOUND_HOST_API,
        ):
            actual = normalize_input_host_api(name).actual_host_api.casefold()
            if actual in available:
                host_apis.append(name)
        profile = normalize_input_host_api(host_api)
        selected_index = (
            available.get(profile.actual_host_api.casefold()) if profile.actual_host_api else None
        )
        inputs = [AudioDeviceOption("", host_api, "", True)]
        seen_inputs = set()
        for device in raw_devices:
            if int(device.get("max_input_channels", 0) or 0) <= 0:
                continue
            raw_host_api = device.get("hostapi", -1)
            device_host = -1 if raw_host_api is None else int(raw_host_api)
            if selected_index is not None and device_host != selected_index:
                continue
            name = str(device.get("name", "") or "").strip()
            if name and name not in seen_inputs:
                seen_inputs.add(name)
                inputs.append(AudioDeviceOption(name, host_api, name))
        outputs = [AudioDeviceOption("", "loopback", "", True)]
        manager = None
        try:
            import pyaudiowpatch

            manager = pyaudiowpatch.PyAudio()
            seen_outputs = set()
            for item in manager.get_loopback_device_info_generator():
                name = str(item.get("name", "") or "").strip()
                if name and name not in seen_outputs:
                    seen_outputs.add(name)
                    outputs.append(AudioDeviceOption(name, "loopback", name))
        except Exception:
            pass
        finally:
            if manager is not None:
                with contextlib.suppress(Exception):
                    manager.terminate()
        return AudioDeviceQueryResult(
            InteractionStatus.APPLIED,
            tuple(host_apis),
            tuple(inputs),
            tuple(outputs),
        )


__all__ = ["ProductionAudioDeviceQuery"]
