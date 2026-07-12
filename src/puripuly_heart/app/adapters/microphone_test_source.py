from __future__ import annotations

import asyncio

from puripuly_heart.core.audio.source import (
    SoundDeviceAudioSource,
    determine_self_mic_capture_channels,
    observe_microphone_test_route,
)


class ProductionMicrophoneTestSourceFactory:
    async def create(self, *, host_api: str, device: str):  # noqa: ANN201
        observation = await asyncio.to_thread(
            observe_microphone_test_route,
            saved_host_api=host_api,
            requested_device=device,
        )
        if not observation.should_attempt_open or observation.resolved_device_idx is None:
            return None
        decision = await asyncio.to_thread(
            determine_self_mic_capture_channels,
            device_idx=observation.resolved_device_idx,
            internal_channels=1,
        )
        return await asyncio.to_thread(
            SoundDeviceAudioSource,
            sample_rate_hz=None,
            channels=decision.preferred_capture_channels,
            device=observation.resolved_device_idx,
            wasapi_auto_convert=observation.wasapi_auto_convert,
            wasapi_exclusive=observation.wasapi_exclusive,
        )


__all__ = ["ProductionMicrophoneTestSourceFactory"]
