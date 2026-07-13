import asyncio
from types import SimpleNamespace

import numpy as np
import pytest

from puripuly_heart.app.ports.application_settings import ApplicationSettingsSnapshot
from puripuly_heart.app.ports.ui_settings import MicrophoneTestStatus
from puripuly_heart.app.services.microphone_test import ApplicationMicrophoneTestService
from puripuly_heart.core.runtime.mic_test import MicTestRuntime


class Queries:
    async def snapshot(self):
        return ApplicationSettingsSnapshot(
            (
                (("audio.input_host_api",), "Windows WASAPI"),
                (("audio.input_device",), "Microphone"),
            ),
            "r1",
        )


class Source:
    def __init__(self, samples=(0.25, -0.75)) -> None:
        self.closed = False
        self.release = asyncio.Event()
        self.samples = samples

    async def close(self):
        self.closed = True
        self.release.set()

    async def _frames(self):
        yield SimpleNamespace(samples=self.samples)
        await self.release.wait()

    def frames(self):
        return self._frames()


class Factory:
    def __init__(self, source=None, error=None) -> None:
        self.source = source
        self.error = error
        self.calls = []

    async def create(self, *, host_api, device):
        self.calls.append((host_api, device))
        if self.error is not None:
            raise self.error
        return self.source


@pytest.mark.asyncio
async def test_microphone_service_runs_frames_and_awaits_stop():
    source = Source()
    service = ApplicationMicrophoneTestService(
        settings_queries=Queries(), runtime=MicTestRuntime(), source_factory=Factory(source)
    )
    result = await service.start()
    await asyncio.sleep(0)
    assert result.status == MicrophoneTestStatus.STARTED
    assert service.last_level == 0.75
    assert (await service.start()).status == MicrophoneTestStatus.ALREADY_ACTIVE
    assert (await service.stop()).status == MicrophoneTestStatus.STOPPED
    assert source.closed is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("samples", "expected"),
    [
        (np.array([0.1, -0.8], dtype=np.float32), 0.8),
        (np.array([[0.1, -0.2], [0.9, -0.3]], dtype=np.float32), 0.9),
        (np.array([], dtype=np.float32), 0.0),
    ],
)
async def test_microphone_service_handles_real_ndarray_shapes(samples, expected):
    source = Source(samples)
    service = ApplicationMicrophoneTestService(
        settings_queries=Queries(), runtime=MicTestRuntime(), source_factory=Factory(source)
    )
    await service.start()
    await asyncio.sleep(0)
    assert service.last_level == pytest.approx(expected)
    await service.stop()


@pytest.mark.asyncio
async def test_microphone_service_maps_missing_device_and_backend_failure():
    unavailable = ApplicationMicrophoneTestService(
        settings_queries=Queries(), runtime=MicTestRuntime(), source_factory=Factory()
    )
    assert (await unavailable.start()).status == MicrophoneTestStatus.UNAVAILABLE

    failed = ApplicationMicrophoneTestService(
        settings_queries=Queries(),
        runtime=MicTestRuntime(),
        source_factory=Factory(error=RuntimeError("backend unavailable")),
    )
    result = await failed.start()
    assert result.status == MicrophoneTestStatus.FAILED


@pytest.mark.asyncio
async def test_microphone_service_close_cancels_and_awaits_source():
    source = Source()
    service = ApplicationMicrophoneTestService(
        settings_queries=Queries(), runtime=MicTestRuntime(), source_factory=Factory(source)
    )
    await service.start()
    await service.close()
    assert source.closed is True
    assert service.is_closed is True
